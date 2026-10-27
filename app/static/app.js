const API = "/api/v1";
const authPanel = document.querySelector("#auth-panel");
const authForm = document.querySelector("#auth-form");
const authModeToggle = document.querySelector("#auth-mode-toggle");
const authError = document.querySelector("#auth-error");
const authSuccess = document.querySelector("#auth-success");
const signoutButton = document.querySelector("#signout-button");
let authMode = "signin";
const fileInput = document.querySelector("#document-files");
const uploadForm = document.querySelector("#upload-form");
const uploadButton = document.querySelector("#upload-button");
const uploadSelection = document.querySelector("#upload-selection");
const documentList = document.querySelector("#document-list");
const documentCount = document.querySelector("#document-count");
const documentsEmpty = document.querySelector("#documents-empty");
const chatForm = document.querySelector("#chat-form");
const questionInput = document.querySelector("#question");
const imageInput = document.querySelector("#chat-images");
const sendButton = document.querySelector("#send-button");
const messages = document.querySelector("#messages");
const chatWelcome = document.querySelector("#chat-welcome");
const chatGate = document.querySelector("#chat-gate");
const attachmentLabel = document.querySelector("#attachment-label");
const attachmentPreview = document.querySelector("#attachment-preview");
const toast = document.querySelector("#toast");
const threadSelect = document.querySelector("#thread-select");
const newThreadButton = document.querySelector("#new-thread");
const memoryToggle = document.querySelector("#memory-toggle");
const memoryManager = document.querySelector("#memory-manager");
const memoryList = document.querySelector("#memory-list");
const forgetAllMemoryButton = document.querySelector("#forget-all-memory");
const documents = new Map();
let toastTimer;
let isSending = false;
let activeThreadId = localStorage.getItem("rag_thread_id");

function notify(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 3600);
}

function safeStatus(status) {
  return ["queued", "processing", "completed", "failed"].includes(status) ? status : "failed";
}

function renderDocument(item) {
  let row = document.querySelector(`[data-job-id="${CSS.escape(item.job_id)}"]`);
  if (!row) {
    row = document.createElement("div");
    row.className = "document-item";
    row.dataset.jobId = item.job_id;
    const icon = document.createElement("div");
    icon.className = "file-icon";
    icon.textContent = item.filename.toLowerCase().endsWith(".pdf") ? "PDF" : "IMG";
    const info = document.createElement("div");
    info.className = "document-info";
    const name = document.createElement("div");
    name.className = "document-name";
    name.title = item.filename;
    name.textContent = item.filename;
    const meta = document.createElement("div");
    meta.className = "document-meta";
    const progress = document.createElement("div");
    progress.className = "document-progress";
    progress.setAttribute("role", "progressbar");
    progress.setAttribute("aria-label", "PDF processing progress");
    const progressFill = document.createElement("div");
    progressFill.className = "document-progress-fill";
    progress.append(progressFill);
    const details = document.createElement("div");
    details.className = "document-details";
    info.append(name, meta, progress, details);
    const status = document.createElement("span");
    status.className = "status";
    status.dataset.status = "";
    row.append(icon, info, status);
    documentList.prepend(row);
  }
  const meta = row.querySelector(".document-meta");
  const percent = Math.max(0, Math.min(100, Number(item.progress_percent || 0)));
  const progress = row.querySelector(".document-progress");
  progress.setAttribute("aria-valuemin", "0");
  progress.setAttribute("aria-valuemax", "100");
  progress.setAttribute("aria-valuenow", String(percent));
  row.querySelector(".document-progress-fill").style.width = `${percent}%`;
  const detail = item.progress_details || {};
  row.querySelector(".document-details").textContent = normalizedDocumentReady(item.status)
    ? "Ready to query"
    : detail.total_records != null
    ? `Pages ${detail.pages || 0} · Text chunks ${detail.text_chunks || 0} · Table chunks ${detail.table_chunks || 0} · Image records ${detail.image_records || 0} · Total ${detail.total_records || 0}`
    : detail.pages != null ? `PDF pages ${detail.pages} · extracting content and counting chunks…` : "Waiting for worker…";
  if (item.progress_message) {
    const count = item.progress_total > 0 ? ` (${item.progress_current}/${item.progress_total})` : "";
    meta.textContent = `${percent}% · ${item.progress_message}${count}`;
  } else {
    meta.textContent = `Document ${item.document_id.slice(0, 8)}`;
  }
  const status = row.querySelector("[data-status]");
  const normalized = safeStatus(item.status);
  status.textContent = normalized === "completed" ? "Ready" : normalized[0].toUpperCase() + normalized.slice(1);
  status.className = `status ${normalized}`;
  if (item.error) row.title = item.error;
  documents.set(item.job_id, {...item, status: normalized});
  documentCount.textContent = String(documents.size);
  documentsEmpty?.classList.toggle("hidden", documents.size > 0);
  updateChatGate();
}

function normalizedDocumentReady(status) {
  return status === "completed";
}

function updateChatGate() {
  const hasReadyDocument = [...documents.values()].some((doc) => doc.status === "completed");
  questionInput.disabled = false;
  imageInput.disabled = false;
  sendButton.disabled = isSending;
  chatGate.classList.add("hidden");
  questionInput.placeholder = hasReadyDocument
    ? "Ask a general question or ask about your documents…"
    : "Ask a general question… Upload documents for domain-specific answers.";
}

async function parseResponse(response) {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return body;
}

function streamIngestion(jobId, filename) {
  const stream = new EventSource(`${API}/ingestions/${encodeURIComponent(jobId)}/events`);
  stream.addEventListener("progress", (event) => {
    const current = JSON.parse(event.data);
    renderDocument({...current, filename});
    if (current.status === "completed") {
      stream.close();
      notify(`${filename} is ready to query.`);
    } else if (current.status === "failed") {
      stream.close();
      notify(`${filename} could not be processed${current.error ? `: ${current.error}` : "."}`);
    }
  });
  stream.onerror = () => {
    if (stream.readyState === EventSource.CLOSED) {
      notify(`Lost the processing event stream for ${filename}.`);
    }
  };
}

fileInput.addEventListener("change", () => {
  const selected = [...fileInput.files];
  uploadSelection.textContent = selected.length ? selected.map((file) => file.name).join(" · ") : "";
  uploadSelection.classList.toggle("hidden", selected.length === 0);
  uploadButton.disabled = selected.length === 0;
});

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const selected = [...fileInput.files];
  if (!selected.length) return;
  uploadButton.disabled = true;
  uploadButton.querySelector("span:first-child").textContent = "Queueing…";
  for (const file of selected) {
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch(`${API}/documents`, {method: "POST", body: form});
      const job = await parseResponse(response);
      renderDocument({...job, filename: file.name});
      if (job.status === "completed") notify(`${file.name} is already indexed.`);
      else if (job.status === "failed") notify(`${file.name} failed: ${job.error || "ingestion error"}`);
      else streamIngestion(job.job_id, file.name);
    } catch (error) {
      notify(`Could not upload ${file.name}: ${error.message}`);
    }
  }
  fileInput.value = "";
  uploadSelection.textContent = "";
  uploadSelection.classList.add("hidden");
  uploadButton.disabled = true;
  uploadButton.querySelector("span:first-child").textContent = "Process documents";
});

const dropzone = document.querySelector("#dropzone");
for (const eventName of ["dragenter", "dragover"]) {
  dropzone.addEventListener(eventName, (event) => { event.preventDefault(); dropzone.classList.add("dragover"); });
}
for (const eventName of ["dragleave", "drop"]) {
  dropzone.addEventListener(eventName, (event) => { event.preventDefault(); dropzone.classList.remove("dragover"); });
}
dropzone.addEventListener("drop", (event) => {
  if (!event.dataTransfer?.files?.length) return;
  fileInput.files = event.dataTransfer.files;
  fileInput.dispatchEvent(new Event("change"));
});

imageInput.addEventListener("change", () => {
  const selected = [...imageInput.files];
  attachmentLabel.textContent = selected.length ? `${selected.length} image${selected.length === 1 ? "" : "s"} attached` : "Add an image";
  attachmentPreview.replaceChildren();
  for (const file of selected) {
    const chip = document.createElement("span");
    chip.className = "preview-chip";
    chip.textContent = file.name;
    chip.title = file.name;
    attachmentPreview.append(chip);
  }
  attachmentPreview.classList.toggle("hidden", selected.length === 0);
});

function appendMessage(role, text, result = null) {
  chatWelcome.classList.add("hidden");
  const bubble = document.createElement("article");
  bubble.className = `message ${role}`;
  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = role === "user" ? "You" : role === "error" ? "Request error" : "Answer";
  const content = document.createElement("div");
  content.className = "message-text";
  content.textContent = text;
  bubble.append(label, content);
  if (result?.insufficient_evidence) {
    const note = document.createElement("div");
    note.className = "insufficient";
    note.textContent = "The retrieved sources did not contain enough evidence for a grounded answer.";
    bubble.append(note);
  }
  if (result?.citations?.length) {
    const citations = document.createElement("div");
    citations.className = "citation-list";
    for (const citation of result.citations) {
      const wrapper = document.createElement("div");
      wrapper.className = citation.evidence_type === "image" ? "image-citation" : "citation-item";
      const source = document.createElement("span");
      source.className = "citation";
      const page = citation.page_number ? ` · p. ${citation.page_number}` : "";
      source.textContent = citation.evidence_type === "image"
        ? `${citation.filename}${page}`
        : `${citation.filename}${page} · ${citation.evidence_type}`;
      source.title = citation.document_id ? `Document ${citation.document_id}` : "User-provided image";
      wrapper.append(source);
      if (citation.evidence_type === "image") {
        const link = document.createElement("a");
        link.className = "image-citation-link";
        link.href = `${API}/evidence/${encodeURIComponent(citation.evidence_id)}/image`;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.title = `Open image from ${citation.filename}${page}`;
        const image = document.createElement("img");
        image.src = link.href;
        image.alt = `Image from ${citation.filename}${page}`;
        image.loading = "lazy";
        link.append(image);
        wrapper.append(link);
      }
      citations.append(wrapper);
    }
    bubble.append(citations);
  }
  messages.append(bubble);
  document.querySelector("#chat-content").scrollTop = document.querySelector("#chat-content").scrollHeight;
  return bubble;
}

function partialAnswer(raw) {
  const marker = /"answer"\s*:\s*"/s.exec(raw);
  if (!marker) return "";
  let output = "";
  const start = marker.index + marker[0].length;
  for (let i = start; i < raw.length; i += 1) {
    const char = raw[i];
    if (char === '"') break;
    if (char !== "\\") { output += char; continue; }
    if (i + 1 >= raw.length) break;
    const escaped = raw[++i];
    const simple = {"\"": '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t"};
    if (Object.hasOwn(simple, escaped)) output += simple[escaped];
    else if (escaped === "u" && /^[0-9a-f]{4}$/i.test(raw.slice(i + 1, i + 5))) {
      output += String.fromCharCode(parseInt(raw.slice(i + 1, i + 5), 16));
      i += 4;
    } else break;
  }
  return output;
}

async function consumeEventStream(response, handleEvent) {
  if (!response.ok) { await parseResponse(response); return; }
  if (!response.body) throw new Error("This browser cannot read streamed responses.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const {value, done} = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
    let boundary;
    while ((boundary = buffer.search(/\r?\n\r?\n/)) >= 0) {
      const block = buffer.slice(0, boundary);
      const separator = buffer.slice(boundary).match(/^\r?\n\r?\n/)[0];
      buffer = buffer.slice(boundary + separator.length);
      let eventName = "message";
      const data = [];
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) handleEvent(eventName, JSON.parse(data.join("\n")));
    }
    if (done) break;
  }
}

async function refreshThreads(selectId = activeThreadId, loadActive = true) {
  const response = await fetch(`${API}/threads`);
  const threads = await parseResponse(response);
  threadSelect.replaceChildren();
  for (const thread of threads) {
    const option = document.createElement("option");
    option.value = thread.id;
    option.textContent = thread.title;
    threadSelect.append(option);
  }
  if (!threads.some((thread) => thread.id === selectId)) selectId = threads[0]?.id;
  if (selectId) {
    threadSelect.value = selectId;
    if (loadActive) await activateThread(selectId, false);
  } else {
    await createThread();
  }
}

async function activateThread(threadId, updateSelection = true) {
  const response = await fetch(`${API}/threads/${encodeURIComponent(threadId)}`);
  const thread = await parseResponse(response);
  activeThreadId = thread.id;
  localStorage.setItem("rag_thread_id", activeThreadId);
  if (updateSelection) threadSelect.value = thread.id;
  messages.replaceChildren();
  chatWelcome.classList.toggle("hidden", thread.messages.length > 0);
  for (const message of thread.messages) {
    appendMessage(message.role, message.content, {citations: message.citations});
  }
}

async function createThread() {
  const response = await fetch(`${API}/threads`, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({}),
  });
  const thread = await parseResponse(response);
  activeThreadId = thread.id;
  localStorage.setItem("rag_thread_id", activeThreadId);
  const option = document.createElement("option");
  option.value = thread.id;
  option.textContent = thread.title;
  threadSelect.prepend(option);
  threadSelect.value = thread.id;
  messages.replaceChildren();
  chatWelcome.classList.remove("hidden");
}

threadSelect.addEventListener("change", () => {
  if (threadSelect.value) activateThread(threadSelect.value).catch((error) => notify(error.message));
});
newThreadButton.addEventListener("click", () => createThread().catch((error) => notify(error.message)));
updateChatGate();

async function loadStoredPDFs() {
  const response = await fetch(`${API}/documents`);
  const rows = await parseResponse(response);
  for (const row of rows) {
    const rendered = {
      ...row, document_id: row.id, job_id: row.job_id || `stored-${row.id}`,
      status: row.status,
      progress_details: row.progress_details || {},
    };
    renderDocument(rendered);
    if (row.job_id && ["queued", "processing"].includes(row.status)) {
      streamIngestion(row.job_id, row.filename);
    }
  }
}

function showWorkspace(signedIn) {
  authPanel.classList.toggle("hidden", signedIn);
  document.querySelectorAll(".requires-auth").forEach((item) => item.classList.toggle("hidden", !signedIn));
  signoutButton.classList.toggle("hidden", !signedIn);
}

function setAuthMode(mode) {
  authMode = mode;
  authSuccess.classList.add("hidden");
  document.querySelector("#auth-heading").textContent = authMode === "signin" ? "Welcome back" : "Create your account";
  document.querySelector("#auth-description").textContent = authMode === "signin"
    ? "Sign in to continue to your workspace."
    : "Start your private workspace. Your account keeps documents and memory separate.";
  document.querySelector("#auth-password").autocomplete = authMode === "signin" ? "current-password" : "new-password";
  document.querySelector("#auth-submit").textContent = authMode === "signin" ? "Sign in" : "Create account";
  document.querySelector("#auth-switch-prompt").textContent = authMode === "signin" ? "New to the workspace?" : "Already have an account?";
  authModeToggle.textContent = authMode === "signin" ? "Create an account" : "Sign in";
  authError.classList.add("hidden");
}

authModeToggle.addEventListener("click", () => {
  setAuthMode(authMode === "signin" ? "signup" : "signin");
});

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  authError.classList.add("hidden");
  const button = document.querySelector("#auth-submit");
  button.disabled = true;
  try {
    const response = await fetch(`${API}/auth/${authMode}`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({email: document.querySelector("#auth-email").value,
        password: document.querySelector("#auth-password").value}),
    });
    const account = await parseResponse(response);
    authForm.reset();
    if (authMode === "signup") {
      setAuthMode("signin");
      document.querySelector("#auth-email").value = account.email;
      authSuccess.textContent = "Your account is ready. Sign in with your new email and password.";
      authSuccess.classList.remove("hidden");
      document.querySelector("#auth-password").focus();
      return;
    }
    showWorkspace(true);
    await Promise.all([loadStoredPDFs(), refreshThreads()]);
  } catch (error) {
    authError.textContent = error.message;
    authError.classList.remove("hidden");
  } finally { button.disabled = false; }
});

signoutButton.addEventListener("click", async () => {
  try {
    await parseResponse(await fetch(`${API}/auth/signout`, {method: "POST"}));
    activeThreadId = null;
    localStorage.removeItem("rag_thread_id");
    documents.clear();
    documentList.replaceChildren();
    documentCount.textContent = "0";
    showWorkspace(false);
  } catch (error) { notify(error.message); }
});

fetch(`${API}/auth/me`).then(async (response) => {
  if (!response.ok) { showWorkspace(false); return; }
  showWorkspace(true);
  await Promise.all([loadStoredPDFs(), refreshThreads()]);
}).catch(() => showWorkspace(false));

async function loadMemories() {
  const response = await fetch(`${API}/memories`);
  const items = await parseResponse(response);
  memoryList.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "memory-empty";
    empty.textContent = "No long-term memories are saved.";
    memoryList.append(empty);
    return;
  }
  for (const item of items) {
    const row = document.createElement("article");
    row.className = "memory-item";
    const category = document.createElement("span");
    category.className = "memory-category";
    category.textContent = item.memory_type;
    const content = document.createElement("span");
    content.className = "memory-content";
    content.textContent = item.content;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "memory-remove";
    remove.textContent = "Delete";
    remove.setAttribute("aria-label", `Delete ${item.memory_type} memory`);
    remove.addEventListener("click", async () => {
      try {
        const result = await fetch(`${API}/memories/${encodeURIComponent(item.id)}`, {method: "DELETE"});
        if (!result.ok) await parseResponse(result);
        await loadMemories();
      } catch (error) { notify(`Could not delete memory: ${error.message}`); }
    });
    row.append(category, content, remove);
    memoryList.append(row);
  }
}

memoryToggle.addEventListener("click", async () => {
  memoryManager.classList.toggle("hidden");
  if (!memoryManager.classList.contains("hidden")) {
    try { await loadMemories(); } catch (error) { notify(`Could not load memories: ${error.message}`); }
  }
});

forgetAllMemoryButton.addEventListener("click", async () => {
  if (!window.confirm("Forget all saved memories and delete all conversation threads and their messages? This cannot be undone.")) return;
  forgetAllMemoryButton.disabled = true;
  try {
    const response = await fetch(`${API}/memory`, {method: "DELETE"});
    const result = await parseResponse(response);
    localStorage.removeItem("rag_thread_id");
    activeThreadId = null;
    messages.replaceChildren();
    await refreshThreads(null);
    await loadMemories();
    notify(`Forgot ${result.deleted_memories} memories and cleared ${result.deleted_threads} threads.`);
  } catch (error) { notify(`Could not forget all memory: ${error.message}`); }
  finally { forgetAllMemoryButton.disabled = false; }
});

window.addEventListener("storage", (event) => {
  if (event.key === "rag_thread_id" && event.newValue === null) {
    activeThreadId = null;
    refreshThreads(null).catch((error) => notify(error.message));
  }
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question || sendButton.disabled) return;
  const attachedImages = [...imageInput.files];
  appendMessage("user", question + (attachedImages.length ? `\n[${attachedImages.length} image${attachedImages.length === 1 ? "" : "s"} attached]` : ""));
  questionInput.value = "";
  isSending = true;
  sendButton.disabled = true;
  sendButton.querySelector("span:first-child").textContent = "Working";
  let answerBubble = appendMessage("assistant", "");
  let answerText = answerBubble.querySelector(".message-text");
  let answerStatus = document.createElement("div");
  answerStatus.className = "message-status";
  answerStatus.textContent = "Starting…";
  answerBubble.append(answerStatus);
  let rawAnswer = "";
  let receivedResult = false;
  try {
    let response;
    if (attachedImages.length) {
      const form = new FormData();
      form.append("question", question);
      form.append("thread_id", activeThreadId);
      attachedImages.forEach((image) => form.append("images", image));
      response = await fetch(`${API}/chat/multimodal/stream`, {method: "POST", body: form});
    } else {
      response = await fetch(`${API}/chat/stream`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({question, thread_id: activeThreadId}),
      });
    }
    await consumeEventStream(response, (eventName, payload) => {
      if (eventName === "stage") answerStatus.textContent = payload.message || "Working…";
      if (eventName === "token") {
        rawAnswer += payload.delta || "";
        answerText.textContent = partialAnswer(rawAnswer);
        answerStatus.textContent = "Generating answer…";
        document.querySelector("#chat-content").scrollTop = document.querySelector("#chat-content").scrollHeight;
      }
      if (eventName === "result") {
        receivedResult = true;
        if (payload.thread_id && payload.thread_id !== activeThreadId) {
          activeThreadId = payload.thread_id;
          localStorage.setItem("rag_thread_id", activeThreadId);
        }
        answerBubble.remove();
        answerBubble = appendMessage("assistant", payload.answer, payload);
        answerText = answerBubble.querySelector(".message-text");
        answerStatus = document.createElement("div");
        answerStatus.className = "message-status";
        answerBubble.append(answerStatus);
        answerText.textContent = payload.answer;
        void refreshThreads(activeThreadId, false);
        if (payload.insufficient_evidence) notify("The answer was limited because the sources lacked enough evidence.");
      }
      if (eventName === "error") throw new Error(payload.message || "The request failed.");
      if (eventName === "done") answerStatus.textContent = "Complete";
    });
    if (!receivedResult) throw new Error("The response stream ended before an answer arrived.");
    imageInput.value = "";
    imageInput.dispatchEvent(new Event("change"));
  } catch (error) {
    if (answerBubble.isConnected && answerText.textContent) answerStatus.textContent = `Stopped: ${error.message}`;
    else { answerBubble.remove(); appendMessage("error", error.message); }
  } finally {
    isSending = false;
    sendButton.querySelector("span:first-child").textContent = "Ask";
    updateChatGate();
  }
});

document.querySelector(".suggestion").addEventListener("click", (event) => {
  questionInput.value = "Why is the sky blue?";
  questionInput.focus();
});
