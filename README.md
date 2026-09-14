Continuation Notes:
   - Make the response text smoother when its coming in
   - Rename the project Curiodrimiai
   - Make a front end that can be accessible on mobile



# Athenaeum — local chatbot front end

A small desktop app: Flask backend + a plain HTML/CSS/JS chat UI, wrapped in a
native window with `pywebview`, talking to a model you run through **Ollama**.
Nothing leaves your machine.

## 1. One-time setup

1. Install [Ollama](https://ollama.com) and pull a model that fits your size
   budget, e.g.:
   ```
   ollama pull qwen3:7b
   ```
2. Install Python dependencies (Python 3.10+ recommended):
   ```
   python -m venv venv
   venv\Scripts\activate        (Windows)
   source venv/bin/activate     (macOS/Linux)
   pip install -r requirements.txt
   ```

## 2. Run it

Make sure Ollama is running (`ollama serve`, or it's already running in the
background after install), then:
```
python app.py
```
A desktop window titled "Athenaeum" opens. Pick your model from the dropdown
(it's populated from whatever you've pulled) and start chatting.

If the model dropdown only shows the default and a message says Ollama is
unreachable, Ollama isn't running — start it and reload.

## 3. Package it as a Windows .exe

From the project folder, with the venv active:
```
pyinstaller --onefile --windowed --name Athenaeum ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  app.py
```
(On macOS/Linux the `--add-data` separator is `:` instead of `;`, and you'd
produce a Mac app bundle or Linux binary instead of an .exe.)

The finished executable lands in `dist/Athenaeum.exe`. Two things to know:

- It still needs Ollama installed and running on the same machine — the exe
  is just the chat window, not the model itself. The model files stay wherever
  Ollama keeps them (not bundled into the exe).
- `pywebview` on Windows renders using Edge WebView2, which ships with
  Windows 10/11 by default. On a very old or stripped-down Windows install
  you may need the [WebView2 runtime](https://developer.microsoft.com/microsoft-edge/webview2/).

## Memory: fold as you go, finalize per conversation, archive by day

Memory tracks **one conversation at a time**, and files it away — as a
single final summary only, never the raw transcript — whenever that
conversation ends (`memory.py`):

**While a conversation is going:**
- Raw messages build up normally
- The instant 10 raw messages (5 exchanges) accumulate, they're folded
  into one short summary and cleared out of raw — this happens
  continuously, so nothing ever piles up long enough to get big or blurry
- Those short summaries just accumulate as a list — they're **not**
  auto-combined further while the conversation is live

**When the conversation ends** (New Chat, or closing the app):
- The remaining raw messages + all the short summaries are combined into
  one final conversation summary
- That final summary is written to `memory.json` under today's date,
  tagged with a short category label the model chooses
- The raw transcript is discarded; only the final summary is kept

**Across days / later sessions:**
- Past final summaries stay in `memory.json`, grouped by date
- On a new conversation the model gets a brief digest of recent past
  summaries (so it has some continuity without loading everything)

`memory.json` is created next to the app on first run. You can delete it
to start fresh.

## UI notes

Dark theme, spiral logo mark (top-left opens the side panel), pill-shaped
bubbles, full-width rounded input bar. Side panel holds conversation
history (final summaries only) and a New Chat button.

## Attachments

The input bar has a file button (cube icon) and send button (triangle):
- **Images** (png/jpg/gif/webp) are base64-encoded and passed to Ollama
  vision models via the per-message `images` field. Not persisted into
  `memory.json`.
- **Plain text documents** (.txt, .md, .csv, .json, .log) are read
  client-side and appended to your message as full text.
- **PDFs and Word docs** (.pdf, .doc, .docx) show as unsupported for now.

## Changing the default model

Edit `DEFAULT_MODEL` near the top of `app.py`, or pick a different one
from the dropdown each time.
