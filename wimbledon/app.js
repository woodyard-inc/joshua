/* ── What Wins on Grass? — Player Intelligence · app.js ─────────── */

"use strict";

// ── State ─────────────────────────────────────────────────────────
const state = {
  year: 2012,
  playerName: null,
  profiles: null,   // { playerName: {...} }
  tournament: null, // { serve:{}, pressure:{}, ... }
};

// ── Boot ──────────────────────────────────────────────────────────
async function boot() {
  await loadYear(2012);

  function onPlayerChange(name) {
    if (!name) { showEmpty(); return; }
    state.playerName = name;
    // Sync both selects
    document.getElementById("player-select").value = name;
    document.getElementById("player-select-empty").value = name;
    renderProfile(name);
  }

  document.getElementById("player-select").addEventListener("change", e => onPlayerChange(e.target.value));
  document.getElementById("player-select-empty").addEventListener("change", e => onPlayerChange(e.target.value));

  document.getElementById("year-pills").addEventListener("click", e => {
    const btn = e.target.closest(".pill");
    if (!btn) return;
    const yr = +btn.dataset.year;
    document.querySelectorAll(".pill").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    state.year = yr;
    loadYear(yr).then(() => {
      const sel = document.getElementById("player-select");
      const cur = sel.value;
      populateDropdown();
      if (state.profiles[cur]) {
        sel.value = cur;
        renderProfile(cur);
      } else {
        sel.value = "";
        showEmpty();
      }
    });
  });
}

async function loadYear(year) {
  const base = location.pathname.includes("github.io") ? "/joshua/wimbledon" : ".";
  const [profiles, tourn] = await Promise.all([
    fetch(`${base}/data/${year}_men_profiles.json`).then(r => r.json()),
    fetch(`${base}/data/${year}_men_tournament.json`).then(r => r.json()),
  ]);
  state.profiles  = profiles;
  state.tournament = tourn;
  populateDropdown();
}

function populateDropdown() {
  const selectors = ["player-select", "player-select-empty"];
  selectors.forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="">Select a player…</option>';
    const names = Object.keys(state.profiles).sort();
    for (const name of names) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      sel.appendChild(opt);
    }
    if (cur && state.profiles[cur]) sel.value = cur;
  });
}

function showEmpty() {
  document.getElementById("empty-state").classList.remove("hidden");
  document.getElementById("profile").classList.add("hidden");
  document.getElementById("player-select").value = "";
  document.getElementById("player-select-empty").value = "";
}

// ── Main render ───────────────────────────────────────────────────
function renderProfile(name) {
  const p  = state.profiles[name];
  const t  = state.tournament;

  document.getElementById("empty-state").classList.add("hidden");
  document.getElementById("profile").classList.remove("hidden");

  renderHero(p, t);
  renderServeWaterfall(p, t);
  renderServeSpeed(p, t);
  renderServeDirection(p);
  renderRally(p, t);
  renderPoints(p);
  renderResilience(p, t);
  renderEnforcer(p, t);
  renderAggression(p, t);
  renderCleanGames(p, t);
  renderStreaks(p, t);
  renderMatchLog(p);
}

// ── Player photo lookup ───────────────────────────────────────────
// Maps profile player name → WebP filename (underscore-separated)
const PLAYER_PHOTOS = {
  "Andy Murray":           "Andy_Murray",
  "Benoit Paire":          "Benoit_Paire",
  "David Ferrer":          "David_Ferrer",
  "Fernando Verdasco":     "Fernando_Verdasco",
  "Florian Mayer":         "Florian_Mayer",
  "Jerzy Janowicz":        "Jerzy_Janowicz",
  "Jo-Wilfried Tsonga":    "Jo-Wilfried_Tsonga",
  "Juan Martin Del Potro": "Juan_Martin_Del_Potro",
  "Juan Monaco":           "Juan_Monaco",
  "Julien Benneteau":      "Julien_Benneteau",
  "Mikhail Youzhny":       "Mikhail_Youzhny",
  "Nicolas Almagro":       "Nicolas_Almagro",
  "Novak Djokovic":        "Novak_Djokovic",
  "Philipp Kohlschreiber": "Philipp_Kohlschreiber",
  "Radek Stepanek":        "Radek_Stepanek",
  "Richard Gasquet":       "Richard_Gasquet",
  "Roger Federer":         "Roger_Federer",
  "Ryan Harrison":         "Ryan_Harrison",
  "Sergiy Stakhovsky":     "Sergiy_Stakhovsky",
  "Stanislas Wawrinka":    "Stanislas_Wawrinka",
  "Viktor Troicki":        "Viktor_Troicki",
};

// ── Hero ──────────────────────────────────────────────────────────
function renderHero(p, t) {
  const parts    = p.player.split(" ");
  const initials = parts.length >= 2
    ? parts[0][0] + parts[parts.length - 1][0]
    : p.player.slice(0, 2);

  const visualEl  = document.getElementById("hero-visual");
  const photoFile = PLAYER_PHOTOS[p.player];
  const base      = location.pathname.includes("github.io") ? "/joshua/wimbledon" : ".";

  function showInitials() {
    visualEl.className = "hero-visual has-initials";
    visualEl.innerHTML = `<span class="hero-initials-text">${initials.toUpperCase()}</span>`;
  }

  if (photoFile) {
    const img = document.createElement("img");
    img.alt   = p.player;
    img.src   = `${base}/players/${photoFile}.webp`;
    img.onerror = showInitials;
    img.onload  = () => {
      // Portrait images (height > width) get pillarbox; landscape get cover-crop
      const isPortrait = img.naturalHeight > img.naturalWidth;
      visualEl.className = "hero-visual has-photo" + (isPortrait ? " portrait" : "");
      visualEl.innerHTML = "";
      visualEl.appendChild(img);
    };
    // Set class optimistically before load completes
    visualEl.className = "hero-visual has-photo";
    visualEl.innerHTML = "";
    visualEl.appendChild(img);
  } else {
    showInitials();
  }
  document.getElementById("player-name").textContent   = p.player;
  document.getElementById("player-meta").textContent   =
    `${p.year} Wimbledon · ${p.matches_played} match${p.matches_played !== 1 ? "es" : ""}`;

  document.getElementById("srv-pts-badge").textContent =
    `${p.serve.total_pts} serve pts`;

  const hl = document.getElementById("headline-stats");
  hl.innerHTML = "";

  const hlStats = [
    { val: `${p.serve.first_in_pct}%`,        label: "1st Srv In" },
    { val: `${p.serve.first_won_pct}%`,        label: "1st Srv Won" },
    { val: `${p.aggression.aggression_index}`, label: "Atk Precision" },
    { val: `${p.pressure.bp_saved_pct ?? "—"}%`, label: "BP Saved" },
    { val: `${p.pressure.bp_created_per_opp_sg ?? "—"}`,  label: "BPs/Opp Sg" },
  ];
  for (const s of hlStats) {
    const div = document.createElement("div");
    div.className = "hl-stat";
    div.innerHTML = `<div class="hl-val">${s.val}</div><div class="hl-label">${s.label}</div>`;
    hl.appendChild(div);
  }
}

// ── Serve Waterfall ───────────────────────────────────────────────
function renderServeWaterfall(p, t) {
  const srv = p.serve;
  const ta  = t.serve;

  const rows = [
    {
      stage: 1,
      label: "1st Serve In",
      desc:  "of all serve points",
      val:   srv.first_in_pct,
      avg:   ta.first_in_pct,
    },
    {
      stage: 2,
      label: "1st Serve Won",
      desc:  "of 1st serves that went in",
      val:   srv.first_won_pct,
      avg:   ta.first_won_pct,
    },
    {
      stage: 3,
      label: "2nd Serve In",
      desc:  "of 2nd serve attempts",
      val:   srv.second_in_pct,
      avg:   null,
    },
    {
      stage: 4,
      label: "2nd Serve Won",
      desc:  "of 2nd serves that went in",
      val:   srv.second_won_pct,
      avg:   ta.second_won_pct,
    },
  ];

  const container = document.getElementById("serve-waterfall");
  container.innerHTML = "";
  for (const r of rows) {
    if (r.val == null) continue;
    const div = document.createElement("div");
    div.className = `wf-row stage-${r.stage}`;
    const avgTick = r.avg != null
      ? `<div class="wf-avg-tick" style="left:${r.avg}%"></div>`
      : "";
    div.innerHTML = `
      <div class="wf-labels">
        <span class="wf-label">${r.label}</span>
        <span class="wf-label">${r.desc}</span>
      </div>
      <div class="wf-vals">
        <span class="wf-val-main">${r.val}%</span>
        ${r.avg != null ? `<span class="wf-val-avg">avg ${r.avg}%</span>` : ""}
      </div>
      <div class="wf-track">
        <div class="wf-bar" style="width:${r.val}%"></div>
        ${avgTick}
      </div>`;
    container.appendChild(div);
  }

  // Ace & DF footnote
  const note = document.createElement("div");
  note.style.cssText = "margin-top:10px;font-size:11px;color:var(--ink-muted);display:flex;gap:16px";
  note.innerHTML = `
    <span>Aces: <strong style="color:var(--ink)">${srv.aces_total}</strong> (${srv.ace_pct}% of srv pts)</span>
    <span>DFs: <strong style="color:var(--ink)">${srv.dfs_total}</strong> (${srv.df_pct}% of srv pts)</span>`;
  container.appendChild(note);
}

// ── Serve Speed ───────────────────────────────────────────────────
function renderServeSpeed(p, t) {
  const sp  = p.serve_speed;
  const ta  = t.serve_speed;
  const MAX = 230; // km/h display max

  const container = document.getElementById("speed-viz");
  container.innerHTML = "";

  const rows = [
    { cls: "first",  label: "1st Serve", mean: sp.first_avg_kmh,  sd: sp.first_sd_kmh,  mph: sp.first_avg_mph,  avg: ta.first_avg_kmh  },
    { cls: "second", label: "2nd Serve", mean: sp.second_avg_kmh, sd: sp.second_sd_kmh, mph: sp.second_avg_mph, avg: ta.second_avg_kmh },
  ];

  for (const r of rows) {
    if (!r.mean) continue;
    const fillPct  = (r.mean / MAX * 100).toFixed(1);
    const sdLoPct  = ((r.mean - (r.sd || 0)) / MAX * 100).toFixed(1);
    const sdHiPct  = ((r.mean + (r.sd || 0)) / MAX * 100).toFixed(1);
    const sdWidPct = (((r.sd || 0) * 2) / MAX * 100).toFixed(1);

    const div = document.createElement("div");
    div.className = `speed-row ${r.cls}`;
    div.innerHTML = `
      <div class="speed-label">${r.label}</div>
      <div class="speed-bar-wrap">
        <div class="speed-track" style="flex:1">
          <div class="speed-fill" style="width:${fillPct}%">${r.mean} km/h</div>
          ${r.sd ? `<div class="speed-sd-range" style="left:${sdLoPct}%;width:${sdWidPct}%"></div>` : ""}
        </div>
        <span class="speed-sd-label">${r.mph} mph${r.sd ? ` ±${r.sd}` : ""}</span>
      </div>
      ${r.avg ? `<div style="font-size:11px;color:var(--ink-muted);margin-top:3px">Tournament avg: ${r.avg} km/h</div>` : ""}`;
    container.appendChild(div);
  }
}

// ── Serve Direction ───────────────────────────────────────────────
function renderServeDirection(p) {
  const sd   = p.serve_direction;
  const deuce = sd.deuce ?? { wide_pct: sd.wide_pct, body_pct: sd.body_pct, t_pct: sd.t_pct };
  const ad    = sd.ad    ?? { wide_pct: sd.wide_pct, body_pct: sd.body_pct, t_pct: sd.t_pct };

  const C = { cobalt:"#002FA7", terracotta:"#D35220", forest:"#01482A", paper:"#FFFDF8", ink:"#141414", muted:"#8A857B" };
  const fmt = v => v != null ? Math.round(v) + "%" : "—";
  const op  = pct => Math.max(0.06, (pct ?? 0) / 100 * 0.55).toFixed(2);

  // Layout constants (viewBox 0 0 200 300)
  const netY = 30, netH = 12;
  const bx = 12;                        // left margin
  const boxW = 176;                     // total court width
  const boxH = 140;                     // service box height
  const boxY = netY + netH;             // top of service boxes
  const halfW = (boxW - 4) / 2;        // width of each service box (gap=4)
  const lx = bx;                        // left (Ad) box x
  const rx = bx + halfW + 4;            // right (Deuce) box x

  // Helper: draw one service box with 3 zones (left→right colour order varies)
  // zones: [{w:pct, fill, label}, ...]  left to right
  function drawBox(x, y, w, h, zones) {
    let html = `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${C.paper}" stroke="${C.ink}" stroke-width="1.2"/>`;
    // zone fills — widths proportional to pct, scaled to box width
    const total = zones.reduce((s, z) => s + (z.w ?? 0), 0) || 100;
    let cx = x;
    for (const z of zones) {
      const zw = (z.w / total) * w;
      html += `<rect x="${cx.toFixed(1)}" y="${y}" width="${zw.toFixed(1)}" height="${h}" fill="${z.fill}" opacity="${op(z.w)}"/>`;
      cx += zw;
    }
    // Dashed zone dividers (inner only)
    cx = x;
    for (let i = 0; i < zones.length - 1; i++) {
      cx += (zones[i].w / total) * w;
      html += `<line x1="${cx.toFixed(1)}" y1="${y}" x2="${cx.toFixed(1)}" y2="${y+h}" stroke="${C.ink}" stroke-width="0.4" stroke-dasharray="3,2"/>`;
    }
    return html;
  }

  // Ad court box zones (left→right): Wide | Body | T
  const adZones = [
    { w: ad.wide_pct ?? 0, fill: C.cobalt,     label: "W", sub: fmt(ad.wide_pct) },
    { w: ad.body_pct ?? 0, fill: C.terracotta, label: "B", sub: fmt(ad.body_pct) },
    { w: ad.t_pct    ?? 0, fill: C.forest,     label: "T", sub: fmt(ad.t_pct)    },
  ];
  // Deuce court box zones (left→right): T | Body | Wide
  const deuceZones = [
    { w: deuce.t_pct    ?? 0, fill: C.forest,     label: "T", sub: fmt(deuce.t_pct)    },
    { w: deuce.body_pct ?? 0, fill: C.terracotta, label: "B", sub: fmt(deuce.body_pct) },
    { w: deuce.wide_pct ?? 0, fill: C.cobalt,     label: "W", sub: fmt(deuce.wide_pct) },
  ];

  const svg = document.getElementById("court-svg");
  let html = "";

  // NET bar
  html += `<rect x="${bx}" y="${netY}" width="${boxW}" height="${netH}" fill="${C.ink}"/>`;
  html += `<text x="${bx + boxW/2}" y="${netY + netH - 3}" text-anchor="middle" fill="${C.paper}" font-size="6.5" font-family="Helvetica,sans-serif" letter-spacing="0.22em">NET</text>`;

  // Service boxes
  html += drawBox(lx, boxY, halfW, boxH, adZones);
  html += drawBox(rx, boxY, halfW, boxH, deuceZones);

  // Court labels (box titles)
  const titleY = boxY + boxH + 14;
  html += `<text x="${lx + halfW/2}" y="${titleY}" text-anchor="middle" fill="${C.muted}" font-size="6" font-family="Helvetica,sans-serif" letter-spacing="0.18em">AD COURT</text>`;
  html += `<text x="${rx + halfW/2}" y="${titleY}" text-anchor="middle" fill="${C.muted}" font-size="6" font-family="Helvetica,sans-serif" letter-spacing="0.18em">DEUCE COURT</text>`;

  // Zone labels + percentages
  const labelY = titleY + 14;
  function zoneLabels(x, w, zones) {
    const total = zones.reduce((s, z) => s + (z.w ?? 0), 0) || 100;
    let lx2 = x, out = "";
    for (const z of zones) {
      const zw = (z.w / total) * w;
      const cx = lx2 + zw / 2;
      const col = z.fill;
      out += `<text x="${cx.toFixed(1)}" y="${labelY}" text-anchor="middle" fill="${col}" font-size="7.5" font-family="Helvetica,sans-serif" font-weight="700">${z.label}</text>`;
      out += `<text x="${cx.toFixed(1)}" y="${labelY+11}" text-anchor="middle" fill="${C.muted}" font-size="6.5" font-family="Helvetica,sans-serif">${z.sub}</text>`;
      lx2 += zw;
    }
    return out;
  }
  html += zoneLabels(lx, halfW, adZones);
  html += zoneLabels(rx, halfW, deuceZones);

  // Baseline
  const baseY = boxY + boxH + 3;
  html += `<line x1="${bx}" y1="${baseY}" x2="${bx+boxW}" y2="${baseY}" stroke="${C.ink}" stroke-width="1.2"/>`;

  svg.innerHTML = html;

  // Compact legend below — overall W/B/T
  const legend = document.getElementById("direction-legend");
  legend.innerHTML = "";
  const zones = [
    { name: "Wide",  pct: sd.wide_pct, color: C.cobalt      },
    { name: "Body",  pct: sd.body_pct, color: C.terracotta  },
    { name: "T / Centre", pct: sd.t_pct, color: C.forest    },
  ];
  for (const z of zones) {
    const div = document.createElement("div");
    div.className = "dir-row";
    div.innerHTML = `
      <div class="dir-label-row">
        <span class="dir-name" style="color:${z.color}">${z.name}</span>
        <span class="dir-pct">${z.pct ?? "—"}%</span>
      </div>
      <div class="dir-track">
        <div class="dir-bar" style="width:${z.pct ?? 0}%;background:${z.color}"></div>
      </div>`;
    legend.appendChild(div);
  }
}

// ── Rally Length (shot count) ─────────────────────────────────────
function renderRally(p, t) {
  const rs = p.rally_shots;
  const container = document.getElementById("rally-viz");

  // Rally column not populated for this year — show clear N/A
  if (!rs?.available) {
    container.innerHTML = `
      <div class="rally-na">
        <span class="rally-na-label">Not Available (${p.year})</span>
        <p class="rally-na-note">The Rally (shot count) column is not populated in the ${p.year} IBM SlamTracker dataset.</p>
      </div>`;
    return;
  }

  const ta = t.rally_shots;
  const groups = [
    {
      heading: "Serving",
      rows: [
        { label: "All",       val: rs.srv_all_avg, avg: ta?.srv_all_avg },
        { label: "1st serve", val: rs.srv_1st_avg, avg: ta?.srv_1st_avg },
        { label: "2nd serve", val: rs.srv_2nd_avg, avg: ta?.srv_2nd_avg },
      ],
    },
    {
      heading: "Returning",
      rows: [
        { label: "All",    val: rs.ret_all_avg, avg: ta?.ret_all_avg },
        { label: "vs 1st", val: rs.ret_1st_avg, avg: ta?.ret_1st_avg },
        { label: "vs 2nd", val: rs.ret_2nd_avg, avg: ta?.ret_2nd_avg },
      ],
    },
  ];

  const MAX_BALLS = 10;

  function ballSVG(filled) {
    return `<svg viewBox="0 0 14 14" class="rally-ball ${filled ? "rally-ball--on" : "rally-ball--off"}">
      <circle cx="7" cy="7" r="6" />
      <path d="M2.5 4.5 Q7 6 11.5 4.5" fill="none" stroke-width="1" />
      <path d="M2.5 9.5 Q7 8 11.5 9.5" fill="none" stroke-width="1" />
    </svg>`;
  }

  function renderRow(label, val, avg) {
    if (val == null) return "";
    const count    = Math.round(val);
    const avgCount = avg != null ? Math.round(avg) : null;
    const shown    = Math.min(Math.max(count, avgCount ?? 0, MAX_BALLS), MAX_BALLS);
    let balls = "";
    for (let i = 1; i <= shown; i++) balls += ballSVG(i <= count);

    const avgLine = avgCount != null
      ? `<div class="rally-avg-line" style="left:${Math.min(avgCount, shown) / shown * 100}%"
           title="Tournament avg: ${avg} shots"></div>`
      : "";

    return `
      <div class="rally-row">
        <span class="rally-label">${label}</span>
        <div class="rally-balls-wrap">
          <div class="rally-balls">${balls}${avgLine}</div>
          <span class="rally-shot-count">${val.toFixed(1)} shots</span>
        </div>
      </div>`;
  }

  container.innerHTML = groups.map(g => `
    <div class="rally-group">
      <div class="rally-group-heading">${g.heading}</div>
      ${g.rows.map(r => renderRow(r.label, r.val, r.avg)).join("")}
    </div>`).join("");
}

// ── Points Won ────────────────────────────────────────────────────
function renderPoints(p) {
  const pts = p.points;
  const container = document.getElementById("points-viz");
  const winPct = pts.win_pct ?? 0;

  let html = `
    <div class="pts-total-row">
      <span class="pts-big">${pts.total_won}</span>
      <span class="pts-denom">/ ${pts.total_played}</span>
      <span class="pts-win-pct">total points won (${winPct}%)</span>
    </div>
    <div class="pts-win-bar"><div class="pts-win-fill" style="width:${winPct}%"></div></div>
    <div class="pts-matches-label">Match breakdown</div>`;

  for (const m of p.match_summaries) {
    const mPct = m.points_played > 0
      ? (m.points_won / m.points_played * 100).toFixed(0)
      : "—";
    const bbHtml = m.bad_break_match
      ? `<span class="bb-flag">⚡ Bad Break</span>`
      : "";
    html += `
      <div class="pts-match-row">
        <span class="pts-opp">vs ${m.opponent}</span>
        <span class="pts-score">${m.points_won}</span>
        <span class="pts-won-frac">/ ${m.points_played} (${mPct}%)</span>
        <span class="td-bb">${bbHtml}</span>
      </div>`;
  }

  container.innerHTML = html;
}

// ── Resilience ────────────────────────────────────────────────────
function renderResilience(p, t) {
  const pr  = p.pressure;
  const avg = t.pressure.bp_saved_pct ?? 0;
  const val = pr.bp_saved_pct ?? 0;

  const container = document.getElementById("resilience-viz");
  container.innerHTML = `
    <div class="res-big-row">
      <div class="res-stat">
        <div class="res-val main">${val}%</div>
        <div class="res-label">BP Saved</div>
      </div>
      <div class="res-stat">
        <div class="res-val secondary">${pr.bp_saved} / ${pr.bp_faced}</div>
        <div class="res-label">Saved / Faced</div>
      </div>
    </div>
    <div class="res-bar-section">
      <div class="res-bar-label">
        <span>0%</span>
        <span>Tournament avg ${avg}%</span>
        <span>100%</span>
      </div>
      <div class="res-track">
        <div class="res-fill" style="width:${val}%"></div>
        <div class="res-avg-tick" style="left:${avg}%"></div>
      </div>
    </div>`;
}

// ── Enforcer ──────────────────────────────────────────────────────
function renderEnforcer(p, t) {
  const pr  = p.pressure;
  const avg = t.pressure.bp_created_per_opp_sg ?? 0;
  const val = pr.bp_created_per_opp_sg ?? 0;
  const convAvg = t.pressure.bp_conv_pct ?? 0;
  const convVal = pr.bp_conv_pct ?? 0;

  // Scale bar to max of (val, avg) * 1.3 for headroom
  const MAX = Math.max(val, avg, 0.5) * 1.4;
  const playerBarPct = (val / MAX * 100).toFixed(1);
  const avgBarPct    = (avg / MAX * 100).toFixed(1);
  const convPlayerPct = convVal;
  const convAvgPct    = convAvg;

  const container = document.getElementById("enforcer-viz");
  container.innerHTML = `
    <div class="enf-big">${val}</div>
    <div class="enf-sub">BP created per opp. service game</div>
    <div class="enf-bar-section">
      <div class="enf-bar-row">
        <div class="enf-bar-label">
          <span>BP creation (per opp. sg)</span>
          <span>${val} vs avg ${avg}</span>
        </div>
        <div class="enf-track">
          <div class="enf-fill enf-player-fill" style="width:${playerBarPct}%"></div>
        </div>
        <div class="enf-track" style="margin-top:3px">
          <div class="enf-fill enf-avg-fill" style="width:${avgBarPct}%"></div>
        </div>
      </div>
      <div class="enf-bar-row" style="margin-top:12px">
        <div class="enf-bar-label">
          <span>BP conversion %</span>
          <span>${convVal}% vs avg ${convAvg}%</span>
        </div>
        <div class="enf-track">
          <div class="enf-fill" style="width:${convVal}%;background:var(--cobalt);opacity:.6"></div>
        </div>
        <div class="enf-track" style="margin-top:3px">
          <div class="enf-fill enf-avg-fill" style="width:${convAvg}%"></div>
        </div>
      </div>
    </div>
    <div style="font-size:11px;color:var(--ink-muted);margin-top:12px">
      Total BPs created: ${pr.bp_created} · Converted: ${pr.bp_converted}
    </div>`;
}

// ── Attack Precision ──────────────────────────────────────────────
function renderAggression(p, t) {
  const ag   = p.aggression;
  const avg  = t.aggression.aggression_index ?? 0;
  const val  = ag.aggression_index ?? 0;

  // Net: winners MINUS errors — positive means more winners than mistakes
  const net     = ag.winners - ag.unf_err;
  const netSign = net >= 0 ? "+" : "";
  // W:UE ratio — how many winners per error
  const ratio   = ag.unf_err > 0 ? (ag.winners / ag.unf_err).toFixed(2) : "∞";

  const container = document.getElementById("aggression-viz");
  container.innerHTML = `
    <div class="agg-meter-wrap">
      <div class="agg-score">${val}</div>
      <div class="agg-label">Winners ÷ (W + UE) × 100 · tournament avg: ${avg}</div>
    </div>
    <div class="agg-track">
      <div class="agg-fill" style="width:${val}%"></div>
      <div class="agg-avg-tick" style="left:${avg}%"></div>
    </div>
    <div class="agg-avg-note">Higher = more attacking shots convert to winners, not errors</div>
    <div class="agg-breakdown">
      <div class="agg-item">
        <span class="agg-num">${ag.winners}</span>
        <span class="agg-sub">Winners</span>
      </div>
      <div class="agg-item">
        <span class="agg-num">${ag.unf_err}</span>
        <span class="agg-sub">Unf. Errors</span>
      </div>
      <div class="agg-item">
        <span class="agg-num" style="color:${net >= 0 ? 'var(--cobalt)' : 'var(--terracotta)'}">${netSign}${net}</span>
        <span class="agg-sub">Net (W − UE)</span>
      </div>
      <div class="agg-item">
        <span class="agg-num">${ratio}</span>
        <span class="agg-sub">W : UE</span>
      </div>
    </div>`;
}

// ── Clean Games ───────────────────────────────────────────────────
function renderCleanGames(p, t) {
  const cg  = p.clean_games;
  const avg = t.clean_games;

  const container = document.getElementById("clean-viz");
  container.innerHTML = "";

  const rows = [
    {
      label: "Service games won cleanly",
      val: cg.srv_clean_pct,
      avg: avg.srv_clean_pct,
      note: `${cg.srv_clean} clean / ${cg.srv_games} service games won`,
    },
    {
      label: "Break games won cleanly",
      val: cg.ret_clean_pct,
      avg: avg.ret_clean_pct,
      note: `${cg.ret_clean} clean / ${cg.ret_games_won} break games won`,
    },
  ];

  for (const r of rows) {
    if (r.val == null) continue;
    const div = document.createElement("div");
    div.className = "clean-row";
    div.innerHTML = `
      <div class="clean-label-row">
        <span class="clean-label">${r.label}</span>
        <span class="clean-pct">${r.val}%</span>
      </div>
      <div class="clean-track">
        <div class="clean-fill" style="width:${r.val}%"></div>
        ${r.avg != null ? `<div class="clean-avg-tick" style="left:${r.avg}%"></div>` : ""}
      </div>
      <div class="clean-games-note">${r.note}${r.avg ? ` · avg ${r.avg}%` : ""}</div>`;
    container.appendChild(div);
  }
}

// ── Streaks ───────────────────────────────────────────────────────
function renderStreaks(p, t) {
  const st  = p.streaks;
  const avg = t.streaks.streaks_per_match ?? 0;
  const val = st.streaks_per_match ?? 0;
  const MAX = Math.max(val, avg, 10) * 1.3;

  const container = document.getElementById("streaks-viz");
  container.innerHTML = `
    <div class="streak-big-row">
      <div class="streak-num">${val}</div>
      <div class="streak-unit">streaks/match</div>
    </div>
    <div class="streak-bar-section">
      <div class="streak-bar-label">
        <span>Streaks per match</span>
        <span>avg ${avg}</span>
      </div>
      <div class="streak-track">
        <div class="streak-fill" style="width:${(val/MAX*100).toFixed(1)}%"></div>
        <div class="streak-avg-tick" style="left:${(avg/MAX*100).toFixed(1)}%"></div>
      </div>
    </div>
    <div style="font-size:11px;color:var(--ink-muted);margin-top:10px">
      Total streaks across tournament: ${st.total_streaks}
      · ${p.matches_played} matches played
    </div>`;
}

// ── Match Log ─────────────────────────────────────────────────────
function renderMatchLog(p) {
  const container = document.getElementById("match-log");
  let rows = "";
  for (const m of p.match_summaries) {
    const pct = m.points_played > 0
      ? (m.points_won / m.points_played * 100).toFixed(0) + "%"
      : "—";
    const bb  = m.bad_break_match ? `<span class="bb-flag">⚡ Bad Break</span>` : "";
    rows += `<tr>
      <td class="td-opp">vs ${m.opponent}</td>
      <td class="td-pts">${m.points_won} / ${m.points_played}</td>
      <td class="td-pct">${pct}</td>
      <td class="td-bb">${bb}</td>
    </tr>`;
  }
  container.innerHTML = `
    <table class="match-log-table">
      <thead>
        <tr>
          <th>Opponent</th>
          <th>Points Won</th>
          <th>Win %</th>
          <th>Flag</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ── Start ─────────────────────────────────────────────────────────
boot();
