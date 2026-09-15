"""
Curiodromiai — a local desktop chatbot front end for a local Ollama model.

Run it directly with:  python app.py
It starts a small Flask API on 127.0.0.1:5005 and opens a native desktop
window (via pywebview) pointing at it. No data leaves your machine.
"""

import argparse
import json
import os
import secrets
import sys
import threading
from datetime import timedelta

import requests
import webview
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    stream_with_context,
    url_for,
)

from memory import ConversationMemory
from rag import RagIndex

OLLAMA_BASE = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:7b"  # change to whatever you `ollama pull`ed

# How long Ollama keeps a model loaded in memory between requests. Without
# this, Ollama's default (a few minutes) can unload the model between
# messages, so the next reply pays the "cold load" cost all over again -
# this keeps responses fast for the length of a normal session.
KEEP_ALIVE = "30m"

# Set this environment variable to turn on the login gate - meant for when
# you're exposing the app over the internet (see the README's "Sharing this
# over the internet" section). If it's unset, the app behaves exactly as
# before: no login, pure local desktop use.
ACCESS_PASSWORD = os.environ.get("CURIODROMIAI_PASSWORD")

DATA_DIR = os.path.join(os.path.expanduser("~"), ".curiodromiai")
SECRET_KEY_FILE = os.path.join(DATA_DIR, "secret.key")

memory = ConversationMemory()
rag_index = RagIndex()

# Tracked so the window-close handler can finalize with whatever model was
# actually last used in this session, instead of always DEFAULT_MODEL.
_last_model_used = DEFAULT_MODEL


def _load_or_create_secret_key():
    """A signing key for login sessions, persisted so logins survive an app
    restart instead of forcing everyone to re-enter the password."""
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(SECRET_KEY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        key = secrets.token_hex(32)
        with open(SECRET_KEY_FILE, "w", encoding="utf-8") as f:
            f.write(key)
        return key


class Api:
    """Exposed to the page as `pywebview.api.*`. Used for actions that need
    real OS-level control (quitting the whole app) rather than just an
    HTTP call to the Flask server."""

    def quit_app(self):
        try:
            memory.finalize_conversation(_last_model_used)
        except Exception:
            pass  # never block quitting on a summarization hiccup
        for w in webview.windows:
            w.destroy()


def resource_path(relative_path: str) -> str:
    """Resolve a path that works both in dev and inside a PyInstaller .exe."""
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base_path, relative_path)


app = Flask(
    __name__,
    template_folder=resource_path("templates"),
    static_folder=resource_path("static"),
)
app.secret_key = _load_or_create_secret_key()
app.permanent_session_lifetime = timedelta(days=30)


@app.before_request
def require_login():
    """Only active when CURIODROMIAI_PASSWORD is set - see the README. With no
    password configured, every request passes through untouched, exactly as
    before this feature existed."""
    if not ACCESS_PASSWORD:
        return
    if request.endpoint in ("login", "static"):
        return
    if not session.get("authed"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = False
    if request.method == "POST":
        if request.form.get("password") == ACCESS_PASSWORD:
            session.permanent = True
            session["authed"] = True
            return redirect(url_for("index"))
        error = True
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("authed", None)
    return redirect(url_for("login"))


@app.route("/api/auth-status")
def auth_status():
    return jsonify({"enabled": bool(ACCESS_PASSWORD)})


@app.route("/")
def index():
    return render_template("index.html", default_model=DEFAULT_MODEL)


@app.route("/api/models")
def list_models():
    """Ask Ollama which models are installed, so the dropdown is accurate."""
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        r.raise_for_status()
        names = [m["name"] for m in r.json().get("models", [])]
        return jsonify({"models": names})
    except requests.exceptions.RequestException:
        return jsonify({"models": [], "error": "ollama_unreachable"})


@app.route("/api/history")
def history():
    """Raw recent messages, so the window can redraw bubbles after a restart."""
    return jsonify({"messages": memory.visible_history()})


@app.route("/api/day-summaries")
def day_summaries():
    """All archived days, each holding a list of that day's conversation summaries."""
    return jsonify({"days": memory.all_day_summaries()})


@app.route("/api/day-summary")
def day_summary():
    day = request.args.get("date", "")
    conversations = memory.day_summary(day)
    if conversations is None:
        return jsonify({"error": f"No archived conversations for {day}."}), 404
    return jsonify({"date": day, "conversations": conversations})


@app.route("/api/conversations")
def conversations():
    """Every archived conversation (final summary only), most recent first,
    for the side panel's history list."""
    return jsonify({"conversations": memory.list_conversations()})


@app.route("/api/conversations/<conv_id>/continue", methods=["POST"])
def continue_conversation(conv_id):
    """Reopen an archived conversation: its final summary becomes the
    starting point of a new active conversation, and the archived entry
    is removed (it'll be re-filed under a fresh summary next time this
    conversation ends)."""
    entry = memory.continue_conversation(conv_id)
    if entry is None:
        return jsonify({"error": "No archived conversation with that id."}), 404
    return jsonify({"ok": True, "summary": entry["summary"]})


@app.route("/api/conversations/<conv_id>", methods=["DELETE"])
def delete_conversation(conv_id):
    """Permanently delete one archived conversation from history."""
    ok = memory.delete_conversation(conv_id)
    if not ok:
        return jsonify({"error": "No archived conversation with that id."}), 404
    return jsonify({"ok": True})


@app.route("/api/conversations/clear-all", methods=["POST"])
def clear_all_conversations():
    """Permanently delete every archived conversation from history."""
    memory.delete_all_conversations()
    return jsonify({"ok": True})


@app.route("/api/conversation-state")
def conversation_state():
    """Whether the current conversation was resumed from an archived one,
    so the UI can show a 'continuing from...' banner (or not, for a fresh
    conversation) without ever resending the summary as a chat bubble."""
    return jsonify(memory.current_conversation_meta())


@app.route("/api/conversation-state/dismiss", methods=["POST"])
def dismiss_conversation_state():
    """Persist that the 'continuing from...' banner was dismissed, so it
    doesn't reappear on the next load/restart."""
    memory.dismiss_continued_banner()
    return jsonify({"ok": True})


@app.route("/api/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        tone = payload.get("tone")
        try:
            memory.set_tone(tone)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    return jsonify({"tone": memory.get_tone()})


@app.route("/api/rag/ingest", methods=["POST"])
def rag_ingest():
    payload = request.get_json(force=True) or {}
    folder = (payload.get("folder") or "").strip()
    if not folder or not os.path.isdir(folder):
        return jsonify({"error": "That folder doesn't exist or isn't accessible."}), 400
    started = rag_index.ingest_folder_async(folder)
    if not started:
        return jsonify({"error": "Indexing is already in progress."}), 409
    return jsonify({"ok": True})


@app.route("/api/rag/progress")
def rag_progress():
    return jsonify(rag_index.get_progress())


@app.route("/api/rag/sources")
def rag_sources():
    return jsonify({"sources": rag_index.sources()})


@app.route("/api/rag/sources/<path:source>", methods=["DELETE"])
def rag_delete_source(source):
    removed = rag_index.remove_source(source)
    return jsonify({"ok": True, "removed": removed})


@app.route("/api/rag/clear", methods=["POST"])
def rag_clear():
    rag_index.clear()
    return jsonify({"ok": True})


@app.route("/api/reset", methods=["POST"])
def reset():
    """Ends the current conversation and immediately starts a fresh, empty
    one - it does not wait for the old conversation to be summarized and
    archived (that happens in the background), so New Chat is instant
    regardless of how slow/unreachable the model is."""
    payload = request.get_json(silent=True) or {}
    model = payload.get("model") or DEFAULT_MODEL
    will_archive = memory.end_conversation_async(model)
    return jsonify({"ok": True, "archived": will_archive})


@app.route("/api/chat", methods=["POST"])
def chat():
    global _last_model_used
    payload = request.get_json(force=True) or {}
    user_message = (payload.get("message") or "").strip()
    model = payload.get("model") or DEFAULT_MODEL
    images = payload.get("images") or []  # base64 strings, no data: prefix
    _last_model_used = model

    if not user_message:
        return jsonify({"error": "No message provided."}), 400

    # Build this turn's context: any referenced-day summaries + this
    # conversation's running summary-so-far + its current raw messages.
    context_messages = memory.build_context(user_message)

    # RAG: pull in the most relevant chunks from the user's indexed library,
    # if any match well enough. This is added fresh for this turn only -
    # never written into memory.record_exchange - so the library's content
    # never bloats the conversation archive/summaries.
    rag_hits = rag_index.search(user_message)
    if rag_hits:
        excerpt_block = "\n\n".join(f"[{h['source']}] {h['text']}" for h in rag_hits)
        context_messages.insert(
            len(context_messages) - 1,  # right before the current user message
            {
                "role": "system",
                "content": (
                    "Relevant excerpts from the user's own library, retrieved for this "
                    "question. Use them where relevant and mention the source file when "
                    "you draw on one; ignore them if they don't actually help:\n\n"
                    + excerpt_block
                ),
            },
        )

    # Attach any images to just the final (current) user message - Ollama
    # expects a per-message "images" list of base64 strings for models
    # that support vision. Images aren't persisted in memory (see
    # memory.record_exchange) to keep the archive/summary storage light,
    # so they only apply to the turn they were sent on.
    if images and context_messages and context_messages[-1]["role"] == "user":
        context_messages[-1]["images"] = images

    def generate():
        """Streams the reply back as newline-delimited JSON objects
        ({"type": "chunk"|"error"|"done", ...}) as soon as each piece
        arrives from Ollama, instead of waiting for the whole reply -
        this is what lets the first words show up immediately rather
        than after the full response has been generated."""
        full_reply = ""
        try:
            with requests.post(
                f"{OLLAMA_BASE}/api/chat",
                json={
                    "model": model,
                    "messages": context_messages,
                    "stream": True,
                    "keep_alive": KEEP_ALIVE,
                },
                timeout=300,
                stream=True,
            ) as r:
                if not r.ok:
                    # Surface Ollama's actual reason instead of a bare HTTP
                    # status - e.g. it 400s when the selected model isn't
                    # multimodal and an image was attached, or when the
                    # model name doesn't exist.
                    try:
                        reason = r.json().get("error") or r.text
                    except ValueError:
                        reason = r.text or f"HTTP {r.status_code}"
                    hint = ""
                    if images:
                        hint = (
                            f' "{model}" likely doesn\'t support image input - try a '
                            "vision-capable model instead (e.g. `ollama pull llava` or "
                            "`ollama pull minicpm-v`), then pick it from the model list."
                        )
                    yield json.dumps(
                        {"type": "error", "message": f"Ollama rejected the request: {reason}.{hint}"}
                    ) + "\n"
                    return

                for line in r.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError:
                        continue
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        full_reply += piece
                        yield json.dumps({"type": "chunk", "text": piece}) + "\n"
                    if chunk.get("done"):
                        break

            if not full_reply.strip():
                yield json.dumps({"type": "error", "message": "The model returned an empty response."}) + "\n"
                return

            # Record the exchange; this may kick off background folding.
            memory.record_exchange(model, user_message, full_reply)
            yield json.dumps({"type": "done"}) + "\n"

        except requests.exceptions.ConnectionError:
            yield json.dumps(
                {
                    "type": "error",
                    "message": (
                        "Can't reach Ollama on localhost:11434. "
                        "Make sure Ollama is installed and running (`ollama serve`), "
                        f"and that you've pulled a model (`ollama pull {model}`)."
                    ),
                }
            ) + "\n"
        except requests.exceptions.Timeout:
            yield json.dumps(
                {"type": "error", "message": "The model timed out. Try a smaller model or a shorter message."}
            ) + "\n"
        except Exception as exc:  # noqa: BLE001 - surface any other error to the UI
            yield json.dumps({"type": "error", "message": f"Unexpected error: {exc}"}) + "\n"

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")


def start_flask():
    app.run(host="127.0.0.1", port=5005, debug=False, use_reloader=False, threaded=True)


def on_window_closing():
    """The close-handler: whenever the window is closed (the 'X', Alt-F4,
    Cmd-Q, whatever the OS uses), the current conversation is finalized -
    folded into its one final summary and archived by day - the same as
    clicking New Chat. This is the only place a conversation ends besides
    the explicit button, so nothing is ever left half-summarized, and the
    on-disk file only ever holds final summaries, not raw transcripts.
    Runs synchronously so it completes before the window actually closes,
    but memory.finalize_conversation is time-boxed and never raises, so
    this is quick even if Ollama is slow, unreachable, or the summary
    call otherwise fails - a local fallback summary is used instead."""
    try:
        memory.finalize_conversation(_last_model_used)
    except Exception:
        # Should not happen (finalize_conversation catches its own
        # errors), but never block the window from closing regardless.
        pass


def run_headless(port):
    """Runs just the Flask server, no desktop window - for a machine you're
    exposing via a tunnel (see the README). Blocking call."""
    if not ACCESS_PASSWORD:
        print(
            "WARNING: CURIODROMIAI_PASSWORD is not set. Anyone who reaches this "
            "server - e.g. via a tunnel URL - will have full, unauthenticated "
            "access. Set CURIODROMIAI_PASSWORD before running --headless if "
            "you're exposing this over the internet."
        )
    print(f"Curiodromiai server running at http://127.0.0.1:{port} (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)


def run_desktop():
    threading.Thread(target=start_flask, daemon=True).start()
    api = Api()
    window = webview.create_window(
        "Curiodromiai",
        "http://127.0.0.1:5005",
        width=920,
        height=740,
        min_size=(640, 520),
        background_color="#17120D",
        js_api=api,
    )
    window.events.closing += on_window_closing
    webview.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Curiodromiai - a local chatbot")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run just the web server, no desktop window - use this when exposing "
        "the app via a tunnel (cloudflared, ngrok, etc).",
    )
    parser.add_argument("--port", type=int, default=5005, help="Port for --headless mode.")
    args = parser.parse_args()

    if args.headless:
        run_headless(args.port)
    else:
        run_desktop()
