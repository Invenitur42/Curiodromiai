"""
Local RAG (retrieval-augmented generation) over the user's own documents.

Ingest: point at a folder of .pdf/.txt/.md files -> extract text -> split
into overlapping chunks -> embed each chunk with a local embedding model
(via Ollama's /api/embeddings) -> store {text, embedding, source} in a
local JSON index on disk.

Retrieve: embed the incoming message the same way, compute cosine
similarity against every stored chunk, return the top few best-matching
chunks. app.py adds these to the model's context for that one turn only -
they are never written into conversation memory/history, so the archive
and summaries stay about the conversation, not the library.

Requires an embedding model pulled in Ollama, e.g.:
    ollama pull nomic-embed-text
"""

import json
import math
import os
import re
import threading

import requests
from pypdf import PdfReader

OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"

DATA_DIR = os.path.join(os.path.expanduser("~"), ".curiodromiai")
INDEX_FILE = os.path.join(DATA_DIR, "rag_index.json")

CHUNK_SIZE = 800      # characters per chunk (roughly 150-200 words)
CHUNK_OVERLAP = 150   # so an idea split across a chunk boundary isn't lost
TOP_K = 4             # how many chunks to pull in per question
MIN_SIMILARITY = 0.15  # below this, a match is probably noise - skip it

SUPPORTED_EXTENSIONS = (".pdf", ".txt", ".md")


def _empty_index():
    return {"chunks": []}  # each: {"id", "text", "embedding", "source"}


class RagIndex:
    def __init__(self):
        self.index = self._load()
        self._lock = threading.RLock()
        self.progress = {"active": False, "file": None, "total_files": 0, "results": []}

    def _load(self):
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return _empty_index()

    def _save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp_path = INDEX_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.index, f)
        os.replace(tmp_path, INDEX_FILE)

    # ---- browsing / removing what's indexed -------------------------------

    def sources(self):
        counts = {}
        for c in self.index["chunks"]:
            counts[c["source"]] = counts.get(c["source"], 0) + 1
        return [{"source": s, "chunks": n} for s, n in sorted(counts.items())]

    def remove_source(self, source):
        with self._lock:
            before = len(self.index["chunks"])
            self.index["chunks"] = [c for c in self.index["chunks"] if c["source"] != source]
            self._save()
            return before - len(self.index["chunks"])

    def clear(self):
        with self._lock:
            self.index = _empty_index()
            self._save()

    # ---- ingestion ----------------------------------------------------------

    def _embed(self, text):
        r = requests.post(
            OLLAMA_EMBED_URL, json={"model": EMBED_MODEL, "prompt": text}, timeout=60
        )
        r.raise_for_status()
        return r.json()["embedding"]

    def _chunk_text(self, text):
        text = re.sub(r"\s+", " ", text).strip()
        chunks = []
        start = 0
        while start < len(text):
            end = start + CHUNK_SIZE
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start = end - CHUNK_OVERLAP
        return chunks

    def _extract_text(self, path):
        ext = path.rsplit(".", 1)[-1].lower()
        if ext == "pdf":
            reader = PdfReader(path)
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    def ingest_file(self, path):
        """Extracts, chunks, embeds, and stores one file. Returns chunks added."""
        filename = os.path.basename(path)
        text = self._extract_text(path)
        chunks = self._chunk_text(text)
        added = 0
        new_entries = []
        for i, chunk in enumerate(chunks):
            try:
                embedding = self._embed(chunk)
            except requests.exceptions.RequestException:
                continue  # skip this chunk rather than fail the whole file
            new_entries.append(
                {"id": f"{filename}:{i}", "text": chunk, "embedding": embedding, "source": filename}
            )
            added += 1
        with self._lock:
            # replace any previous chunks from this same filename first,
            # so re-indexing a file doesn't leave stale duplicates behind
            self.index["chunks"] = [c for c in self.index["chunks"] if c["source"] != filename]
            self.index["chunks"].extend(new_entries)
            self._save()
        return added

    def ingest_folder_async(self, folder):
        """Non-blocking: indexing can take a while (one embedding call per
        chunk), so this runs on a background thread and progress is polled
        via get_progress(). Returns False if a run is already in progress."""
        with self._lock:
            if self.progress["active"]:
                return False
            self.progress = {"active": True, "file": None, "total_files": 0, "results": []}
        threading.Thread(target=self._ingest_folder_worker, args=(folder,), daemon=True).start()
        return True

    def _ingest_folder_worker(self, folder):
        paths = []
        for root, _dirs, files in os.walk(folder):
            for fname in files:
                if fname.lower().endswith(SUPPORTED_EXTENSIONS):
                    paths.append(os.path.join(root, fname))

        with self._lock:
            self.progress["total_files"] = len(paths)

        for path in paths:
            fname = os.path.basename(path)
            with self._lock:
                self.progress["file"] = fname
            try:
                added = self.ingest_file(path)
                result = {"file": fname, "chunks_added": added, "ok": True}
            except Exception as exc:  # noqa: BLE001 - report and keep going
                result = {"file": fname, "error": str(exc), "ok": False}
            with self._lock:
                self.progress["results"].append(result)

        with self._lock:
            self.progress["active"] = False
            self.progress["file"] = None

    def get_progress(self):
        with self._lock:
            return dict(self.progress)

    # ---- retrieval ------------------------------------------------------

    @staticmethod
    def _cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    def search(self, query, top_k=TOP_K):
        if not self.index["chunks"]:
            return []
        try:
            q_embedding = self._embed(query)
        except requests.exceptions.RequestException:
            return []  # e.g. the embedding model isn't pulled - fail quietly
        scored = [(self._cosine(q_embedding, c["embedding"]), c) for c in self.index["chunks"]]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {"text": c["text"], "source": c["source"], "score": round(score, 3)}
            for score, c in scored[:top_k]
            if score >= MIN_SIMILARITY
        ]
