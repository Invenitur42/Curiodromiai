"""
Per-conversation memory, archived and categorized by day.

Within the CURRENT conversation:
  - Raw messages accumulate.
  - Every 10 raw messages (5 exchanges) are folded into one short summary
    and the raw buffer is cleared. Short summaries just accumulate.
When the conversation ends (New Chat / window close):
  - Remaining raw + short summaries are combined into one final summary.
  - Final summary is written under today\'s date in memory.json with a
    short category label chosen by the model.
  - Raw transcript is discarded.
Across sessions the model receives a brief digest of recent past summaries.
"""

import json
import os
from datetime import date, datetime
from pathlib import Path

import requests

OLLAMA_BASE = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:7b"
MEMORY_PATH = Path(__file__).with_name("memory.json")
FOLD_EVERY = 10  # raw messages


class ConversationMemory:
    def __init__(self):
        self.raw = []           # current conversation raw turns
        self.summaries = []     # folded short summaries for current convo
        self._load()

    def _load(self):
        if MEMORY_PATH.exists():
            try:
                with open(MEMORY_PATH, encoding="utf-8") as f:
                    self.archive = json.load(f)
            except Exception:
                self.archive = {}
        else:
            self.archive = {}

    def _save(self):
        with open(MEMORY_PATH, "w", encoding="utf-8") as f:
            json.dump(self.archive, f, indent=2, ensure_ascii=False)

    def record(self, user: str, assistant: str):
        self.raw.append({"role": "user", "content": user})
        self.raw.append({"role": "assistant", "content": assistant})
        if len(self.raw) >= FOLD_EVERY:
            self._fold()

    def _fold(self):
        if not self.raw:
            return
        text = "\n".join(f"{m[\'role\'].upper()}: {m[\'content\']}" for m in self.raw)
        prompt = (
            "Summarize the following conversation fragment in 2-4 concise sentences. "
            "Keep key facts, decisions, and open questions. No preamble.\n\n" + text
        )
        try:
            r = requests.post(
                f"{OLLAMA_BASE}/api/generate",
                json={"model": DEFAULT_MODEL, "prompt": prompt, "stream": False},
                timeout=60,
            )
            r.raise_for_status()
            summary = r.json().get("response", "").strip()
        except Exception:
            summary = text[:400] + "..."
        self.summaries.append(summary)
        self.raw.clear()

    def end_conversation(self):
        if not self.raw and not self.summaries:
            return
        self._fold()  # fold any remaining
        if not self.summaries:
            return
        combined = "\n".join(self.summaries)
        # Ask model for a short category + final summary
        prompt = (
            "Given these conversation summaries, produce:\n"
            "1. A short category label (2-4 words)\n"
            "2. One final paragraph summary of the whole conversation.\n"
            "Format exactly:\nCATEGORY: ...\nSUMMARY: ...\n\n" + combined
        )
        try:
            r = requests.post(
                f"{OLLAMA_BASE}/api/generate",
                json={"model": DEFAULT_MODEL, "prompt": prompt, "stream": False},
                timeout=60,
            )
            r.raise_for_status()
            out = r.json().get("response", "").strip()
            category = "general"
            summary = combined
            for line in out.splitlines():
                if line.upper().startswith("CATEGORY:"):
                    category = line.split(":", 1)[1].strip() or "general"
                elif line.upper().startswith("SUMMARY:"):
                    summary = line.split(":", 1)[1].strip() or combined
        except Exception:
            category = "general"
            summary = combined

        today = date.today().isoformat()
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "category": category,
            "summary": summary,
        }
        self.archive.setdefault(today, []).append(entry)
        self._save()
        self.raw.clear()
        self.summaries.clear()

    def build_system_prompt(self) -> str:
        parts = [
            "You are Athenaeum, a helpful local assistant. Keep answers concise and useful."
        ]
        # Recent past summaries
        recent = []
        for day in sorted(self.archive.keys(), reverse=True)[:3]:
            for e in self.archive[day][-3:]:
                recent.append(f"[{e.get(\'category\', \'\')}] {e.get(\'summary\', \'\')}")
        if recent:
            parts.append("Recent memory (older conversations):\n" + "\n".join(recent[-6:]))
        if self.summaries:
            parts.append("Current conversation so far:\n" + "\n".join(self.summaries))
        return "\n\n".join(parts)

    def history(self):
        return self.archive
