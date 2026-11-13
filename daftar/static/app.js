"use strict";
const $ = (s, el = document) => el.querySelector(s);
const main = $("#main");
const state = { status: null, chats: [], streaming: null, pollTimer: null, uploadFiles: [], chatQuery: "" };

// ---------- helpers ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"]; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}
function ago(t) {
  const s = Date.now() / 1000 - t;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  const d = new Date(t * 1000), today = new Date();
  if (d.toDateString() === today.toDateString()) return "Today";
  if (new Date(today - 864e5).toDateString() === d.toDateString()) return "Yesterday";
  return d.toLocaleDateString();
}
const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
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
async function readStream(r, onEvent) {
  const reader = r.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl); buf = buf.slice(nl + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
}
let toastTimer;
function toast(text, isErr = false) {
  const t = $("#toast");
  t.textContent = text; t.className = "toast" + (isErr ? " err" : ""); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), isErr ? 6000 : 2800);
}
const fail = (e) => toast(e.message || String(e), true);
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const ta = document.createElement("textarea"); ta.value = text; document.body.append(ta); ta.select();
    document.execCommand("copy"); ta.remove();
  }
  toast("Copied");
}
const ICON = {
  copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h9"/></svg>',
  edit: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M5 7h14M10 11v6M14 11v6M7 7l1 13h8l1-13M9 7V4h6v3"/></svg>',
  redo: '<svg viewBox="0 0 24 24"><path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v5h-5"/></svg>',
  file: '<svg viewBox="0 0 24 24"><path d="M6 3h8l4 4v14H6zM14 3v4h4"/></svg>',
  pieces: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="7" height="7" rx="1"/><rect x="13" y="4" width="7" height="7" rx="1"/><rect x="4" y="13" width="7" height="7" rx="1"/><rect x="13" y="13" width="7" height="7" rx="1"/></svg>',
  db: '<svg viewBox="0 0 24 24"><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/></svg>',
  clock: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg>',
  folder: '<svg viewBox="0 0 24 24"><path d="M3 7h7l2 2h9v10H3z"/></svg>',
  user: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>',
  upload: '<svg viewBox="0 0 24 24"><path d="M12 16V4M7 9l5-5 5 5M4 20h16"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
};

// ---------- modal ----------
function modal(html, { wide = false, onSubmit } = {}) {
  const box = $("#modalBox");
  box.className = "modal-box" + (wide ? " wide" : "");
  box.innerHTML = html;
  $("#modal").hidden = false;
  const first = box.querySelector("input:not([type=checkbox]), textarea, select");
  if (first) first.focus();
  const form = box.querySelector("form");
  if (form && onSubmit) form.onsubmit = async (e) => {
    e.preventDefault();
    const btn = form.querySelector("button[type=submit], button:not([type])"); if (btn) btn.disabled = true;
    try { await onSubmit(new FormData(form)); closeModal(); } catch (err) { fail(err); if (btn) btn.disabled = false; }
  };
}
function closeModal() { $("#modal").hidden = true; $("#modalBox").innerHTML = ""; }
$("#modal").addEventListener("mousedown", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#modal").hidden) closeModal(); });
function confirmBox(title, text, label = "Remove") {
  return new Promise((resolve) => {
    modal(`<h2>${esc(title)}</h2><p class="sub" style="font-size:14px">${esc(text)}</p>
      <div class="modal-actions"><button class="btn" id="noBtn">Cancel</button><button class="btn primary" id="okBtn">${esc(label)}</button></div>`);
    $("#noBtn").onclick = () => { closeModal(); resolve(false); };
    $("#okBtn").onclick = () => { closeModal(); resolve(true); };
  });
}
function askText(title, value = "", label = "Save") {
  return new Promise((resolve) => {
    modal(`<form><h2>${esc(title)}</h2><div class="field" style="margin-top:12px"><input type="text" name="v" value="${esc(value)}" maxlength="120" required></div>
      <div class="modal-actions"><button type="button" class="btn" data-act="close">Cancel</button><button type="submit" class="btn primary">${esc(label)}</button></div></form>`,
      { onSubmit: async (f) => resolve(f.get("v").trim()) });
  });
}

// ---------- markdown-lite (escaped first, so model output can never inject HTML) ----------
function md(text, nSources = Infinity) {
  const code = [];
  const s = esc(text).replace(/```[\w-]*\n?([\s\S]*?)(```|$)/g, (_, c) => `\u0000${code.push(c) - 1}\u0000`);
  const inline = (t) => t
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[(\d{1,2})\]/g, (m, n) => (Number(n) >= 1 && Number(n) <= nSources
      ? `<button class="cite" data-n="${n}" title="Show source ${n}">${n}</button>` : (nSources === Infinity ? m : "")));
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
const HL_STOP = new Set("the and are was were what when where which who why how for with this that from does did has have had you your our their about into than then there these those much many need needed some any all also just can will".split(" "));
function highlight(escapedText, query) {
  const words = [...new Set((query || "").toLowerCase().match(/[\p{L}\p{N}]{3,}/gu) || [])].filter((w) => !HL_STOP.has(w));
  if (!words.length) return escapedText;
  // Same rule as the search engine: words of 6+ letters match on their first 6 (terminate ~ termination).
  const pattern = words.map((w) => (w.length >= 6 ? w.slice(0, 6) + "[\\p{L}\\p{N}]*" : w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  const re = new RegExp(`(?<![&#\\p{L}\\p{N}])(${pattern.join("|")})(?![\\p{L}\\p{N}])`, "giu");
  return escapedText.replace(re, "<mark>$1</mark>");
}
const where = (s) => [s.collection, s.ext === ".pdf" ? `page ${s.page}` : "", s.section ? s.section.split(" > ").pop() : ""]
  .filter(Boolean).map(esc).join(" · ");
const matchLabel = (s) => `<span class="match" title="${s.match_kind === "words" ? "Share of the question's words found in this passage (install a search model to compare meaning)" : "How closely this passage matches the question's meaning and words"}">${meter(s.match)}${s.match}% ${s.match_kind === "words" ? "word match" : "match"}</span>`;
const meter = (pct) => `<div class="meter"><i style="width:${Math.max(0, Math.min(100, pct || 0))}%"></i></div>`;

// ---------- routing ----------
function route() {
  const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
  const parts = path.split("/");
  return { view: parts[0] || "chat", id: parts[1] ? Number(parts[1]) : null, params: new URLSearchParams(query || "") };
}
const go = (hash) => { if (location.hash === hash) render(); else location.hash = hash; };
window.addEventListener("hashchange", () => { closeModal(); render(); });

async function render() {
  clearTimeout(state.pollTimer);
  const r = route();
  document.querySelectorAll(".nav button").forEach((b) => b.classList.toggle("on", b.dataset.view === r.view));
  renderChatList();
  try {
    const views = { collections: renderCollections, documents: renderDocuments, search: renderSearch, settings: renderSettings, help: renderHelp };
    await (views[r.view] || renderChat)(r);
  } catch (e) {
    main.innerHTML = `<div class="page"><div class="empty"><h3>Something went wrong</h3>${esc(e.message)}</div></div>`;
  }
}
document.querySelectorAll(".nav button").forEach((b) => (b.onclick = () => go("#/" + b.dataset.view)));
$("#newChat").onclick = () => go("#/chat");

// ---------- sidebar ----------
const app = $("#app");
try { if (localStorage.getItem("daftar.collapsed") === "1") app.classList.add("collapsed"); } catch { /* storage blocked */ }
$("#collapse").onclick = () => {
  app.classList.toggle("collapsed");
  try { localStorage.setItem("daftar.collapsed", app.classList.contains("collapsed") ? "1" : "0"); } catch { /* ignore */ }
};
let searchTimer;
$("#chatSearch").oninput = (e) => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.chatQuery = e.target.value.trim(); loadChats(); }, 200); };
async function loadChats() {
  state.chats = await api("/chats" + (state.chatQuery ? `?q=${encodeURIComponent(state.chatQuery)}` : ""));
  renderChatList();
}
function renderChatList() {
  const r = route();
  $("#chatList").innerHTML = state.chats.length ? state.chats.map((c) => `
    <div class="chat-item ${r.view === "chat" && r.id === c.id ? "on" : ""}" data-chat="${c.id}" title="${esc(c.all_collections ? "All collections" : c.collection || "No collection")}">
      <svg viewBox="0 0 24 24"><path d="M4 5h16v11H8l-4 4z"/></svg>
      <div class="t"><div>${esc(c.title)}</div><small>${ago(c.updated)}</small></div>
      <button class="icon-btn x" data-del-chat="${c.id}" title="Delete chat">${ICON.x}</button></div>`).join("")
    : `<div class="chat-item" style="cursor:default">${state.chatQuery ? "No matching chats" : "No chats yet"}</div>`;
}
$("#chatList").addEventListener("click", async (e) => {
  const del = e.target.closest("[data-del-chat]");
  if (del) {
    e.stopPropagation();
    if (!(await confirmBox("Delete this chat?", "The conversation will be removed from this computer.", "Delete"))) return;
    await api(`/chats/${del.dataset.delChat}`, { method: "DELETE" }).catch(fail);
    await loadChats();
    if (route().id === Number(del.dataset.delChat)) go("#/chat");
    return;
  }
  const item = e.target.closest("[data-chat]");
  if (item) go(`#/chat/${item.dataset.chat}`);
});

function applyTheme(theme) { document.documentElement.dataset.theme = theme === "light" ? "light" : "dark"; }
async function refreshStatus() {
  try { state.status = await api("/status"); } catch { state.status = { ollama: false, models: [], settings: {}, error: "Daftar's local server is not responding" }; }
  const s = state.status, pill = $("#statusPill");
  if (s.settings.theme) applyTheme(s.settings.theme);
  const strip = (m) => (m || "").replace(/:latest$/, "");
  if (!s.ollama) pill.innerHTML = `<span class="dot err"></span><span>Ollama not running</span>`;
  else if (!s.chat_model) pill.innerHTML = `<span class="dot warn"></span><span>No chat model</span>`;
  else if (strip(s.chat_model) !== strip(s.settings.chat_model)) pill.innerHTML = `<span class="dot warn"></span><span>${esc(s.chat_model)} (fallback)</span>`;
  else pill.innerHTML = `<span class="dot ok"></span><span>${esc(s.chat_model)}</span>`;
  if (s.ollama && s.compute === "cpu") pill.innerHTML = pill.innerHTML.replace('dot ok', 'dot warn').replace("</span>" + esc(s.chat_model), "</span>" + esc(s.chat_model) + " · CPU only");
  pill.title = s.ollama ? `Ollama ${s.ollama_version} · ${s.embed_ready ? "semantic + keyword search" : "keyword search only"}` : (s.error || "");
  return s;
}
const band = (items) => `<div class="band">${items.map(([k, v]) => `<div><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("")}</div>`;

// ---------- collections ----------
async function renderCollections() {
  const [cols, st] = await Promise.all([api("/collections"), api("/stats")]);
  main.innerHTML = `<div class="page">
    <div class="head"><div><h1>My Collections</h1><p>Separate knowledge bases. A question only searches its own collection.</p></div>
      <div class="actions"><button class="btn primary" data-act="new-col">${ICON.folder}Create collection</button></div></div>
    ${band([["Total collections", st.collections], ["Total documents", st.documents], ["Content pieces", st.chunks.toLocaleString()], ["Storage used", fmtBytes(st.size + st.db_size)]])}
    ${cols.length ? `<div class="grid">${cols.map(collectionCard).join("")}</div>`
      : `<div class="empty"><h3>No collections yet</h3>Create one per project, client or topic, for example “Contracts” or “Research”.<br><br>
         <button class="btn primary" data-act="new-col">Create collection</button></div>`}
  </div>`;
  state.cols = cols;
  if (cols.some((c) => c.indexing)) state.pollTimer = setTimeout(() => route().view === "collections" && renderCollections(), 2000);
}
function collectionCard(c) {
  const ready = c.readiness;
  const note = c.indexing ? `<span class="badge warn">Indexing ${c.indexing}…</span>`
    : c.failed ? `<span class="badge err">${plural(c.failed, "file")} failed</span>`
    : c.stale ? `<span class="badge warn">${c.stale} need re-index</span>`
    : c.total ? `<span class="badge ok">Ready</span>` : `<span class="badge">Empty</span>`;
  return `<div class="card">
    <div class="card-top"><div class="icon-box">${ICON.folder}</div>
      <div style="min-width:0;flex:1"><h3 title="${esc(c.name)}">${esc(c.name)}</h3><div class="sub">${esc(c.description || "Created " + ago(c.created))}</div></div>${note}</div>
    <div class="facts"><span>${ICON.file}${plural(c.documents, "document")}</span><span>${ICON.pieces}${plural(c.chunks, "content piece")}</span><span>${ICON.db}${fmtBytes(c.size)}</span></div>
    <div><div class="row" style="justify-content:space-between;font-size:12px;color:var(--muted);margin-bottom:4px"><span>Ready for search</span><b>${ready == null ? "—" : ready + "%"}</b></div>${meter(ready)}</div>
    <div class="row bottom">
      <button class="btn small primary" data-act="chat-col" data-id="${c.id}">Chat</button>
      <button class="btn small" data-act="col-status" data-id="${c.id}">Check status</button>
      <button class="btn small ghost" data-act="edit-col" data-id="${c.id}">Edit</button>
      <button class="btn small ghost danger" data-act="del-col" data-id="${c.id}" data-name="${esc(c.name)}">Remove</button></div>
  </div>`;
}
function collectionForm(c = {}) {
  return `<form><h2>${c.id ? "Edit collection" : "Create collection"}</h2><div style="height:12px"></div>
    <div class="field"><label>Name</label><input type="text" name="name" required maxlength="80" value="${esc(c.name || "")}" placeholder="e.g. Contracts"></div>
    <div class="field"><label>Description <span class="hint">(optional)</span></label><textarea name="description" rows="2">${esc(c.description || "")}</textarea></div>
    <div class="modal-actions"><button type="button" class="btn" data-act="close">Cancel</button><button type="submit" class="btn primary">${c.id ? "Save" : "Create"}</button></div></form>`;
}
async function collectionStatus(id) {
  const s = await api(`/collections/${id}/status`);
  const search = (e) => e.embed_model ? "semantic + keyword" : "keyword only";
  modal(`<div class="modal-head"><div><h2>${esc(s.name)}</h2><div class="sub">Search index status</div></div><button class="icon-btn" data-act="close">${ICON.x}</button></div>
    <div class="row" style="align-items:baseline;gap:10px"><span style="font-size:34px;font-weight:800">${s.readiness == null ? "—" : s.readiness + "%"}</span><span class="sub">ready for search</span></div>
    ${s.embed_ready ? "" : `<p class="sub">Semantic search is off because the search model “${esc(s.embed_model)}” is not installed, so only keyword matching is used. Install it in Settings, then optimize.</p>`}
    ${meter(s.readiness)}
    <div class="row" style="margin:12px 0;gap:14px;font-size:13px">
      <span><b>${s.ready}</b> ready</span><span><b>${s.indexing}</b> indexing</span><span><b>${s.stale}</b> need re-index</span><span><b>${s.failed}</b> failed</span>
      <span><b>${s.vectors.toLocaleString()}</b> / ${s.chunks.toLocaleString()} pieces with vectors</span></div>
    <p class="hint">Embedding model: ${esc(s.embed_model)} · Last optimized: ${s.optimized ? ago(s.optimized) : "never"}</p>
    ${s.entries.length ? `<div class="tbl"><table class="t"><tr><th>Document</th><th>Pieces</th><th>Search</th><th>Status</th></tr>
      ${s.entries.map((e) => `<tr><td>${esc(e.document)}</td><td>${e.chunks}</td><td>${search(e)}</td>
        <td>${e.status === "error" ? `<span class="badge err" title="${esc(e.error)}">failed</span>` : e.stale ? '<span class="badge warn">needs re-index</span>' : `<span class="badge ${e.status === "ready" ? "ok" : "warn"}">${e.status}</span>`}</td></tr>`).join("")}</table></div>` : '<p class="sub">No documents yet.</p>'}
    <div class="modal-actions"><button class="btn" data-act="close">Close</button>
      <button class="btn primary" data-act="optimize" data-id="${id}" title="Re-index stale or failed documents, compact the keyword index and preload vectors">Optimize search</button></div>`, { wide: true });
}

// ---------- documents ----------
const TYPES = [[".pdf", "PDF"], [".docx", "Word"], [".txt", "Text"], [".md", "Markdown"], [".csv", "CSV"], [".html", "HTML"],
  [".py", "Python"], [".js", "JavaScript"], [".ts", "TypeScript"], [".json", "JSON"]];
async function renderDocuments(r) {
  const cid = r.params.get("c") || "", type = r.params.get("t") || "";
  const qs = new URLSearchParams(); if (cid) qs.set("collection_id", cid); if (type) qs.set("type", type);
  const [cols, docs] = await Promise.all([api("/collections"), api("/documents?" + qs)]);
  state.cols = cols;
  if (!cols.length) {
    main.innerHTML = `<div class="page"><div class="head"><div><h1>My Documents</h1><p>Upload and manage your files</p></div></div>
      <div class="empty"><h3>Create a collection first</h3>Every document lives in at least one collection.<br><br><button class="btn primary" data-act="new-col">Create collection</button></div></div>`;
    return;
  }
  const inScope = (d) => d.entries.filter((e) => !cid || String(e.collection_id) === cid);
  const pieces = docs.reduce((a, d) => a + inScope(d).reduce((b, e) => b + e.chunks, 0), 0);
  const busy = docs.some((d) => d.entries.some((e) => e.status === "queued" || e.status === "processing"));
  main.innerHTML = `<div class="page">
    <div class="head"><div><h1>My Documents</h1><p>Upload and manage your files. Everything stays in Daftar's local data folder.</p></div>
      <div class="actions"><button class="btn primary" data-act="upload">${ICON.upload}Upload documents</button></div></div>
    ${band([["Total documents", docs.length], ["Content pieces", pieces.toLocaleString()], ["Storage used", fmtBytes(docs.reduce((a, d) => a + d.size, 0))]])}
    <div class="filters">${ICON.folder}<select id="fCol"><option value="">All collections</option>${cols.map((c) => `<option value="${c.id}" ${String(c.id) === cid ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
      ${ICON.file}<select id="fType"><option value="">All file types</option>${TYPES.map(([v, l]) => `<option value="${v}" ${v === type ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    <div class="drop" id="drop" data-act="upload">Drop PDF, Word, Markdown, text, CSV, HTML or code files here, or click to choose.</div>
    ${docs.length ? `<div class="grid">${docs.map((d) => docCard(d, cid)).join("")}</div>` : `<div class="empty"><h3>No documents${cid || type ? " match these filters" : " yet"}</h3>Upload files to start asking questions about them.</div>`}
  </div>`;
  const nav = () => { const p = new URLSearchParams(); if ($("#fCol").value) p.set("c", $("#fCol").value); if ($("#fType").value) p.set("t", $("#fType").value); go("#/documents" + (p.toString() ? "?" + p : "")); };
  $("#fCol").onchange = nav; $("#fType").onchange = nav;
  const drop = $("#drop");
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); openUpload([...e.dataTransfer.files], cid); };
  if (busy) state.pollTimer = setTimeout(() => route().view === "documents" && renderDocuments(route()), 1500);
}
function entryBadge(e) {
  if (e.status === "ready") return `<span class="badge ok" title="${e.embed_model ? "semantic + keyword search" : "keyword search only"}">${esc(e.collection)}</span>`;
  if (e.status === "error") return `<span class="badge err" title="${esc(e.error)}">${esc(e.collection)} · failed</span>`;
  return `<span class="badge warn">${esc(e.collection)} · ${e.status === "queued" ? "queued" : "indexing…"}</span>`;
}
function docCard(d, cid) {
  const entries = d.entries.filter((e) => !cid || String(e.collection_id) === cid);
  const pieces = entries.reduce((a, e) => a + e.chunks, 0);
  const err = d.entries.find((e) => e.error);
  return `<div class="card">
    <div class="card-top"><div class="icon-box">${ICON.file}</div><div style="min-width:0"><h3 title="${esc(d.name)}">${esc(d.name)}</h3>
      <div class="row sub"><span class="tag">${esc(d.ext.slice(1).toUpperCase())}</span><span>${plural(pieces, "piece")}</span><span>${fmtBytes(d.size)}</span><span>${ago(d.created)}</span></div></div></div>
    <div class="row">${d.entries.map(entryBadge).join("")}</div>
    ${err ? `<div class="sub err-text">${esc(err.error)}</div>` : ""}
    <div class="row bottom">
      <button class="btn small ghost" data-act="preview" data-id="${d.id}">Preview</button>
      <button class="btn small ghost" data-act="doc-info" data-id="${d.id}">More info</button>
      <button class="btn small ghost danger" data-act="remove-doc" data-id="${d.id}" data-name="${esc(d.name)}" data-cid="${cid}">Remove</button></div>
  </div>`;
}
function openUpload(files = [], preselect = "") {
  state.uploadFiles = files;
  const cols = state.cols || [];
  let last = []; try { last = JSON.parse(localStorage.getItem("daftar.uploadCols") || "[]"); } catch { /* ignore */ }
  const checked = (id) => (preselect ? String(id) === String(preselect) : last.includes(id) || cols.length === 1);
  modal(`<form><h2>Upload documents</h2><p class="sub">Files are copied into Daftar's data folder and indexed on this computer.</p>
    <div class="field"><span class="label">Files</span><div id="upFiles" class="checks"></div>
      <button type="button" class="btn small" data-act="pick-files" style="align-self:flex-start">Choose files…</button></div>
    <div class="field"><span class="label">Add to collections</span><div class="checks" id="upCols">${cols.map((c) => `<label><input type="checkbox" name="c" value="${c.id}" ${checked(c.id) ? "checked" : ""}>${esc(c.name)}</label>`).join("")}</div>
      <div class="row"><input type="text" id="newColName" placeholder="New collection name" style="flex:1"><button type="button" class="btn small" data-act="quick-col">Add</button></div>
      <span class="hint">One file can live in several collections; each gets its own search index.</span></div>
    <div class="modal-actions"><button type="button" class="btn" data-act="close">Cancel</button><button type="submit" class="btn primary">Upload</button></div></form>`,
  { onSubmit: async (f) => {
    const ids = f.getAll("c").map(Number);
    if (!state.uploadFiles.length) throw new Error("Choose at least one file");
    if (!ids.length) throw new Error("Choose at least one collection");
    try { localStorage.setItem("daftar.uploadCols", JSON.stringify(ids)); } catch { /* ignore */ }
    const form = new FormData();
    state.uploadFiles.forEach((file) => form.append("files", file));
    ids.forEach((id) => form.append("collection_ids", id));
    const res = await api("/documents", { method: "POST", body: form });
    const skipped = res.filter((x) => x.skipped);
    toast(skipped.length ? `Skipped: ${skipped.map((s) => `${s.name} (${s.skipped})`).join(", ")}` : `Indexing ${plural(res.length, "file")}…`, skipped.length > 0);
    go(`#/documents${ids.length === 1 ? "?c=" + ids[0] : ""}`);
  } });
  renderUploadFiles();
}
function renderUploadFiles() {
  const el = $("#upFiles"); if (!el) return;
  el.innerHTML = state.uploadFiles.length ? state.uploadFiles.map((f, i) => `<label style="justify-content:space-between"><span>${esc(f.name)} <span class="hint">${fmtBytes(f.size)}</span></span>
    <button type="button" class="icon-btn" data-act="unpick" data-i="${i}">${ICON.x}</button></label>`).join("") : '<span class="hint">No files chosen yet.</span>';
}
$("#filePick").onchange = (e) => { state.uploadFiles = [...state.uploadFiles, ...e.target.files]; e.target.value = ""; renderUploadFiles(); };

async function previewDocument(id, query = "") {
  const info = await api(`/documents/${id}`);
  let text;
  try { text = (await api(`/documents/${id}/text`)).pages; } catch (e) { text = null; }
  const body = text ? text.map((p) => `${text.length > 1 ? `<h5>Page ${p.page}</h5>` : ""}${highlight(esc(p.text), query)}`).join("") : '<span class="sub">This document has not been read yet. Try again in a moment.</span>';
  const fmts = [["pdf", "PDF"], ["docx", "Word"], ["md", "Markdown"], ["txt", "Text"], ["html", "HTML"]];
  modal(`<div class="modal-head"><div style="min-width:0"><h2>${esc(info.name)}</h2>
      <div class="row sub"><span>${plural(info.words, "word")}</span><span>~${info.read_minutes} min read</span>${info.ext === ".pdf" ? `<span>${plural(info.pages, "page")}</span>` : ""}<span>${fmtBytes(info.size)}</span></div></div>
      <button class="icon-btn" data-act="close">${ICON.x}</button></div>
    <div class="preview-text">${body}</div>
    <div class="modal-actions" style="justify-content:space-between">
      <div class="row"><span class="sub">Export as</span>${fmts.map(([f, l]) => `<a class="btn small" href="/api/documents/${id}/export?format=${f}" download>${l}</a>`).join("")}</div>
      <div class="row"><a class="btn small ghost" href="/api/documents/${id}/file" target="_blank" rel="noopener">Open original</a><button class="btn small" data-act="doc-info" data-id="${id}">More info</button></div></div>`, { wide: true });
  const mark = $("#modalBox mark"); if (mark) mark.scrollIntoView({ block: "center" });
}
async function documentInfo(id) {
  const [info, cols] = await Promise.all([api(`/documents/${id}`), api("/collections")]);
  const missing = cols.filter((c) => !info.entries.some((e) => e.collection_id === c.id));
  const row = (k, v) => `<tr><th style="width:150px">${k}</th><td>${v}</td></tr>`;
  modal(`<div class="modal-head"><div style="min-width:0"><h2>File information</h2><div class="sub">${esc(info.name)}</div></div><button class="icon-btn" data-act="close">${ICON.x}</button></div>
    <table class="t">${row("Type", esc(info.ext.slice(1).toUpperCase()))}${row("Size", fmtBytes(info.size))}${row("Pages", info.pages)}
      ${row("Words", info.words.toLocaleString())}${row("Reading time", `~${info.read_minutes} min`)}${row("Added", new Date(info.created * 1000).toLocaleString())}
      ${row("Fingerprint", `<code style="font-size:11px">${esc(info.sha256.slice(0, 16))}…</code>`)}</table>
    <h3 style="font-size:14px;margin:18px 0 6px">Saved in ${plural(info.entries.length, "collection")}</h3>
    <div class="tbl"><table class="t"><tr><th>Collection</th><th>Content pieces</th><th>Search</th><th>Status</th><th></th></tr>
      ${info.entries.map((e) => `<tr><td>${esc(e.collection)}</td><td>${e.chunks}</td><td>${e.embed_model ? "semantic + keyword" : "keyword only"}</td>
        <td>${e.status === "error" ? `<span class="badge err" title="${esc(e.error)}">failed</span>` : e.stale ? '<span class="badge warn">needs re-index</span>' : `<span class="badge ${e.status === "ready" ? "ok" : "warn"}">${e.status}</span>`}</td>
        <td class="row"><button class="btn small ghost" data-act="reindex" data-id="${e.id}" data-doc="${id}">Re-index</button>
          <button class="btn small ghost danger" data-act="remove-entry" data-cid="${e.collection_id}" data-doc="${id}">Remove</button></td></tr>`).join("")}</table></div>
    ${missing.length ? `<div class="row" style="margin-top:14px"><select id="addCol">${missing.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select>
      <button class="btn small" data-act="add-to-col" data-doc="${id}">Add to collection</button></div>` : ""}
    <div class="modal-actions"><button class="btn" data-act="preview" data-id="${id}">Preview</button><button class="btn primary" data-act="close">Done</button></div>`, { wide: true });
}

// ---------- chat ----------
async function renderChat(r) {
  const id = r.id;
  const [cols, status] = await Promise.all([api("/collections"), state.status ? Promise.resolve(state.status) : refreshStatus()]);
  const chat = id ? await api(`/chats/${id}`) : null;
  let last = null; try { last = Number(localStorage.getItem("daftar.lastCollection")); } catch { /* ignore */ }
  let lastAll = false; try { lastAll = localStorage.getItem("daftar.lastCollection") === "all"; } catch { /* ignore */ }
  const everything = chat ? !!chat.all_collections : lastAll;
  const selected = everything ? null : chat ? chat.collection_id : (cols.find((c) => c.id === last) || cols[0] || {}).id;
  const options = `<option value="all" ${everything ? "selected" : ""}>All collections</option>` +
    cols.map((c) => `<option value="${c.id}" ${c.id === selected ? "selected" : ""}>${esc(c.name)} · ${plural(c.documents, "document")}</option>`).join("");
  const msgs = chat ? chat.messages : [];
  const lastAssistant = [...msgs].reverse().find((m) => m.role === "assistant");
  const setup = !status.chat_model ? await api("/setup").catch(() => null) : null;
  const canAsk = cols.length > 0 && !!status.chat_model;
  main.innerHTML = `<div class="chat">
    <div class="chat-head"><div class="chat-title">
        <button ${chat ? `data-act="rename-chat" data-id="${chat.id}" title="Rename chat"` : ""}>${esc(chat ? chat.title : "New chat")}</button>
        ${cols.length ? `<select id="chatCol" title="Collection this chat searches">${chat && !chat.collection_id && !chat.all_collections ? '<option value="">Pick a collection…</option>' : ""}${options}</select>` : ""}</div>
      <span class="badge" title="Model answering in this chat">${esc(status.chat_model || "no model")}</span></div>
    <div class="msgs" id="msgs">${msgs.length ? msgs.map((m, i) => msgHTML(m, { last: m === lastAssistant, question: questionFor(msgs, i) })).join("") : setup ? setupHTML(setup) : heroHTML(cols)}</div>
    <div class="composer"><form id="askForm"><textarea id="q" rows="1" placeholder="${canAsk ? "Ask anything about your documents…" : status.chat_model ? "Create a collection first" : "Set up a chat model first"}" ${canAsk ? "" : "disabled"}></textarea>
      <button type="submit" class="btn primary" id="sendBtn" ${canAsk ? "" : "disabled"}>Send</button></form>
      <div class="note">Press <kbd>Enter</kbd> to send, <kbd>Shift</kbd> + <kbd>Enter</kbd> for a new line. Answers are generated on this computer; check the sources for anything important.</div></div>
  </div>`;
  state.chat = chat;
  const box = $("#msgs"); box.scrollTop = box.scrollHeight;
  const q = $("#q");
  q.oninput = () => { q.style.height = "auto"; q.style.height = Math.min(q.scrollHeight, 200) + "px"; };
  q.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#askForm").requestSubmit(); } };
  if (!r.noFocus) q.focus();
  const sel = $("#chatCol");
  if (sel) sel.onchange = async () => {
    try { localStorage.setItem("daftar.lastCollection", sel.value); } catch { /* ignore */ }
    const body = sel.value === "all" ? { all_collections: true } : { collection_id: Number(sel.value) || null };
    if (chat) { await api(`/chats/${chat.id}`, { method: "PATCH", body }).catch(fail); loadChats(); }
  };
  $("#askForm").onsubmit = async (e) => {
    e.preventDefault();
    if (!canAsk) return;
    if (state.streaming) { state.streaming.abort(); return; }
    const question = q.value.trim();
    if (!question) return;
    let chatId = chat && chat.id;
    if (!chatId) {
      if (!sel || !sel.value) return toast("Pick a collection first", true);
      const created = await api("/chats", { method: "POST", body: sel.value === "all" ? { all_collections: true } : { collection_id: Number(sel.value) } }).catch(fail);
      if (!created) return;
      chatId = created.id;
      history.replaceState(null, "", `#/chat/${chatId}`);
    }
    q.value = ""; q.oninput();
    await streamAnswer(chatId, question);
  };
}
function questionFor(msgs, i) { for (let j = i; j >= 0; j--) if (msgs[j].role === "user") return msgs[j].content; return ""; }
function heroHTML(cols) {
  if (!cols.length) return `<div class="hero"><img src="logo.svg" alt=""><h2>Welcome to Daftar</h2>
    <p>Create a collection, add your contracts, reports or notes, then ask questions. Everything stays on this computer.</p>
    <button class="btn primary" data-act="new-col">Create collection</button></div>`;
  const ideas = ["Summarize the key points", "What deadlines or dates are mentioned?", "Which files are in this collection?", "List the parties and amounts involved"];
  return `<div class="hero"><img src="logo.svg" alt=""><h2>Ask your documents</h2>
    <p>Every answer lists the passages it used, with a match score, so you can check it.</p>
    <div class="suggest">${ideas.map((i) => `<button class="btn small" data-act="suggest">${esc(i)}</button>`).join("")}</div></div>`;
}
function setupHTML(s) {
  const hw = s.hardware || {}, gpu = hw.gpu || {};
  const facts = [hw.ram_gb ? `${hw.ram_gb} GB memory` : null, hw.cpu_count ? `${hw.cpu_count} CPU cores` : null,
    gpu.vram_gb ? `${gpu.name} (${gpu.vram_gb} GB, graphics)` : null].filter(Boolean).map(esc).join(" · ");
  const steps = (s.steps || []).map((st) => `<div class="setup-step">
      <span class="dot ${st.done ? "ok" : st.optional ? "warn" : "err"}"></span>
      <div class="setup-step-body"><b>${esc(st.title)}</b> <span class="sub">${esc(st.detail || "")}</span>
        ${st.fit ? `<div class="hint">${esc(st.fit.label)} — ${esc(st.fit.note)}</div>` : ""}</div>
      ${st.download ? `<button class="btn small" data-act="setup-pull" data-name="${esc(st.download)}">Download</button>` : ""}
    </div>`).join("");
  return `<div class="hero setup"><img src="logo.svg" alt=""><h2>Set up Daftar</h2>
    <p>${facts ? facts : "Checking this computer…"}<br><span class="sub">Everything runs on this computer; only model downloads use the internet.</span></p>
    <div class="panel" style="text-align:left;max-width:640px;width:100%;margin:0 auto">${steps}
      ${s.older_engine ? '<p class="hint err-text">Your Ollama is older than 0.40. Update it to run Qwen3.5, or download the smaller model below.</p>' : ""}
      <div class="progress" id="setupProgress" hidden><div class="row" style="justify-content:space-between"><span class="sub" id="setupPullStatus"></span><span class="sub" id="setupPullPct"></span></div><div class="meter"><i id="setupPullBar" style="width:0"></i></div></div>
      <div class="row" style="margin-top:12px;flex-wrap:wrap"><button class="btn" data-act="setup-test">Test my setup</button>
        ${s.engine_ready ? "" : '<button class="btn ghost" data-act="setup-refresh">Refresh</button>'}
        <a class="btn ghost" href="#/settings">Settings</a></div>
      <p class="hint" id="setupTest"></p>
    </div></div>`;
}
function sourcesHTML(sources, question) {
  if (!sources || !sources.length) return "";
  return `<button class="src-toggle" data-act="toggle-sources"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 8v.01M11 12h1v4h1"/></svg>${plural(sources.length, "source")}</button>
    <div class="sources">${sources.map((s, i) => `<div class="src" data-n="${i + 1}">
      <div class="src-head"><span class="n">[${i + 1}]</span><button class="doc" data-act="preview" data-id="${s.document_id}" data-q="${esc(question)}" title="Open in preview">${esc(s.document)}</button>
        <span class="sub">${where(s)}</span>${matchLabel(s)}</div>
      <div class="snip" data-act="expand-src">${highlight(esc(s.text), question)}</div></div>`).join("")}</div>`;
}
function msgHTML(m, { last = false, question = "" } = {}) {
  if (m.role === "user") return `<div class="msg user" data-id="${m.id ?? ""}"><div class="avatar">${ICON.user}</div><div class="msg-body">
      <div class="bubble">${esc(m.content)}</div>
      ${m.id ? `<div class="msg-tools"><button class="icon-btn" data-act="edit-msg" title="Edit and ask again">${ICON.edit}</button>
        <button class="icon-btn" data-act="copy-msg" title="Copy">${ICON.copy}</button><button class="icon-btn" data-act="del-msg" title="Delete">${ICON.trash}</button></div>` : ""}</div></div>`;
  const meta = m.meta || {};
  const dbg = meta.debug ? `<details class="debug"><summary>Debug details</summary><pre class="code">${esc(JSON.stringify(meta.debug, null, 2))}</pre></details>` : "";
  return `<div class="msg assistant" data-id="${m.id ?? ""}"><div class="avatar"><img src="logo.svg" alt=""></div><div class="msg-body">
      <div class="bubble">${md(m.content, (m.sources || []).length)}${m.error ? `<p class="err-text">⚠ ${esc(m.error)}</p>` : ""}${sourcesHTML(m.sources, question)}</div>
      ${m.id ? `<div class="msg-tools"><button class="icon-btn" data-act="copy-msg" title="Copy">${ICON.copy}</button>
        ${last ? `<button class="icon-btn" data-act="regen" title="Regenerate">${ICON.redo}</button>` : ""}
        <button class="icon-btn" data-act="del-msg" title="Delete">${ICON.trash}</button>
        <span class="hint" style="align-self:center;margin-left:6px">${esc(meta.model || "")}${meta.mode ? ` · ${meta.mode} search` : ""}${meta.stopped ? " · stopped" : ""}</span></div>` : ""}${dbg}</div></div>`;
}
async function streamAnswer(chatId, question, resend = false) {
  const box = $("#msgs");
  if (box.querySelector(".hero")) box.innerHTML = "";
  if (question) box.insertAdjacentHTML("beforeend", msgHTML({ role: "user", content: question }));
  box.insertAdjacentHTML("beforeend", `<div class="msg assistant"><div class="avatar"><img src="logo.svg" alt=""></div><div class="msg-body"><div class="bubble cursor"></div></div></div>`);
  const el = box.lastElementChild, bubble = el.querySelector(".bubble");
  box.scrollTop = box.scrollHeight;
  const ctrl = new AbortController(); state.streaming = ctrl;
  $("#sendBtn").textContent = "Stop";
  const m = { role: "assistant", content: "", sources: [] };
  let frame = 0;
  const paint = () => { frame = 0; bubble.innerHTML = md(m.content, m.sources.length); bubble.classList.add("cursor"); if (box.scrollHeight - box.scrollTop - box.clientHeight < 140) box.scrollTop = box.scrollHeight; };
  try {
    const r = await fetch(`/api/chats/${chatId}/ask`, { method: "POST", headers: { "X-Daftar": "1", "Content-Type": "application/json" },
      body: JSON.stringify({ question: question || "", resend }), signal: ctrl.signal });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    await readStream(r, (ev) => {
      if (ev.type === "meta") Object.assign(m, { sources: ev.sources });
      else if (ev.type === "token") { m.content += ev.text; if (!frame) frame = requestAnimationFrame(paint); }
      else if (ev.type === "error") m.error = ev.text;
    });
  } catch (e) { if (e.name !== "AbortError") m.error = e.message; }
  state.streaming = null;
  if (frame) cancelAnimationFrame(frame);
  await loadChats();
  if (route().view === "chat") await renderChat({ id: chatId, noFocus: false });
  if (m.error) toast(m.error, true);
}

// ---------- search ----------
async function renderSearch(r) {
  const q = r.params.get("q") || "", cid = r.params.get("c") || "";
  const cols = await api("/collections");
  main.innerHTML = `<div class="page"><div class="head"><div><h1>Search</h1><p>Find passages across your documents without asking the AI.</p></div></div>
    <form class="searchbar" id="searchForm"><input type="search" id="sq" value="${esc(q)}" placeholder="Search words or a question…" autofocus>
      <select id="sc"><option value="">All collections</option>${cols.map((c) => `<option value="${c.id}" ${String(c.id) === cid ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
      <button type="submit" class="btn primary">Search</button></form><div id="results"></div></div>`;
  $("#searchForm").onsubmit = (e) => { e.preventDefault(); const p = new URLSearchParams({ q: $("#sq").value.trim() }); if ($("#sc").value) p.set("c", $("#sc").value); go("#/search?" + p); };
  if (!q) { $("#results").innerHTML = `<div class="empty">Results show the best-matching passages with a match score. Click a file name to preview it.</div>`; return; }
  const res = await api(`/search?q=${encodeURIComponent(q)}${cid ? "&collection_id=" + cid : ""}`);
  $("#results").innerHTML = `<p class="sub">${plural(res.results.length, "passage")} · ${res.mode} search</p>` + (res.results.length
    ? `<div class="sources">${res.results.map((s) => `<div class="src">
        <div class="src-head"><button class="doc" data-act="preview" data-id="${s.document_id}" data-q="${esc(q)}">${esc(s.document)}</button>
        <span class="sub">${where(s)}</span>${matchLabel(s)}</div>
        <div class="snip" data-act="expand-src">${highlight(esc(s.text), q)}</div></div>`).join("")}</div>`
    : `<div class="empty"><h3>Nothing found</h3>Try other words, or check that the documents finished indexing.</div>`);
}

// ---------- settings ----------
async function renderSettings() {
  const s = await refreshStatus();
  const cfg = s.settings;
  let models = []; try { models = await api("/models"); } catch { /* ollama down */ }
  const names = models.map((m) => m.name);
  const has = (n) => names.includes(n) || names.includes(n + ":latest");
  const opts = (list, cur) => [...new Set([cur, ...list])].map((n) => `<option value="${esc(n)}" ${n === cur ? "selected" : ""}>${esc(n)}${has(n) ? "" : " (not installed)"}</option>`).join("");
  const chatModels = s.chat_models || names.filter((n) => !n.includes("embed"));
  const embedModels = s.embed_models || names.filter((n) => n.includes("embed"));
  const sw = (key, title, hint) => `<label class="switch"><input type="checkbox" data-setting="${key}" ${cfg[key] ? "checked" : ""}><span><b>${title}</b><br><span class="hint">${hint}</span></span></label>`;
  const num = (key, title, hint, min, max, step = 1) => `<div class="field"><label>${title}</label><input type="number" data-setting="${key}" min="${min}" max="${max}" step="${step}" value="${cfg[key]}"><span class="hint">${hint}</span></div>`;
  main.innerHTML = `<div class="page">
    <div class="head"><div><h1>Settings</h1><p>Daftar ${esc(s.version)} · plain-language controls, saved as you change them.</p></div>
      <div class="actions"><button class="btn ghost" data-act="reset-settings">Reset to defaults</button></div></div>
    <div class="cols"><div>
      <div class="panel"><h2>AI model</h2>
        <div class="pill" style="margin-bottom:14px">${s.ollama ? `<span class="dot ok"></span>${cfg.engine === "openai" ? "Connected to your OpenAI-compatible AI app" : `Ollama ${esc(s.ollama_version)} is running`}` : `<span class="dot err"></span>${cfg.engine === "openai" ? "Your AI app is not reachable at " + esc(cfg.openai_url) : "Ollama is not reachable"}`}</div>
        ${s.compute === "cpu" ? `<p class="sub err-text" style="margin-top:-6px">Ollama is running models on the <b>processor only</b> (nothing in GPU memory), so answers and scanned-page reading are slow. If this computer has a graphics card, reinstall Ollama with its GPU libraries (see Help).</p>`
          : s.compute === "partial" ? `<p class="sub" style="margin-top:-6px">The model only partly fits in GPU memory; a smaller model or shorter context will be faster.</p>` : ""}
        <div class="field"><label>Chat model</label><select data-setting="chat_model">${opts(chatModels, cfg.chat_model)}</select>
          <span class="hint">${s.chat_model && !has(cfg.chat_model) ? `Not installed, so Daftar is answering with <b>${esc(s.chat_model)}</b>. ` : ""}Larger models answer better but need more memory.</span></div>
        <div class="field"><label>Search model (embeddings)</label><select data-setting="embed_model">${opts(embedModels, cfg.embed_model)}</select>
          <span class="hint">${!s.embed_ready ? "No search model installed: search matches words only, not meaning."
            : s.embed_active !== cfg.embed_model.replace(/:latest$/, "") ? `Not installed, so Daftar uses <b>${esc(s.embed_active)}</b> for meaning-based search.` : "Meaning-based search is on."}</span></div>
        <div class="field"><label>Download a model</label><div class="row"><input type="text" id="pullName" placeholder="e.g. qwen3.5:2b or nomic-embed-text" style="flex:1">
          <button class="btn" data-act="pull" ${cfg.engine === "openai" ? "disabled title=\"Download models in your AI app, then pick them here\"" : ""}>Download</button></div>
          <span class="hint">This is the one action that uses the internet: Ollama fetches the model from its library. Your documents are never sent.</span>
          <div class="progress" id="pullProgress" hidden><div class="row" style="justify-content:space-between"><span class="sub" id="pullStatus"></span><span class="sub" id="pullPct"></span></div><div class="meter"><i id="pullBar" style="width:0"></i></div></div></div>
        ${models.length ? `<div class="models">${models.map((m) => `<div><span>${esc(m.name)}</span><span>${esc(m.parameters)} ${m.size ? fmtBytes(m.size) : ""}</span></div>`).join("")}</div>` : ""}
      </div>
      <div class="panel"><h2>Answers</h2>
        <div class="field"><label>Creativity level: <span id="tempVal">${Number(cfg.temperature).toFixed(1)}</span></label>
          <input type="range" min="0" max="2" step="0.1" data-setting="temperature" value="${cfg.temperature}">
          <div class="range-labels"><span>0 · Precise &amp; factual</span><span>2 · Creative &amp; varied</span></div></div>
        ${num("top_k", "Passages to use", "How many passages the AI reads before answering (1–20).", 1, 20)}
        ${num("scan_k", "Passages to scan", "How many candidate passages are examined to pick the best ones (4–100).", 4, 100)}
        ${sw("rerank", "Improve answer relevance", "Re-scores candidates by meaning and word overlap, and drops weak passages.")}
        ${cfg.engine === "openai" ? "" : sw("think", "Let the model reason first", "Reasoning models think before answering. Daftar does not show that reasoning, so leaving this off is faster; on a processor it can take a minute per answer.")}
      </div></div>
    <div>
      <div class="panel"><h2>Reading documents</h2>
        <div class="field"><label>Reading scanned pages (OCR model)</label><select data-setting="ocr_model">${opts(s.vision_models || [], cfg.ocr_model)}</select>
          <span class="hint">${s.ocr_active ? (s.ocr_active.replace(/:latest$/, "") === cfg.ocr_model.replace(/:latest$/, "") ? "Scanned PDF pages and photos are read with this model." : `Not installed, so Daftar uses <b>${esc(s.ocr_active)}</b>.`)
            : "No vision model installed: scanned PDF pages and photos cannot be read yet. PaddleOCR-VL is small and strong for Arabic and English."}</span></div>
        ${num("chunk_size", "Text block size", "Characters per passage (200–4000). Smaller blocks give more precise sources.", 200, 4000, 50)}
        ${num("chunk_overlap", "Text block overlap", "Characters shared between neighbouring blocks, so sentences are not cut off.", 0, 1000, 10)}
        ${s.stale_entries ? `<p class="sub">${plural(s.stale_entries, "document index")} use older settings or no search model.</p><button class="btn" data-act="reindex-all">Re-index now</button>` : '<p class="hint">All documents use the current settings.</p>'}
      </div>
      <div class="panel"><h2>AI engine</h2>
        <div class="field"><label>Run the models with</label><select data-setting="engine">
          <option value="ollama" ${cfg.engine !== "openai" ? "selected" : ""}>Ollama</option>
          <option value="openai" ${cfg.engine === "openai" ? "selected" : ""}>Another app (OpenAI-compatible): LM Studio, Jan, llama.cpp, vLLM, LocalAI, KoboldCpp…</option></select>
          <span class="hint">Use whichever local AI app you already have. Your documents stay on this computer either way.</span></div>
        ${cfg.engine === "openai" ? `
        <div class="field"><label>Server address</label><input type="text" data-setting="openai_url" value="${esc(cfg.openai_url)}">
          <span class="hint">LM Studio :1234, Jan :1337, llama.cpp :8080, vLLM :8000, KoboldCpp :5001. The context window is set in that app.</span></div>
        <div class="field"><label>API key <span class="hint">(only if your server requires one)</span></label><input type="text" data-setting="openai_key" value="${esc(cfg.openai_key)}" autocomplete="off"></div>`
        : `<div class="field"><label>Ollama server address</label><input type="text" data-setting="ollama_url" value="${esc(cfg.ollama_url)}"></div>`}
        <div class="row" style="margin-bottom:12px"><button class="btn small" data-act="detect">Find AI apps running on this computer</button></div>
        <div id="detected"></div>
        ${sw("allow_remote_ollama", "Allow a server on another computer", "Only for a server you control. Your documents and questions will be sent to it.")}
      </div>
      <div class="panel"><h2>Appearance &amp; advanced</h2>
        <label class="switch"><input type="checkbox" id="darkMode" ${cfg.theme !== "light" ? "checked" : ""}><span><b>Dark mode</b></span></label>
        ${sw("debug", "Developer debug mode", "Shows retrieval details and timings under answers, and the technical log below.")}
        ${cfg.debug ? `<div class="row" style="justify-content:space-between"><b style="font-size:13px">Technical log</b><button class="btn small" data-act="logs">Refresh</button></div><pre class="code" id="logs">Loading…</pre>` : ""}
      </div>
      <div class="panel"><h2>Privacy</h2><ul>
        <li><b>Local</b>: documents, chats and indexes stay in the data folder below.</li>
        <li><b>No telemetry</b>: no accounts, analytics or update checks.</li>
        <li><b>Offline</b>: once models are installed, no internet is needed.</li></ul>
        <div class="field" style="margin:14px 0 0"><span class="label">Data folder</span><code style="word-break:break-all;font-size:12px">${esc(s.data_dir)}</code></div></div>
    </div></div></div>`;
  main.querySelectorAll("[data-setting]").forEach((el) => {
    const save = async () => {
      const key = el.dataset.setting;
      const value = el.type === "checkbox" ? el.checked : el.type === "number" || el.type === "range" ? Number(el.value) : el.value;
      try {
        await api("/settings", { method: "PUT", body: { [key]: value } });
        toast("Saved");
        if (["chunk_size", "chunk_overlap", "embed_model", "chat_model", "debug", "allow_remote_ollama", "ollama_url", "engine", "openai_url", "openai_key", "ocr_model"].includes(key)) render();
        else refreshStatus();
      } catch (err) { fail(err); render(); }
    };
    el.addEventListener("change", save);
    if (el.type === "range") el.addEventListener("input", () => ($("#tempVal").textContent = Number(el.value).toFixed(1)));
  });
  $("#darkMode").onchange = async (e) => { const theme = e.target.checked ? "dark" : "light"; applyTheme(theme); await api("/settings", { method: "PUT", body: { theme } }).catch(fail); refreshStatus(); };
  if (cfg.debug) loadLogs();
}
async function loadLogs() { const el = $("#logs"); if (!el) return; const { lines } = await api("/logs"); el.textContent = lines.join("\n") || "(empty)"; el.scrollTop = el.scrollHeight; }
async function pullStream(name, show) {
  const r = await fetch("/api/models/pull", { method: "POST", headers: { "X-Daftar": "1", "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  let error = null;
  await readStream(r, (ev) => {
    if (ev.error) error = ev.error;
    else show(ev.status, ev.total ? Math.round((100 * (ev.completed || 0)) / ev.total) : null);
  });
  if (error) throw new Error(error);
}
async function pullModel() {
  const name = $("#pullName").value.trim();
  if (!name) return toast("Type a model name first", true);
  $("#pullProgress").hidden = false;
  const show = (status, pct) => { $("#pullStatus").textContent = status; $("#pullPct").textContent = pct == null ? "" : pct + "%"; $("#pullBar").style.width = (pct || 0) + "%"; };
  show("Starting…", 0);
  try { await pullStream(name, show); toast(`${name} is ready`); render(); }
  catch (e) { show("Failed: " + e.message, 0); fail(e); }
}
async function setupPull(name) {
  const progress = $("#setupProgress"), status = $("#setupPullStatus"), pct = $("#setupPullPct"), bar = $("#setupPullBar");
  if (!progress) return;
  progress.hidden = false;
  const show = (text, p) => { status.textContent = text; pct.textContent = p == null ? "" : p + "%"; bar.style.width = (p || 0) + "%"; };
  show("Starting…", 0);
  try {
    await pullStream(name, show);
    toast(`${name} is ready`);
    state.status = null;
    await refreshStatus();
    render();
  } catch (e) { show("Failed: " + e.message, 0); fail(e); }
}

// ---------- help ----------
async function renderHelp() {
  main.innerHTML = `<div class="page"><div class="head"><div><h1>Help</h1><p>How Daftar works, in five minutes.</p></div></div>
    <div class="cols"><div>
      <div class="panel"><h2>Getting started</h2><ol>
        <li><b>Create a collection</b> for a project, client or topic.</li>
        <li><b>Upload documents</b> (PDF, Word, Markdown, text, CSV). Pick one or more collections in the same dialog.</li>
        <li><b>Wait for “Ready”</b>. Each file is split into passages and indexed on this computer.</li>
        <li><b>Start a chat</b>, choose the collection it searches, and ask in plain language.</li>
        <li><b>Check the sources</b> under each answer. Match % shows how closely a passage fits the question. Click a file name to preview it.</li></ol></div>
      <div class="panel"><h2>Messages</h2><ul>
        <li>Hover a message to <b>copy</b>, <b>delete</b>, <b>edit</b> your question (it is asked again) or <b>regenerate</b> the last answer.</li>
        <li>Click a chat's title to rename it. Use the sidebar box to search all conversations.</li>
        <li><kbd>Enter</kbd> sends, <kbd>Shift</kbd>+<kbd>Enter</kbd> adds a line, <kbd>Esc</kbd> closes dialogs.</li></ul></div></div>
    <div>
      <div class="panel"><h2>Getting better answers</h2><ul>
        <li>Install a search model (embeddings), e.g. <code>nomic-embed-text</code>, so search understands meaning, not just words.</li>
        <li>Raise <b>Passages to use</b> for broad questions; lower <b>Creativity</b> for precise facts.</li>
        <li>After changing block size or the search model, use <b>Collections → Check status → Optimize search</b>.</li></ul></div>
      <div class="panel"><h2>Troubleshooting</h2><ul>
        <li><b>“Ollama not running”</b>: start Ollama, then reopen Daftar.</li>
        <li><b>“CPU only”</b>: Ollama found no GPU libraries. Reinstall Ollama from its official package (it includes the NVIDIA/AMD runtimes), restart it, and check that <code>ollama ps</code> shows “100% GPU”.</li>
        <li><b>Prefer LM Studio, Jan, llama.cpp or vLLM?</b> Start its local server, then Settings → AI engine → “Find AI apps running on this computer” → Use.</li>
        <li><b>Scanned PDFs and photos</b>: install a vision model for reading text, e.g. PaddleOCR-VL, and pick it under Settings → Reading scanned pages.</li>

        <li><b>Model not installed</b>: download it in Settings, or run <code>ollama pull &lt;name&gt;</code>.</li></ul></div>
      <div class="panel"><h2>Privacy</h2><p class="sub" style="margin:0">Daftar runs a small server on 127.0.0.1 that only this computer can reach, and talks only to your Ollama. Nothing is uploaded, tracked or synced.</p></div>
    </div></div></div>`;
}

// ---------- shared click actions ----------
document.body.addEventListener("click", async (e) => {
  const cite = e.target.closest(".cite");
  if (cite) {
    const msg = cite.closest(".msg"), src = msg?.querySelector(`.src[data-n="${cite.dataset.n}"]`);
    if (src) { msg.querySelector(".sources").hidden = false; src.classList.add("flash", "open"); src.scrollIntoView({ block: "nearest", behavior: "smooth" }); setTimeout(() => src.classList.remove("flash"), 1500); }
    return;
  }
  const a = e.target.closest("[data-act]");
  if (!a) return;
  const id = a.dataset.id, msgEl = a.closest(".msg");
  try {
    switch (a.dataset.act) {
      case "close": closeModal(); break;
      case "new-col": modal(collectionForm(), { onSubmit: async (f) => { await api("/collections", { method: "POST", body: { name: f.get("name"), description: f.get("description") } }); toast("Collection created"); render(); } }); break;
      case "edit-col": {
        const c = (state.cols || []).find((x) => String(x.id) === id);
        modal(collectionForm(c), { onSubmit: async (f) => { await api(`/collections/${id}`, { method: "PATCH", body: { name: f.get("name"), description: f.get("description") } }); render(); } });
        break;
      }
      case "del-col":
        if (await confirmBox(`Remove “${a.dataset.name}”?`, "Its search index is deleted. Documents that are not in any other collection are deleted too. Chats keep their messages.")) {
          await api(`/collections/${id}`, { method: "DELETE" }); toast("Collection removed"); render();
        }
        break;
      case "chat-col": try { localStorage.setItem("daftar.lastCollection", id); } catch { /* ignore */ } go("#/chat"); break;
      case "col-status": await collectionStatus(id); break;
      case "optimize": { a.disabled = true; const r = await api(`/collections/${id}/optimize`, { method: "POST" }); toast(r.requeued ? `Re-indexing ${plural(r.requeued, "document")}` : "Search index optimized"); await collectionStatus(id); if (route().view === "collections") renderCollections(); break; }
      case "upload": if (!state.cols) state.cols = await api("/collections"); openUpload([], route().params.get("c") || ""); break;
      case "pick-files": $("#filePick").click(); break;
      case "unpick": state.uploadFiles.splice(Number(a.dataset.i), 1); renderUploadFiles(); break;
      case "quick-col": {
        const name = $("#newColName").value.trim(); if (!name) break;
        const { id: newId } = await api("/collections", { method: "POST", body: { name } });
        state.cols = await api("/collections");
        $("#newColName").value = "";
        $("#upCols").insertAdjacentHTML("beforeend", `<label><input type="checkbox" name="c" value="${newId}" checked>${esc(name)}</label>`);
        break;
      }
      case "preview": await previewDocument(id, a.dataset.q || ""); break;
      case "doc-info": await documentInfo(id); break;
      case "remove-doc": {
        const cid = a.dataset.cid;
        if (cid) {
          if (await confirmBox(`Remove “${a.dataset.name}” from this collection?`, "If it is not in any other collection, the file is deleted from Daftar.")) { await api(`/collections/${cid}/documents/${id}`, { method: "DELETE" }); render(); }
        } else if (await confirmBox(`Delete “${a.dataset.name}”?`, "The file and its search index are removed from every collection.", "Delete")) {
          await api(`/documents/${id}`, { method: "DELETE" }); render();
        }
        break;
      }
      case "remove-entry":
        if (await confirmBox("Remove from this collection?", "If it is not in any other collection, the file is deleted from Daftar.")) {
          await api(`/collections/${a.dataset.cid}/documents/${a.dataset.doc}`, { method: "DELETE" });
          closeModal(); render();
        }
        break;
      case "add-to-col": await api(`/documents/${a.dataset.doc}/collections`, { method: "POST", body: { collection_ids: [Number($("#addCol").value)] } }); toast("Added; indexing…"); await documentInfo(a.dataset.doc); render(); break;
      case "reindex": await api(`/entries/${id}/reindex`, { method: "POST" }); toast("Re-indexing…"); await documentInfo(a.dataset.doc); break;
      case "reindex-all": { const r = await api("/reindex", { method: "POST" }); toast(`Re-indexing ${plural(r.queued, "document")}`); go("#/documents"); break; }
      case "toggle-sources": { const s = a.nextElementSibling; s.hidden = !s.hidden; break; }
      case "expand-src": a.closest(".src").classList.toggle("open"); break;
      case "suggest": { const q = $("#q"); q.value = a.textContent; $("#askForm").requestSubmit(); break; }
      case "rename-chat": {
        const title = await askText("Rename chat", state.chat?.title || "");
        if (title) { await api(`/chats/${id}`, { method: "PATCH", body: { title } }); await loadChats(); render(); }
        break;
      }
      case "copy-msg": {
        const m = state.chat?.messages.find((x) => String(x.id) === msgEl.dataset.id);
        if (m) copyText(m.content);
        break;
      }
      case "del-msg":
        if (await confirmBox("Delete this message?", "It is removed from the conversation.", "Delete")) {
          await api(`/messages/${msgEl.dataset.id}`, { method: "DELETE" }); render();
        }
        break;
      case "regen": {
        if (state.streaming) break;
        await api(`/messages/${msgEl.dataset.id}`, { method: "DELETE" });
        msgEl.remove();
        await streamAnswer(state.chat.id, null, true);
        break;
      }
      case "edit-msg": {
        const m = state.chat.messages.find((x) => String(x.id) === msgEl.dataset.id);
        const bubble = msgEl.querySelector(".bubble");
        bubble.outerHTML = `<div class="edit-box"><textarea>${esc(m.content)}</textarea><div class="row" style="justify-content:flex-end">
          <button class="btn small" data-act="cancel-edit">Cancel</button><button class="btn small primary" data-act="save-edit">Save &amp; ask again</button></div></div>`;
        msgEl.querySelector(".msg-tools").hidden = true;
        msgEl.querySelector("textarea").focus();
        break;
      }
      case "cancel-edit": render(); break;
      case "save-edit": {
        const content = msgEl.querySelector("textarea").value.trim();
        if (!content) break;
        await api(`/messages/${msgEl.dataset.id}`, { method: "PATCH", body: { content } });
        while (msgEl.nextElementSibling) msgEl.nextElementSibling.remove();
        msgEl.querySelector(".edit-box").outerHTML = `<div class="bubble">${esc(content)}</div>`;
        await streamAnswer(state.chat.id, null, true);
        break;
      }
      case "reset-settings":
        if (await confirmBox("Reset all settings?", "Models, answer and reading settings go back to their defaults. Your documents and chats are not touched.", "Reset")) {
          await api("/settings/reset", { method: "POST" }); toast("Settings reset"); render();
        }
        break;
      case "pull": await pullModel(); break;
      case "logs": await loadLogs(); break;
      case "setup-pull": {
        a.disabled = true;
        await setupPull(a.dataset.name);
        a.disabled = false;
        break;
      }
      case "setup-refresh": {
        state.status = null;
        await render();
        break;
      }
      case "setup-test": {
        const out = $("#setupTest");
        out.textContent = "Testing…"; a.disabled = true;
        const r = await api("/setup/test", { method: "POST" }).catch(() => null);
        a.disabled = false;
        state.status = null;
        await refreshStatus();
        if (!r) { out.textContent = "The local server did not answer."; break; }
        out.textContent = r.ok ? `${r.model} answered in ${(r.ms / 1000).toFixed(1)} s: “${r.answer}”` : `Not working: ${r.error}`;
        out.className = r.ok ? "hint" : "hint err-text";
        break;
      }
      case "detect": {
        a.disabled = true; $("#detected").innerHTML = '<p class="sub">Looking…</p>';
        const found = await api("/engines/detect");
        $("#detected").innerHTML = found.length ? `<div class="sources" style="margin-bottom:12px">${found.map((f) => `<div class="src row" style="justify-content:space-between">
          <span><b>${esc(f.name)}</b> <span class="sub">${esc(f.url)} · ${plural(f.models, "model")}</span></span>
          <button class="btn small primary" data-act="use-engine" data-name="${esc(f.name)}" data-url="${esc(f.url)}">Use</button></div>`).join("")}</div>`
          : '<p class="sub">No AI app answered on the usual addresses. Start Ollama, LM Studio (Developer → Start server), Jan (Local API Server) or llama-server, then try again.</p>';
        a.disabled = false;
        break;
      }
      case "use-engine": {
        const body = a.dataset.name === "Ollama" ? { engine: "ollama", ollama_url: a.dataset.url } : { engine: "openai", openai_url: a.dataset.url };
        await api("/settings", { method: "PUT", body }); toast(`Using ${a.dataset.name}`); render(); break;
      }
    }
  } catch (err) { fail(err); }
});

// ---------- boot ----------
(async () => {
  await Promise.all([refreshStatus(), loadChats().catch(() => {})]);
  render();
  setInterval(refreshStatus, 15000);
})();
