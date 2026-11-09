"use strict";
const $ = (s, el = document) => el.querySelector(s);
const main = $("#main");
const state = { status: null, chats: [], streaming: null, pollTimer: null };

// ---------- helpers ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"]; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n < 10 && i ? 1 : 0)} ${u[i]}`;
}
function ago(t) {
  const s = Date.now() / 1000 - t;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString();
}
async function api(path, opts = {}) {
  const headers = { "X-Daftar": "1" };
  let body = opts.body;
  if (body && !(body instanceof FormData)) { headers["Content-Type"] = "application/json"; body = JSON.stringify(body); }
  const r = await fetch("/api" + path, { ...opts, headers, body });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch { /* not json */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return r.json();
}
let toastTimer;
function toast(text, isErr = false) {
  const t = $("#toast");
  t.textContent = text; t.className = "toast" + (isErr ? " err" : ""); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), isErr ? 6000 : 3000);
}
const fail = (e) => toast(e.message || String(e), true);

// ---------- modal ----------
function modal(html, { wide = false, onSubmit } = {}) {
  const box = $("#modalBox");
  box.className = "modal-box" + (wide ? " wide" : "");
  box.innerHTML = html;
  $("#modal").hidden = false;
  const first = box.querySelector("input, textarea, select");
  if (first) first.focus();
  const form = box.querySelector("form");
  if (form && onSubmit) form.onsubmit = async (e) => {
    e.preventDefault();
    try { await onSubmit(Object.fromEntries(new FormData(form))); closeModal(); } catch (err) { fail(err); }
  };
  box.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeModal));
}
function closeModal() { $("#modal").hidden = true; $("#modalBox").innerHTML = ""; }
$("#modal").addEventListener("mousedown", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#modal").hidden) closeModal(); });
function confirmBox(title, text, label = "Delete") {
  return new Promise((resolve) => {
    modal(`<h2>${esc(title)}</h2><p style="color:var(--muted)">${esc(text)}</p>
      <div class="modal-actions"><button class="btn" data-close>Cancel</button><button class="btn primary" id="okBtn">${esc(label)}</button></div>`);
    $("#modalBox").querySelectorAll("[data-close]").forEach((b) => (b.onclick = () => { closeModal(); resolve(false); }));
    $("#okBtn").onclick = () => { closeModal(); resolve(true); };
  });
}

// ---------- markdown-lite (escaped first, so model output can never inject HTML) ----------
function md(text) {
  const code = [];
  let s = esc(text).replace(/```[\w-]*\n?([\s\S]*?)(```|$)/g, (_, c) => `\u0000${code.push(c) - 1}\u0000`);
  const inline = (t) => t
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[(\d{1,2})\]/g, '<button class="cite" data-n="$1">$1</button>');
  const out = []; let list = null; let para = [];
  const flushPara = () => { if (para.length) out.push(`<p>${inline(para.join("<br>"))}</p>`); para = []; };
  const flushList = () => { if (list) out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`); list = null; };
  for (const line of s.split("\n")) {
    const ul = line.match(/^\s*[-*•]\s+(.*)/), ol = line.match(/^\s*\d+[.)]\s+(.*)/), h = line.match(/^#{1,4}\s+(.*)/);
    if (ul || ol) {
      flushPara();
      const tag = ul ? "ul" : "ol";
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push((ul || ol)[1]);
    } else if (h) { flushPara(); flushList(); out.push(`<h4>${inline(h[1])}</h4>`); }
    else if (!line.trim()) { flushPara(); flushList(); }
    else { flushList(); para.push(line); }
  }
  flushPara(); flushList();
  return out.join("").replace(/\u0000(\d+)\u0000/g, (_, i) => `<pre><code>${code[i]}</code></pre>`);
}

// ---------- routing ----------
function route() {
  const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
  const parts = path.split("/");
  const params = new URLSearchParams(query || "");
  return { view: parts[0] || "chat", id: parts[1] ? Number(parts[1]) : null, params };
}
const go = (hash) => { if (location.hash === hash) render(); else location.hash = hash; };
window.addEventListener("hashchange", render);

async function render() {
  clearTimeout(state.pollTimer);
  const r = route();
  document.querySelectorAll(".nav button").forEach((b) => b.classList.toggle("on", b.dataset.view === r.view));
  renderChatList();
  try {
    if (r.view === "collections") await renderCollections();
    else if (r.view === "documents") await renderDocuments(r.params.get("c"));
    else if (r.view === "settings") await renderSettings();
    else await renderChat(r.id);
  } catch (e) {
    main.innerHTML = `<div class="page"><div class="empty"><h3>Something went wrong</h3>${esc(e.message)}</div></div>`;
  }
}
document.querySelectorAll(".nav button").forEach((b) => (b.onclick = () => go("#/" + b.dataset.view)));
$("#newChat").onclick = () => go("#/chat");

// ---------- sidebar ----------
async function loadChats() { state.chats = await api("/chats"); renderChatList(); }
function renderChatList() {
  const r = route();
  $("#chatList").innerHTML = state.chats.length ? state.chats.map((c) => `
    <div class="chat-item ${r.view === "chat" && r.id === c.id ? "on" : ""}" data-chat="${c.id}" title="${esc(c.collection || "")}">
      <span>${esc(c.title)}</span><button class="x" data-del-chat="${c.id}" title="Delete chat">✕</button></div>`).join("")
    : `<div class="chat-item" style="cursor:default">No chats yet</div>`;
}
$("#chatList").addEventListener("click", async (e) => {
  const del = e.target.closest("[data-del-chat]");
  if (del) {
    e.stopPropagation();
    if (!(await confirmBox("Delete chat?", "The conversation will be removed from this device."))) return;
    await api(`/chats/${del.dataset.delChat}`, { method: "DELETE" }).catch(fail);
    await loadChats();
    if (route().id === Number(del.dataset.delChat)) go("#/chat"); else renderChatList();
    return;
  }
  const item = e.target.closest("[data-chat]");
  if (item) go(`#/chat/${item.dataset.chat}`);
});

async function refreshStatus() {
  try { state.status = await api("/status"); } catch { state.status = { ollama: false, error: "Daftar server unreachable" }; }
  const s = state.status, pill = $("#statusPill");
  if (!s.ollama) pill.innerHTML = `<span class="dot err"></span>Ollama not running`;
  else if (!s.chat_model) pill.innerHTML = `<span class="dot warn"></span>No chat model installed`;
  else if (s.chat_model.replace(/:latest$/, "") !== s.settings.chat_model.replace(/:latest$/, ""))
    pill.innerHTML = `<span class="dot warn"></span><span>${esc(s.chat_model)} <span style="color:var(--faint)">(fallback)</span></span>`;
  else pill.innerHTML = `<span class="dot ok"></span>${esc(s.chat_model)}`;
  pill.title = s.ollama ? `Ollama ${s.ollama_version} · embeddings ${s.embed_ready ? "on" : "off (keyword search)"}` : (s.error || "");
  return s;
}

const statsRow = (items) => `<div class="stats">${items.map(([k, v]) => `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("")}</div>`;

// ---------- collections ----------
async function renderCollections() {
  const [cols, st] = await Promise.all([api("/collections"), api("/stats")]);
  main.innerHTML = `<div class="page">
    <div class="head"><div><h1>My Collections</h1><p>Private knowledge bases. Each chat searches one collection.</p></div>
      <div class="actions"><button class="btn primary" data-act="new-col">+ New collection</button></div></div>
    ${statsRow([["Collections", st.collections], ["Documents", st.documents], ["Chunks indexed", st.chunks.toLocaleString()], ["Storage used", fmtBytes(st.size + st.db_size)]])}
    ${cols.length ? `<div class="grid">${cols.map((c) => `
      <div class="card click" data-act="open-col" data-id="${c.id}">
        <div class="card-top"><div class="icon-box"><svg viewBox="0 0 24 24"><path d="M3 7h7l2 2h9v10H3z"/></svg></div>
          <div style="min-width:0"><h3>${esc(c.name)}</h3><div class="sub">Created ${ago(c.created)}</div></div></div>
        ${c.description ? `<div class="sub">${esc(c.description)}</div>` : ""}
        <div class="meta"><span><b>${c.documents}</b> docs</span><span><b>${c.chunks.toLocaleString()}</b> chunks</span><span><b>${fmtBytes(c.size)}</b></span></div>
        <div class="row">
          <button class="btn small primary" data-act="chat-col" data-id="${c.id}">Chat</button>
          <button class="btn small" data-act="edit-col" data-id="${c.id}">Edit</button>
          <button class="btn small danger" data-act="del-col" data-id="${c.id}">Delete</button></div>
      </div>`).join("")}</div>`
      : `<div class="empty"><h3>No collections yet</h3>Create one (for example “Contracts” or “Q3 reports”), then add files to it.<br><br>
         <button class="btn primary" data-act="new-col">+ New collection</button></div>`}
  </div>`;
  main.dataset.cols = JSON.stringify(cols);
}
function collectionForm(c = {}) {
  return `<form><h2>${c.id ? "Edit collection" : "New collection"}</h2>
    <div class="field"><label>Name</label><input type="text" name="name" required maxlength="80" value="${esc(c.name || "")}"></div>
    <div class="field"><label>Description <span class="hint">(optional)</span></label><textarea name="description" rows="2">${esc(c.description || "")}</textarea></div>
    <div class="modal-actions"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">${c.id ? "Save" : "Create"}</button></div></form>`;
}
function newCollection(after) {
  modal(collectionForm(), { onSubmit: async (v) => { const r = await api("/collections", { method: "POST", body: v }); toast("Collection created"); after ? after(r.id) : render(); } });
}

// ---------- documents ----------
async function renderDocuments(cid) {
  const cols = await api("/collections");
  if (!cols.length) {
    main.innerHTML = `<div class="page"><div class="head"><div><h1>My Documents</h1></div></div>
      <div class="empty"><h3>Create a collection first</h3>Documents live inside collections.<br><br><button class="btn primary" data-act="new-col">+ New collection</button></div></div>`;
    return;
  }
  if (!cid && cols.length === 1) cid = String(cols[0].id);
  const docs = await api("/documents" + (cid ? `?collection_id=${cid}` : ""));
  const busy = docs.some((d) => d.status === "queued" || d.status === "processing");
  const sum = (k) => docs.reduce((a, d) => a + d[k], 0);
  main.innerHTML = `<div class="page">
    <div class="head"><div><h1>My Documents</h1><p>PDF, Word, text, Markdown and CSV, indexed on this device.</p></div>
      <div class="actions">
        <select id="colFilter"><option value="">All collections</option>${cols.map((c) => `<option value="${c.id}" ${String(c.id) === cid ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
        <button class="btn primary" data-act="upload">Add files</button></div></div>
    ${statsRow([["Documents", docs.length], ["Pages", sum("pages").toLocaleString()], ["Chunks", sum("chunks").toLocaleString()], ["Total size", fmtBytes(sum("size"))]])}
    <div class="drop" id="drop" data-act="upload">Drop files here or click to add them${cid ? ` to <b>${esc(cols.find((c) => String(c.id) === cid)?.name)}</b>` : ""}.
      <div style="font-size:12px;color:var(--faint);margin-top:4px">Files are copied into Daftar's local data folder. Nothing is uploaded anywhere.</div></div>
    ${docs.length ? `<div class="grid">${docs.map(docCard).join("")}</div>` : `<div class="empty"><h3>No documents here yet</h3>Add files to start asking questions about them.</div>`}
  </div>`;
  $("#colFilter").onchange = (e) => go("#/documents" + (e.target.value ? `?c=${e.target.value}` : ""));
  const drop = $("#drop");
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); uploadFiles([...e.dataTransfer.files], cid); };
  main.dataset.cid = cid || "";
  if (busy) state.pollTimer = setTimeout(() => { if (route().view === "documents") renderDocuments(cid); }, 1500);
}
function docCard(d) {
  const st = d.status === "ready"
    ? `<span class="badge ok" title="${d.embed_model ? "Semantic + keyword search" : "Keyword search only (no embedding model was available)"}">Indexed · ${d.embed_model ? "hybrid" : "keyword"}</span>`
    : d.status === "error" ? `<span class="badge err">Failed</span>`
    : `<span class="badge warn">${d.status === "queued" ? "Queued" : "Indexing…"}</span>`;
  return `<div class="card">
    <div class="card-top"><div class="icon-box">${esc(d.ext.slice(1).toUpperCase())}</div>
      <div style="min-width:0"><h3 title="${esc(d.name)}">${esc(d.name)}</h3><div class="sub">${esc(d.collection)} · ${ago(d.created)}</div></div></div>
    <div class="meta"><span><b>${fmtBytes(d.size)}</b></span>${d.ext === ".pdf" ? `<span><b>${d.pages}</b> pages</span>` : ""}<span><b>${d.chunks}</b> chunks</span>${st}</div>
    ${d.error ? `<div class="sub err-text">${esc(d.error)}</div>` : ""}
    <div class="row">
      <a class="btn small" href="/api/documents/${d.id}/file" target="_blank" rel="noopener">Open</a>
      <button class="btn small" data-act="passages" data-id="${d.id}" data-name="${esc(d.name)}">Passages</button>
      <button class="btn small" data-act="reindex" data-id="${d.id}">Re-index</button>
      <button class="btn small danger" data-act="del-doc" data-id="${d.id}" data-name="${esc(d.name)}">Delete</button></div>
  </div>`;
}
async function pickCollection() {
  const cols = await api("/collections");
  if (cols.length === 1) return String(cols[0].id);
  return new Promise((resolve) => {
    modal(`<form><h2>Add files to…</h2><div class="field"><select name="c">${cols.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select></div>
      <div class="modal-actions"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">Choose files</button></div></form>`,
      { onSubmit: async (v) => resolve(v.c) });
  });
}
let pendingUploadCid = null;
async function uploadFiles(files, cid) {
  if (!files.length) return;
  cid = cid || (await pickCollection());
  if (!cid) return;
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  toast(`Adding ${files.length} file${files.length > 1 ? "s" : ""}…`);
  try {
    const res = await api(`/collections/${cid}/documents`, { method: "POST", body: form });
    const skipped = res.filter((r) => r.skipped);
    toast(skipped.length ? `Skipped: ${skipped.map((s) => `${s.name} (${s.skipped})`).join(", ")}` : "Indexing started", skipped.length > 0);
  } catch (e) { fail(e); }
  go(`#/documents?c=${cid}`);
}
$("#filePick").onchange = (e) => { const files = [...e.target.files]; e.target.value = ""; uploadFiles(files, pendingUploadCid); };

// ---------- chat ----------
async function renderChat(id) {
  const [cols, status] = await Promise.all([api("/collections"), state.status ? Promise.resolve(state.status) : refreshStatus()]);
  const chat = id ? await api(`/chats/${id}`) : null;
  const colOptions = (sel) => cols.map((c) => `<option value="${c.id}" ${c.id === sel ? "selected" : ""}>${esc(c.name)} (${c.documents})</option>`).join("");
  const last = Number(localStorage.getItem("daftar.lastCollection")) || (cols[0] && cols[0].id);
  main.innerHTML = `<div class="chat">
    <div class="chat-head"><h2>${esc(chat ? chat.title : "New chat")}</h2>
      ${cols.length ? `<label class="sub" style="color:var(--muted)">Collection</label><select id="chatCol">${chat && !chat.collection_id ? `<option value="">— pick one —</option>` : ""}${colOptions(chat ? chat.collection_id : last)}</select>` : ""}
      <span class="badge" title="Model answering in this chat">${esc(status.chat_model || "no model")}</span></div>
    <div class="msgs" id="msgs">${chat && chat.messages.length ? chat.messages.map(msgHTML).join("") : heroHTML(cols)}</div>
    <div class="composer"><form id="askForm"><textarea id="q" rows="1" placeholder="${cols.length ? "Ask anything about your documents…" : "Create a collection first"}" ${cols.length ? "" : "disabled"}></textarea>
      <button class="btn primary" id="sendBtn" ${cols.length ? "" : "disabled"}>Send</button></form>
      <div class="note">Answers are generated on this device from your documents. Check citations for anything important.</div></div>
  </div>`;
  const msgs = $("#msgs"); msgs.scrollTop = msgs.scrollHeight;
  const q = $("#q");
  q.oninput = () => { q.style.height = "auto"; q.style.height = Math.min(q.scrollHeight, 200) + "px"; };
  q.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#askForm").requestSubmit(); } };
  q.focus();
  const sel = $("#chatCol");
  if (sel) sel.onchange = async () => {
    localStorage.setItem("daftar.lastCollection", sel.value);
    if (chat) await api(`/chats/${chat.id}`, { method: "PATCH", body: { title: chat.title, collection_id: Number(sel.value) || null } }).catch(fail);
  };
  $("#askForm").onsubmit = async (e) => {
    e.preventDefault();
    if (state.streaming) { state.streaming.abort(); return; }
    const question = q.value.trim();
    if (!question) return;
    let chatId = chat && chat.id;
    if (!chatId) {
      const created = await api("/chats", { method: "POST", body: { collection_id: Number(sel.value) } }).catch(fail);
      if (!created) return;
      chatId = created.id;
      history.replaceState(null, "", `#/chat/${chatId}`);
    }
    q.value = ""; q.oninput();
    await ask(chatId, question);
    if (!chat) { await loadChats(); render(); } else loadChats();
  };
}
function heroHTML(cols) {
  if (!cols.length) return `<div class="hero"><img src="logo.svg" alt=""><h2>Welcome to Daftar</h2>
    <p>Create a collection, add your contracts, reports or notes, then ask questions. Everything stays on this computer.</p>
    <button class="btn primary" data-act="new-col">+ New collection</button></div>`;
  const ideas = ["Summarize the key points", "What deadlines or dates are mentioned?", "List the parties and amounts involved"];
  return `<div class="hero"><img src="logo.svg" alt=""><h2>Ask your documents</h2>
    <p>Answers cite the exact passages they come from. Nothing leaves this device.</p>
    <div class="suggest">${ideas.map((i) => `<button class="btn small" data-act="suggest">${esc(i)}</button>`).join("")}</div></div>`;
}
function sourcesHTML(sources) {
  if (!sources || !sources.length) return "";
  return `<div class="sources">${sources.map((s, i) => `<details class="src" data-n="${i + 1}"><summary><span class="n">[${i + 1}]</span>
    <span>${esc(s.document)}${s.ext === ".pdf" ? ` · page ${s.page}` : ""}</span></summary><div class="snip">${esc(s.text)}</div></details>`).join("")}</div>`;
}
function msgHTML(m) {
  if (m.role === "user") return `<div class="msg user"><div class="bubble">${esc(m.content)}</div></div>`;
  return `<div class="msg assistant"><div class="bubble">${md(m.content)}</div>
    ${m.sources && m.sources.length ? `<div class="msg-foot">${m.sources.length} source${m.sources.length > 1 ? "s" : ""}${m.model ? ` · ${esc(m.model)}` : ""}${m.mode ? ` · ${m.mode} search` : ""}</div>` : ""}
    ${sourcesHTML(m.sources)}</div>`;
}
async function ask(chatId, question) {
  const msgs = $("#msgs");
  if (msgs.querySelector(".hero")) msgs.innerHTML = "";
  msgs.insertAdjacentHTML("beforeend", msgHTML({ role: "user", content: question }));
  msgs.insertAdjacentHTML("beforeend", `<div class="msg assistant"><div class="bubble cursor"></div></div>`);
  const el = msgs.lastElementChild, bubble = el.querySelector(".bubble");
  msgs.scrollTop = msgs.scrollHeight;
  const ctrl = new AbortController(); state.streaming = ctrl;
  $("#sendBtn").textContent = "Stop";
  const m = { role: "assistant", content: "", sources: [] };
  let frame = 0;
  const paint = () => { frame = 0; bubble.innerHTML = md(m.content); const near = msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 120; if (near) msgs.scrollTop = msgs.scrollHeight; };
  try {
    const r = await fetch(`/api/chats/${chatId}/ask`, { method: "POST", headers: { "X-Daftar": "1", "Content-Type": "application/json" }, body: JSON.stringify({ question }), signal: ctrl.signal });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl); buf = buf.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.type === "meta") Object.assign(m, { sources: ev.sources, model: ev.model, mode: ev.mode });
        else if (ev.type === "token") { m.content += ev.text; if (!frame) frame = requestAnimationFrame(paint); }
        else if (ev.type === "error") m.error = ev.text;
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") m.error = e.message;
    else m.content += m.content ? "\n\n*(stopped)*" : "*(stopped)*";
  }
  state.streaming = null;
  if (frame) cancelAnimationFrame(frame);
  el.outerHTML = msgHTML(m);
  if (m.error) msgs.lastElementChild.querySelector(".bubble").insertAdjacentHTML("beforeend", `<p class="err-text">⚠ ${esc(m.error)}</p>`);
  const btn = $("#sendBtn"); if (btn) btn.textContent = "Send";
  msgs.scrollTop = msgs.scrollHeight;
}

// ---------- settings ----------
async function renderSettings() {
  const s = await refreshStatus();
  const cfg = s.settings || {};
  const chatModels = [...new Set([cfg.chat_model, ...s.models.filter((m) => !m.includes("embed"))])];
  const embedModels = [...new Set([cfg.embed_model, ...s.models.filter((m) => m.includes("embed"))])];
  const opt = (list, cur) => list.map((m) => `<option value="${esc(m)}" ${m === cur ? "selected" : ""}>${esc(m)}${s.models.includes(m) || s.models.includes(m + ":latest") ? "" : " (not installed)"}</option>`).join("");
  const chatMissing = s.ollama && s.chat_model && s.chat_model.replace(/:latest$/, "") !== cfg.chat_model.replace(/:latest$/, "");
  main.innerHTML = `<div class="page">
    <div class="head"><div><h1>Settings</h1><p>Daftar ${esc(s.version || "")} · all processing happens on this computer.</p></div></div>
    <div class="cols">
      <div>
        <div class="panel"><h2>Local AI engine</h2>
          <div class="pill" style="margin-bottom:14px">${s.ollama ? `<span class="dot ok"></span>Ollama ${esc(s.ollama_version)} is running` : `<span class="dot err"></span>Ollama is not reachable: ${esc(s.error || "")}`}</div>
          <form id="setForm">
            <div class="field"><label>Ollama address</label><input type="text" name="ollama_url" value="${esc(cfg.ollama_url)}"><span class="hint">Must be this computer (127.0.0.1 or localhost).</span></div>
            <div class="field"><label>Chat model</label><select name="chat_model">${opt(chatModels, cfg.chat_model)}</select><span class="hint">Default: gemma3:1b. Any installed Ollama model works; larger ones answer better.</span></div>
            <div class="field"><label>Embedding model</label><select name="embed_model">${opt(embedModels, cfg.embed_model)}</select><span class="hint">Used for semantic search. Without it Daftar falls back to keyword search.</span></div>
            <div class="field"><label>Passages per answer</label><input type="number" name="top_k" min="1" max="20" value="${cfg.top_k}"></div>
            <button class="btn primary">Save settings</button>
          </form></div>
      </div>
      <div>
        ${chatMissing || (s.ollama && !s.embed_ready) ? `<div class="panel"><h2>Finish model setup</h2>
          ${chatMissing ? `<p class="sub">“${esc(cfg.chat_model)}” is not installed, so Daftar is answering with <b>${esc(s.chat_model)}</b>.</p>` : ""}
          ${!s.embed_ready ? `<p class="sub">“${esc(cfg.embed_model)}” is not installed, so search is keyword-only.</p>` : ""}
          <p class="sub">Install with Ollama, or import a GGUF you already downloaded:</p>
          <pre class="cmd">ollama pull ${esc(chatMissing ? cfg.chat_model : cfg.embed_model)}

# or, from a local .gguf file:
printf 'FROM ./model.gguf\\n' &gt; Modelfile
ollama create ${esc(chatMissing ? cfg.chat_model : cfg.embed_model)} -f Modelfile</pre></div>` : ""}
        ${s.stale_documents ? `<div class="panel"><h2>Search index</h2><p class="sub">${s.stale_documents} document(s) were indexed without “${esc(cfg.embed_model)}”.${s.embed_ready ? " Re-index them to turn on semantic search." : " Install the embedding model first, then re-index."}</p>
          <button class="btn" data-act="reindex-all" ${s.embed_ready ? "" : "disabled"}>Re-index ${s.stale_documents} document(s)</button></div>` : ""}
        <div class="panel"><h2>Privacy</h2><ul>
          <li><b>100% local</b>: documents, questions and answers never leave this computer.</li>
          <li><b>Zero telemetry</b>: no accounts, no analytics, no update checks.</li>
          <li><b>Works offline</b>: after models are installed, no internet is needed.</li>
          <li>The app only listens on 127.0.0.1 and only talks to your local Ollama.</li></ul>
          <div class="field" style="margin:14px 0 0"><label>Data folder</label><code style="word-break:break-all">${esc(s.data_dir || "")}</code></div></div>
      </div>
    </div></div>`;
  $("#setForm").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("/settings", { method: "PUT", body: Object.fromEntries(new FormData(e.target)) }); toast("Settings saved"); render(); } catch (err) { fail(err); }
  };
}

// ---------- shared click actions ----------
main.addEventListener("click", async (e) => {
  const a = e.target.closest("[data-act], .cite");
  if (!a) return;
  if (a.classList.contains("cite")) {
    const src = a.closest(".msg")?.querySelector(`.src[data-n="${a.dataset.n}"]`);
    if (src) { src.open = true; src.classList.add("flash"); src.scrollIntoView({ block: "nearest", behavior: "smooth" }); setTimeout(() => src.classList.remove("flash"), 1500); }
    return;
  }
  e.stopPropagation();
  const id = a.dataset.id;
  try {
    switch (a.dataset.act) {
      case "new-col": newCollection(); break;
      case "open-col": go(`#/documents?c=${id}`); break;
      case "chat-col": {
        localStorage.setItem("daftar.lastCollection", id);
        go("#/chat"); break;
      }
      case "edit-col": {
        const c = JSON.parse(main.dataset.cols).find((x) => String(x.id) === id);
        modal(collectionForm(c), { onSubmit: async (v) => { await api(`/collections/${id}`, { method: "PATCH", body: v }); render(); } });
        break;
      }
      case "del-col":
        if (await confirmBox("Delete collection?", "All its documents and their index will be removed from this device. Chats stay but lose their collection.")) {
          await api(`/collections/${id}`, { method: "DELETE" }); toast("Collection deleted"); render();
        }
        break;
      case "upload": pendingUploadCid = main.dataset.cid || (await pickCollection()); if (pendingUploadCid) $("#filePick").click(); break;
      case "del-doc":
        if (await confirmBox(`Delete “${a.dataset.name}”?`, "The file copy and its index will be removed from this device.")) {
          await api(`/documents/${id}`, { method: "DELETE" }); render();
        }
        break;
      case "reindex": await api(`/documents/${id}/reindex`, { method: "POST" }); render(); break;
      case "reindex-all": { const r = await api("/reindex", { method: "POST" }); toast(`Re-indexing ${r.queued} document(s)`); go("#/documents"); break; }
      case "passages": {
        const chunks = await api(`/documents/${id}/chunks`);
        modal(`<h2>${esc(a.dataset.name)}</h2><p class="sub">${chunks.length} indexed passages</p>
          <div class="sources">${chunks.map((c) => `<details class="src"><summary><span class="n">#${c.ord + 1}</span><span>page ${c.page} · ${esc(c.text.slice(0, 80))}…</span></summary><div class="snip">${esc(c.text)}</div></details>`).join("")}</div>
          <div class="modal-actions"><button class="btn" data-close>Close</button></div>`, { wide: true });
        break;
      }
      case "suggest": { const q = $("#q"); q.value = a.textContent; $("#askForm").requestSubmit(); break; }
    }
  } catch (err) { fail(err); }
});

// ---------- boot ----------
(async () => {
  await Promise.all([refreshStatus(), loadChats().catch(() => {})]);
  render();
  setInterval(refreshStatus, 15000);
})();
