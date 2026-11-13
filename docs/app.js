const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

$("#year").textContent = new Date().getFullYear();

const menuBtn = $("#menuBtn");
const mobileMenu = $("#mobileMenu");
menuBtn.addEventListener("click", () => {
  const open = mobileMenu.classList.toggle("open");
  menuBtn.setAttribute("aria-expanded", String(open));
  menuBtn.textContent = open ? "✕" : "☰";
});
$$("#mobileMenu a").forEach(a => a.addEventListener("click", () => {
  mobileMenu.classList.remove("open");
  menuBtn.setAttribute("aria-expanded", "false");
  menuBtn.textContent = "☰";
}));

(function openDesktopApp() {
  const fallback = $("#appFallback");
  const links = [$("#openApp"), $("#openBrowser")].filter(Boolean);
  if (!links.length || !fallback) return;
  let active = null;

  links.forEach(link => {
    link.addEventListener("click", event => {
      event.preventDefault();
      if (active) active.cancel();
      let settled = false;
      let timer = 0;
      fallback.hidden = true;
      link.classList.add("is-launching");
      link.setAttribute("aria-busy", "true");

      const cleanup = () => {
        document.removeEventListener("visibilitychange", onVisibility);
        window.removeEventListener("blur", onBlur);
        clearTimeout(timer);
        link.classList.remove("is-launching");
        link.removeAttribute("aria-busy");
      };
      const finish = ok => {
        if (settled) return;
        settled = true;
        cleanup();
        if (!ok) {
          fallback.hidden = false;
          fallback.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest" });
        }
      };
      const onBlur = () => finish(true);
      const onVisibility = () => { if (document.hidden) finish(true); };
      timer = setTimeout(() => finish(false), 4000);
      document.addEventListener("visibilitychange", onVisibility);
      window.addEventListener("blur", onBlur, { once: true });
      active = { cancel: () => { settled = true; cleanup(); } };
      try {
        window.location.href = link.href;
      } catch {
        finish(false);
      }
    });
  });
})();

const io = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      e.target.classList.add("in");
      io.unobserve(e.target);
    }
  });
}, { threshold: 0.12 });
$$(".reveal").forEach(el => io.observe(el));

$$(".copy").forEach(btn => {
  btn.addEventListener("click", async () => {
    const pre = document.getElementById(btn.dataset.copy + "Code");
    const text = pre.innerText.replace(/\u00a0/g, " ");
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = "Copied";
      setTimeout(() => { btn.textContent = "Copy"; }, 1600);
    } catch {
      btn.textContent = "Select and copy";
    }
  });
});

function setupCanvas(canvas) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const rect = canvas.getBoundingClientRect();
  const w = Math.max(1, rect.width);
  const h = Math.max(1, rect.height);
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

function fibSphere(n, radius) {
  const pts = [];
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < n; i++) {
    const y = n === 1 ? 0 : 1 - (i / (n - 1)) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = golden * i;
    pts.push({ x: Math.cos(theta) * r * radius, y: y * radius, z: Math.sin(theta) * r * radius });
  }
  return pts;
}

function rotatePoint(p, yaw, pitch) {
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  const x1 = p.x * cy - p.z * sy;
  const z1 = p.x * sy + p.z * cy;
  const y1 = p.y * cp - z1 * sp;
  const z2 = p.y * sp + z1 * cp;
  return { x: x1, y: y1, z: z2 };
}

function projectPoint(p, w, h, spread, fov) {
  const scale = fov / (fov + p.z);
  return { x: w / 2 + p.x * spread * scale, y: h / 2 + p.y * spread * scale, scale, z: p.z };
}

(function hero() {
  const canvas = $("#heroCanvas");
  if (!canvas) return;
  let state = setupCanvas(canvas);
  const nodes = fibSphere(64, 1);
  const edges = [];
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const dx = nodes[i].x - nodes[j].x, dy = nodes[i].y - nodes[j].y, dz = nodes[i].z - nodes[j].z;
      if (dx * dx + dy * dy + dz * dz < 0.38) edges.push([i, j]);
    }
  }
  let mouseX = 0, mouseY = 0, spin = 0, visible = true, raf = 0;

  function draw() {
    const { ctx, w, h } = state;
    ctx.clearRect(0, 0, w, h);
    const spread = Math.min(w, h) * 0.34;
    const fov = 2.6;
    const yaw = 0.4 + spin + mouseX * 0.5;
    const pitch = -0.18 + mouseY * 0.35;
    const projected = nodes.map(p => projectPoint(rotatePoint(p, yaw, pitch), w, h, spread, fov));
    ctx.lineWidth = 1;
    edges.forEach(([a, b]) => {
      const pa = projected[a], pb = projected[b];
      const alpha = 0.04 + Math.max(0, (pa.scale + pb.scale) / 2 - 0.55) * 0.2;
      ctx.strokeStyle = `rgba(45,212,191,${Math.min(alpha, 0.26)})`;
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      ctx.lineTo(pb.x, pb.y);
      ctx.stroke();
    });
    projected
      .map((p, i) => ({ ...p, i }))
      .sort((a, b) => b.z - a.z)
      .forEach(p => {
        const r = Math.max(1.2, 2.4 * p.scale);
        const glow = Math.max(0, Math.min(1, (p.scale - 0.6) * 1.4));
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(94,234,212,${0.18 + glow * 0.65})`;
        ctx.shadowColor = "rgba(45,212,191,.8)";
        ctx.shadowBlur = 10 * glow;
        ctx.fill();
        ctx.shadowBlur = 0;
      });
  }

  function frame() {
    raf = 0;
    if (!visible || document.hidden) return;
    if (!reduceMotion) {
      spin += 0.0016;
      draw();
      raf = requestAnimationFrame(frame);
    }
  }

  function kick() {
    if (reduceMotion) draw();
    else if (!raf) raf = requestAnimationFrame(frame);
  }

  canvas.parentElement.addEventListener("pointermove", e => {
    const r = canvas.getBoundingClientRect();
    mouseX = (e.clientX - r.left) / r.width - 0.5;
    mouseY = (e.clientY - r.top) / r.height - 0.5;
    if (reduceMotion) draw();
  });

  const vio = new IntersectionObserver(entries => {
    visible = entries[0].isIntersecting;
    if (visible) kick();
  }, { threshold: 0 });
  vio.observe(canvas);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) kick(); });

  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      state = setupCanvas(canvas);
      draw();
    }, 150);
  });
  kick();
})();

const SAMPLE_DOCS = [
  {
    id: "agreement",
    title: "Northwind Supplier Agreement",
    kind: "Contract · 6 sections",
    text: `Section 1 — Parties and purpose
This Supply Agreement is made between Northwind Components Ltd ("Supplier") and Cedar Works GmbH ("Buyer"). It covers the supply of precision fasteners and related components for the Buyer's assembly line in Rotterdam. The agreement takes effect on 1 March 2026 and replaces all earlier purchase terms.

Section 2 — Term and renewal
The initial term ends on 28 February 2027. After that, the agreement renews automatically for successive 12-month terms unless either party gives written notice at least 60 days before the end of the current term. Notice must be sent by registered mail or email to the address listed in Schedule A. A renewal does not change prices unless both parties sign a written amendment.

Section 3 — Pricing and payment
Prices are listed in Schedule B and are fixed for the first 12 months. Invoices are due 30 days after the invoice date. Late payments carry interest of 1.5% per month. The Supplier may charge reasonable shipping costs only when the Buyer requests expedited delivery. All prices exclude VAT.

Section 4 — Delivery and acceptance
The Supplier must deliver at least 95% of confirmed order lines on the agreed date. If a delivery is more than five business days late, the Buyer may cancel that order line without penalty. The Buyer must inspect goods within 10 business days and report visible defects in writing. Goods not rejected within that period are considered accepted.

Section 5 — Quality and liability
The Supplier warrants that all components meet the specifications in Schedule C for 18 months after delivery. The Supplier's total liability is limited to the value of the affected order, except for fraud, wilful misconduct, or death and personal injury. The Buyer must give notice of a warranty claim within 30 days of discovering the defect.

Section 6 — Termination
Either party may terminate for material breach if the breach is not cured within 30 days of written notice. The Buyer may terminate immediately if the Supplier becomes insolvent. On termination, the Buyer must pay for goods already delivered and accepted. Sections 5 and 7 survive termination.`
  },
  {
    id: "review",
    title: "Q3 Operations Review",
    kind: "Report · internal",
    text: `Executive summary
Revenue in Q3 reached EUR 4.82 million, 6.4% above the same quarter last year. Gross margin held at 31.2%. On-time delivery was 88%, below our 95% target, mainly because of port congestion and one customs delay. The team recovered to 94% in the final three weeks of the quarter.

Delivery performance
On-time delivery fell to 88% in Q3 from 96% in Q2. The largest cause was congestion at the Port of Rotterdam in August, which added four to seven days to inbound shipments. Two orders were held at customs for nine days because a certificate of origin was missing from the document pack. After we added a second carrier and increased buffer stock for the ten fastest-moving parts, on-time delivery returned to 94% in the last three weeks of September.

Customer impact
Three customers reported late deliveries. Northwind Components accepted a credit note of EUR 12,400 for one delayed line. No customer cancelled a contract. The customer satisfaction score fell from 4.6 to 4.4 out of 5, driven almost entirely by delivery dates rather than product quality.

Costs and inventory
Freight costs rose 11% quarter on quarter, while inventory value increased by EUR 380,000 because of the buffer stock decision. Warehouse utilisation reached 82%. We expect freight costs to stay elevated through Q4 and to normalise in the first quarter of next year.

Priorities for Q4
1. Keep on-time delivery above 94% through the holiday period.
2. Complete the supplier scorecard pilot with the five largest vendors.
3. Reduce buffer stock by EUR 150,000 without dropping below the service target.
4. Move the certificate of origin check into the standard document pack for every customs shipment.`
  },
  {
    id: "invoice",
    title: "Invoice 2026-114",
    kind: "Invoice · PDF, 2 pages",
    text: `Northwind Components Ltd
Unit 7, Harbour Industrial Estate, Rotterdam

INVOICE 2026-114
Invoice date: 3 September 2026
Due date: 3 October 2026
Bill to: Cedar Works GmbH, Assembly Line 2, Hamburg

Line items
1. Precision fastener M8, 12,000 units at EUR 0.0142 each — EUR 170.40
2. Locking washer M8, 12,000 units at EUR 0.0061 each — EUR 73.20
3. Expedited shipping, Rotterdam to Hamburg — EUR 13.80
Subtotal: EUR 257.40
VAT 5%: EUR 12.87
TOTAL DUE: EUR 270.27

Payment terms: 30 days from invoice date. Late payments carry 1.5% interest per month. Please quote invoice number 2026-114 with your payment.

ملاحظة: تم استلام البضاعة كاملة، والمبلغ الإجمالي المستحق هو 270.27 يورو شامل ضريبة القيمة المضافة.
(Note: all goods were received in full; the total amount due is EUR 270.27 including VAT.)`
  }
];

const STOP = new Set("a an and are as at be been being but by can could did do does for from had has have how i if in into is it its may might must my no not of on or our should so than that the their them then there these they this to was were what when where which who why will with would you your".split(" "));
const AR_STOP = new Set("ما هو هي في من على عن الى هل تم مع هذا هذه التي الذي كل أو او".split(" "));

function normalizeText(s) {
  return s.toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[إأآٱ]/g, "ا")
    .replace(/ى/g, "ي")
    .replace(/ة/g, "ه")
    .replace(/ـ/g, "")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

function stem(w) {
  if (/[\u0600-\u06ff]/.test(w)) {
    return w.replace(/^ال/, "").replace(/(ات|ون|ين|ها|هم|ه)$/, "");
  }
  w = w.replace(/^(.{3,}?)ies$/, "$1y");
  w = w.replace(/(ss|us|is)$/, "$1");
  w = w.replace(/(.{3,}?)(ing|ed|es|s)$/, "$1");
  w = w.replace(/(.{3,}?)(ation|tion|sion|ment|al)$/, "$1");
  w = w.replace(/(.{4,}?)y$/, "$1i");
  w = w.replace(/(.{6,}?)e$/, "$1");
  return w.slice(0, 6);
}

function tokenize(s) {
  return normalizeText(s).split(" ").filter(w => w && !STOP.has(w) && !AR_STOP.has(w)).map(stem).filter(Boolean);
}

const CHUNKS = [];
SAMPLE_DOCS.forEach(doc => {
  doc.text.split(/\n\s*\n/).forEach((para, i) => {
    const body = para.trim();
    if (!body) return;
    const lines = body.split("\n");
    const section = lines[0].length < 90 ? lines[0] : "Passage " + (i + 1);
    CHUNKS.push({ docId: doc.id, docTitle: doc.title, kind: doc.kind, section, text: body, tokens: tokenize(body), order: i });
  });
});

const DF = {};
CHUNKS.forEach(c => {
  new Set(c.tokens).forEach(t => { DF[t] = (DF[t] || 0) + 1; });
});

function retrieve(query, limit = 4) {
  const q = tokenize(query);
  if (!q.length) return [];
  const qSet = new Set(q);
  const normQuery = normalizeText(query);
  const scored = CHUNKS.map(c => {
    let score = 0;
    const counts = {};
    c.tokens.forEach(t => { counts[t] = (counts[t] || 0) + 1; });
    qSet.forEach(t => {
      if (counts[t]) score += (1 + Math.log(counts[t])) * (1 + Math.log((CHUNKS.length + 1) / ((DF[t] || 0) + 1)));
    });
    if (normQuery.length > 8 && normalizeText(c.text).includes(normQuery)) score += 3.2;
    if (qSet.size && c.tokens.length) {
      const overlap = [...qSet].filter(t => counts[t]).length / qSet.size;
      score += overlap * 1.4;
    }
    score /= Math.sqrt(c.tokens.length / 70 + 1);
    return { ...c, score };
  }).filter(c => c.score > 0.05).sort((a, b) => b.score - a.score).slice(0, limit);
  const top = scored.length ? scored[0].score : 1;
  return scored.map((c, i) => ({ ...c, rank: i + 1, match: Math.max(42, Math.min(98, Math.round(58 + 40 * (c.score / top)))) }));
}

const TEMPLATES = {
  renewal: {
    doc: "agreement",
    section: "Section 2",
    text: "The initial term ends on 28 February 2027. After that, the agreement renews automatically for successive 12-month terms unless either party gives written notice at least 60 days before the end of the current term. A renewal does not change prices unless both parties sign a written amendment."
  },
  delivery: {
    doc: "review",
    section: "Delivery performance",
    text: "On-time delivery fell to 88% because congestion at the Port of Rotterdam added four to seven days to inbound shipments in August, and two orders were held at customs for nine days because a certificate of origin was missing. After adding a second carrier and buffer stock, delivery returned to 94% in the final three weeks of September."
  },
  invoice: {
    doc: "invoice",
    section: "Line items",
    text: "Invoice 2026-114 totals EUR 270.27: a subtotal of EUR 257.40 plus 5% VAT of EUR 12.87. Payment is due 30 days after the invoice date."
  },
  arabic: {
    doc: "invoice",
    section: "ملاحظة",
    text: "إجمالي الفاتورة 2026-114 هو 270.27 يورو، شامل ضريبة القيمة المضافة: 257.40 يورو قيمة البضاعة والشحن، و12.87 يورو ضريبة. تاريخ الاستحقاق بعد 30 يوماً من تاريخ الفاتورة."
  }
};

function detectTemplate(query) {
  const n = normalizeText(query);
  if (/[\u0600-\u06ff]/.test(query) && (n.includes("اجمالي") || n.includes("فاتور"))) return "arabic";
  if (n.includes("renew") && (n.includes("notice") || n.includes("term") || n.includes("agreement"))) return "renewal";
  if (n.includes("deliver") && (n.includes("late") || n.includes("below") || n.includes("q3") || n.includes("target") || n.includes("why"))) return "delivery";
  if (n.includes("invoice") && (n.includes("total") || n.includes("vat") || n.includes("amount") || n.includes("270"))) return "invoice";
  return null;
}

function preferSource(sources, template) {
  if (!template) return sources;
  const idx = sources.findIndex(s => s.docId === template.doc && s.section.toLowerCase().includes(template.section.toLowerCase()));
  if (idx <= 0) return sources;
  const copy = [...sources];
  const [hit] = copy.splice(idx, 1);
  copy.unshift(hit);
  return copy.map((s, i) => ({ ...s, rank: i + 1 }));
}

const SUGGESTED = [
  { label: "When does the agreement renew?", q: "When does the supplier agreement renew, and how much notice is needed?" },
  { label: "Why was delivery late in Q3?", q: "Why was on-time delivery below target in Q3?" },
  { label: "What is the invoice total?", q: "What is the total on invoice 2026-114, including VAT?" },
  { label: "ما هو إجمالي الفاتورة؟", q: "ما هو إجمالي الفاتورة؟" }
];

const chatLog = $("#chatLog");
const chatForm = $("#chatForm");
const chatInput = $("#chatInput");
const sendBtn = $("#sendBtn");
const sourcesBox = $("#demoSources");
let demoBusy = false;

function esc(s) {
  return s.replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function addMessage(role, html) {
  const wrap = document.createElement("div");
  wrap.className = "msg " + role;
  if (role === "assistant") {
    wrap.innerHTML = `<span class="avatar" aria-hidden="true">D</span><div class="bubble" dir="auto">${html}</div>`;
  } else {
    wrap.innerHTML = `<div class="bubble" dir="auto">${esc(html)}</div>`;
  }
  chatLog.appendChild(wrap);
  chatLog.scrollTop = chatLog.scrollHeight;
  return wrap;
}

function citeButton(n = 1) {
  return `<button class="cite" type="button" data-n="${n}" aria-label="Jump to source ${n}">${n}</button>`;
}

function renderSources(sources) {
  if (!sources.length) {
    sourcesBox.innerHTML = `<p style="color:var(--faint);font-size:.75rem;margin:0">No matching passage in the three sample documents. Try a different question.</p>`;
    return;
  }
  sourcesBox.innerHTML = sources.map(s => `
    <div class="source ${s.rank === 1 ? "top" : ""}" id="source-${s.rank}">
      <div class="s-row"><b>[${s.rank}] ${esc(s.docTitle)}</b><span class="match">${s.match}% match</span></div>
      <div class="s-meta">${esc(s.section)} · ${esc(s.kind)}</div>
      <p>${esc(s.text.replace(/\s+/g, " ").slice(0, 190))}${s.text.length > 190 ? "…" : ""}</p>
    </div>`).join("");
}

function splitSentences(text) {
  const clean = text.replace(/\s+/g, " ");
  return (clean.match(/[^.!?]+[.!?]+/g) || [clean]).map(s => s.trim()).filter(s => s.length > 25);
}

function genericAnswer(question, sources) {
  if (!sources.length) {
    return "I could not find that in these three sample documents. Try one of the suggested questions, or ask about renewal, delivery, the invoice total, or the Arabic note.";
  }
  const q = new Set(tokenize(question));
  const top = sources[0];
  const sentences = splitSentences(top.text);
  const ranked = sentences.map((s, i) => {
    const overlap = tokenize(s).filter(t => q.has(t)).length;
    return { s, i, overlap };
  }).sort((a, b) => b.overlap - a.overlap || a.i - b.i).slice(0, 2).sort((a, b) => a.i - b.i);
  let picked = ranked.map(r => r.s).join(" ");
  if (!picked) picked = sentences.slice(0, 2).join(" ");
  if (picked.length > 430) picked = picked.slice(0, 410).replace(/\s+\S*$/, "") + "…";
  return `The most relevant passage says: “${picked}”`;
}

function answer(question) {
  const templateKey = detectTemplate(question);
  const template = templateKey ? TEMPLATES[templateKey] : null;
  let sources = retrieve(question);
  sources = preferSource(sources, template);
  if (template && sources.length && sources[0].docId === template.doc) {
    return { html: `${esc(template.text)} ${citeButton(1)}`, sources };
  }
  return { html: `${esc(genericAnswer(question, sources))} ${sources.length ? citeButton(1) : ""}`, sources };
}

function ask(question) {
  if (demoBusy) return;
  const q = question.trim();
  if (!q) return;
  demoBusy = true;
  sendBtn.disabled = true;
  chatInput.value = "";
  addMessage("user", q);
  const thinking = addMessage("assistant", `<span class="dots" aria-label="Searching"><i></i><i></i><i></i></span>`);
  const delay = reduceMotion ? 60 : 620;
  setTimeout(() => {
    const result = answer(q);
    thinking.querySelector(".bubble").innerHTML = result.html;
    renderSources(result.sources);
    chatLog.scrollTop = chatLog.scrollHeight;
    demoBusy = false;
    sendBtn.disabled = false;
    chatInput.focus();
  }, delay);
}

chatForm.addEventListener("submit", e => {
  e.preventDefault();
  ask(chatInput.value);
});

chatLog.addEventListener("click", e => {
  const btn = e.target.closest(".cite");
  if (!btn) return;
  const card = document.getElementById("source-" + btn.dataset.n);
  if (!card) return;
  card.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest" });
  card.classList.remove("flash");
  void card.offsetWidth;
  card.classList.add("flash");
});

const suggestions = $("#suggestions");
SUGGESTED.forEach(s => {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "chip";
  b.textContent = s.label;
  b.addEventListener("click", () => ask(s.q));
  suggestions.appendChild(b);
});

addMessage("assistant", "Ask me about the three documents on the right. I will find the passages, quote them, and show you exactly where each answer comes from.");

const CONCEPTS = [
  { label: "Document", desc: "A file you own: a contract, a report, a note or a scan." },
  { label: "Text extraction", desc: "Daftar reads the text page by page. Scans go through OCR." },
  { label: "Passages", desc: "Long text is split into short passages, so a citation can point to the right page." },
  { label: "Embeddings", desc: "Each passage becomes a list of numbers that represents its meaning." },
  { label: "Keyword index", desc: "Exact names, codes and dates are indexed too, so search never loses them." },
  { label: "Your question", desc: "Your question is turned into the same kind of numbers as the passages." },
  { label: "Retrieval", desc: "Daftar fetches the passages closest to your question." },
  { label: "Rerank", desc: "Weak and duplicate matches are dropped before the model sees anything." },
  { label: "Grounded answer", desc: "The local model writes an answer using only those passages." },
  { label: "Citations", desc: "Every answer points back to its passage and match score, so you can check it." }
];

(function lab() {
  const canvas = $("#labCanvas");
  if (!canvas) return;
  const stepEl = $("#labStep");
  const scoreEl = $("#labScore");
  const scoreBadge = $("#labScoreBadge");
  const barEl = $("#labBar");
  const builtEl = $("#labBuilt");
  const streakEl = $("#labStreak");
  const feedbackEl = $("#labFeedback");
  const titleEl = $("#labTitle");
  const descEl = $("#labDesc");
  const questionEl = $("#labQuestion");
  const keysEl = $("#labKeys");
  const resetBtn = $("#labReset");
  const speakBtn = $("#labSpeak");
  const transcriptEl = $("#labTranscript");

  const nodes3d = fibSphere(CONCEPTS.length, 1);
  const edges = [];
  for (let i = 0; i < CONCEPTS.length - 1; i++) {
    edges.push([i, i + 1]);
    if (i + 3 < CONCEPTS.length) edges.push([i, i + 3]);
  }
  let state = setupCanvas(canvas);
  let progress = 0;
  let streak = 0;
  let yaw = 0.5;
  let pitch = -0.15;
  let dragYaw = 0, dragPitch = 0;
  let dragging = false;
  let lastX = 0, lastY = 0;
  let moved = 0;
  let hover = -1;
  let hit = [];
  let shakeUntil = 0;
  let raf = 0;
  let visible = true;
  let keyOrder = [];

  function shuffle(arr) {
    const a = [...arr];
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  function resetKeys() {
    keyOrder = shuffle(CONCEPTS.map((_, i) => i));
    keysEl.innerHTML = "";
    keyOrder.forEach(i => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "key-chip";
      b.textContent = CONCEPTS[i].label;
      b.dataset.i = i;
      b.addEventListener("click", () => pick(i));
      keysEl.appendChild(b);
    });
  }

  function updateKeys() {
    $$(".key-chip", keysEl).forEach(b => {
      const i = Number(b.dataset.i);
      b.classList.toggle("done", i < progress);
      b.disabled = i < progress;
    });
  }

  function setPanel() {
    stepEl.textContent = Math.min(progress + 1, 10);
    scoreEl.textContent = progress * 10;
    barEl.style.width = (progress / CONCEPTS.length) * 100 + "%";
    builtEl.textContent = progress;
    streakEl.textContent = "Streak " + streak;
    scoreBadge.setAttribute("aria-label", "Score " + progress * 10);
    updateKeys();
    if (progress >= CONCEPTS.length) {
      questionEl.textContent = "Network complete";
      titleEl.textContent = "You built a retrieval brain.";
      descEl.textContent = "Ten concepts, one pipeline: your files stay local, the passages are indexed, the model writes only from evidence, and every answer carries a citation.";
      feedbackEl.className = "lab-feedback ok";
      feedbackEl.textContent = "All ten steps are connected. Press “Let the brain speak” to hear the summary.";
      speakBtn.disabled = false;
      return;
    }
    questionEl.textContent = `Step ${progress + 1} of 10 — which concept is this?`;
    titleEl.textContent = "Which concept matches this definition?";
    descEl.textContent = `“${CONCEPTS[progress].desc}”`;
    speakBtn.disabled = true;
  }

  function pick(index) {
    if (index < 0 || index >= CONCEPTS.length) return;
    if (index < progress) {
      feedbackEl.className = "lab-feedback";
      feedbackEl.textContent = `Already connected: ${CONCEPTS[index].label}.`;
      return;
    }
    if (index === progress) {
      progress++;
      streak++;
      feedbackEl.className = "lab-feedback ok";
      feedbackEl.textContent = `Correct — ${CONCEPTS[index].label}. ${CONCEPTS[index].desc}`;
      setPanel();
      draw();
      if (progress === CONCEPTS.length) {
        feedbackEl.textContent = "Correct — Citations. Every answer points back to its passage and match score. The network is complete.";
      }
      return;
    }
    streak = 0;
    shakeUntil = performance.now() + 320;
    feedbackEl.className = "lab-feedback bad";
    feedbackEl.textContent = `Not yet — that node is “${CONCEPTS[index].label}”. Read the definition again and look for the matching label.`;
    setPanel();
    draw();
  }

  function draw() {
    const { ctx, w, h } = state;
    const shake = performance.now() < shakeUntil ? Math.sin(performance.now() / 18) * 6 : 0;
    ctx.clearRect(0, 0, w, h);
    const spread = Math.min(w, h) * 0.34;
    const fov = 2.5;
    const projected = nodes3d.map((p, i) => {
      const r = rotatePoint(p, yaw + dragYaw, pitch + dragPitch);
      return { ...projectPoint(r, w + shake, h, spread, fov), i };
    });
    hit = projected;
    ctx.lineWidth = 1.2;
    edges.forEach(([a, b]) => {
      const pa = projected[a], pb = projected[b];
      const done = a < progress && b < progress;
      const alpha = done ? 0.55 : 0.08;
      ctx.strokeStyle = done ? `rgba(45,212,191,${alpha})` : `rgba(147,165,171,${alpha})`;
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      ctx.lineTo(pb.x, pb.y);
      ctx.stroke();
    });
    projected
      .slice()
      .sort((a, b) => b.z - a.z)
      .forEach(p => {
        const i = p.i;
        const done = i < progress;
        const isHover = i === hover;
        const r = Math.max(5, 9 * p.scale);
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        if (done) {
          ctx.fillStyle = "rgba(45,212,191,.9)";
          ctx.shadowColor = "rgba(45,212,191,.9)";
          ctx.shadowBlur = 18;
        } else if (isHover) {
          ctx.fillStyle = "rgba(230,238,240,.22)";
          ctx.shadowColor = "rgba(230,238,240,.35)";
          ctx.shadowBlur = 12;
        } else {
          ctx.fillStyle = "rgba(26,35,40,.95)";
          ctx.shadowBlur = 0;
        }
        ctx.fill();
        ctx.shadowBlur = 0;
        ctx.strokeStyle = done ? "rgba(236,253,245,.85)" : "rgba(147,165,171,.55)";
        ctx.lineWidth = 1.2;
        ctx.stroke();
        ctx.font = `${Math.max(9, 10.5 * p.scale)}px system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillStyle = done ? "rgba(230,238,240,.95)" : isHover ? "rgba(230,238,240,.85)" : "rgba(147,165,171,.7)";
        ctx.fillText(CONCEPTS[i].label, p.x, p.y + r + 5);
      });
    if (progress === CONCEPTS.length) {
      ctx.beginPath();
      ctx.arc(w / 2, h / 2, Math.min(w, h) * 0.36, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(251,191,36,${0.12 + 0.08 * Math.sin(performance.now() / 500)})`;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  }

  function frame() {
    raf = 0;
    if (!visible || document.hidden) return;
    if (!reduceMotion) {
      if (!dragging) yaw += 0.0022;
      draw();
      raf = requestAnimationFrame(frame);
    }
  }

  function kick() {
    if (reduceMotion) draw();
    else if (!raf) raf = requestAnimationFrame(frame);
  }

  function pointFromEvent(e) {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  function nodeAt(x, y) {
    let best = -1;
    let bestD = 1e9;
    hit.forEach(p => {
      const d = Math.hypot(p.x - x, p.y - y);
      const radius = Math.max(18, 22 * p.scale);
      if (d < radius && d < bestD) {
        bestD = d;
        best = p.i;
      }
    });
    return best;
  }

  canvas.addEventListener("pointerdown", e => {
    dragging = true;
    moved = 0;
    lastX = e.clientX;
    lastY = e.clientY;
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", e => {
    const p = pointFromEvent(e);
    hover = nodeAt(p.x, p.y);
    if (dragging) {
      const dx = e.clientX - lastX;
      const dy = e.clientY - lastY;
      moved += Math.abs(dx) + Math.abs(dy);
      dragYaw += dx * 0.006;
      dragPitch += dy * 0.005;
      dragPitch = Math.max(-0.9, Math.min(0.9, dragPitch));
      lastX = e.clientX;
      lastY = e.clientY;
      if (reduceMotion) draw();
    } else if (reduceMotion) {
      draw();
    }
  });
  canvas.addEventListener("pointerup", e => {
    if (!dragging) return;
    dragging = false;
    if (moved < 7) {
      const p = pointFromEvent(e);
      const i = nodeAt(p.x, p.y);
      if (i >= 0) pick(i);
      else {
        feedbackEl.className = "lab-feedback";
        feedbackEl.textContent = "Click directly on a concept node.";
      }
    }
    if (reduceMotion) draw();
  });
  canvas.addEventListener("pointerleave", () => {
    dragging = false;
    hover = -1;
    if (reduceMotion) draw();
  });

  resetBtn.addEventListener("click", () => {
    progress = 0;
    streak = 0;
    dragYaw = 0;
    dragPitch = 0;
    transcriptEl.classList.remove("show");
    transcriptEl.textContent = "";
    speakBtn.textContent = "Let the brain speak";
    feedbackEl.className = "lab-feedback";
    feedbackEl.textContent = "Pick the concept that matches the definition.";
    resetKeys();
    setPanel();
    draw();
  });

  const SUMMARY = "Your documents stayed on your computer. Daftar read them, split them into passages, turned meaning into numbers, found the closest passages, and wrote an answer grounded in them. Every claim carries a citation, so you can check the source. That is retrieval-augmented generation, running locally.";

  speakBtn.addEventListener("click", () => {
    transcriptEl.textContent = SUMMARY;
    transcriptEl.classList.add("show");
    if (!("speechSynthesis" in window)) {
      feedbackEl.className = "lab-feedback";
      feedbackEl.textContent = "This browser has no speech voice. The transcript is shown below.";
      return;
    }
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(SUMMARY);
    u.rate = 1.02;
    u.pitch = 0.95;
    speakBtn.textContent = "Speaking…";
    const resetSpeak = () => { speakBtn.textContent = "Let the brain speak"; };
    u.onend = resetSpeak;
    u.onerror = resetSpeak;
    window.speechSynthesis.speak(u);
    setTimeout(() => {
      if (!window.speechSynthesis.speaking) {
        resetSpeak();
        if (!window.speechSynthesis.getVoices().length) {
          feedbackEl.className = "lab-feedback";
          feedbackEl.textContent = "This browser has no speech voice. The transcript is shown below.";
        }
      }
    }, 1200);
  });

  const vio = new IntersectionObserver(entries => {
    visible = entries[0].isIntersecting;
    if (visible) kick();
  }, { threshold: 0 });
  vio.observe(canvas);

  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      state = setupCanvas(canvas);
      draw();
    }, 150);
  });

  resetKeys();
  setPanel();
  kick();
})();

const QUIZ = [
  {
    q: "What does RAG stand for?",
    options: ["Rapid Answer Generation", "Retrieval-Augmented Generation", "Random Access Grouping", "Remote Assistant Gateway"],
    correct: 1,
    explain: "RAG means the system retrieves relevant passages first, then generates an answer from them. Retrieval is the part that keeps the answer grounded."
  },
  {
    q: "Where does Daftar run the language model by default?",
    options: ["On your own computer", "In a Daftar cloud account", "Inside the browser tab", "On the document provider's server"],
    correct: 0,
    explain: "The default is local: the built-in engine runs on your machine. A remote engine is possible, but it is an explicit choice."
  },
  {
    q: "What is a passage (chunk)?",
    options: ["A password for the index", "A short section of a document used for search", "A compressed image of a page", "A type of language model"],
    correct: 1,
    explain: "Long documents are split into short passages. Retrieval picks the passages closest to the question, which makes precise citations possible."
  },
  {
    q: "Why keep a keyword index next to the embeddings?",
    options: ["To make the app larger", "To translate documents", "To catch exact names, codes and dates", "To compress the model"],
    correct: 2,
    explain: "Embeddings capture meaning but can blur exact strings. A keyword index keeps part numbers, names and dates findable. Daftar uses both."
  },
  {
    q: "What does a citation in an answer let you do?",
    options: ["Encrypt the document", "Check the exact source passage", "Increase the model size", "Skip the retrieval step"],
    correct: 1,
    explain: "The citation is the audit trail. You can open the passage, compare it with the answer and decide whether the answer is supported."
  },
  {
    q: "What does “local-first” mean here?",
    options: ["The app only works in one country", "Your data stays on your machine and the app works without a cloud service", "The interface is translated locally", "The model is trained on local news"],
    correct: 1,
    explain: "Local-first means the core work happens on your device. Daftar does not need an account or a cloud API to answer questions about your files."
  },
  {
    q: "What happens when you add a scanned PDF?",
    options: ["Nothing — scans are ignored", "The text is extracted with OCR", "It is uploaded to a search engine", "It is converted into an image caption"],
    correct: 1,
    explain: "Scans contain pixels, not text. OCR turns the page into text so it can be indexed. In Daftar this needs a vision model such as PaddleOCR-VL through Ollama."
  },
  {
    q: "Why is prompt injection a risk for document chat?",
    options: ["A document can contain instructions that try to hijack the model", "It makes the PDF larger", "It changes the screen resolution", "It stops the OCR model from loading"],
    correct: 0,
    explain: "Retrieved text is untrusted input. A malicious document might say “ignore your rules”. Daftar treats documents as data, keeps instructions separate and was tested against strong attacks."
  },
  {
    q: "What is the first thing Daftar does with a new document?",
    options: ["Uploads it", "Extracts text, then splits and indexes it", "Asks the model to memorise it", "Deletes the original"],
    correct: 1,
    explain: "The document is read, its text extracted, then split into passages and indexed. The original file is not modified."
  },
  {
    q: "An answer has a low match score. What is the best next step?",
    options: ["Trust it anyway", "Open the cited passage or rephrase the question", "Delete the collection", "Turn off citations"],
    correct: 1,
    explain: "The score is a relevance signal, not a truth guarantee. Open the source, rephrase the question, or add a better document before relying on the answer."
  }
];

(function quiz() {
  const card = $("#quizCard");
  const scoreEl = $("#quizScore");
  const tierEl = $("#quizTier");
  const barEl = $("#quizBar");
  const progressEl = $("#quizProgress");
  let index = 0;
  let score = 0;
  let answered = false;

  function tierFor(n) {
    if (n === 10) return "Retrieval engineer. You can explain the whole pipeline.";
    if (n >= 8) return "Grounded expert. You understand the important parts.";
    if (n >= 5) return "Solid foundation. Review the citations and the pipeline.";
    return "Curious beginner. The beginner guide is the next step.";
  }

  function updateSide() {
    scoreEl.textContent = score;
    tierEl.textContent = answered || index > 0 ? tierFor(score) : "Answer the first question to begin.";
    const done = index + (answered ? 1 : 0);
    barEl.style.width = (done / QUIZ.length) * 100 + "%";
    progressEl.textContent = `${Math.min(done, QUIZ.length)} / ${QUIZ.length}`;
  }

  function render() {
    answered = false;
    if (index >= QUIZ.length) {
      const pct = Math.round((score / QUIZ.length) * 100);
      card.innerHTML = `
        <div class="result">
          <div class="ring">${score}<span style="font-size:.9rem;color:var(--faint)">/10</span></div>
          <h3>${score === 10 ? "Perfect score." : score >= 8 ? "Strong result." : score >= 5 ? "Good start." : "Keep exploring."}</h3>
          <p>${tierFor(score)} You answered ${pct}% correctly. Every explanation is above, and the beginner guide in the repository goes deeper.</p>
          <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin-top:18px">
            <button class="btn primary" type="button" id="quizRestart">Try again</button>
            <a class="btn" href="#how">Review the pipeline</a>
          </div>
        </div>`;
      $("#quizRestart").addEventListener("click", () => {
        index = 0;
        score = 0;
        answered = false;
        updateSide();
        render();
      });
      updateSide();
      return;
    }
    const item = QUIZ[index];
    card.innerHTML = `
      <div class="qnum">Question ${index + 1} of ${QUIZ.length}</div>
      <h3>${esc(item.q)}</h3>
      <div class="options" role="group" aria-label="Answer options">
        ${item.options.map((o, i) => `<button class="option" type="button" data-i="${i}"><span class="letter">${"ABCD"[i]}</span><span>${esc(o)}</span></button>`).join("")}
      </div>
      <div class="explain" id="quizExplain"></div>
      <div class="quiz-foot"><span class="hint">Choose one answer. The explanation appears immediately.</span><button class="btn primary" type="button" id="quizNext" disabled>Next</button></div>`;
    updateSide();
    $$(".option", card).forEach(btn => {
      btn.addEventListener("click", () => choose(Number(btn.dataset.i)));
    });
    $("#quizNext").addEventListener("click", () => {
      index++;
      render();
    });
  }

  function choose(i) {
    if (answered) return;
    answered = true;
    const item = QUIZ[index];
    const options = $$(".option", card);
    options.forEach((btn, n) => {
      btn.disabled = true;
      if (n === item.correct) btn.classList.add("correct");
      if (n === i && i !== item.correct) btn.classList.add("wrong");
    });
    if (i === item.correct) score++;
    const ex = $("#quizExplain");
    ex.innerHTML = `<b>${i === item.correct ? "Correct." : "Not quite."}</b> ${esc(item.explain)}`;
    ex.classList.add("show");
    const next = $("#quizNext");
    next.disabled = false;
    next.textContent = index === QUIZ.length - 1 ? "See result" : "Next";
    updateSide();
    scoreEl.textContent = score;
    tierEl.textContent = tierFor(score);
  }

  render();
})();

(function roi() {
  const searches = $("#roiSearches");
  const minutes = $("#roiMinutes");
  const rate = $("#roiRate");
  const searchesOut = $("#roiSearchesOut");
  const minutesOut = $("#roiMinutesOut");
  const rateOut = $("#roiRateOut");
  const yearOut = $("#roiYear");
  const monthOut = $("#roiMonth");
  const hoursOut = $("#roiHours");
  const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  function update() {
    const s = Number(searches.value);
    const m = Number(minutes.value);
    const r = Number(rate.value);
    const hours = (s * m * 21) / 60;
    const month = hours * r;
    searchesOut.textContent = s;
    minutesOut.textContent = m;
    rateOut.textContent = money.format(r);
    hoursOut.textContent = hours.toFixed(1);
    monthOut.textContent = money.format(month);
    yearOut.textContent = money.format(month * 12);
  }
  [searches, minutes, rate].forEach(el => el.addEventListener("input", update));
  update();
})();
