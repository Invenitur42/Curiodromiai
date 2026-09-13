"""
Athenaeum — a local desktop chatbot front end for a local Ollama model.

Run it directly with:  python app.py
It starts a small Flask API on 127.0.0.1:5005 and opens a native desktop
window (via pywebview) pointing at it. No data leaves your machine.
"""

import json
import os
import sys
import threading

import requests
import webview
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from memory import ConversationMemory

OLLAMA_BASE = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:7b"
PORT = 5005

app = Flask(__name__, static_folder="static", template_folder="templates")
memory = ConversationMemory()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/models")
def models():
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=2)
        r.raise_for_status()
        names = [m["name"] for m in r.json().get("models", [])]
        return jsonify({"ok": True, "models": names, "default": DEFAULT_MODEL})
    except Exception as e:
        return jsonify({"ok": False, "models": [DEFAULT_MODEL], "default": DEFAULT_MODEL, "error": str(e)})


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True) or {}
    model = data.get("model") or DEFAULT_MODEL
    messages = data.get("messages") or []
    stream = data.get("stream", True)

    system = memory.build_system_prompt()
    ollama_msgs = [{"role": "system", "content": system}]
    for m in messages:
        entry = {"role": m["role"], "content": m.get("content", "")}
        if m.get("images"):
            entry["images"] = m["images"]
        ollama_msgs.append(entry)

    payload = {"model": model, "messages": ollama_msgs, "stream": stream}

    if not stream:
        try:
            r = requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=300)
            r.raise_for_status()
            reply = r.json()["message"]["content"]
            if messages:
                memory.record(messages[-1].get("content", ""), reply)
            return jsonify({"reply": reply})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    def generate():
        try:
            with requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, stream=True, timeout=300) as r:
                r.raise_for_status()
                full = []
                for line in r.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if "message" in chunk and "content" in chunk["message"]:
                        token = chunk["message"]["content"]
                        full.append(token)
                        yield f"data: {json.dumps({'token': token})}\n\n"
                    if chunk.get("done"):
                        break
                reply = "".join(full)
                if messages:
                    memory.record(messages[-1].get("content", ""), reply)
                yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/memory/new", methods=["POST"])
def new_chat():
    memory.end_conversation()
    return jsonify({"ok": True})


@app.route("/api/memory/history")
def history():
    return jsonify(memory.history())


def on_window_closing():
    memory.end_conversation()


def run_flask():
    app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
    window = webview.create_window(
        "Athenaeum",
        f"http://127.0.0.1:{PORT}",
        width=920,
        height=740,
        min_size=(640, 520),
        background_color="#000000",
    )
    window.events.closing += on_window_closing
    webview.start()
