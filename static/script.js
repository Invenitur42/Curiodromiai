const chatEl = document.getElementById("chat");
const emptyState = document.getElementById("empty-state");
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const statusEl = document.getElementById("status");
const modelSelect = document.getElementById("model-select");
const newChatBtn = document.getElementById("new-chat-btn");

const sidePanel = document.getElementById("side-panel");
const panelScrim = document.getElementById("panel-scrim");
const panelPullTab = document.getElementById("panel-pull-tab");
const panelCloseBtn = document.getElementById("panel-close-btn");

const toneBtn = document.getElementById("tone-btn");
const toneDropdown = document.getElementById("tone-dropdown");
const exitBtn = document.getElementById("exit-btn");
const conversationsList = document.getElementById("conversations-list");
const clearConversationsBtn = document.getElementById("clear-conversations-btn");
const logoutLink = document.getElementById("logout-link");

async function checkAuthStatus() {
  try {
    const res = await fetch("/api/auth-status");
    const data = await res.json();
    logoutLink.hidden = !data.enabled;
  } catch (e) {
    // leave it hidden - fine for local-only use
  }
}

const ragFolderInput = document.getElementById("rag-folder-input");
const ragIndexBtn = document.getElementById("rag-index-btn");
const ragStatus = document.getElementById("rag-status");
const ragSourcesList = document.getElementById("rag-sources-list");

const continuingBanner = document.getElementById("continuing-banner");
const continuingBannerText = document.getElementById("continuing-banner-text");
const continuingBannerDismiss = document.getElementById("continuing-banner-dismiss");

const fileInput = document.getElementById("file-input");
const fileImportBtn = document.getElementById("file-import-btn");
const attachmentsRow = document.getElementById("attachments-row");

// Attachments staged for the next message: { id, name, kind: "image"|"text"|"unsupported", data }
// "image" -> data is base64 (no data: prefix); "text" -> data is the extracted text content.
let attachments = [];

const TEXT_EXTENSIONS = ["txt", "md", "csv", "json", "log"];
const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];

function autoResize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
}

function setStatus(text, isError = false) {
  statusEl.textContent = text || "";
  statusEl.classList.toggle("error", isError);
}

function renderMessage(role, content, attachmentNames = []) {
  if (emptyState) emptyState.remove();

  const row = document.createElement("div");
  row.className = `msg-row ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "msg";
  bubble.textContent = content;

  if (attachmentNames.length) {
    const meta = document.createElement("div");
    meta.className = "msg-attachments";
    meta.textContent = "📎 " + attachmentNames.join(", ");
    bubble.appendChild(meta);
  }

  if (role === "user") {
    const avatar = document.createElement("div");
    avatar.className = "avatar-dot";
    row.appendChild(bubble);
    row.appendChild(avatar);
  } else {
    row.appendChild(bubble);
  }

  chatEl.appendChild(row);
  chatEl.scrollTop = chatEl.scrollHeight;
}

// ---------------- side panel open/close ----------------

function openPanel() {
  sidePanel.classList.add("open");
  panelScrim.classList.add("visible");
  panelPullTab.classList.add("hidden");
}

function closePanel() {
  sidePanel.classList.remove("open");
  panelScrim.classList.remove("visible");
  panelPullTab.classList.remove("hidden");
}

panelPullTab.addEventListener("click", openPanel);
panelCloseBtn.addEventListener("click", closePanel);
panelScrim.addEventListener("click", closePanel);

// ---------------- models ----------------

async function loadModels() {
  try {
    const res = await fetch("/api/models");
    const data = await res.json();
    if (data.models && data.models.length) {
      modelSelect.innerHTML = "";
      data.models.forEach((name) => {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        modelSelect.appendChild(opt);
      });
    } else if (data.error === "ollama_unreachable") {
      setStatus("Ollama isn't running — start it with `ollama serve`.", true);
    }
  } catch (e) {
    // keep the default option already in the select; not fatal
  }
}

// ---------------- tone setting (dropdown under the spiral) ----------------

function markActiveTone(tone) {
  toneDropdown.querySelectorAll(".tone-menu-option").forEach((btn) => {
    const isActive = btn.dataset.tone === tone;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-checked", isActive ? "true" : "false");
  });
}

async function loadTone() {
  try {
    const res = await fetch("/api/settings");
    const data = await res.json();
    markActiveTone(data.tone || "factual");
  } catch (e) {
    markActiveTone("factual");
  }
}

function openToneDropdown() {
  toneDropdown.classList.remove("hidden");
  toneBtn.setAttribute("aria-expanded", "true");
}

function closeToneDropdown() {
  toneDropdown.classList.add("hidden");
  toneBtn.setAttribute("aria-expanded", "false");
}

toneBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  if (toneDropdown.classList.contains("hidden")) {
    openToneDropdown();
  } else {
    closeToneDropdown();
  }
});

document.addEventListener("click", (e) => {
  if (!toneDropdown.classList.contains("hidden") && !toneDropdown.contains(e.target) && e.target !== toneBtn) {
    closeToneDropdown();
  }
});

toneDropdown.addEventListener("click", async (e) => {
  const btn = e.target.closest(".tone-menu-option");
  if (!btn) return;
  const tone = btn.dataset.tone;
  markActiveTone(tone); // optimistic
  closeToneDropdown();
  try {
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tone }),
    });
  } catch (e) {
    // fine, will just retry next toggle
  }
});

// ---------------- exit app ----------------

exitBtn.addEventListener("click", () => {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.quit_app) {
    window.pywebview.api.quit_app();
  } else {
    setStatus("Exit only works when running as the desktop app.", true);
  }
});

// ---------------- history (raw messages in the current conversation) ----

async function loadHistory() {
  try {
    const res = await fetch("/api/history");
    const data = await res.json();
    (data.messages || []).forEach((m) => renderMessage(m.role, m.content));
  } catch (e) {
    // fine to start with an empty window if this fails
  }
}

// ---------------- "continuing from" banner --------------------------

function showContinuingBanner(summary) {
  const snippet = (summary || "").trim();
  continuingBannerText.textContent = snippet
    ? `Continuing an earlier conversation: ${snippet}`
    : "Continuing an earlier conversation.";
  continuingBanner.hidden = false;
}

function hideContinuingBanner() {
  continuingBanner.hidden = true;
}

async function loadConversationState() {
  try {
    const res = await fetch("/api/conversation-state");
    const data = await res.json();
    if (data.continued_from) {
      showContinuingBanner(data.summary);
    } else {
      hideContinuingBanner();
    }
  } catch (e) {
    hideContinuingBanner();
  }
}

continuingBannerDismiss.addEventListener("click", async () => {
  hideContinuingBanner(); // optimistic - hide immediately
  try {
    // Persist the dismissal server-side so the banner doesn't come back
    // on the next load/restart while this conversation is still active.
    await fetch("/api/conversation-state/dismiss", { method: "POST" });
  } catch (e) {
    // fine — worst case it reappears next load and can be dismissed again
  }
});

// ---------------- library / RAG (side panel) ----------------

let ragPollTimer = null;

async function loadRagSources() {
  try {
    const res = await fetch("/api/rag/sources");
    const data = await res.json();
    const items = data.sources || [];
    ragSourcesList.innerHTML = "";

    if (!items.length) {
      const p = document.createElement("p");
      p.className = "conversations-empty";
      p.textContent = "No documents indexed yet.";
      ragSourcesList.appendChild(p);
      return;
    }

    items.forEach((s) => {
      const row = document.createElement("div");
      row.className = "rag-source-item";

      const name = document.createElement("span");
      name.className = "rag-source-name";
      name.textContent = s.source;
      name.title = s.source;

      const count = document.createElement("span");
      count.className = "rag-source-count";
      count.textContent = `${s.chunks} chunks`;

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "conversation-delete";
      removeBtn.setAttribute("aria-label", `Remove ${s.source}`);
      removeBtn.title = "Remove from library";
      removeBtn.textContent = "✕";
      removeBtn.addEventListener("click", async () => {
        await fetch(`/api/rag/sources/${encodeURIComponent(s.source)}`, { method: "DELETE" });
        loadRagSources();
      });

      row.appendChild(name);
      row.appendChild(count);
      row.appendChild(removeBtn);
      ragSourcesList.appendChild(row);
    });
  } catch (e) {
    // leave whatever was already rendered
  }
}

async function pollRagProgress() {
  try {
    const res = await fetch("/api/rag/progress");
    const data = await res.json();
    if (data.active) {
      const done = (data.results || []).length;
      const total = data.total_files || "?";
      ragStatus.textContent = `Indexing "${data.file || "…"}" (${done}/${total} files done)…`;
      ragIndexBtn.disabled = true;
      ragPollTimer = setTimeout(pollRagProgress, 1500);
    } else {
      ragIndexBtn.disabled = false;
      if (data.results && data.results.length) {
        const failed = data.results.filter((r) => !r.ok).length;
        ragStatus.textContent = failed
          ? `Done, but ${failed} file(s) failed — check the folder path/format.`
          : "Indexing complete.";
      }
      loadRagSources();
      clearTimeout(ragPollTimer);
    }
  } catch (e) {
    ragIndexBtn.disabled = false;
  }
}

ragIndexBtn.addEventListener("click", async () => {
  const folder = ragFolderInput.value.trim();
  if (!folder) return;
  ragStatus.textContent = "Starting…";
  ragIndexBtn.disabled = true;
  try {
    const res = await fetch("/api/rag/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder }),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      ragStatus.textContent = data.error || "Couldn't start indexing.";
      ragIndexBtn.disabled = false;
      return;
    }
    pollRagProgress();
  } catch (e) {
    ragStatus.textContent = "Couldn't reach the local server.";
    ragIndexBtn.disabled = false;
  }
});

// ---------------- past conversations (side panel) ---------------------

function formatDate(dateStr) {
  try {
    const d = new Date(dateStr + "T00:00:00");
    return d.toLocaleDateString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
    });
  } catch (e) {
    return dateStr;
  }
}

async function loadConversations() {
  try {
    const res = await fetch("/api/conversations");
    const data = await res.json();
    const items = data.conversations || [];
    conversationsList.innerHTML = "";

    if (!items.length) {
      const p = document.createElement("p");
      p.className = "conversations-empty";
      p.textContent = "Nothing archived yet.";
      conversationsList.appendChild(p);
      return;
    }

    items.forEach((item) => {
      const el = document.createElement("div");
      el.className = "conversation-item";

      const topRow = document.createElement("div");
      topRow.className = "conversation-top-row";

      const dateEl = document.createElement("div");
      dateEl.className = "conversation-date";
      dateEl.textContent = formatDate(item.date);

      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "conversation-delete";
      deleteBtn.setAttribute("aria-label", "Delete this conversation");
      deleteBtn.title = "Delete this conversation";
      deleteBtn.textContent = "✕";
      deleteBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteConversation(item.id);
      });

      topRow.appendChild(dateEl);
      topRow.appendChild(deleteBtn);

      const snippetEl = document.createElement("div");
      snippetEl.className = "conversation-snippet";
      snippetEl.textContent = item.summary;

      const continueBtn = document.createElement("button");
      continueBtn.type = "button";
      continueBtn.className = "conversation-continue";
      continueBtn.textContent = "Continue this conversation";
      continueBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        continueConversation(item.id);
      });

      el.appendChild(topRow);
      el.appendChild(snippetEl);
      el.appendChild(continueBtn);
      conversationsList.appendChild(el);
    });
  } catch (e) {
    // leave whatever was already rendered
  }
}

async function continueConversation(convId) {
  setStatus("Loading conversation…");
  try {
    const res = await fetch(`/api/conversations/${convId}/continue`, {
      method: "POST",
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      setStatus(data.error || "Couldn't load that conversation.", true);
      return;
    }
    chatEl.innerHTML = "";
    showContinuingBanner(data.summary);
    closePanel();
    setStatus("");
    loadConversations();
    input.focus();
  } catch (e) {
    setStatus("Couldn't reach the local server.", true);
  }
}

async function deleteConversation(convId) {
  if (!confirm("Delete this conversation from history? This can't be undone.")) return;
  try {
    const res = await fetch(`/api/conversations/${convId}`, { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.error) {
      setStatus(data.error || "Couldn't delete that conversation.", true);
      return;
    }
    loadConversations();
  } catch (e) {
    setStatus("Couldn't reach the local server.", true);
  }
}

clearConversationsBtn.addEventListener("click", async () => {
  if (!confirm("Delete ALL conversation history? This can't be undone.")) return;
  try {
    await fetch("/api/conversations/clear-all", { method: "POST" });
    loadConversations();
  } catch (e) {
    setStatus("Couldn't reach the local server.", true);
  }
});

// ---------------- file / image attachments ----------------

function extOf(filename) {
  const parts = filename.split(".");
  return parts.length > 1 ? parts.pop().toLowerCase() : "";
}

function renderAttachments() {
  attachmentsRow.innerHTML = "";
  attachmentsRow.hidden = attachments.length === 0;

  attachments.forEach((att) => {
    const chip = document.createElement("span");
    chip.className = "attachment-chip" + (att.kind === "unsupported" ? " unsupported" : "");

    const name = document.createElement("span");
    name.className = "attachment-chip-name";
    name.textContent = att.name;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "attachment-chip-remove";
    remove.setAttribute("aria-label", `Remove ${att.name}`);
    remove.textContent = "✕";
    remove.addEventListener("click", () => {
      attachments = attachments.filter((a) => a.id !== att.id);
      renderAttachments();
    });

    chip.appendChild(name);
    chip.appendChild(remove);
    attachmentsRow.appendChild(chip);
  });
}

function readFileAsText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1] || "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

async function handleFiles(fileList) {
  for (const file of Array.from(fileList)) {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    try {
      if (IMAGE_TYPES.includes(file.type)) {
        const base64 = await readFileAsBase64(file);
        attachments.push({ id, name: file.name, kind: "image", data: base64 });
      } else if (TEXT_EXTENSIONS.includes(extOf(file.name))) {
        const text = await readFileAsText(file);
        attachments.push({ id, name: file.name, kind: "text", data: text });
      } else {
        // PDFs, Word docs, etc. — no client-side parser available yet.
        attachments.push({ id, name: file.name, kind: "unsupported", data: "" });
      }
    } catch (e) {
      attachments.push({ id, name: file.name, kind: "unsupported", data: "" });
    }
  }
  renderAttachments();
  const hasUnsupported = attachments.some((a) => a.kind === "unsupported");
  if (hasUnsupported) {
    setStatus(
      "Some attached files can't be read yet — only images and plain text/markdown/CSV/JSON are supported.",
      false
    );
  }
}

fileImportBtn.addEventListener("click", () => fileInput.click());

fileInput.addEventListener("change", () => {
  if (fileInput.files && fileInput.files.length) {
    handleFiles(fileInput.files);
  }
  fileInput.value = ""; // allow re-selecting the same file later
});

// ---------------- sending messages ----------------

/**
 * Streamed text arrives from the network in uneven bursts - sometimes a
 * whole sentence at once, sometimes one word, depending on buffering. If
 * we just dump each burst straight into the bubble, replies look jerky:
 * a chunk of words appears, then a pause, then another chunk. This queues
 * incoming text and reveals it a little at a time on a steady clock, so
 * it always reads like smooth, continuous typing regardless of how the
 * network actually delivered it. The reveal rate scales up automatically
 * if the backlog grows (e.g. a fast model outrunning the display), so a
 * big burst still catches up quickly instead of trailing far behind.
 */
function createTypewriter(bubble, onTick) {
  const TICK_MS = 18;
  let pending = "";
  let displayed = "";
  let timer = null;

  function tick() {
    if (!pending.length) {
      timer = null;
      return;
    }
    // Reveal more per tick as the backlog grows, so a large burst doesn't
    // take unreasonably long to catch up, while small bursts still get
    // the smooth one-or-two-characters-at-a-time feel.
    const take = Math.max(1, Math.min(pending.length, Math.ceil(pending.length / 15)));
    displayed += pending.slice(0, take);
    pending = pending.slice(take);
    bubble.textContent = displayed;
    if (onTick) onTick();
    timer = setTimeout(tick, TICK_MS);
  }

  return {
    push(text) {
      pending += text;
      if (!timer) tick();
    },
    // Show everything immediately - used when we're discarding/replacing
    // the bubble anyway (e.g. an error arrived), so there's no point
    // continuing to animate text nobody will see finish.
    finishInstantly() {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      displayed += pending;
      pending = "";
      bubble.textContent = displayed;
    },
  };
}

async function sendMessage(typedText) {
  const staged = attachments;
  attachments = [];
  renderAttachments();

  // Build what actually goes to the model: typed text + any extracted
  // document text appended, plus any images passed through separately.
  let fullMessage = typedText;
  const images = [];
  const displayAttachmentNames = staged.map((a) => a.name);

  staged.forEach((att) => {
    if (att.kind === "text") {
      fullMessage += `\n\n--- Attached file: ${att.name} ---\n${att.data}`;
    } else if (att.kind === "image") {
      images.push(att.data);
      fullMessage += `\n\n[Attached image: ${att.name}]`;
    } else {
      fullMessage += `\n\n[Attached file: ${att.name} — couldn't extract text from this file type]`;
    }
  });

  if (!fullMessage.trim()) fullMessage = "(sent an attachment with no message)";

  renderMessage("user", typedText || "(attachment)", displayAttachmentNames);
  input.value = "";
  autoResize();
  sendBtn.disabled = true;
  setStatus("Thinking…");

  // An empty assistant bubble that gets filled in as chunks stream in -
  // this is what makes replies feel fast: the first words appear as soon
  // as the model produces them, instead of waiting for the whole reply.
  if (emptyState) emptyState.remove();
  const row = document.createElement("div");
  row.className = "msg-row assistant";
  const bubble = document.createElement("div");
  bubble.className = "msg";
  row.appendChild(bubble);
  chatEl.appendChild(row);
  chatEl.scrollTop = chatEl.scrollHeight;

  let assistantText = "";
  let sawError = false;
  const typewriter = createTypewriter(bubble, () => {
    chatEl.scrollTop = chatEl.scrollHeight;
  });

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: fullMessage,
        model: modelSelect.value,
        images: images,
      }),
    });

    if (!res.ok || !res.body) {
      const data = await res.json().catch(() => ({}));
      row.remove();
      renderMessage("error", data.error || "Something went wrong.");
      setStatus("");
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let newlineIdx;
      while ((newlineIdx = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, newlineIdx).trim();
        buffer = buffer.slice(newlineIdx + 1);
        if (!line) continue;

        let msg;
        try {
          msg = JSON.parse(line);
        } catch (e) {
          continue;
        }

        if (msg.type === "chunk") {
          if (assistantText === "") setStatus("");
          assistantText += msg.text;
          typewriter.push(msg.text);
        } else if (msg.type === "error") {
          sawError = true;
          typewriter.finishInstantly();
          row.remove();
          renderMessage("error", msg.message || "Something went wrong.");
        }
      }
    }

    if (!assistantText && !sawError) {
      row.remove();
      renderMessage("error", "The model returned an empty response.");
    }
    setStatus("");
  } catch (e) {
    row.remove();
    renderMessage("error", "Couldn't reach the local server.");
    setStatus("");
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
}

newChatBtn.addEventListener("click", () => {
  // Clear the UI immediately - don't wait on the network call. The
  // previous conversation is archived in the background on the server,
  // so New Chat feels instant even if the model is slow or unreachable.
  chatEl.innerHTML = "";
  hideContinuingBanner();
  setStatus("Started a new chat.");

  fetch("/api/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: modelSelect.value }),
  })
    .then(() => loadConversations())
    .catch(() => {
      // fine — the reset still happened server-side even if this
      // follow-up refresh of the history list fails
    });
});

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text && attachments.length === 0) return;
  sendMessage(text);
});

input.addEventListener("input", autoResize);

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

loadModels();
loadHistory();
loadTone();
loadConversations();
loadConversationState();
loadRagSources();
checkAuthStatus();
input.focus();
