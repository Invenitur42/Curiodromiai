"""
Per-conversation memory, archived and categorized by day.

Within the CURRENT conversation:
  - Raw messages accumulate.
  - Every time 10 raw messages (5 exchanges) pile up, they're folded into
    one short summary ("level-1 summary") and cleared from raw. Folding
    runs in a background thread so it never delays the reply that's
    already on its way back to the user.
  - Level-1 summaries just accumulate as a list. They are NOT automatically
    combined further while the conversation is still going.

When the conversation ENDS (New Chat, continuing a different archived
conversation, or the app window closing):
  - All of that conversation's level-1 summaries, plus any leftover raw
    messages - even a single exchange - get combined into ONE final
    summary for the whole conversation.
  - That summary is filed away permanently under the calendar date the
    conversation started on, alongside any other conversations from that
    same day.
  - Working memory then resets empty for the next conversation.
  - This step is time-boxed (FINALIZE_TIMEOUT) and never raises: if the
    model is slow, unreachable, or errors, a fast local fallback summary
    is used instead so the app always closes/resets promptly and a
    conversation is never silently left un-archived.

If you DON'T end the conversation - including closing and reopening the
app - you're still in the same conversation. Nothing gets finalized; the
existing level-1 summaries and raw messages are simply the starting point
you keep building on, exactly as before.

Archived days are pulled into context only when referenced ("yesterday",
a weekday, an explicit date), or via the /api/day-summary(-ies) endpoints -
never resent on every message.
"""

import calendar
import json
import os
import re
import threading
import uuid
from datetime import date, datetime, timedelta

import requests

OLLAMA_URL = "http://localhost:11434/api/chat"

MESSAGES_PER_CHUNK = 10  # fold every 10 raw messages (5 exchanges) into one L1 summary

# Time-boxes for the two model-assisted summarization calls. Kept short so
# folding never noticeably delays a reply, and so ending a conversation
# (including the app window closing) never hangs waiting on a slow/unreachable
# model. If the call doesn't finish in time (or errors at all), a fast local
# fallback summary is used instead - summarization always completes.
FOLD_TIMEOUT = 20
FINALIZE_TIMEOUT = 10

DATA_DIR = os.path.join(os.path.expanduser("~"), ".curiodromiai")
DATA_FILE = os.path.join(DATA_DIR, "memory.json")

WEEKDAYS = {name.lower(): i for i, name in enumerate(calendar.day_name)}  # monday=0
MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})

TONES = ("empathetic", "factual")
DEFAULT_TONE = "factual"

TONE_INSTRUCTIONS = {
    "empathetic": (
        "Respond in a warm, empathetic tone. Acknowledge feelings, be "
        "supportive and gentle, and favor understanding over blunt "
        "correction, while still being honest and useful."
    ),
    "factual": (
        "Respond in a direct, factual tone. Be concise, neutral, and "
        "precise. Prioritize accuracy over cushioning the message."
    ),
}


def _new_conversation(seed_summary=None, continued_from=None):
    convo = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "level1_summaries": [],
        "raw_history": [],
        "continued_from": continued_from,
    }
    if seed_summary:
        convo["level1_summaries"].append(seed_summary)
    return convo


def _empty_state():
    return {
        "conversation": _new_conversation(),
        "day_archive": {},
        "settings": {"tone": DEFAULT_TONE},
    }


def _heuristic_summary(text_block, max_chars=400):
    """A fast, model-free fallback summary. No network round trip, so it
    never blocks (or fails to complete) a fold or a conversation exit.
    Just the gist, trimmed to a sane length - better than losing the
    conversation's context entirely because a model call was slow or the
    server was unreachable."""
    lines = [l.strip() for l in text_block.strip().splitlines() if l.strip()]
    if not lines:
        return "Empty conversation."
    joined = " ".join(lines)
    if len(joined) <= max_chars:
        return joined
    return joined[:max_chars].rsplit(" ", 1)[0] + "…"


class ConversationMemory:
    def __init__(self):
        self._lock = threading.RLock()
        self.state = self._load()

    def _load(self):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("conversation", _new_conversation())
            data.setdefault("day_archive", {})
            data.setdefault("settings", {"tone": DEFAULT_TONE})
            data["settings"].setdefault("tone", DEFAULT_TONE)
            # backfill ids for any conversations archived before ids existed
            for entries in data["day_archive"].values():
                for entry in entries:
                    entry.setdefault("id", uuid.uuid4().hex)
            return data
        except (FileNotFoundError, json.JSONDecodeError):
            return _empty_state()

    def _save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp_path = DATA_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, DATA_FILE)  # atomic - never leaves a half-written file

    def _summarize(self, model, text_block, instruction, timeout):
        """Ask the model to summarize; on ANY failure (unreachable, slow,
        bad response) fall back to a fast local summary instead of raising.
        This is what keeps folding and conversation-ending fast and
        reliable regardless of whether Ollama is up."""
        try:
            r = requests.post(
                OLLAMA_URL,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": instruction},
                        {"role": "user", "content": text_block},
                    ],
                    "stream": False,
                    "keep_alive": "30m",
                },
                timeout=timeout,
            )
            r.raise_for_status()
            content = r.json().get("message", {}).get("content", "").strip()
            return content or _heuristic_summary(text_block)
        except (requests.exceptions.RequestException, ValueError, KeyError):
            return _heuristic_summary(text_block)

    # ---- within-conversation folding (raw -> level-1) --------------------

    def _fold_if_needed(self, model):
        """Runs in a background thread (see record_exchange) so it never
        delays the reply already sent back to the user."""
        with self._lock:
            convo = self.state["conversation"]
            raw = convo["raw_history"]
            chunks = []
            while len(raw) >= MESSAGES_PER_CHUNK:
                chunk, raw = raw[:MESSAGES_PER_CHUNK], raw[MESSAGES_PER_CHUNK:]
                chunks.append(chunk)
            if not chunks:
                return
            convo["raw_history"] = raw

        for chunk in chunks:
            chunk_text = "\n".join(f"{m['role']}: {m['content']}" for m in chunk)
            summary = self._summarize(
                model,
                chunk_text,
                "Summarize this conversation excerpt in 3-5 sentences. "
                "Keep names, decisions, and any facts the user shared. Be concise.",
                FOLD_TIMEOUT,
            )
            with self._lock:
                self.state["conversation"]["level1_summaries"].append(summary)
                self._save()

    def fold_in_background(self, model):
        """Kick off folding on a daemon thread if enough raw messages have
        piled up. Fire-and-forget: record_exchange already saved the raw
        messages synchronously, so nothing is lost even if this thread is
        still running when the app closes (finalize_conversation reads
        whatever raw_history/level1_summaries exist at that moment)."""
        if len(self.state["conversation"]["raw_history"]) >= MESSAGES_PER_CHUNK:
            threading.Thread(target=self._fold_if_needed, args=(model,), daemon=True).start()

    # ---- ending a conversation -> one final summary, filed by day --------

    def _reset_conversation_and_snapshot(self):
        """Atomically swap in a fresh, empty conversation and hand back
        whatever the previous one had to summarize. This is the fast part
        (no model calls) - it's what lets callers get their new
        conversation immediately instead of waiting on a summarization
        round trip."""
        with self._lock:
            convo = self.state["conversation"]
            pieces = list(convo["level1_summaries"])
            if convo["raw_history"]:
                pieces.append(
                    "\n".join(f"{m['role']}: {m['content']}" for m in convo["raw_history"])
                )
            started_at = convo["started_at"]
            self.state["conversation"] = _new_conversation()
            self._save()
        return pieces, started_at

    def _archive_pieces(self, model, pieces, started_at):
        """The slow part: summarize (time-boxed, with a local fallback -
        see _summarize) and file the result under the day the conversation
        started on. Safe to run on a background thread."""
        if not pieces:
            return False
        combined = "\n\n".join(pieces)
        final_summary = self._summarize(
            model,
            combined,
            "Combine this into one concise summary of the whole conversation. "
            "Keep names, decisions, topics discussed, and any facts the user "
            "shared. Be concise but don't drop important specifics.",
            FINALIZE_TIMEOUT,
        )
        with self._lock:
            day_key = started_at[:10]  # YYYY-MM-DD
            self.state["day_archive"].setdefault(day_key, []).append(
                {
                    "id": uuid.uuid4().hex,
                    "summary": final_summary,
                    "started_at": started_at,
                    "ended_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            self._save()
        return True

    def finalize_conversation(self, model):
        """Blocking version: swaps in a fresh conversation AND waits for
        the old one to be archived before returning. Used when the app is
        about to fully exit, since a background thread wouldn't survive
        the process closing. Time-boxed and never raises - see
        _summarize's fallback - so this still completes quickly even if
        the model is slow or unreachable. Returns True if something was
        archived, False if the conversation was empty."""
        pieces, started_at = self._reset_conversation_and_snapshot()
        return self._archive_pieces(model, pieces, started_at)

    def end_conversation_async(self, model):
        """Non-blocking version: the new empty conversation is ready
        immediately (no waiting on a model call), and the old one is
        summarized/archived on a background thread. Used for "New Chat"
        while the app keeps running, so starting a new chat never has to
        wait on a summarization round trip. Returns True if the old
        conversation had content and will be archived shortly, False if
        it was empty (nothing to archive)."""
        pieces, started_at = self._reset_conversation_and_snapshot()
        if not pieces:
            return False
        threading.Thread(
            target=self._archive_pieces, args=(model, pieces, started_at), daemon=True
        ).start()
        return True

    # ---- pulling an old day's conversations back in when referenced ------

    def _referenced_dates(self, text):
        text_lower = text.lower()
        today = date.today()
        found = set()

        if "today" in text_lower:
            found.add(today.isoformat())
        if "yesterday" in text_lower:
            found.add((today - timedelta(days=1)).isoformat())

        for name, idx in WEEKDAYS.items():
            if name in text_lower:
                delta = (today.weekday() - idx) % 7
                found.add((today - timedelta(days=delta)).isoformat())

        for match in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
            try:
                found.add(date(*map(int, match.groups())).isoformat())
            except ValueError:
                pass

        month_pattern = "|".join(sorted(MONTHS.keys(), key=len, reverse=True))
        for match in re.finditer(
            rf"\b({month_pattern})\.?\s+(\d{{1,2}})(?:,?\s+(\d{{4}}))?\b", text_lower
        ):
            month_name, day_str, year_str = match.groups()
            month = MONTHS.get(month_name)
            if not month:
                continue
            year = int(year_str) if year_str else today.year
            try:
                found.add(date(year, month, int(day_str)).isoformat())
            except ValueError:
                pass

        return [d for d in found if d in self.state["day_archive"]]

    # ---- tone setting -------------------------------------------------

    def get_tone(self):
        return self.state["settings"].get("tone", DEFAULT_TONE)

    def set_tone(self, tone):
        if tone not in TONES:
            raise ValueError(f"tone must be one of {TONES}")
        with self._lock:
            self.state["settings"]["tone"] = tone
            self._save()
        return tone

    # ---- browsing / continuing / deleting archived conversations --------

    def list_conversations(self):
        """All archived conversations, flattened, most recent first."""
        out = []
        for day, entries in self.state["day_archive"].items():
            for e in entries:
                out.append({**e, "date": day})
        out.sort(key=lambda e: e["started_at"], reverse=True)
        return out

    def continue_conversation(self, conv_id):
        """Pull an archived conversation's final summary back in as the
        starting point of a fresh active conversation. The archived entry
        is removed from the day archive since it now lives on as the
        (still open) current conversation again; it will be re-archived
        under a new summary whenever this conversation is next finalized."""
        with self._lock:
            for day, entries in list(self.state["day_archive"].items()):
                for i, e in enumerate(entries):
                    if e["id"] == conv_id:
                        entries.pop(i)
                        if not entries:
                            del self.state["day_archive"][day]
                        self.state["conversation"] = _new_conversation(
                            seed_summary=e["summary"], continued_from=conv_id
                        )
                        self._save()
                        return e
        return None

    def delete_conversation(self, conv_id):
        """Permanently remove one archived conversation. Does not touch
        the currently active (unfinished) conversation."""
        with self._lock:
            for day, entries in list(self.state["day_archive"].items()):
                for i, e in enumerate(entries):
                    if e["id"] == conv_id:
                        entries.pop(i)
                        if not entries:
                            del self.state["day_archive"][day]
                        self._save()
                        return True
        return False

    def delete_all_conversations(self):
        """Wipe every archived conversation. Does not touch the currently
        active (unfinished) conversation."""
        with self._lock:
            had_any = bool(self.state["day_archive"])
            self.state["day_archive"] = {}
            self._save()
        return had_any

    def dismiss_continued_banner(self):
        """Stop showing the 'continuing from...' banner for the active
        conversation. Persisted server-side so it stays dismissed across
        restarts, without discarding the summary context it already added
        to the active conversation."""
        with self._lock:
            self.state["conversation"]["continued_from"] = None
            self._save()

    # ---- public API used by app.py ---------------------------------------

    def build_context(self, latest_user_message):
        convo = self.state["conversation"]
        messages = [{"role": "system", "content": TONE_INSTRUCTIONS[self.get_tone()]}]

        for d in self._referenced_dates(latest_user_message):
            entries = self.state["day_archive"][d]
            joined = "\n\n".join(
                f"Conversation {i + 1}: {e['summary']}" for i, e in enumerate(entries)
            )
            messages.append(
                {"role": "system", "content": f"Conversations from {d}:\n{joined}"}
            )

        if convo["level1_summaries"]:
            joined = "\n".join(convo["level1_summaries"])
            messages.append(
                {"role": "system", "content": "Summary of this conversation so far:\n" + joined}
            )

        messages.extend(convo["raw_history"])
        messages.append({"role": "user", "content": latest_user_message})
        return messages

    def record_exchange(self, model, user_message, assistant_message):
        """Appends and saves immediately (fast) - folding, if needed, is
        kicked off separately in the background so it never delays the
        response the user is waiting on."""
        with self._lock:
            convo = self.state["conversation"]
            convo["raw_history"].append({"role": "user", "content": user_message})
            convo["raw_history"].append({"role": "assistant", "content": assistant_message})
            self._save()
        self.fold_in_background(model)

    def visible_history(self):
        return self.state["conversation"]["raw_history"]

    def current_conversation_meta(self):
        """Lightweight info so the UI can show a 'continuing from...' banner
        when the active conversation was resumed from an archived summary."""
        convo = self.state["conversation"]
        continued_from = convo.get("continued_from")
        if not continued_from:
            return {"continued_from": None}
        seed = convo["level1_summaries"][0] if convo["level1_summaries"] else ""
        return {"continued_from": continued_from, "summary": seed}

    def all_day_summaries(self):
        return self.state["day_archive"]

    def day_summary(self, day_str):
        return self.state["day_archive"].get(day_str)
