/* Dashboard logic. Renders "Data unavailable" placeholders wherever a source
   does not provide a metric — verified data only, with provenance shown. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const state = { trendType: "all", demandType: "all", search: "" };

/* Theme: light / dark with localStorage + system preference */
function applyTheme(t){
  document.documentElement.setAttribute("data-theme", t);
  try{ localStorage.setItem("theme", t); }catch(e){}
  const btn = $("#theme-toggle");
  if(btn) btn.textContent = t==="light" ? "☀" : "☾";
  if(btn) btn.title = t==="light" ? "Switch to dark mode" : "Switch to light mode";
}
function initTheme(){
  const saved = (()=>{try{return localStorage.getItem("theme")}catch(e){return null}})();
  const initial = saved || (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  applyTheme(initial);
  const btn = $("#theme-toggle");
  if(btn) btn.addEventListener("click", ()=>{
    const cur = document.documentElement.getAttribute("data-theme") || "dark";
    applyTheme(cur==="dark"?"light":"dark");
  });
  // sync with system changes if no explicit preference
  if(!saved && window.matchMedia){
    window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", e=>{
      applyTheme(e.matches?"light":"dark");
    });
  }
}

function ago(ts) {
  if (!ts) return "never updated";
  const s = (Date.now() - new Date(ts + "Z".repeat(!ts.endsWith("Z")))) / 1000;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} hours ago`;
  return `${Math.round(s / 86400)} days ago`;
}
const fmt = (v, suffix = "") => (v === null || v === undefined)
  ? `<span class="unavail">Data unavailable</span>` : `${v}${suffix}`;
const arrow = (pct) => pct === null || pct === undefined
  ? `<span class="flat">– trend unavailable</span>`
  : pct > 2 ? `<span class="up">↑ ${pct}%</span>`
  : pct < -2 ? `<span class="down">↓ ${Math.abs(pct)}%</span>`
  : `<span class="flat">→ stable</span>`;
const typeBadge = (t) => `<span class="badge type">${t === "series" ? "TV Series" : "Movie"}</span>`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function provenanceLine(p) {
  const prov = Object.values(p || {})[0];
  if (!prov) return `Source: n/a · <span class="unavail">no measurements yet</span>`;
  return `Source: <b>${esc(prov.source)}</b> · Region: ${esc(prov.region)} · Updated: ${ago(prov.collected_at)}`;
}

function card(t, opts = {}) {
  const poster = t.poster_url
    ? `<img class="poster" src="${esc(t.poster_url)}" alt="" loading="lazy" onerror="this.outerHTML='<div class=poster>🎬</div>'">`
    : `<div class="poster">🎬</div>`;
  return `<div class="card" onclick="openDetail(${t.id})">${poster}
    <div class="card-body">
      <div class="card-title">${esc(t.title)}</div>
      <div class="badges">${typeBadge(t.type)}${t.genres.slice(0, 2).map(g => `<span class="badge">${esc(g)}</span>`).join("")}</div>
      <div class="meta">Released: ${t.release_date ?? "unknown"} · Rating: ${fmt(t.rating, "/10")}</div>
      <div class="trend-line">
        <div>Search trend: ${arrow(t.search_growth_pct)}</div>
        <div>Interest: ${fmt(t.search_interest, "/100")} · Popularity: ${fmt(t.popularity)} ${arrow(opts.popDelta ?? null)}</div>
        ${t.trend_score !== undefined ? `<span class="score-pill">Trend score ${t.trend_score} · ${esc(t.confidence)} confidence</span>` : ""}
      </div>
      <div class="prov">${provenanceLine(t.provenance)} · ${ago(t.last_updated)}</div>
    </div></div>`;
}

async function loadTrending() {
  const data = await get(`/api/trending?type=${state.trendType}`);
  let titles = data.titles;
  if (state.search) {
    const q = state.search.toLowerCase();
    titles = titles.filter(t => t.title.toLowerCase().includes(q));
  }
  $("#trend-grid").innerHTML = titles.length
    ? titles.map(t => card(t)).join("")
    : emptyState(data);
}
async function loadDemand() {
  const selRegion = $("#d-region").value, selPeriod = $("#d-period").value;
  const p = new URLSearchParams({ type: state.demandType, genre: $("#d-genre").value,
    region: selRegion, period: selPeriod, intensity: $("#d-intensity").value });
  const data = await get(`/api/search-demand?${p}`);
  // Keep dropdown availability in sync with what the API actually collected.
  if (Array.isArray(data.available_regions)) availableRegions = new Set(data.available_regions);
  if (Array.isArray(data.available_periods)) availablePeriods = new Set(data.available_periods);
  const uncollected = data.not_collected === true
    || !availableRegions.has(selRegion) || !availablePeriods.has(selPeriod);
  const notCollectedBanner = `<div class="card"><div class="card-body"><div class="card-title">Data unavailable — not collected (set GOOGLE_TRENDS_GEOS / period and refresh)</div>
    <div class="meta">No search-demand measurements have been collected for <b>${esc(selRegion)} / ${esc(selPeriod)}</b> yet. Collected: <b>${[...availableRegions].join(", ") || "none yet"}</b> / <b>${[...availablePeriods].join(", ") || "none yet"}</b>. Set <code>GOOGLE_TRENDS_GEOS</code> / <code>GOOGLE_TRENDS_PERIODS</code> in <code>.env</code> and trigger a refresh to collect it. No metrics are fabricated for uncollected regions/periods.</div></div></div>`;
  if (uncollected) { $("#demand-grid").innerHTML = notCollectedBanner; return; }
  // Coverage stats: show verified vs unavailable
  const verified = data.titles.filter(x=> x.search_growth_pct !== null && x.search_growth_pct !== undefined).length;
  const total = data.titles.length;
  const coverageNote = total ? `<div class="meta" style="margin-bottom:12px">Showing ${total} titles — ${verified} with verified search demand (${Math.round(verified/total*100)}% coverage). Titles without Trends data show \"Data unavailable\" (no fabricated data, see provenance). Available: ${[...availableRegions].join(", ") || "Global"} / ${[...availablePeriods].join(", ") || "7d"}.</div>` : "";
  $("#demand-grid").innerHTML = data.titles.length
    ? coverageNote + data.titles.map(t => card(t)).join("")
    : emptyState(data);
}
function sparkPointsFor(values) {
  if (!Array.isArray(values) || values.length < 2) return "";
  const min = Math.min(...values), max = Math.max(...values), rng = (max - min) || 1;
  return values.map((v, i) =>
    `${(i / (values.length - 1)) * 100},${28 - ((v - min) / rng) * 26}`).join(" ");
}
async function loadInterest() {
  const data = await get("/api/trending?limit=20");
  $("#interest-list").innerHTML = data.titles.length ? data.titles.map(t => {
    const dir = t.search_growth_pct === null || t.search_growth_pct === undefined ? "flat"
      : t.search_growth_pct > 15 ? "up" : t.search_growth_pct < -5 ? "down" : "flat";
    const label = dir === "up" ? "↑ Rapidly increasing" : dir === "down" ? "↓ Decreasing" : "→ Stable";
    // Snapshot-only series embedded in /api/trending — no per-row fetch, no Trends calls.
    const pts = Array.isArray(t.interest_history)
      ? t.interest_history.filter(v => v !== null && v !== undefined)
      : [];
    const spark = pts.length >= 2
      ? `<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none"><polyline fill="none" stroke="${dir === 'down' ? '#f85149' : '#3fb950'}" stroke-width="2" points="${sparkPointsFor(pts)}"/></svg>`
      : `<span class="unavail">${pts.length === 0 ? "No time-series from source" : "Current signal only"}</span>`;
    return `<div class="interest-row" onclick="openDetail(${t.id})">
      <span class="dir ${dir}">${label}</span>${spark}
      <div><b>${esc(t.title)}</b> <span class="meta">${typeBadge(t.type)} · ${esc(t.genres.slice(0, 2).join(" / ") || "genre n/a")}</span></div>
      <div class="meta" style="text-align:right">Signal: Google Trends (7d)<br>${ago(t.last_updated)}</div>
    </div>`;
  }).join("") : `<p class="sub">No scored titles yet. Click “Refresh data” once configured.</p>`;
}
let sparkCache = {};
function drawSparks() {
  // Snapshot-only fallback: never calls GET /api/titles/{id} (which could hit Trends).
  // loadInterest already renders inline; this only upgrades any legacy placeholder polylines.
  $$(".spark polyline[data-tid]").forEach(async (pl) => {
    const id = pl.dataset.tid;
    if (!(id in sparkCache)) {
      try {
        const d = await get(`/api/titles/${id}/history`);
        sparkCache[id] = (d.history || []).map(h => h.search_interest).filter(v => v !== null && v !== undefined);
      } catch (e) { sparkCache[id] = []; }
    }
    const s = sparkCache[id];
    if (s.length >= 2) {
      pl.setAttribute("points", sparkPointsFor(s));
    } else { pl.closest(".spark")?.replaceWith(Object.assign(document.createElement("span"),
      { className: "unavail", textContent: "Current signal only" })); }
  });
}

let availableRegions = new Set(["Global"]);
let availablePeriods = new Set(["7d"]);
let configuredGeos = [];

function emptyState(data) {
  if (data.tmdb_configured === false)
    return `<div class="card"><div class="card-body"><div class="card-title">Data unavailable</div>
      <div class="meta">No TMDB_API_KEY configured, so no metadata can be collected. No numbers are fabricated —
      add a TMDB API key to .env and run a refresh.</div></div></div>`;
  // Explain why region/period yields empty (no fabricated data)
  const selRegion = $("#d-region")?.value || "Global";
  const selPeriod = $("#d-period")?.value || "7d";
  if (!availableRegions.has(selRegion)) {
    return `<div class="card"><div class="card-body"><div class="card-title">Data unavailable — not collected (set GOOGLE_TRENDS_GEOS / period and refresh)</div>
      <div class="meta">No search-demand measurements have been collected for <b>${esc(selRegion)}</b> yet. The pipeline currently collects only <b>${[...availableRegions].join(", ") || "Global"}</b> (see <code>GOOGLE_TRENDS_GEOS</code> in <code>.env</code>/<code>app/config.py</code>). Set <code>GOOGLE_TRENDS_GEOS=GLOBAL,US,GB</code> and trigger a refresh to collect per-region data. No data is fabricated — the UI shows “Data unavailable” where a source did not provide a metric.</div></div></div>`;
  }
  if (!availablePeriods.has(selPeriod)) {
    return `<div class="card"><div class="card-body"><div class="card-title">Data unavailable — not collected (set GOOGLE_TRENDS_GEOS / period and refresh)</div>
      <div class="meta">No search-demand measurements have been collected for period <b>${esc(selPeriod)}</b> yet. The pipeline currently stores only <b>${[...availablePeriods].join(", ")}</b> search windows. Set <code>GOOGLE_TRENDS_PERIODS</code> (e.g. <code>now 7-d,today 1-m</code>) and refresh to collect 24h/30d/90d. Default is <code>now 7-d</code> (7d).</div></div></div>`;
  }
  return `<p class="sub">No titles match. Try different filters, or trigger a data refresh.</p>`;
}

async function get(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function openDetail(id) {
  const t = await get(`/api/titles/${id}`);
  // Interest = Google Trends search_interest over real snapshots (collected_at order).
  // Popularity = TMDB popularity — never mixed into the interest polyline.
  const interestHist = (t.history || []).filter(h => h.search_interest !== null && h.search_interest !== undefined);
  const popHist = (t.history || []).filter(h => h.popularity !== null && h.popularity !== undefined);
  const interestChart = (() => {
    if (interestHist.length < 2) return "";
    const vals = interestHist.map(h => h.search_interest);
    const min = Math.min(...vals), max = Math.max(...vals), rng = (max - min) || 1;
    const pts = interestHist.map((h, i) =>
      `${(i / (interestHist.length - 1)) * 400},${95 - ((h.search_interest - min) / rng) * 90}`).join(" ");
    return `<div class="section"><h3>Google Trends search interest (real snapshots)</h3>
       <svg class="chart" viewBox="0 0 400 100" preserveAspectRatio="none">
         <polyline fill="none" stroke="#3fb950" stroke-width="2.5" points="${pts}"/>
       </svg>
       <div class="meta">${interestHist.length} Google Trends search-interest snapshots collected · first ${ago(interestHist[0].collected_at)} · Source: Google Trends</div></div>`;
  })();
  const interestBlock = interestChart
    ? interestChart
    : `<div class="section"><h3>Google Trends search interest (real snapshots)</h3>
       <span class="unavail">Data unavailable — not enough search-interest snapshots to show a real trend.</span></div>`;
  const popBlock = (() => {
    if (popHist.length < 2) return "";
    const vals = popHist.map(h => h.popularity);
    const min = Math.min(...vals), max = Math.max(...vals), rng = (max - min) || 1;
    const pts = popHist.map((h, i) =>
      `${(i / (popHist.length - 1)) * 400},${95 - ((h.popularity - min) / rng) * 90}`).join(" ");
    return `<div class="section"><h3>TMDB popularity (real snapshots)</h3>
       <svg class="chart" viewBox="0 0 400 100" preserveAspectRatio="none">
         <polyline fill="none" stroke="#58a6ff" stroke-width="2.5" points="${pts}"/>
       </svg>
       <div class="meta">${popHist.length} popularity snapshots collected · first ${ago(popHist[0].collected_at)} · Source: TMDB</div></div>`;
  })();
  const spark = interestBlock + popBlock;

  const comps = t.components || {};
  const compRows = Object.entries(comps).map(([k, c]) =>
    `<tr><td>${esc(k)}</td><td>${c.available ? esc(c.value) : '<span class="unavail">Data unavailable</span>'}</td>
     <td>${c.available ? `weight contribution shown in score` : "not provided by source"}</td></tr>`).join("");

  const provRows = Object.entries(t.provenance || {}).map(([name, p]) =>
    `<tr><td>${esc(name)}</td><td>${fmt(p.value)}</td><td>${esc(p.source)}</td>
     <td>${esc(p.region)}</td><td>${esc(p.period || "current")}</td><td>${ago(p.collected_at)}</td></tr>`).join("");

  $("#modal-body").innerHTML = `
    <div class="detail-head">
      ${t.poster_url ? `<img class="poster" src="${esc(t.poster_url)}" alt="">` : `<div class="poster">🎬</div>`}
      <div>
        <h2>${esc(t.title)}</h2>
        <div class="badges">${typeBadge(t.type)}${t.genres.map(g => `<span class="badge">${esc(g)}</span>`).join("")}</div>
        <div class="meta">Released: ${t.release_date ?? "unknown"} · Rating: ${fmt(t.rating, "/10")} · IMDb: ${fmt(t.imdb_rating, "/10")}${t.imdb_votes ? ` (${Number(t.imdb_votes).toLocaleString("en-US")} votes)` : ""}
          ${t.seasons ? ` · ${t.seasons} seasons / ${t.episodes} episodes` : ""}</div>
        ${t.directors.length ? `<div class="meta">Directed/Created by: ${esc(t.directors.join(", "))}</div>` : ""}
        ${t.cast.length ? `<div class="meta">Cast: ${esc(t.cast.join(", "))}</div>` : ""}
        <p class="meta" style="margin-top:8px">${esc(t.overview || "No description provided by source.")}</p>
      </div>
    </div>
    <div class="section"><h3>Current demand</h3>
      <div class="bar-wrap"><div class="bar" style="width:${t.trend_score ?? 0}%"></div></div>
      <div class="meta">Trend score ${fmt(t.trend_score, "/100")} · confidence: ${esc(t.confidence || "unavailable")}</div></div>
    <div class="section"><h3>Search trend</h3>
      ${arrow(t.search_growth_pct)} · interest ${fmt(t.search_interest, "/100")}
      <div class="meta">Source: Google Trends · last 7 days</div></div>
    <div class="section"><h3>Why it is trending</h3>
      <ul class="why">${(t.why_trending || []).map(w => `<li>${esc(w)}</li>`).join("") ||
        '<li class="unavail">Not enough verified data to explain</li>'}</ul></div>
    ${spark}
    <div class="section" id="ai-insight-section"><h3>AI Insight (Gemini - derived)</h3><div id="ai-insight-body" class="meta"><span class="unavail">Loading AI insight...</span></div></div>
    <div class="section"><h3>Score components</h3>
      <table class="prov-table"><tr><th>Component</th><th>Value</th><th>Status</th></tr>${compRows || '<tr><td colspan=3 class="unavail">Not scored yet</td></tr>'}</table></div>
    <div class="section"><h3>Data sources & provenance</h3>
      <table class="prov-table"><tr><th>Metric</th><th>Value</th><th>Source</th><th>Region</th><th>Period</th><th>Updated</th></tr>
      ${provRows || '<tr><td colspan=6 class="unavail">No metrics collected yet</td></tr>'}</table>
      <div class="meta" style="margin-top:6px">Ratings: <b>IMDb Official Datasets</b> (datasets.imdbws.com) · Popularity & metadata: <b>TMDB</b> · Search demand: <b>Google Trends</b>.<br>
      Rotten Tomatoes and Letterboxd publish no official public API, so no numbers are shown for them ("Data unavailable") rather than estimated.</div></div>
    <div class="section" id="related-section"><h3>Rising related searches</h3><div id="related-body" class="badges"><span class="unavail">Loading…</span></div></div>
    <div class="section"><h3>Last updated</h3><div>${ago(t.last_updated)} (${esc(t.last_updated || "")} UTC)</div></div>`;
  $("#modal").classList.remove("hidden");
  // Lazy-load related queries only for the opened title (never from list/spark paths).
  (async ()=>{
    const sec = $("#related-section"), body = $("#related-body");
    if(!sec || !body) return;
    try{
      const rq = await get(`/api/titles/${t.id}/related-queries`);
      const qs = rq.related_queries || [];
      if(!qs.length){ sec.style.display = "none"; return; }
      body.innerHTML = qs.map(q => `<span class="badge">${esc(q)}</span>`).join("");
    }catch(e){ sec.style.display = "none"; }
  })();
  // Fetch AI insight (derived, cached 24h) - improves data where Trends is Data unavailable
  (async ()=>{
    const el = $("#ai-insight-body");
    if(!el) return;
    try{
      const ai = await get(`/api/titles/${t.id}/ai-insights`);
      const ins = ai.insight || {};
      el.innerHTML = `
        <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:10px">
          <div><b>Summary:</b> ${esc(ins.summary || "N/A")}</div>
          <div style="margin-top:6px"><b>Why trending (AI):</b> ${esc(ins.why_trending_ai || "N/A")}</div>
          ${ins.demand_proxy !== null && ins.demand_proxy !== undefined ? `<div><b>Demand proxy (0-100, derived):</b> ${esc(ins.demand_proxy)} <span class="unavail"> - AI estimate where Trends unavailable, not verified</span></div>` : ""}
          ${ins.tags && ins.tags.length ? `<div class="badges" style="margin-top:6px">${ins.tags.map(x=>`<span class="badge">${esc(x)}</span>`).join("")}</div>` : ""}
          ${ins.recommendation ? `<div style="margin-top:6px"><b>Recommendation:</b> ${esc(ins.recommendation)}</div>` : ""}
          <div class="meta" style="margin-top:8px">Source: ${esc(ins.source || ai.model || "Gemini")} (${esc(ins.quality || "derived")}) - Generated ${ai.generated_at ? ago(ai.generated_at) : "just now"}${ai.cached ? " (cached)" : ""}</div>
        </div>`;
    }catch(e){
      el.innerHTML = `<span class="unavail">AI insight unavailable - ${esc(e.message || "Gemini not configured or rate-limited")}. Verified metrics above remain the source of truth.</span>`;
    }
  })();
}
$("#modal-close").onclick = () => $("#modal").classList.add("hidden");
$("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); };

/* navigation + filters */
$$(".nav-btn").forEach(b => b.onclick = () => {
  $$(".nav-btn").forEach(x => x.classList.toggle("active", x === b));
  $$(".view").forEach(v => v.classList.add("hidden"));
  const view = { trending: "view-trending", demand: "view-demand", interest: "view-interest" }[b.dataset.view];
  $("#" + view).classList.remove("hidden");
  ({ trending: loadTrending, demand: loadDemand, interest: loadInterest })[b.dataset.view]();
});
$$("#f-type .pill").forEach(p => p.onclick = () => {
  $$("#f-type .pill").forEach(x => x.classList.toggle("active", x === p));
  state.trendType = p.dataset.v; loadTrending();
});
const demandTypes = [["all", "All"], ["movie", "Movies"], ["series", "Series"]];
$("#d-type").innerHTML = demandTypes.map(([v, l], i) =>
  `<button class="pill ${i === 0 ? "active" : ""}" data-v="${v}">${l}</button>`).join("");
$$("#d-type .pill").forEach(p => p.onclick = () => {
  $$("#d-type .pill").forEach(x => x.classList.toggle("active", x === p));
  state.demandType = p.dataset.v; loadDemand();
});
["d-genre", "d-region", "d-period", "d-intensity"].forEach(id =>
  $("#" + id).addEventListener("change", loadDemand));

$("#search").addEventListener("input", (e) => {
  state.search = e.target.value.trim();
  if (!$("#view-trending").classList.contains("hidden")) loadTrending();
});

async function init() {
  initTheme();
  const regions = await get("/api/regions").catch(() => ({ regions: { Global: "" }, available_regions: ["Global"], available_periods: ["7d"] }));
  availableRegions = new Set(regions.available_regions || ["Global"]);
  availablePeriods = new Set(regions.available_periods || ["7d"]);
  configuredGeos = regions.configured_geos || [];
  $("#d-region").innerHTML = Object.keys(regions.regions).map(r => {
    const hasData = availableRegions.has(r);
    return `<option value="${esc(r)}"${hasData ? "" : " disabled style=\"color:#8b949e\""}>${esc(r)}${hasData ? " ✓" : " — no data yet"}</option>`;
  }).join("");
  // Drive period options from collected data: disable uncollected, default to collected.
  $$("#d-period option").forEach(o => {
    const hasData = availablePeriods.has(o.value);
    o.disabled = !hasData;
    o.textContent = o.textContent.replace(" — no data yet", "");
    if (!hasData) o.textContent += " — no data yet";
  });
  // Default selection to a region/period that actually has rows (prefer Global / 7d).
  const regionKeys = Object.keys(regions.regions);
  const defaultRegion = availableRegions.has("Global") ? "Global"
    : [...availableRegions].find(r => regionKeys.includes(r)) || regionKeys[0] || "Global";
  const periodOrder = ["7d", "24h", "30d", "90d"];
  const defaultPeriod = availablePeriods.has("7d") ? "7d"
    : periodOrder.find(x => availablePeriods.has(x)) || [...availablePeriods][0] || "7d";
  $("#d-region").value = regionKeys.includes(defaultRegion) ? defaultRegion : (regionKeys[0] || "Global");
  const periodValues = $$("#d-period option").map(o => o.value);
  $("#d-period").value = periodValues.includes(defaultPeriod) ? defaultPeriod : (periodValues[0] || "7d");
  try {
    const st = await get("/api/status");
    $("#status-chip").textContent = st.tmdb_configured
      ? `${st.counts.titles} titles · updated ${ago(st.last_refresh?.finished_at)}`
      : "TMDB key not configured — data unavailable";
    $("#status-chip").className = "chip " + (st.tmdb_configured ? "ok" : "warn");
    const al = await get("/api/alerts");
    if (al.alerts.length) {
      $("#alerts-bar").classList.remove("hidden");
      $("#alerts-bar").innerHTML = "🔔 " + al.alerts.slice(0, 3).map(a =>
        `<b>${esc(a.title)}</b>: search interest +${a.value}% (${esc(a.source)}, ${esc(a.region)}, last 7 days)`).join(" · · ");
    }
    const g = await get("/api/genres");
    $("#d-genre").innerHTML = "<option>All</option>" + g.genres.map(x => `<option>${esc(x)}</option>`).join("");
  } catch (e) { /* first run before refresh */ }
  loadTrending();
}
init();
