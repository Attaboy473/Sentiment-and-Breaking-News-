/* MarketForge Demo frontend */
const $ = (sel) => document.querySelector(sel);
/* P2 security: string dari luar (RSS / Stockbit / error upstream) di-escape sebelum masuk HTML */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? esc(u) : null);
let STATE = null;
let WINDOW_H = 24;
let NEWS_FILTER = "ALL";

function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), ms);
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function netClass(v) {
  if (v > 0) return "net-pos";
  if (v < 0) return "net-neg";
  return "net-zero";
}

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("id-ID", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}

/* ---------- health ---------- */
function renderHealth(h) {
  const ev = STATE.events || [];
  const trig = ev.filter((e) => e.status === "TRIGGERED").length;
  $("#sideStats").innerHTML =
    `post tersimpan: <b>${h.posts_in_store}</b><br>` +
    `event (3 jam): <b>${ev.length}</b> (${trig} trigger)<br>` +
    `rekomendasi stale: <b>${(STATE.recommendations || []).filter((r) => r.stale).length}</b><br>` +
    `watchlist: <b>${h.watchlist.length}</b> ticker`;
}

/* ---------- recommendations ---------- */
function renderRecos(recs) {
  const el = $("#recoBoard");
  el.innerHTML = recs.map((r) => {
    let note = "";
    if (r.stale) note = `<div class="stale-note">STALE — ${esc(r.stale_reason || "material event")}</div>`;
    else if (r.reassessed_at) note = `<div class="reassessed">Reassessed ${fmtTime(r.reassessed_at)} — ${esc(r.reassessed_note || "")}</div>`;
    return `<div class="reco-card ${r.stale ? "stale" : ""}">
      ${r.stale ? '<span class="stale-tag">STALE</span>' : ""}
      <div class="reco-ticker">${esc(r.ticker)}</div>
      <div class="reco-score">${r.score}</div>
      <div class="reco-label ${r.label}">${r.label} · grade ${r.grade}</div>
      ${note}
    </div>`;
  }).join("");
}

/* ---------- sentiment table ---------- */
function renderSentiment(rows) {
  const el = $("#sentTable");
  if (!rows.length) { el.innerHTML = '<div class="empty">Belum ada data — klik "Poll sekarang".</div>'; return; }
  const sorted = [...rows].sort((a, b) => b.mention_count - a.mention_count);
  el.innerHTML = `<table>
  <thead><tr>
    <th>Ticker</th><th>Mention</th><th>Bull/Bear</th><th>Net</th><th>Authors</th><th>Velocity</th><th>Fresh</th><th>Filter</th><th>Engage</th>
  </tr></thead>
    <tbody>
    ${sorted.map((r) => {
      const thin = !r.sample_ok;
      const maxEng = Math.max(1, ...sorted.map((x) => x.engagement_weighted));
      const w = Math.round(100 * r.engagement_weighted / maxEng);
      const color = r.net_sentiment >= 0 ? "var(--green)" : "var(--red)";
      const fresh = r.latest_post_at ? Math.round((Date.now() - new Date(r.latest_post_at).getTime()) / 60000) : null;
      const freshTxt = fresh == null ? "—" : (fresh < 60 ? fresh + " mnt" : Math.round(fresh / 60) + " jam");
      const freshCls = fresh == null ? "" : (fresh <= 60 ? "fresh-ok" : (fresh <= 180 ? "fresh-mid" : "fresh-old"));
      return `<tr class="${thin ? "thin-sample" : ""}">
        <td><span class="tkr" data-tkr="${esc(r.ticker)}" title="Klik: lihat stream asli ${esc(r.ticker)} di panel bawah News"><span class="tkr-logo"><img src="https://assets.stockbit.com/logos/companies/${esc(r.ticker)}.png" alt="" onerror="this.parentNode.style.display='none'"></span><b>${esc(r.ticker)}</b></span>${thin ? ' <span class="pill">sample tipis</span>' : ""}</td>
        <td>${r.mention_count}</td>
        <td>${r.bullish}/${r.bearish}</td>
        <td class="${netClass(r.net_sentiment)}">${r.net_sentiment > 0 ? "+" : ""}${r.net_sentiment}</td>
        <td>${r.unique_authors}</td>
        <td class="${r.mention_velocity > 0 ? "vel-up" : ""}">${r.mention_velocity == null ? "—" : (r.mention_velocity > 0 ? "+" : "") + r.mention_velocity + "%"}</td>
        <td class="${freshCls}" title="umur post terbaru di window ini">${freshTxt}</td>
        <td title="post dibuang: akun resmi/isreport/isnews/terlalu pendek">${r.noise_filtered}</td>
        <td><span class="meter"><span style="width:${w}%;background:${color}"></span></span></td>
      </tr>`;
    }).join("")}
    </tbody></table>
    <p class="hint" style="margin-top:8px">${sorted.filter((r) => !r.sample_ok).length} dari ${sorted.length} ticker masih sample tipis (&lt;5 post) — tampil redup, jangan dinilai.</p>`;
}

/* ---------- events (kartu ala News Preview) ---------- */
function relTime(iso) {
  const ms = Date.now() - new Date(iso).getTime();
  const m = Math.max(0, Math.round(ms / 60000));
  if (m < 60) return `${m} menit lalu`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} jam lalu`;
  return `${Math.round(h / 24)} hari lalu`;
}

function wibTime(iso) {
  try {
    return new Date(iso).toLocaleString("id-ID", {
      timeZone: "Asia/Jakarta", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    }) + " WIB";
  } catch { return iso || "—"; }
}

function renderNewsChips(events) {
  const counts = { ALL: events.length };
  for (const e of events) counts[e.severity] = (counts[e.severity] || 0) + 1;
  const order = ["ALL", "CRITICAL", "HIGH", "MEDIUM", "INFO"];
  $("#newsChips").innerHTML = order.filter((o) => counts[o])
    .map((o) => `<button class="chip ${NEWS_FILTER === o ? "active" : ""}" data-sev="${o}">${o === "ALL" ? "Semua" : o} · ${counts[o]}</button>`)
    .join("");
  document.querySelectorAll("#newsChips .chip").forEach((c) => {
    c.addEventListener("click", () => { NEWS_FILTER = c.dataset.sev; if (STATE) renderEvents(STATE.events); });
  });
}

function renderEvents(events) {
  renderNewsChips(events);
  const list = NEWS_FILTER === "ALL" ? events : events.filter((e) => e.severity === NEWS_FILTER);
  const el = $("#eventList");
  if (!list.length) {
    el.innerHTML = '<div class="empty">Belum ada kandidat event (score ≥45 dalam 3 jam terakhir). Klik "Simulasi breaking event" untuk trigger alur stale→reassess.</div>';
    return;
  }
  el.innerHTML = list.map((e) => {
    const kws = Object.entries(e.matched_keywords || {}).map(([k, v]) => `${k} ×${v}`).join(", ");
    const sectors = (e.sectors || []).join(", ");
    const desc = [
      kws ? `Keyword material: ${kws}` : null,
      sectors ? `Sektor: ${sectors}` : null,
    ].filter(Boolean).join(" · ") || "Tidak ada keyword material yang match";
    const href = safeUrl(e.source_url);
    const title = href
      ? `<a class="news-title" href="${href}" target="_blank" rel="noopener noreferrer">${esc(e.headline)}</a>`
      : `<div class="news-title">${esc(e.headline)}</div>`;
    return `<div class="news-card status-${e.status}" data-hash="${e.event_hash}">
      <div class="news-main">
        ${title}
        <div class="news-meta">
          <span class="badge sev-${e.severity}">${e.severity}</span>
          <span class="badge status-${e.status}">${e.status}</span>
          <span>${esc(e.source_name)}</span><span class="dotsep">·</span>
          <span>${relTime(e.published_at)} (${wibTime(e.published_at)})</span>
          <span class="cashtags">${(e.tickers || []).map((t) => `<span class="cashtag">$${esc(t)}</span>`).join("")}</span>
        </div>
        <div class="news-desc">${desc} · confidence ${e.confidence}</div>
      </div>
      <div class="news-score">
        <span class="score-label">Skor</span>
        <span class="score-num sev-${e.severity}">${e.rule_score}</span>
      </div>
    </div>`;
  }).join("");
  el.querySelectorAll(".news-card").forEach((card) => {
    card.addEventListener("click", (ev) => {
      if (ev.target.closest("a")) return; // klik link sumber gak buka modal
      openEvent(card.dataset.hash);
    });
  });
}

async function openEvent(ehash) {
  try {
    const j = await api("/api/event/" + ehash);
    const e = j.event;
    $("#mTitle").textContent = `${e.headline}`;
    const stale = (e.stale_recommendations || []).map((r) =>
      `<tr><td><b>${r.ticker}</b></td><td class="net-neg">${r.stale ? "STALE" : "clear"}</td>
       <td>${r.reassessed_at ? "reassessed " + fmtTime(r.reassessed_at) : "—"}</td></tr>`).join("");
    $("#mBody").innerHTML = `
      <div class="kv">
        <b>Source</b><span>${esc(e.source_name)} ${safeUrl(e.source_url) ? `— <a href="${safeUrl(e.source_url)}" target="_blank" rel="noopener noreferrer">link</a>` : ""}</span>
        <b>Published</b><span>${fmtTime(e.published_at)}</span>
        <b>Detected</b><span>${fmtTime(e.detected_at)}</span>
        <b>Rule score</b><span>${e.rule_score}</span>
        <b>Severity</b><span class="sev-${e.severity}">${e.severity}</span>
        <b>Confidence</b><span>${e.confidence} (proxy rule-only)</span>
        <b>Status</b><span>${e.status}</span>
        <b>Keywords</b><span>${Object.entries(e.matched_keywords || {}).map(([k, v]) => `${k} (${v})`).join(", ") || "—"}</span>
      </div>
      <b>Score reasoning (audit trail):</b>
      <ul class="reason-list">${(e.reasons || []).map((r) => `<li>${r}</li>`).join("")}</ul>
      <b>Affected tickers:</b> ${(e.tickers || []).map((t) => `<span class="pill ticker">$${t}</span>`).join("") || "—"}
      ${stale ? `<p style="margin-top:10px"><b>Recommendation flow:</b></p>
        <table><thead><tr><th>Ticker</th><th>Status</th><th>Reassessment</th></tr></thead><tbody>${stale}</tbody></table>` : ""}`;
    $("#modal").classList.remove("hidden");
  } catch (err) { toast("Gagal buka event: " + err.message); }
}

/* ---------- news hub: artikel ala News Preview + chatter ---------- */
let CAT_FILTER = "ALL";

function renderNewsStats(st) {
  const el = $("#newsStats");
  if (!el) return;
  el.innerHTML = `
    <div class="nstat"><b>${st.fetched}</b><span>artikel ter-scan</span></div>
    <div class="nstat"><b>${st.dividen}</b><span>kategori Dividen</span></div>
    <div class="nstat"><b>${st.lainnya}</b><span>kategori Lainnya</span></div>
    <div class="nstat"><b>${(STATE.events || []).length}</b><span>breaking event</span></div>
    <div class="nstat"><b>${(STATE.recommendations || []).filter((r) => r.stale).length}</b><span>rekomendasi stale</span></div>`;
}

function renderArticles(articles) {
  const chipsEl = $("#newsCatChips");
  const divn = articles.filter((a) => a.category === "Dividen").length;
  chipsEl.innerHTML = ["ALL", "Dividen", "Lainnya"].map((c) => {
    const n = c === "ALL" ? articles.length : (c === "Dividen" ? divn : articles.length - divn);
    return `<button class="chip ${CAT_FILTER === c ? "active" : ""}" data-cat="${c}">${c} · ${n}</button>`;
  }).join("");
  chipsEl.querySelectorAll(".chip").forEach((ch) => {
    ch.addEventListener("click", () => { CAT_FILTER = ch.dataset.cat; if (STATE) renderArticles(STATE.articles || []); });
  });

  const list = CAT_FILTER === "ALL" ? articles : articles.filter((a) => a.category === CAT_FILTER);
  const el = $("#articleList");
  if (!list.length) { el.innerHTML = '<div class="empty">Belum ada artikel — klik "Poll sekarang" untuk scan RSS.</div>'; return; }
  el.innerHTML = list.map((a) => {
    const ahref = safeUrl(a.source_url);
    const title = ahref
      ? `<a class="news-title" href="${ahref}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a>`
      : `<div class="news-title">${esc(a.title)}</div>`;
    const tick = (a.tickers || []).length
      ? ` <span class="cashtags">${a.tickers.map((t) => `<span class="cashtag">$${esc(t)}</span>`).join("")}</span>` : "";
    return `<div class="news-card article">
      <div class="news-main">
        ${title}
        <div class="news-meta">
          <span class="badge cat-${a.category === "Dividen" ? "DIV" : "LAIN"}">${a.category}</span>
          <span>${esc(a.source_name)}</span><span class="dotsep">·</span>
          <span>${relTime(a.published_at)} (${wibTime(a.published_at)})</span>${tick}
          ${a.sem_margin != null ? `<span class="dotsep">·</span><span class="sem-chip" title="Kedekatan makna ke arketipe kejadian material (MiniLM, pendukung — bukan penentu trigger)">SEM ${a.sem_margin >= 0 ? "+" : ""}${a.sem_margin.toFixed(2)} ${a.sem_label === "material" ? "material" : "non-material"}</span>` : ""}
        </div>
        ${a.summary ? `<div class="news-desc">${esc(a.summary.slice(0, 160))}…</div>` : ""}
      </div>
      <div class="news-score">
        <span class="score-label">Relevansi</span>
        <span class="score-num">${Math.round(a.rule_score)}</span>
      </div>
    </div>`;
  }).join("");
}


/* ---------- theme (DESIGN.md: html[data-theme="dark"], persist localStorage["mf.theme"]) ---------- */
const SUN = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const MOON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/></svg>';
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  const b = $("#btnTheme");
  if (b) b.innerHTML = t === "dark" ? SUN : MOON;
}
applyTheme(localStorage.getItem("mf.theme") || "light");
$("#btnTheme").addEventListener("click", () => {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  localStorage.setItem("mf.theme", next);
  applyTheme(next);
});

/* ---------- ticker click -> lihat stream asli (panel chatter di News) ---------- */
function wireTickerClicks() {
  document.querySelectorAll("#sentTable .tkr").forEach((el) => {
    el.onclick = () => showTickerStream(el.dataset.tkr);
  });
}

async function showTickerStream(ticker) {
  setView("news");
  const sel = $("#chatterTicker");
  if (sel && [...sel.options].some((o) => o.value === ticker)) sel.value = ticker;
  const head = $("#chatterHead");
  if (head) head.innerHTML = `Stream asli <span class="tkr-logo"><img src="https://assets.stockbit.com/logos/companies/${esc(ticker)}.png" alt="" onerror="this.parentNode.style.display='none'"></span><b>$${esc(ticker)}</b> <span class="muted">(12 post terbaru, dari collector)</span>`;
  try {
    await loadChatter();
    document.querySelector("#chatterTicker").scrollIntoView({ behavior: "smooth", block: "start" });
    toast(`Nampilin stream $${ticker} — klik judul post buat buka di Stockbit.`);
  } catch (err) { toast("Gagal load stream: " + err.message); }
}

async function loadChatter() {
  const sel = $("#chatterTicker");
  const ticker = sel.value;
  if (!ticker) return;
  try {
    const j = await api("/api/chatter?ticker=" + ticker);
    const el = $("#chatterList");
    el.innerHTML = j.posts.map((p) => {
      const h = p.semantic_hint;
      const hint = h
        ? `<div class="chatter-hint" title="Semantic: post ini mirip ${h.matches.length} post beropini yang sepakat arah (cosine >= 0.75). Hint makna — bukan pengganti label lexicon.">≈ ${esc(h.direction)} <span class="hint-sim">· mirip ${Math.round(h.sim * 100)}% · contoh: ${esc(h.matches[0].snippet.slice(0, 60))}</span></div>`
        : "";
      return `<div class="chatter-post">
        <div class="chatter-top">
          <span class="chatter-author">${esc(p.author)}</span>
          <span class="badge sent-${h ? h.direction : (p.sentiment || "neutral")}">${h ? "≈ " + h.direction : (p.sentiment || "neutral")}</span>
          <span class="chatter-time">${relTime(p.created_at)} · ${p.likes} suka · ${p.replies} balasan</span>
          ${safeUrl(p.url) ? `<a class="chatter-link" href="${safeUrl(p.url)}" target="_blank" rel="noopener noreferrer" title="Buka post asli di Stockbit — bukti datanya beneran dari stream">Buka di Stockbit</a>` : ""}
        </div>
        <div class="chatter-text">${esc((p.text || "").slice(0, 220))}</div>
        ${hint}
      </div>`;
    }).join("") || '<div class="empty">Belum ada post untuk ticker ini.</div>';
  } catch (err) { $("#chatterList").innerHTML = `<div class="empty">Gagal load chatter: ${err.message}</div>`; }
}

function initChatterSelector() {
  const sel = $("#chatterTicker");
  if (sel.options.length) return;
  for (const t of (STATE?.health.watchlist || [])) {
    const o = document.createElement("option");
    o.value = t; o.textContent = "$" + t;
    sel.appendChild(o);
  }
  sel.addEventListener("change", loadChatter);
  loadChatter();
}

/* ---------- semantic search ---------- */
function renderSearch(j) {
  const el = $("#searchResults");
  const modeEl = $("#searchMode");
  if (modeEl) {
    modeEl.textContent = j.mode === "semantic" ? "(semantic)" :
      j.mode === "literal" ? "(ticker)" :
      j.mode === "keyword" ? "(fallback kata kunci)" : "";
  }
  if (!j.results || !j.results.length) {
    el.innerHTML = '<div class="empty">Gak ada hasil. Coba kata lain.</div>';
    return;
  }
  el.innerHTML = j.results.map((r) => {
    const pct = Math.round((r.score || 0) * 100);
    if (r.kind === "news") {
      const href = safeUrl(r.source_url);
      const title = r.title || r.snippet;
      return `<div class="search-item">
        <div class="search-top"><span class="search-kind news">BERITA</span><span class="search-score">mirip ${pct}%</span></div>
        ${href ? `<a class="news-title" href="${href}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>` : `<div class="news-title">${esc(title)}</div>`}
        <div class="search-meta">${esc(r.source || "")}${r.published_at ? " · " + relTime(r.published_at) : ""}</div>
      </div>`;
    }
    return `<div class="search-item">
      <div class="search-top"><span class="search-kind post">POST</span><span class="search-score">mirip ${pct}%</span></div>
      <div class="search-snippet">${esc(r.snippet || "")}</div>
      <div class="search-meta">${r.created_at ? relTime(r.created_at) : ""}${r.likes ? " · " + r.likes + " suka" : ""}</div>
    </div>`;
  }).join("");
}

async function doSearch(q) {
  if (!q || !q.trim()) return;
  const el = $("#searchResults");
  el.innerHTML = '<div class="empty">Nyari…</div>';
  setView("search");
  try {
    const j = await api("/api/search?q=" + encodeURIComponent(q.trim()) + "&limit=15");
    renderSearch(j);
  } catch (err) {
    el.innerHTML = `<div class="empty">Search gagal: ${err.message}</div>`;
  }
}
$("#searchForm").addEventListener("submit", (e) => {
  e.preventDefault();
  doSearch($("#searchInput").value);
});

/* ---------- sidebar view switching ---------- */
function setView(v) {
  document.querySelectorAll(".side-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  document.querySelectorAll("[data-view]").forEach((sec) => {
    if (sec.classList.contains("side-btn")) return;
    sec.classList.toggle("hidden-view", sec.dataset.view !== v);
  });
}
document.querySelectorAll(".side-btn").forEach((btn) => {
  btn.addEventListener("click", () => setView(btn.dataset.view));
});

/* ---------- sidebar collapse (keluar-masuk via pull bar) ---------- */
function setSidebar(show) {
  document.querySelector(".layout").classList.toggle("side-hidden", !show);
  $("#sidePull").classList.toggle("closed", !show);
  localStorage.setItem("mf_sidebar", show ? "1" : "0");
}
$("#sidePull").addEventListener("click", () => {
  setSidebar(document.querySelector(".layout").classList.contains("side-hidden"));
});
setSidebar(localStorage.getItem("mf_sidebar") !== "0");


/* ---------- sector rollup ---------- */
function renderSectors(roll) {
  const el = $("#sectorBoard");
  if (!el) return;
  if (!roll || !roll.length) { el.innerHTML = '<div class="empty">Belum ada data sektor.</div>'; return; }
  const max = Math.max(1, ...roll.map((s) => s.mention_count));
  el.innerHTML = roll.map((s) => {
    const w = Math.round(100 * s.mention_count / max);
    const color = s.net_sentiment >= 0 ? "var(--green)" : "var(--red)";
    return `<div class="sector-card">
      <div class="sector-top">
        <b>${s.sectors}</b>
        <span class="${netClass(s.net_sentiment)}">${s.net_sentiment > 0 ? "+" : ""}${s.net_sentiment}</span>
      </div>
      <div class="sector-meta">${s.mention_count} mention · ${s.bullish} bull / ${s.bearish} bear · ${s.tickers}</div>
      <span class="meter"><span style="width:${w}%;background:${color}"></span></span>
    </div>`;
  }).join("");
}

/* ---------- actions ---------- */
async function refresh() {
  try {
    STATE = await api("/api/state");
    renderHealth(STATE.health);
    renderRecos(STATE.recommendations);
    renderSentiment(WINDOW_H === 1 ? STATE.tickers_1h : STATE.tickers_24h);
    renderSectors(STATE.sector_rollup);
    wireTickerClicks();
    renderEvents(STATE.events);
    renderNewsStats(STATE.articles_stats || { fetched: 0, dividen: 0, lainnya: 0 });
    renderArticles(STATE.articles || []);
    initChatterSelector();
  } catch (err) { toast("Gagal load state: " + err.message); }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    WINDOW_H = parseInt(tab.dataset.w, 10);
    if (STATE) renderSentiment(WINDOW_H === 1 ? STATE.tickers_1h : STATE.tickers_24h);
  });
});

$("#btnPoll").addEventListener("click", async () => {
  toast("Polling Stockbit + RSS…");
  try {
    const j = await api("/api/poll", { method: "POST" });
    toast(`Collector: ${j.collector.new} post baru (${j.collector.errors} err) · Breaking: ${j.breaking.candidates} kandidat, ${j.breaking.triggered} trigger`);
    refresh();
  } catch (err) { toast("Poll gagal: " + err.message); }
});

$("#btnSim").addEventListener("click", async () => {
  const staleTickers = (STATE?.recommendations || []).filter((r) => r.stale).map((r) => r.ticker);
  const pool = (STATE?.health.watchlist || ["ANTM"]).filter((t) => !staleTickers.includes(t));
  const ticker = pool[Math.floor(Math.random() * pool.length)] || "ANTM";
  try {
    const j = await api("/api/simulate_event", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker }),
    });
    setView("news");
    toast(`Simulasi event $${ticker} — score ${j.score}, ${j.severity}. Rekomendasi di-flag STALE, reassessment ±20 dtk.`);
    refresh();
  } catch (err) { toast("Simulasi gagal: " + err.message); }
});

$("#btnReset").addEventListener("click", async () => {
  if (!confirm("Hapus semua data demo (post, event, flag) dan seed ulang rekomendasi?")) return;
  try { await api("/api/reset", { method: "POST" }); toast("DB direset."); refresh(); }
  catch (err) { toast("Reset gagal: " + err.message); }
});


document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("#modal").classList.add("hidden");
});

$("#mClose").addEventListener("click", () => $("#modal").classList.add("hidden"));
$("#modal").addEventListener("click", (e) => { if (e.target === $("#modal")) $("#modal").classList.add("hidden"); });

setView("sentiment");
refresh();
setInterval(refresh, 30000);
