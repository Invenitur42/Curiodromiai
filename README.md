# Curiodromiai — local chatbot front end

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
A desktop window titled "Curiodromiai" opens. Pick your model from the dropdown
(it's populated from whatever you've pulled) and start chatting.

If the model dropdown only shows the default and a message says Ollama is
unreachable, Ollama isn't running — start it and reload.

## 3. Package it as a Windows .exe

From the project folder, with the venv active:
```
pyinstaller --onefile --windowed --name Curiodromiai ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  app.py
```
(On macOS/Linux the `--add-data` separator is `:` instead of `;`, and you'd
produce a Mac app bundle or Linux binary instead of an .exe.)

The finished executable lands in `dist/Curiodromiai.exe`. Two things to know:

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
  auto-combined into anything bigger while the conversation is still open

**When the conversation ends**, everything left (all the short summaries
+ any leftover raw messages) gets combined into **one final summary for
that whole conversation** and archived under the calendar date it
started on — the raw messages themselves are discarded, so the archive
only ever holds space-efficient summaries. A day can hold several of
these if you had multiple separate conversations that day. Working
memory then resets empty for the next one. A conversation now ends in
**two** ways:
- Clicking **New Chat**, same as before.
- **Closing the app window** — a close-handler (`on_window_closing` in
  `app.py`, wired to `window.events.closing`) finalizes the current
  conversation automatically, the same way New Chat does, so nothing is
  left un-archived just because you quit instead of clicking a button.

**If you don't end the conversation** — including closing and reopening
the app before this was wired up — you're still in the same
conversation. Its summaries-so-far and raw messages are exactly where
you left them, and you just keep building on that as the starting
point.

**Archived conversations are never force-fed back in.** They're pulled
into context only when:
- You reference that day in a message ("yesterday," a weekday,
  "September 2nd," "2026-09-01"), or
- You explicitly pick one from the side panel's **Past conversations**
  list and hit **Continue this conversation** — its final summary
  becomes the seed/starting point for a fresh active conversation (a
  small "Continuing an earlier conversation…" banner shows this happened
  so it's never a silent, surprising context change). That archived
  entry is removed from the day list since it's "reopened," and gets
  re-filed under a new summary whenever this conversation next ends.

Starting a plain **New Chat** or just opening the app fresh gives a
clean conversation with nothing pulled in — the "fresh experience" is
always the default; old context only shows up when you ask for it.

Endpoints:
- `GET /api/conversations` — every archived conversation, flattened, most recent first (powers the side panel)
- `POST /api/conversations/<id>/continue` — reopen one as the seed of a new active conversation
- `GET /api/conversation-state` — whether the *current* conversation was resumed from an archive (for the banner)
- `GET /api/day-summaries` — every day, each with its list of conversation summaries
- `GET /api/day-summary?date=2026-09-01` — just that day's conversations
- `GET|POST /api/settings` — read or set the response tone (`empathetic` / `factual`)

Everything lives in `~/.curiodromiai/memory.json`.

Known limitations: date detection is simple pattern matching (won't catch
"two weeks ago"), and a conversation is filed under whichever date it
*started* on, even if it ran past midnight.

## Tone setting

The side panel has a two-way **Empathetic / Factual** toggle. It's saved
server-side (`GET|POST /api/settings`) and applied as a system
instruction on every turn — Empathetic favors warmth and validation,
Factual favors concise, neutral directness. Neither mode changes what
gets archived or how memory folds; it only shapes the model's replies.

## Side panel

Opened via the menu icon in the header (or closed via the ✕ or by
tapping outside it). Holds, top to bottom: the tone toggle, the model
picker (moved here from the header), and the scrollable list of past
conversations, each showing its date, a snippet of its final summary,
and a "Continue this conversation" button.

## UI

The interface was remapped to match a supplied reference image: solid
black background, a spiral logo mark, dark pill-shaped message bubbles
(sent messages on the right with a small avatar dot, received on the
left), and a full-width rounded input bar. The spiral logo itself is the
button that opens the side panel (top-left, next to New Chat on the
right).

## Attachments

The input bar has a file button (cube icon, left of the text field) and
a send button (triangle icon, right):
- **Images** (png/jpg/gif/webp) are base64-encoded and passed straight
  through to Ollama's vision-capable models via the per-message
  `images` field. They apply to the turn you send them on; to keep the
  archive/summary storage lightweight they aren't persisted into
  `memory.json`, so reference them again if you need the model to
  recall an image in a later message.
- **Plain text documents** (.txt, .md, .csv, .json, .log) are read
  client-side and appended to your message as their full text content.
- **PDFs and Word docs** (.pdf, .doc, .docx) can be attached, but there's
  no parser wired up yet — they show up as an "unsupported" chip and the
  model is told the file couldn't be read, rather than silently
  dropping it.

Attached files appear as removable chips above the input bar before you
send, and as a small "📎 filename" note under your sent message
afterward.

## The window layout

- **+** (top-left) — New chat. Archives the current conversation in the
  background and starts fresh instantly.
- **Spiral** (top-center) — click it for a dropdown to switch between
  **Empathetic** and **Factual** response tone.
- **✕** (top-right) — Exit the app. Finalizes the current conversation
  first, then closes the window (works only when running as the actual
  desktop app via `python app.py` / the packaged `.exe` — not when just
  viewing `localhost:5005` in a browser tab).
- **Pull tab** (left edge, vertical) — opens the side panel: model picker,
  your book library (see below), and your archived conversation history
  (continue or delete any of them, or clear everything).
- **Square icon** (bottom-left of the input bar) — attach a file or image.
- **Triangle** (bottom-right of the input bar) — send.

## RAG: grounding answers in your own books (psychology, religion, etc.)

Instead of fine-tuning the model on your books, they're kept as searchable
text on disk (`rag.py`) and pulled in only when relevant — an "open book"
approach rather than trying to bake the content into the model's weights.

**One-time setup:** pull an embedding model in Ollama (separate from your
chat model):
```
ollama pull nomic-embed-text
```

**Indexing your library:** in the side panel's **Library (RAG)** section,
paste the full path to a folder containing your books (`.pdf`, `.txt`, or
`.md`) and click **Index**. Each file gets:
1. Text extracted (PDF pages concatenated, or read directly for txt/md)
2. Split into overlapping ~150-200 word chunks (overlap so an idea split
   across a chunk boundary isn't lost)
3. Each chunk embedded via `nomic-embed-text` and stored in
   `~/.curiodromiai/rag_index.json`

Indexing runs in the background — the panel polls progress every 1.5s and
shows which file it's on. This can take a while for a large library (one
embedding call per chunk), but only has to happen once per file; re-running
Index on the same folder replaces stale chunks for any file that changed
rather than duplicating them.

**When you ask something**, your question is embedded the same way, and
compared (cosine similarity) against every stored chunk. The best-matching
few (score above a threshold, so unrelated questions don't drag in noise)
are added to the model's context for that reply only — never written into
conversation memory or the day/conversation archive, so your library's
content never bloats those summaries. Ask the model to mention which book
a claim came from and it generally will, since the source filename is
included with each excerpt.

**Managing the library:** each indexed file is listed with its chunk count
and a **✕** to remove just that file, or use the search to confirm what's
indexed via `GET /api/rag/sources`.

Known limitations: retrieval is plain cosine similarity over all chunks in
Python (no vector database) — fine for a personal library (thousands of
chunks), but would get slow well beyond that. PDF text extraction can come
out messy for scanned/image-based PDFs (no OCR) or heavily-formatted
layouts (tables, multi-column text) — a plain-text export of the same book
will generally index more cleanly if you hit that.

## Sharing this over the internet

By default the app only listens on your own machine (`127.0.0.1`) - nothing
outside your computer can reach it. To let someone else use it over a link,
you need two things: a way to punch a public URL through to your machine
(a "tunnel"), and a password, since anyone with that URL would otherwise
have full run of your chatbot, your archived conversations, and your
indexed library.

**Important limitation:** this app has **one shared conversation** - there
is no per-visitor separation. Whoever you send the link to is chatting into
the *same* active conversation as you, sharing the same memory and RAG
library. This is meant for sharing with one person at a time (e.g. a
friend, or yourself from another device), not for hosting multiple
strangers at once.

**1. Set a password** (environment variable, before starting the app):
```
# macOS/Linux
export CURIODROMIAI_PASSWORD="something only you and your guest know"

# Windows (PowerShell)
$env:CURIODROMIAI_PASSWORD="something only you and your guest know"
```
With this set, every page - yours included - requires logging in at
`/login` first. Leave it unset and the app behaves exactly as before (no
login, pure local use).

**2. Run the server.** You can either run the normal desktop app (the
window itself will also ask you to log in once), or run headless if you
don't need the window open:
```
python app.py --headless
```

**3. Get a public URL with a tunnel.** The easiest option needs no account:
[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)'s
quick tunnels. Install `cloudflared`, then:
```
cloudflared tunnel --url http://127.0.0.1:5005
```
It prints a URL like `https://random-words-here.trycloudflare.com` in the
terminal - that's the link to share. (Alternative: [ngrok](https://ngrok.com)
works similarly but requires a free account and authtoken.)

**4. Share the URL.** Whoever opens it gets the password page first, then
the normal chat interface - talking to the model running on your machine.

Things worth knowing:
- Your computer has to stay on and both the app and the tunnel have to
  keep running for the link to work.
- Quick tunnel URLs are temporary - a new random URL is generated each time
  you restart `cloudflared`. For a stable, permanent URL you'd set up a
  *named* Cloudflare Tunnel tied to a domain you own (more setup, worth it
  only if you want this to be a lasting thing rather than an occasional
  share).
- Response speed depends entirely on your machine's hardware - your guest
  experiences whatever speed your GPU/CPU gives the model locally.

## Changing the default model

Edit `DEFAULT_MODEL` near the top of `app.py`, or just pick a different one
from the dropdown each time — it's not persisted between runs currently.
