"""
Athenaeum — a local desktop chatbot front end for a local Ollama model.

Run it directly with:  python app.py
Or package it (see README).
"""

import os
import sys
import json
import threading
import webview
from flask import Flask, request, jsonify, render_template, send_from_directory
import requests
from memory import MemoryManager

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "qwen3:7b"
OLLAMA_BASE = "http://localhost:11434"
HOST = "127.0.0.1"
PORT = 5000

app = Flask(__name__, static_folder="static", template_folder="templates")
memory = MemoryManager()

# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/models")
def list_models():
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=3)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        return jsonify({"models": models, "default": DEFAULT_MODEL})
    except Exception as e:
        return jsonify({"models": [DEFAULT_MODEL], "default": DEFAULT_MODEL, "error": str(e)})

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True) or {}
    model = data.get("model") or DEFAULT_MODEL
    messages = data.get("messages") or []
    images = data.get("images")  # list of base64 strings, optional

    # Inject memory context
    system_prompt = memory.get_system_prompt()
    ollama_messages = [{"role": "system", "content": system_prompt}]
    for m in messages:
        msg = {"role": m["role"], "content": m.get("content", "")}
        if m.get("images"):
            msg["images"] = m["images"]
        ollama_messages.append(msg)

    payload = {
        "model": model,
        "messages": ollama_messages,
        "stream": False,
    }
    try:
        r = requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=300)
        r.raise_for_status()
        reply = r.json()["message"]["content"]
        # Record turn in memory
        if messages:
            user_msg = messages[-1].get("content", "")
            memory.add_turn(user_msg, reply)
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/memory", methods=["GET"])
def get_memory():
    return jsonify(memory.to_dict())

@app.route("/api/memory/new", methods=["POST"])
def new_conversation():
    memory.end_conversation()
    return jsonify({"ok": True})

@app.route("/api/memory/history")
def history():
    return jsonify(memory.get_history())

# ---------------------------------------------------------------------------
# Desktop window
# ---------------------------------------------------------------------------

def start_server():
    app.run(host=HOST, port=PORT, threaded=True, use_reloader=False)

def main():
    t = threading.Thread(target=start_server, daemon=True)
    t.start()
    webview.create_window(
        "Athenaeum",
        f"http://{HOST}:{PORT}",
        width=900,
        height=700,
        resizable=True,
    )
    webview.start()

if __name__ == "__main__":
    main()
