const chatEl = document.getElementById("chat");
const emptyState = document.getElementById("empty-state");
const inputEl = document.getElementById("input");
const sendBtn = document.getElementById("send-btn");
const attachBtn = document.getElementById("attach-btn");
const fileInput = document.getElementById("file-input");
const attachmentsEl = document.getElementById("attachments");
const menuBtn = document.getElementById("menu-btn");
const sidebar = document.getElementById("sidebar");
const newChatBtn = document.getElementById("new-chat-btn");
const historyEl = document.getElementById("history");

let messages = [];
let pendingFiles = [];

menuBtn.addEventListener("click", () => {
  sidebar.classList.toggle("open");
});

newChatBtn.addEventListener("click", async () => {
  await fetch("/api/memory/new", { method: "POST" });
  messages = [];
  chatEl.innerHTML = "";
  chatEl.appendChild(emptyState);
  emptyState.style.display = "";
});

attachBtn.addEventListener("click", () => fileInput.click());

fileInput.addEventListener("change", () => {
  for (const f of fileInput.files) {
    pendingFiles.push(f);
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.textContent = f.name;
    const rm = document.createElement("button");
    rm.textContent = "×";
    rm.onclick = () => {
      pendingFiles = pendingFiles.filter(x => x !== f);
      chip.remove();
    };
    chip.appendChild(rm);
    attachmentsEl.appendChild(chip);
  }
  fileInput.value = "";
});

function addMessage(role, content) {
  emptyState.style.display = "none";
  const div = document.createElement("div");
  div.className = "message " + role;
  div.textContent = content;
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
  return div;
}

async function send() {
  const text = inputEl.value.trim();
  if (!text && pendingFiles.length === 0) return;

  let content = text;
  const images = [];

  for (const f of pendingFiles) {
    if (f.type.startsWith("image/")) {
      const b64 = await fileToBase64(f);
      images.push(b64.split(",")[1]);
    } else if (/text|json|csv|markdown|log/.test(f.type) || /\.(txt|md|csv|json|log)$/i.test(f.name)) {
      content += "\n\n--- " + f.name + " ---\n" + await f.text();
    } else {
      content += "\n\n[Attached file " + f.name + " could not be read]";
    }
  }

  pendingFiles = [];
  attachmentsEl.innerHTML = "";
  inputEl.value = "";

  const userMsg = { role: "user", content };
  if (images.length) userMsg.images = images;
  messages.push(userMsg);
  addMessage("user", text || "(attachment)");

  const assistantDiv = addMessage("assistant", "...");

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages, stream: false }),
    });
    const data = await res.json();
    if (data.error) {
      assistantDiv.textContent = "Error: " + data.error;
    } else {
      assistantDiv.textContent = data.reply;
      messages.push({ role: "assistant", content: data.reply });
    }
  } catch (e) {
    assistantDiv.textContent = "Error: " + e.message;
  }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

sendBtn.addEventListener("click", send);
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});

// Auto-resize textarea
inputEl.addEventListener("input", () => {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + "px";
});
