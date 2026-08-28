// ---------------------------------------------------------------------
// Simple view-switcher (no framework needed for a demo this size)
//
// Nav used to live in a hamburger-triggered slide-out drawer; it's now
// inline in the top bar, so switching views is just the class toggling
// below — no open/close state to manage anymore.
// ---------------------------------------------------------------------
const navItems = document.querySelectorAll(".nav-item");
const views = {
  inventory: document.getElementById("view-inventory"),
  performance: document.getElementById("view-performance"), // merged heatmap + behavioural matching
  category: document.getElementById("view-category"),
  targeting: document.getElementById("view-targeting"),
};

navItems.forEach(btn => {
  btn.addEventListener("click", () => {
    navItems.forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    Object.values(views).forEach(v => v.classList.add("hidden"));
    views[btn.dataset.view].classList.remove("hidden");
    window.scrollTo({ top: 0, behavior: "instant" });
  });
});

function fmtNum(n) {
  return new Intl.NumberFormat("en-US").format(Math.round(n));
}
function fmtMoney(n) {
  return "$" + new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(n);
}

// ---------------------------------------------------------------------
// VIEW 1 — Segment Inventory Audit
//
// Three things render here: a portfolio overview (always on) showing all
// canonical audiences at a glance, a view toggle for how each card's
// platform breakdown is drawn, and the card list itself. Everything is
// hand-rolled inline SVG rather than a charting library — the rest of
// this app has zero external dependencies beyond the Google Font, and a
// donut or radar chart is simple enough geometry not to need one.
// ---------------------------------------------------------------------

// Fixed platform → color mapping, reused across the donut, radar, and
// portfolio bar so a platform reads the same color everywhere on this
// screen. Four distinct hues: two from the brand pair (navy, red), plus
// yellow and a steel-blue to round out four without introducing new
// off-brand colors.
const PLATFORM_COLOR = {
  AMC: "#001E3C",
  ICDH: "#FF0C49",
  ADH: "#FFC800",
  META_AA: "#5B7A99",
};
const PLATFORM_ORDER = ["AMC", "ICDH", "ADH", "META_AA"];
const PLATFORM_SHORT = { AMC: "Amazon", ICDH: "Instacart", ADH: "Google", META_AA: "Meta" };

function platformBreakdown(seg) {
  const byPlat = {};
  PLATFORM_ORDER.forEach(p => { byPlat[p] = { count: 0, spend: 0 }; });
  seg.raw_segments.forEach(rs => {
    if (!byPlat[rs.platform_code]) byPlat[rs.platform_code] = { count: 0, spend: 0 };
    byPlat[rs.platform_code].count += 1;
    byPlat[rs.platform_code].spend += rs.spend || 0;
  });
  return byPlat;
}

// ---- Option B: small donut of spend share per platform ----
function donutSVG(breakdown, size = 64) {
  const r = size / 2 - 6, cx = size / 2, cy = size / 2, circumference = 2 * Math.PI * r;
  const total = PLATFORM_ORDER.reduce((s, p) => s + breakdown[p].spend, 0) || 1;
  let offset = 0;
  const circles = PLATFORM_ORDER.filter(p => breakdown[p].spend > 0).map(p => {
    const frac = breakdown[p].spend / total;
    const dash = frac * circumference;
    const circle = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${PLATFORM_COLOR[p]}"
      stroke-width="9" stroke-dasharray="${dash} ${circumference - dash}"
      stroke-dashoffset="${-offset}" transform="rotate(-90 ${cx} ${cy})"></circle>`;
    offset += dash;
    return circle;
  }).join("");
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" class="mini-chart">${circles}</svg>`;
}

// ---- Option C: small radar / spider showing the shape of this audience's
// platform fragmentation. Axes are fixed (always AMC/ICDH/ADH/META_AA, in
// the same order, even when a platform is absent — a flat spot on the
// shape IS the information). Radius is the raw-name count on that
// platform, normalized against this group's own highest platform, so the
// shape reads as "where is this audience's naming mess concentrated" —
// magnitude across groups is what the portfolio bar above is for.
function radarSVG(breakdown, size = 108) {
  const n = PLATFORM_ORDER.length;
  const cx = size / 2, cy = size / 2, maxR = size / 2 - 26;
  const maxVal = Math.max(1, ...PLATFORM_ORDER.map(p => breakdown[p].count));
  const angle = i => (Math.PI * 2 * i) / n - Math.PI / 2;
  const pt = (i, frac) => [cx + Math.cos(angle(i)) * maxR * frac, cy + Math.sin(angle(i)) * maxR * frac];

  const rings = [0.33, 0.66, 1].map(frac => {
    const pts = PLATFORM_ORDER.map((_, i) => pt(i, frac).join(",")).join(" ");
    return `<polygon points="${pts}" fill="none" stroke="#E4E9EE" stroke-width="1"></polygon>`;
  }).join("");

  const spokes = PLATFORM_ORDER.map((_, i) => {
    const [x, y] = pt(i, 1);
    return `<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" stroke="#E4E9EE" stroke-width="1"></line>`;
  }).join("");

  const shapePts = PLATFORM_ORDER.map((p, i) => pt(i, breakdown[p].count / maxVal).join(",")).join(" ");

  const labels = PLATFORM_ORDER.map((p, i) => {
    const [x, y] = pt(i, 1.32);
    const anchor = Math.abs(Math.cos(angle(i))) < 0.3 ? "middle" : (Math.cos(angle(i)) > 0 ? "start" : "end");
    return `<text x="${x}" y="${y + 3}" font-size="8.5" font-weight="700" fill="#5B6472" text-anchor="${anchor}">${p === "META_AA" ? "META" : p}</text>`;
  }).join("");

  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" class="mini-chart mini-chart-radar" overflow="visible">
    ${rings}${spokes}
    <polygon points="${shapePts}" fill="${"#FF0C49"}22" stroke="#FF0C49" stroke-width="1.75"></polygon>
    ${labels}
  </svg>`;
}

function legendHtml() {
  return `<div class="platform-legend">
    ${PLATFORM_ORDER.map(p => `<span class="legend-swatch"><i style="background:${PLATFORM_COLOR[p]}"></i>${PLATFORM_SHORT[p]}</span>`).join("")}
  </div>`;
}

// ---- Portfolio overview: every canonical audience, one horizontal bar
// each, segments colored by platform's share of spend, sorted by total
// spend so the biggest naming-fragmentation cost sits at the top. ----
function renderPortfolioOverview(data) {
  const maxSpend = Math.max(...data.map(s => s.total_spend), 1);
  const sorted = [...data].sort((a, b) => b.total_spend - a.total_spend);

  const rows = sorted.map(seg => {
    const bd = platformBreakdown(seg);
    const widthPct = (seg.total_spend / maxSpend) * 100;
    const segments = PLATFORM_ORDER.filter(p => bd[p].spend > 0).map(p => {
      const share = (bd[p].spend / seg.total_spend) * 100;
      return `<span class="pbar-seg" style="width:${share}%; background:${PLATFORM_COLOR[p]};"
        title="${PLATFORM_SHORT[p]}: ${fmtMoney(bd[p].spend)}"></span>`;
    }).join("");
    return `
      <div class="pbar-row">
        <span class="pbar-label">${seg.canonical_name}${seg.flag ? '<i class="pbar-flag" title="Naming redundancy"></i>' : ""}</span>
        <div class="pbar-track" style="width:${widthPct}%;">${segments}</div>
        <span class="pbar-value">${fmtMoney(seg.total_spend)}</span>
      </div>`;
  }).join("");

  return `
    <div class="explain-block portfolio-block">
      <div class="portfolio-head">
        <h2 class="section-heading" style="margin:0;">Portfolio overview</h2>
        ${legendHtml()}
      </div>
      <p class="section-sub">All ${data.length} canonical audiences, sized by total spend and colored by which
        platform that spend runs on. The dot marks audiences flagged for naming redundancy.</p>
      <div class="pbar-list">${rows}</div>
    </div>`;
}

let inventoryData = null;
let inventoryView = "compact"; // compact | donut | radar

function renderInventoryList() {
  const el = document.getElementById("inventory-list");
  const data = inventoryData;
  if (!data.length) { el.innerHTML = '<div class="empty-state">No data yet.</div>'; return; }

  el.innerHTML = data.map((seg, i) => {
    const bd = platformBreakdown(seg);
    const visual = inventoryView === "donut" ? donutSVG(bd)
                 : inventoryView === "radar" ? radarSVG(bd)
                 : "";
    return `
    <div class="card" id="inv-card-${i}">
      <div class="card-head" onclick="document.getElementById('inv-card-${i}').classList.toggle('open')">
        <div class="card-head-left">
          ${visual ? `<div class="inv-visual">${visual}</div>` : ""}
          <div class="card-title-group">
            <span class="card-title">${seg.canonical_name}</span>
            <span class="card-category">${seg.category}</span>
            ${seg.flag ? `<span class="badge flag">${seg.flag}</span>` : ""}
          </div>
        </div>
        <div style="display:flex; align-items:center;">
          <div class="card-stats">
            <span><b>${seg.raw_segment_count}</b> names</span>
            <span><b>${seg.platform_count}</b> platforms</span>
            <span><b>${fmtMoney(seg.total_spend)}</b> spend</span>
          </div>
          <span class="chevron">▶</span>
        </div>
      </div>
      <div class="card-body">
        <table class="detail-table">
          <thead><tr><th>Platform</th><th>Segment name as sold</th><th>Match confidence</th><th>Est. spend</th></tr></thead>
          <tbody>
            ${seg.raw_segments.map(rs => `
              <tr>
                <td><span class="pf-tag">${rs.platform_name}</span></td>
                <td>${rs.raw_name}</td>
                <td>${Math.round(rs.confidence * 100)}%</td>
                <td>${fmtMoney(rs.spend)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>

      </div>
    </div>
  `;
  }).join("");
}

async function loadInventory() {
  const el = document.getElementById("inventory-list");
  el.innerHTML = '<div class="empty-state">Loading…</div>';
  inventoryData = await fetch("/api/inventory-audit").then(r => r.json());
  if (!inventoryData.length) { el.innerHTML = '<div class="empty-state">No data yet.</div>'; return; }

  document.getElementById("inv-portfolio").innerHTML = renderPortfolioOverview(inventoryData);

  const toggle = document.getElementById("inv-view-toggle");
  toggle.querySelectorAll(".seg-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      toggle.querySelectorAll(".seg-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      inventoryView = btn.dataset.mode;
      renderInventoryList();
    });
  });

  renderInventoryList();
}

// ---------------------------------------------------------------------
// VIEW 2 — Performance Heatmap
// ---------------------------------------------------------------------

let heatmapData = null;

function colorForIndex(idx) {
  // 100 = neutral. Below = cool blue-gray, above = warm green, scaled.
  if (idx === null || idx === undefined) return "#EEF1F4";
  const clamped = Math.max(40, Math.min(200, idx));
  if (clamped >= 100) {
    const t = (clamped - 100) / 100; // 0..1
    const r = Math.round(255 - t * 165);
    const g = Math.round(255 - t * 70);
    const b = Math.round(255 - t * 165);
    return `rgb(${r},${g},${b})`;
  } else {
    const t = (100 - clamped) / 60;
    const r = Math.round(255 - t * 60);
    const g = Math.round(255 - t * 40);
    const b = Math.round(255 - t * 10);
    return `rgb(${r},${g},${b})`;
  }
}

const HEATMAP_CAPTIONS = {
  CLICKS: "100 means an audience performs exactly like that platform's own average — above 100 means it outperforms, " +
          "below means it underperforms. Because platforms measure things differently, every cell also carries a trust " +
          "grade (A/B/C) for how safe it is to compare that number against another platform's.",
  CONVERSIONS: "100 means an audience performs exactly like that platform's own average — above 100 means it outperforms, " +
               "below means it underperforms. Because platforms measure things differently, every cell also carries a trust " +
               "grade (A/B/C) for how safe it is to compare that number against another platform's.",
  IMPRESSIONS: "Reach isn't a performance score — it's a size comparison. 100 means this audience is a typical size for " +
               "that platform; 200 means roughly twice the usual size, 50 means about half. Bigger isn't automatically " +
               "better here, just bigger — the trust grade still tells you how safe it is to compare across platforms.",
};

function renderHeatmap() {
  const metric = document.getElementById("metric-select").value;
  const wrap = document.getElementById("heatmap-wrap");
  if (!heatmapData) { wrap.innerHTML = '<div class="empty-state">Loading…</div>'; return; }

  const caption = document.getElementById("heatmap-caption");
  if (caption) caption.textContent = HEATMAP_CAPTIONS[metric] || HEATMAP_CAPTIONS.CLICKS;

  const segIds = Object.keys(heatmapData.segment_names);
  const platCodes = Object.keys(heatmapData.platform_names);

  const cellLookup = {};
  heatmapData.cells.forEach(c => {
    if (c.metric_code === metric) cellLookup[`${c.canonical_id}__${c.platform_code}`] = c;
  });

  let html = '<table class="heatmap"><thead><tr><th class="row-label-head">Audience</th>';
  platCodes.forEach(p => html += `<th>${heatmapData.platform_names[p]}</th>`);
  html += "</tr></thead><tbody>";

  segIds.forEach(seg => {
    html += `<tr><td class="row-label">${heatmapData.segment_names[seg]}</td>`;
    platCodes.forEach(p => {
      const cell = cellLookup[`${seg}__${p}`];
      if (!cell || cell.index === null) {
        html += `<td><div class="heat-cell empty">No data</div></td>`;
      } else {
        const bg = colorForIndex(cell.index);
        html += `<td>
          <div class="heat-cell grade-border-${cell.grade}" style="background:${bg}; border-color: ${cell.grade === 'A' ? '#2E7D46' : cell.grade === 'B' ? '#A9740B' : '#B23A48'}"
               title="${cell.grade_explain}">
            ${cell.index}
            <span class="idx-sub">grade ${cell.grade}</span>
          </div>
        </td>`;
      }
    });
    html += "</tr>";
  });
  html += "</tbody></table>";
  wrap.innerHTML = html;
}

async function loadHeatmap() {
  heatmapData = await fetch("/api/performance-heatmap").then(r => r.json());
  renderHeatmap();
}
document.getElementById("metric-select").addEventListener("change", renderHeatmap);

// ---------------------------------------------------------------------
// VIEW 2, Part 2 — Does the name match the behaviour?
//
// This used to open with a standalone panel explaining the clustering
// methodology (named features, platform-confounding check, surrogate
// rule, cluster profiles). That's dropped here — the story this combined
// screen tells is Part 1's performance numbers feeding directly into
// "and here's where the name-based grouping doesn't hold up," rather than
// a separate methodology lesson. The per-segment "why it landed there"
// evidence below still carries the reasoning; it just isn't front-loaded
// as its own section anymore.
// ---------------------------------------------------------------------
async function loadMatching() {
  const el = document.getElementById("matching-list");
  el.innerHTML = '<div class="empty-state">Loading…</div>';
  const payload = await fetch("/api/behavioural-matching").then(r => r.json());
  const data = payload.segments || [];
  if (!data.length) { el.innerHTML = '<div class="empty-state">No data yet.</div>'; return; }

  el.innerHTML = data.map((seg, i) => `
    <div class="card" id="match-card-${i}">
      <div class="card-head" onclick="document.getElementById('match-card-${i}').classList.toggle('open')">
        <div class="card-title-group">
          <span class="card-title">${seg.canonical_name}</span>
          ${seg.behaviourally_agrees
            ? `<span class="badge ok">Behaviour matches the name</span>`
            : `<span class="badge mismatch">Splits into ${seg.distinct_clusters} behaviour groups</span>`}
          <span class="badge stab-${seg.stability_band}">${Math.round(seg.mean_coassignment * 100)}% stable</span>
        </div>
        <div style="display:flex; align-items:center;">
          <div class="card-stats"><span><b>${seg.members.length}</b> name-matched segments</span></div>
          <span class="chevron">▶</span>
        </div>
      </div>
      <div class="card-body">
        <table class="detail-table">
          <thead><tr>
            <th>Platform</th><th>Segment name</th><th>Name match</th>
            <th>Behaviour group</th><th>Why it landed there</th>
          </tr></thead>
          <tbody>
            ${seg.members.map(m => `
              <tr class="${m.borderline ? "row-borderline" : ""}">
                <td><span class="pf-tag">${m.platform_name}</span></td>
                <td>${m.raw_name}</td>
                <td>${Math.round(m.confidence * 100)}%</td>
                <td><span class="group-tag">${m.cluster_name}</span></td>
                <td class="why-cell">
                  ${m.why}
                  <div class="why-nums">
                    ${m.why_detail.map(w => `
                      <span class="why-num ${w.aligned ? "aligned" : "counter"}">
                        ${w.short} ${Math.round(w.value)}
                        <em>${w.z >= 0 ? "+" : ""}${w.z.toFixed(1)} sd</em>
                      </span>`).join("")}
                  </div>
                </td>
              </tr>`).join("")}
          </tbody>
        </table>

        <h4 class="pair-heading">How reliable is this grouping?</h4>
        <p class="fine-print" style="margin-top:0;">
          Each pair below was re-tested across ${window.__nBootstrap || 100} reruns with the click and
          purchase counts re-drawn from their own sampling noise. The number is how often the two
          landed in the same behaviour group.
        </p>
        <table class="detail-table pair-table">
          <tbody>
            ${seg.pairs.slice(0, 10).map(p => `
              <tr>
                <td>${p.a_name} <span class="pf-tag mini">${p.a_platform}</span></td>
                <td>${p.b_name} <span class="pf-tag mini">${p.b_platform}</span></td>
                <td class="pair-freq">
                  <div class="pair-bar"><div class="pair-fill stab-fill-${p.band}" style="width:${p.coassignment * 100}%;"></div></div>
                  <b>${p.runs_together}/100</b>
                </td>
                <td class="pair-band"><span class="badge stab-${p.band}">${p.band}</span></td>
              </tr>`).join("")}
          </tbody>
        </table>
        <p class="fine-print">
          ${seg.pairs.length > 10 ? `Showing the 10 most stable of ${seg.pairs.length} pairs. ` : ""}
          Group average ${Math.round(seg.mean_coassignment * 100)}%. ${seg.stability_text}</p>

        ${!seg.behaviourally_agrees ? `<p class="finding-note">
          <b>What this means.</b> These segments share a name but land in different behaviour groups
          (${seg.groups_present.join(" · ")}). Buying them as one audience means averaging together
          populations that respond differently — worth a human review before the next flight.</p>` : ""}
      </div>
    </div>
  `).join("");
}

// ---------------------------------------------------------------------
// Cross-Platform Overlap has been removed from this demo entirely — its
// numbers were modelled estimates built on an assumed duplication rate
// rather than anything a clean room actually discloses, and the decision
// was made not to use it in the analysis. See backend/targeting.py's
// module docstring for the fuller reasoning (Target Groups & Next Best
// Action deliberately never depended on it either).
// ---------------------------------------------------------------------


// ---------------------------------------------------------------------
// VIEW 3 — Category Affinity
//
// Ordered so the constraint arrives before the numbers: coverage panel
// first (two of four platforms can answer this at all), then the anchor
// spotlight, then the full matrix, then the single-source basket view.
// ---------------------------------------------------------------------
let categoryData = null;

function catIndexColor(idx) {
  if (idx === null || idx === undefined) return "#EEF1F4";
  const c = Math.max(50, Math.min(200, idx));
  if (c >= 100) {
    const t = (c - 100) / 100;
    return `rgb(${Math.round(255 - t * 165)},${Math.round(255 - t * 70)},${Math.round(255 - t * 165)})`;
  }
  const t = (100 - c) / 50;
  return `rgb(${Math.round(255 - t * 60)},${Math.round(255 - t * 40)},${Math.round(255 - t * 10)})`;
}

function renderCategory() {
  const d = categoryData;
  const el = document.getElementById("category-body");
  if (!d) { el.innerHTML = '<div class="empty-state">Loading…</div>'; return; }

  const cov = d.coverage;
  const sup = d.suppression;

  const coverageHtml = `
    <div class="explain-block">
      <p class="source-callout">
        <b>Based on Amazon Marketing Cloud and Instacart Data Hub</b> — the two platforms in this
        set that observe actual purchases, rather than interest or content.
      </p>
      <div class="diag-row">
        <div class="diag diag-warn">
          <div class="diag-head">
            <span class="diag-title">How safe is it to compare the two?</span>
            <span class="diag-score">${d.grade}</span>
          </div>
          <p>${d.grade_explain}</p>
        </div>
        <div class="diag diag-${sup.mapped_suppressed_cells === 0 ? "pass" : "warn"}">
          <div class="diag-head">
            <span class="diag-title">How much was hidden by the minimum-users floor?</span>
            <span class="diag-score">${(sup.raw_rate * 100).toFixed(1)}%</span>
          </div>
          <p>${sup.raw_suppressed_cells} of ${sup.raw_total_cells} raw cells were suppressed. ${sup.reassurance}</p>
          <p class="fine-print">${sup.note}</p>
        </div>
      </div>
    </div>`;

  const spotlightHtml = `
    <h2 class="section-heading">Who over-buys ${d.anchor.name}</h2>
    <p class="section-sub">
      Each bar is this audience's own buy rate. The dark tick mark is what a typical shopper on that
      platform looks like — the gap between the fill and the tick is the whole story. Both sources are
      shown separately; where they disagree, that disagreement is the finding.
    </p>
    <div class="stack">
      ${d.spotlight.map((s, i) => `
        <div class="card cat-row">
          <div class="cat-row-main">
            <div class="cat-seg">
              <span class="card-title">${s.canonical_name}</span>
              <span class="card-category">${s.segment_category}</span>
            </div>
            <div class="cat-bars">
              ${Object.entries(s.per_platform).sort().map(([code, v]) => {
                const segPct = v.penetration * 100;
                const basePct = v.baseline_penetration !== null ? v.baseline_penetration * 100 : null;
                const scaleMax = Math.max(segPct, basePct || 0) * 1.35;
                return `
                <div class="cat-bar-row">
                  <span class="cat-plat">${v.platform_name}</span>
                  <div class="cat-pct-wrap">
                    <div class="cat-track">
                      ${basePct !== null ? `<div class="cat-base-mark" style="left:${(basePct / scaleMax) * 100}%;" title="Platform average: ${basePct.toFixed(1)}%"></div>` : ""}
                      <div class="cat-fill" style="width:${(segPct / scaleMax) * 100}%; background:${catIndexColor(v.index)};"></div>
                    </div>
                    <div class="cat-pct-caption">
                      <b>${segPct.toFixed(1)}%</b> of this audience buys it${basePct !== null ? ` &nbsp;\u00b7&nbsp; <b>${basePct.toFixed(1)}%</b> of all ${v.platform_name.split(" ")[0]} shoppers do` : ""}
                    </div>
                  </div>
                  <span class="cat-buyers">${fmtNum(v.buyers)} buyers</span>
                </div>`;
              }).join("")}
            </div>
            <div class="cat-verdict">
              <span class="badge agree-${s.agreement.status}">${
                s.agreement.status === "agree" ? "Sources agree"
                : s.agreement.status === "conflict" ? "Sources conflict"
                : s.agreement.status === "partial" ? "Partly agree" : "Single source"}</span>
              <span class="cat-verdict-text">${s.verdict}</span>
            </div>
          </div>
          <p class="cat-agree-note ${s.agreement.status === "conflict" ? "is-conflict" : ""}">${s.agreement.text}</p>
        </div>`).join("")}
    </div>`;

  // ---- full matrix ----
  const plats = d.matrix.platforms;
  const activePlat = document.getElementById("cat-plat-select")
    ? document.getElementById("cat-plat-select").value : plats[0].platform_code;
  const lookup = {};
  d.matrix.cells.forEach(c => {
    if (c.platform_code === activePlat) lookup[`${c.canonical_id}__${c.category_code}`] = c;
  });
  const segsWithData = d.matrix.segments.filter(sg =>
    d.matrix.cells.some(c => c.canonical_id === sg.canonical_id && c.platform_code === activePlat));

  const matrixHtml = `
    <h2 class="section-heading">Every audience, every category</h2>
    <p class="section-sub">One platform at a time — the two see different shopping trips, so
      putting them in one grid would imply a precision that isn't there.</p>
    <div class="controls">
      <label for="cat-plat-select">Source:</label>
      <select id="cat-plat-select">
        ${plats.map(p => `<option value="${p.platform_code}" ${p.platform_code === activePlat ? "selected" : ""}>${p.platform_name}</option>`).join("")}
      </select>
      <span class="fine-print" style="margin:0;">Green = buys more than that platform's average. Grey = less.</span>
    </div>
    <div class="heatmap-wrap">
      <table class="heatmap cat-matrix">
        <thead><tr><th class="row-label-head">Audience</th>
          ${d.categories.map(c => `<th class="${c.is_anchor ? "anchor-col" : ""}">${c.category_name}</th>`).join("")}
        </tr></thead>
        <tbody>
          ${segsWithData.map(sg => `
            <tr><td class="row-label">${sg.canonical_name}</td>
              ${d.categories.map(c => {
                const cell = lookup[`${sg.canonical_id}__${c.category_code}`];
                if (!cell || cell.index === null) return `<td><div class="heat-cell empty">–</div></td>`;
                return `<td><div class="heat-cell ${c.is_anchor ? "anchor-cell" : ""}"
                          style="background:${catIndexColor(cell.index)};"
                          title="${fmtNum(cell.buyers)} buyers of ${fmtNum(cell.users)} in audience">
                          ${Math.round(cell.index)}</div></td>`;
              }).join("")}
            </tr>`).join("")}
        </tbody>
      </table>
    </div>`;

  // ---- basket halo ----
  const b = d.basket;
  const maxLift = Math.max(...b.rows.map(r => r.ci_high), 1.6);
  const scale = v => (v / maxLift) * 100;
  const basketHtml = `
    <h2 class="section-heading">What else is in the basket with ${b.anchor_name}</h2>
    <p class="section-sub">${b.note}</p>
    <div class="explain-block" style="padding:16px 18px;">
      <div class="single-source-warning">
        <b>${b.platform_name} only.</b> ${b.single_source_warning}
      </div>
      <div class="lift-list">
        ${b.rows.map(r => `
          <div class="lift-row ${r.significant ? "" : "lift-ns"}">
            <span class="lift-name">${r.category_name}<em>${r.department}</em></span>
            <div class="lift-track">
              <div class="lift-one" style="left:${scale(1)}%;"></div>
              <div class="lift-ci" style="left:${scale(r.ci_low)}%; width:${scale(r.ci_high - r.ci_low)}%;"></div>
              <div class="lift-point" style="left:${scale(r.lift)}%;"></div>
            </div>
            <span class="lift-val">${r.lift.toFixed(2)}×</span>
            <span class="lift-ci-text">${r.significant ? `${r.ci_low.toFixed(2)}–${r.ci_high.toFixed(2)}` : "not distinguishable from chance"}</span>
          </div>`).join("")}
      </div>
      <p class="fine-print">The dashed line is 1.00 — no relationship. Bars whose range crosses it
        are greyed out, because a lift of 1.03 on a category this size is a coin flip, not a halo.</p>
    </div>`;

  el.innerHTML = coverageHtml + spotlightHtml + matrixHtml + basketHtml;
  const sel = document.getElementById("cat-plat-select");
  if (sel) sel.addEventListener("change", renderCategory);
}

async function loadCategory() {
  categoryData = await fetch("/api/category-affinity").then(r => r.json());
  renderCategory();
}


// ---------------------------------------------------------------------
// VIEW 4 — Target Groups & Next Best Action
//
// Synthesises screens 1, 2, 3 and 5 into one ranked action per canonical
// audience. Every threshold that shapes the outcome is adjustable, and
// every adjustable control (and every score on the page) carries a hover
// tooltip explaining what it means and what moving it does — built with
// the reusable .tip component rather than native title attributes, since
// the demo leans on these explanations being immediately visible, not
// delayed and unstyled.
// ---------------------------------------------------------------------
let targetingData = null;
let targetingThresholds = null; // current slider state, keyed by threshold id

function tip(labelHtml, text, opts) {
  const cls = opts && opts.right ? "tip tip-right" : "tip";
  return `<span class="${cls}">${labelHtml}<span class="tip-bubble">${text}</span></span>`;
}

function actionBadge(action, meta) {
  if (!action) return "";
  return tip(
    `<span class="action-badge action-${action}">${meta.label}</span>`,
    meta.one_liner
  );
}

function renderThresholdPanel(d) {
  const th = d.thresholds;
  const rows = Object.entries(th).map(([key, spec]) => {
    const val = targetingThresholds[key];
    return `
      <div class="thresh-item">
        <div class="thresh-label-row">
          ${tip(`<span class="thresh-label">${spec.label}</span>`, spec.tooltip)}
          <span class="thresh-value" id="thresh-val-${key}">${val}</span>
        </div>
        <input type="range" id="thresh-input-${key}" data-key="${key}"
               min="${spec.min}" max="${spec.max}" step="${spec.step}" value="${val}">
      </div>`;
  }).join("");

  return `
    <div class="thresh-panel" id="thresh-panel">
      <div class="thresh-toggle" id="thresh-toggle">
        <span class="chevron-sm">▶</span>
        <span>Adjust the thresholds behind these calls</span>
        ${tip('<span class="info-dot">ⓘ</span>',
          "Every cutoff below is a business judgment call, not a fact about the data — move any of them and the groups above are recalculated live.")}
      </div>
      <div class="thresh-grid">${rows}</div>
      <div class="thresh-reset-row"><button class="thresh-reset" id="thresh-reset">Reset to defaults</button></div>
      <p class="confidence-note">
        ${tip('<b>Why isn\'t "confidence" itself a slider?</b>', d.confidence_method_note)}
        Confidence combines three checks (comparability grade, behavioural stability, category-source
        agreement) by taking the <b>weakest</b> of the three, not their average — hover the bold text
        for why that's fixed rather than adjustable.
      </p>
    </div>`;
}

function renderSummary(s) {
  const items = [
    ["Audiences reviewed", s.total_canonical_groups, "Every canonical audience with at least one raw segment mapped to it, across all four platforms."],
    ["Targeting priorities", s.targeting_count, "Expand, Test or Split — audiences with a specific action ready to act on now."],
    ["Operational hygiene", s.hygiene_investigate_count + s.hygiene_consolidate_count, "Investigate or Consolidate flags — spend and delivery cleanup, not new targeting."],
    ["Needs review", s.needs_review_count, "Evidence too thin or conflicting to recommend anything yet."],
    ["No action needed", s.no_action_count, "Performing as expected, present everywhere it should be, nothing to change."],
  ];
  return `<div class="tg-summary">${items.map(([label, num, t]) => `
    <div class="tg-summary-item">
      ${tip(`<div class="tg-summary-num">${num}</div>`, t)}
      <div class="tg-summary-label">${label}</div>
    </div>`).join("")}</div>`;
}

function confidenceAxesHtml(axes) {
  const labels = {
    comparability_grade: "Comparability grade (screen 2)",
    behavioural_stability: "Behavioural stability (screen 3)",
    category_agreement: "Category-source agreement (screen 5)",
  };
  return Object.entries(axes).map(([k, v]) => `
    <div class="conf-axis-row">
      <span class="conf-axis-label">${labels[k] || k}</span>
      <div class="conf-axis-track"><div class="conf-axis-fill" style="width:${v * 100}%;"></div></div>
      <span class="conf-axis-val">${v.toFixed(2)}</span>
    </div>`).join("");
}

function targetingCard(g, i) {
  const consolidateBadge = g.consolidate && g.consolidate.flagged
    ? tip('<span class="tg-secondary-badge">also: naming cleanup</span>',
        `This audience is also bought under ${g.consolidate.raw_names.length} different names — see Operational Hygiene below.`)
    : "";

  const platformsHtml = `
    <div class="tg-platform-row">
      ${g.active_platforms.map(p => `<span class="plat-chip active">${p.name}</span>`).join("")}
      ${g.missing_platforms.map(p => tip(`<span class="plat-chip missing">${p.name}</span>`,
          `No raw segment currently maps to this audience on ${p.name}.`)).join("")}
    </div>`;

  return `
    <div class="tg-card" id="tg-card-${i}">
      <div class="tg-card-top">
        <div class="tg-rank">${i + 1}</div>
        <div class="tg-title-block">
          <div class="tg-name-row">
            <span class="tg-name">${g.canonical_name}</span>
            ${actionBadge(g.action, g.action_meta)}
            ${consolidateBadge}
          </div>
          <div class="tg-one-liner">${g.action_meta.one_liner}</div>
        </div>
        <div class="tg-priority-block">
          ${tip(`<div class="tg-priority-num">${g.priority}</div>`,
            "Priority = Confidence × Opportunity size, scaled to 100. Confidence is capped by this group's weakest evidence axis; opportunity size depends on which action applies — see the breakdown below.")}
          <div class="tg-priority-cap">priority</div>
        </div>
      </div>

      <div class="tg-evidence-row">
        <div class="tg-evidence">
          ${tip('<div class="tg-evidence-label">Confidence</div>', "How much this tool trusts the pattern is real — the weakest of three checks, not their average.")}
          <div class="tg-evidence-val">${g.confidence.toFixed(2)}</div>
        </div>
        <div class="tg-evidence">
          ${tip('<div class="tg-evidence-label">Media performance</div>', "Conversion index versus this audience's own platform average, averaged across active platforms. 100 = typical. (Screen 2.)")}
          <div class="tg-evidence-val">${g.performance_index !== null ? Math.round(g.performance_index) : "—"}</div>
        </div>
        <div class="tg-evidence">
          ${tip('<div class="tg-evidence-label">Energy-drink index</div>', "How much more (or less) than average this audience buys energy drinks, from Amazon and Instacart purchase data. (Screen 5.)")}
          <div class="tg-evidence-val">${g.category_index !== null ? Math.round(g.category_index) : "—"}</div>
        </div>
        <div class="tg-evidence">
          ${tip('<div class="tg-evidence-label">Behaviour check</div>', "Whether the raw segments matched under this name actually behave alike, and how often that holds up on repeat testing. (Screen 3.)")}
          <div class="tg-evidence-val" style="font-size:13px;">${g.behavioural.distinct_clusters > 1
            ? `${g.behavioural.distinct_clusters} groups` : "1 group"}</div>
        </div>
      </div>

      <p class="tg-reason">${g.action_reason}</p>
      ${platformsHtml}

      <div class="tg-detail-toggle" onclick="document.getElementById('tg-card-${i}').classList.toggle('open')">
        <span class="chevron-sm">▶</span> Why this confidence score
      </div>
      <div class="tg-detail">
        ${confidenceAxesHtml(g.confidence_axes)}
      </div>
    </div>`;
}

function hygieneCard(g, kind) {
  const meta = ACTION_META_CACHE[kind.toUpperCase()];
  const icon = kind === "investigate" ? "🔍" : "🔗";
  let facts;
  if (kind === "investigate") {
    facts = `
      <span>${tip('Buys energy drinks', "Energy-drink purchase index versus average, from Amazon and Instacart purchase data.")}: <b>${Math.round(g.category_index)}</b></span>
      <span>${tip('Ad performance', "Conversion index versus this audience's own platform average.")}: <b>${Math.round(g.performance_index)}</b></span>`;
  } else {
    facts = `
      <span>${tip('Different names', "How many separately-named audiences across platforms all map to this one group.")}: <b>${g.raw_segment_count}</b></span>
      <span>${tip('Combined spend', "Total spend currently split across all those different names.")}: <b>$${Math.round(g.consolidate.total_spend).toLocaleString()}</b></span>
      <span>Platforms: <b>${g.consolidate.platforms.join(", ")}</b></span>`;
  }
  return `
    <div class="hygiene-card">
      <div class="hygiene-icon ${kind}">${icon}</div>
      <div class="hygiene-body">
        <div class="hygiene-name-row">
          <span class="hygiene-name">${g.canonical_name}</span>
          ${tip(`<span class="priority-pill priority-${g.priority_label}">${g.priority_label} priority</span>`,
            kind === "investigate"
              ? "Ranked against other Investigate cases by the size of the gap between category affinity and delivered performance."
              : "Ranked against other Consolidate cases by total spend currently split across duplicate names.")}
        </div>
        <p class="hygiene-plain">${meta.plain}</p>
        <div class="hygiene-facts">${facts}</div>
      </div>
    </div>`;
}

let ACTION_META_CACHE = {};

function renderTargeting() {
  const el = document.getElementById("targeting-body");
  const d = targetingData;
  if (!d) { el.innerHTML = '<div class="empty-state">Loading…</div>'; return; }
  ACTION_META_CACHE = d.action_meta;

  const targetingHtml = d.targeting_priorities.length
    ? d.targeting_priorities.map((g, i) => targetingCard(g, i)).join("")
    : `<div class="empty-state">No audiences currently clear the thresholds for Expand, Test or Split.
        Try loosening the over-index threshold or the minimum confidence below.</div>`;

  const invHtml = d.operational_hygiene.investigate.length
    ? d.operational_hygiene.investigate.map(g => hygieneCard(g, "investigate")).join("")
    : `<div class="empty-state">Nothing currently flagged.</div>`;
  const conHtml = d.operational_hygiene.consolidate.length
    ? d.operational_hygiene.consolidate.map(g => hygieneCard(g, "consolidate")).join("")
    : `<div class="empty-state">Nothing currently flagged.</div>`;

  const reviewHtml = d.needs_review.length
    ? d.needs_review.map(g => `
        <div class="review-card">
          <div class="review-name">${g.canonical_name}</div>
          <ul class="review-reasons">${g.gate_reasons.map(r => `<li>${r}</li>`).join("")}</ul>
        </div>`).join("")
    : `<div class="empty-state">Nothing is currently held back for lack of evidence.</div>`;

  const noActionHtml = d.no_action.length
    ? `<p class="no-action-note"><b>${d.no_action.length} other audience${d.no_action.length === 1 ? "" : "s"}</b>
        performing as expected with no action needed: ${d.no_action.map(g => g.canonical_name).join(", ")}.</p>`
    : "";

  el.innerHTML = `
    ${renderThresholdPanel(d)}
    ${renderSummary(d.summary)}

    <h2 class="section-heading" style="margin-top:26px;">Targeting priorities</h2>
    <p class="section-sub">Ranked by priority — how trustworthy the pattern is, multiplied by how big a lever it is.</p>
    ${targetingHtml}

    <h2 class="section-heading">Operational hygiene</h2>
    <div class="hygiene-intro">
      These two aren't about who to target \u2014 they're about spend and delivery problems worth fixing
      regardless of who the audience is. Investigate means the right people aren't responding to the ads;
      Consolidate means the same people are being bought several times over under different names.
    </div>
    <div class="hygiene-subhead">Investigate \u2014 right audience, ads aren't landing</div>
    <div class="hygiene-subintro">These groups genuinely buy energy drinks a lot, but the media running against them isn't converting. That points at the creative, offer or placement \u2014 not the targeting.</div>
    ${invHtml}
    <div class="hygiene-subhead">Consolidate \u2014 same audience, paid for multiple times</div>
    <div class="hygiene-subintro">Combining these won't change who gets reached \u2014 it clears up reporting and may unlock better rates by concentrating spend under one name.</div>
    ${conHtml}

    <h2 class="section-heading">Needs review</h2>
    <p class="section-sub">Evidence too thin or too contradictory to recommend an action \u2014 flagged rather than guessed at.</p>
    ${reviewHtml}

    ${noActionHtml}
  `;

  const panel = document.getElementById("thresh-panel");
  document.getElementById("thresh-toggle").addEventListener("click", () => panel.classList.toggle("open"));
  Object.keys(d.thresholds).forEach(key => {
    const input = document.getElementById(`thresh-input-${key}`);
    input.addEventListener("input", () => {
      document.getElementById(`thresh-val-${key}`).textContent = input.value;
    });
    input.addEventListener("change", () => {
      targetingThresholds[key] = parseFloat(input.value);
      loadTargeting();
    });
  });
  document.getElementById("thresh-reset").addEventListener("click", () => {
    targetingThresholds = null;
    loadTargeting();
  });
}

async function loadTargeting() {
  if (!targetingThresholds) {
    // First load: pull defaults from the backend rather than hard-coding
    // them a second time in JS.
    const probe = await fetch("/api/target-groups").then(r => r.json());
    targetingThresholds = {};
    Object.entries(probe.thresholds).forEach(([k, v]) => { targetingThresholds[k] = v.value; });
    targetingData = probe;
    renderTargeting();
    return;
  }
  const qs = new URLSearchParams(targetingThresholds).toString();
  targetingData = await fetch(`/api/target-groups?${qs}`).then(r => r.json());
  renderTargeting();
}

// ---------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------
loadInventory();
loadHeatmap();
loadMatching();
loadCategory();
loadTargeting();
