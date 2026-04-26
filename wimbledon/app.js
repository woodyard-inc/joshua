/* ── What Wins on Grass — Player Intelligence · app.js ─────────── */
"use strict";

// ── State ─────────────────────────────────────────────────────────
const state = {
  year:       2017,
  playerName: null,
  profiles:   null,
  tournament: null,
};

const AVAILABLE_YEARS = [2017, 2018, 2019];

// ── Boot ──────────────────────────────────────────────────────────
async function boot() {
  // Set active year pill
  document.querySelectorAll(".pill").forEach(p => {
    p.classList.toggle("active", +p.dataset.year === state.year);
  });

  await loadYear(state.year);

  function onPlayerChange(name) {
    if (!name) { showEmpty(); return; }
    state.playerName = name;
    document.getElementById("player-select").value       = name;
    document.getElementById("player-select-empty").value = name;
    renderProfile(name);
  }

  document.getElementById("player-select")
    .addEventListener("change", e => onPlayerChange(e.target.value));
  document.getElementById("player-select-empty")
    .addEventListener("change", e => onPlayerChange(e.target.value));

  document.getElementById("year-pills").addEventListener("click", e => {
    const btn = e.target.closest(".pill");
    if (!btn) return;
    const yr = +btn.dataset.year;
    if (yr === state.year) return;
    document.querySelectorAll(".pill").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    state.year = yr;
    loadYear(yr).then(() => {
      // Try to keep same player; if they didn't compete this year say so
      const prev = state.playerName;
      if (prev && state.profiles[prev]) {
        document.getElementById("player-select").value = prev;
        renderProfile(prev);
      } else if (prev) {
        // Player known but not in this year's draw
        showNotCompeting(prev, yr);
      } else {
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
  state.year      = year;
  populateDropdown();
}

function populateDropdown() {
  const ids = ["player-select", "player-select-empty"];
  ids.forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="">Select a player…</option>';
    for (const name of Object.keys(state.profiles).sort()) {
      const opt = document.createElement("option");
      opt.value = opt.textContent = name;
      sel.appendChild(opt);
    }
    if (cur && state.profiles[cur]) sel.value = cur;
  });
}

function showEmpty() {
  document.getElementById("empty-state").classList.remove("hidden");
  document.getElementById("profile").classList.add("hidden");
  document.getElementById("player-select").value       = "";
  document.getElementById("player-select-empty").value = "";
  state.playerName = null;
}

function showNotCompeting(name, year) {
  document.getElementById("empty-state").classList.add("hidden");
  document.getElementById("profile").classList.remove("hidden");
  // Keep the player selected in the dropdown so switching back restores them
  state.playerName = name;

  // Clear hero and hide metrics grid
  document.getElementById("hero-visual").innerHTML = "";
  document.getElementById("player-name").textContent = name;
  document.getElementById("player-meta").textContent = "";
  document.getElementById("headline-stats").innerHTML = "";
  document.querySelector(".metrics-grid").classList.add("hidden");

  // Show (or create) not-competing message block
  let nc = document.getElementById("not-competing");
  if (!nc) {
    nc = document.createElement("div");
    nc.id = "not-competing";
    document.getElementById("profile").appendChild(nc);
  }
  nc.className = "not-competing";
  nc.innerHTML = `
    <p class="not-competing-year">Wimbledon ${year}</p>
    <p class="not-competing-msg">${name} did not compete</p>
    <p class="not-competing-sub">This player does not appear in the ${year} men's draw. Select a different year or choose another player.</p>`;
  nc.classList.remove("hidden");
}

// ── Main render ───────────────────────────────────────────────────
function renderProfile(name) {
  const p = state.profiles[name];
  const t = state.tournament;

  document.getElementById("empty-state").classList.add("hidden");
  document.getElementById("profile").classList.remove("hidden");

  // Clear any not-competing state
  document.querySelector(".metrics-grid").classList.remove("hidden");
  const nc = document.getElementById("not-competing");
  if (nc) nc.classList.add("hidden");

  renderHero(p, t);
  renderServeWaterfall(p, t);
  renderServeSpeed(p, t);
  renderServeDirection(p, t);
  renderRally(p, t);
  renderPoints(p);
  renderResilience(p, t);
  renderEnforcer(p, t);
  renderAggression(p, t);
  renderCleanGames(p, t);
  renderStreaks(p, t);
  renderMatchLog(p);
}

// ── Player photos ─────────────────────────────────────────────────
// Players whose WebP exists in /players/.  Falls back to initials for anyone else.
const PLAYER_PHOTOS = {
  // 2012 era — many also appear in 2017-2019 draws
  "Andy Murray":           "Andy_Murray",
  "Benoit Paire":          "Benoit_Paire",
  "David Ferrer":          "David_Ferrer",
  "Fernando Verdasco":     "Fernando_Verdasco",
  "Florian Mayer":         "Florian_Mayer",
  "Jerzy Janowicz":        "Jerzy_Janowicz",
  "Jo Wilfried Tsonga":    "Jo-Wilfried_Tsonga",
  "Jo-Wilfried Tsonga":    "Jo-Wilfried_Tsonga",
  "Juan Martin Del Potro": "Juan_Martin_Del_Potro",
  "Juan Martin del Potro": "Juan_Martin_Del_Potro",
  "Juan Monaco":           "Juan_Monaco",
  "Julien Benneteau":      "Julien_Benneteau",
  "Mikhail Youzhny":       "Mikhail_Youzhny",
  "Nicolas Almagro":       "Nicolas_Almagro",
  "Dominic Thiem":         "Dominic_Thiem",
  "Novak Djokovic":        "Novak_Djokovic",
  "Philipp Kohlschreiber": "Philipp_Kohlschreiber",
  "Radek Stepanek":        "Radek_Stepanek",
  "Rafael Nadal":          "Rafael_Nadal",
  "Richard Gasquet":       "Richard_Gasquet",
  "Roger Federer":         "Roger_Federer",
  "Ryan Harrison":         "Ryan_Harrison",
  "Sergiy Stakhovsky":     "Sergiy_Stakhovsky",
  "Stan Wawrinka":         "Stanislas_Wawrinka",
  "Stanislas Wawrinka":    "Stanislas_Wawrinka",
  "Viktor Troicki":        "Viktor_Troicki",
};

// ── Hero ──────────────────────────────────────────────────────────
function renderHero(p, t) {
  const parts    = p.player.split(" ");
  const initials = parts.length >= 2
    ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
    : p.player.slice(0, 2).toUpperCase();

  const visualEl  = document.getElementById("hero-visual");
  const photoFile = PLAYER_PHOTOS[p.player];
  const base      = location.pathname.includes("github.io") ? "/joshua/wimbledon" : ".";

  function showInitials() {
    visualEl.className = "hero-visual has-initials";
    visualEl.innerHTML = `<div class="initials-inner"><div class="initials-square"><span class="hero-initials-text">${initials}</span></div></div>`;
  }

  if (photoFile) {
    const img   = document.createElement("img");
    img.alt     = p.player;
    img.src     = `${base}/players/${photoFile}.webp`;
    img.onerror = showInitials;
    visualEl.className = "hero-visual has-photo";
    visualEl.innerHTML = "";
    visualEl.appendChild(img);
  } else {
    showInitials();
  }

  document.getElementById("player-name").textContent =
    p.player;
  document.getElementById("player-meta").textContent =
    `${p.year} Wimbledon · ${p.matches_played} match${p.matches_played !== 1 ? "es" : ""}`;
  document.getElementById("srv-pts-badge").textContent =
    `${p.serve.total_pts} serve pts`;

  const hl = document.getElementById("headline-stats");
  hl.innerHTML = "";
  const hlStats = [
    { val: `${p.serve.first_in_pct ?? "—"}%`,            label: "1st Srv In" },
    { val: `${p.serve.first_won_pct ?? "—"}%`,           label: "1st Srv Won" },
    { val: `${p.aggression.aggression_index ?? "—"}`,    label: "Atk Precision" },
    { val: `${p.pressure.bp_saved_pct ?? "—"}%`,         label: "BP Saved" },
    { val: `${p.pressure.bp_created_per_opp_sg ?? "—"}`, label: "BPs/Opp Sg" },
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
    { stage:1, label:"1st Serve In",   desc:"of all serve points",       val:srv.first_in_pct,   avg:ta.first_in_pct   },
    { stage:2, label:"1st Serve Won",  desc:"of 1st serves that went in", val:srv.first_won_pct,  avg:ta.first_won_pct  },
    { stage:3, label:"2nd Serve In",   desc:"of 2nd-serve attempts",      val:srv.second_in_pct,  avg:null              },
    { stage:4, label:"2nd Serve Won",  desc:"of 2nd serves that went in", val:srv.second_won_pct, avg:ta.second_won_pct },
  ];

  const container = document.getElementById("serve-waterfall");
  container.innerHTML = "";
  for (const r of rows) {
    if (r.val == null) continue;
    const div = document.createElement("div");
    div.className = `wf-row stage-${r.stage}`;
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
        ${r.avg != null ? `<div class="wf-avg-tick" style="left:${r.avg}%"></div>` : ""}
      </div>`;
    container.appendChild(div);
  }

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
  const MAX = 230;
  const container = document.getElementById("speed-viz");
  container.innerHTML = "";

  if (!sp?.available) {
    container.innerHTML = naCard("Serve Speed", p.year);
    return;
  }

  const rows = [
    { cls:"first",  label:"1st Serve", mean:sp.first_avg_kmh,  sd:sp.first_sd_kmh,  mph:sp.first_avg_mph,  avg:ta.first_avg_kmh  },
    { cls:"second", label:"2nd Serve", mean:sp.second_avg_kmh, sd:sp.second_sd_kmh, mph:sp.second_avg_mph, avg:ta.second_avg_kmh },
  ];

  for (const r of rows) {
    if (!r.mean) continue;
    const fillPct  = (r.mean / MAX * 100).toFixed(1);
    const sdLoPct  = ((r.mean - (r.sd || 0)) / MAX * 100).toFixed(1);
    const sdWidPct = (((r.sd || 0) * 2)      / MAX * 100).toFixed(1);
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

  // Omit note: show when radar misses were stripped from speed calculations.
  // Double faults carry ServeNumber==0 so are never included in these counts.
  const omitted = sp.omitted_zero_count ?? 0;
  if (omitted > 0) {
    const note = document.createElement("p");
    note.className = "card-note speed-omit-note";
    note.textContent = `${omitted} serve${omitted === 1 ? "" : "s"} omitted (0 km/h — radar not captured · double faults excluded)`;
    container.appendChild(note);
  }
}

// ── Serve Direction ───────────────────────────────────────────────
// Uses ServeWidth field (W/BW=Wide, B/BC=Body, C=Centre).
// ServingTo not available → no deuce/ad split.
function renderServeDirection(p, t) {
  const sd = p.serve_direction;
  const ta = t.serve_direction;
  const C  = { cobalt:"#002FA7", terracotta:"#D35220", forest:"#01482A",
               paper:"#FFFDF8", ink:"#141414", muted:"#8A857B" };

  const svg     = document.getElementById("court-svg");
  const legend  = document.getElementById("direction-legend");

  if (!sd?.available) {
    svg.innerHTML = "";
    legend.innerHTML = naCard("Serve Direction", p.year);
    return;
  }

  const fmt = v => v != null ? Math.round(v) + "%" : "—";

  // ── Single service-box court (top = net, bottom = baseline)
  // viewBox 0 0 200 300
  const netY = 28; const netH = 10;
  const bx = 20; const boxW = 160; const boxH = 120; const boxY = netY + netH;

  // Zones left→right inside the box: Wide | Body | Centre
  // (mirroring how W/B/C look across the full-court width)
  const zones = [
    { pct: sd.wide_pct   ?? 0, fill: C.cobalt,     label: "Wide",   sub: fmt(sd.wide_pct)   },
    { pct: sd.body_pct   ?? 0, fill: C.terracotta, label: "Body",   sub: fmt(sd.body_pct)   },
    { pct: sd.centre_pct ?? 0, fill: C.forest,     label: "Centre", sub: fmt(sd.centre_pct) },
  ];
  const total = zones.reduce((s, z) => s + z.pct, 0) || 100;

  let html = "";

  // Net
  html += `<rect x="${bx}" y="${netY}" width="${boxW}" height="${netH}" fill="${C.ink}"/>`;
  html += `<text x="${bx + boxW/2}" y="${netY + netH - 2}" text-anchor="middle" fill="${C.paper}" font-size="6" font-family="Helvetica,sans-serif" letter-spacing="0.22em">NET</text>`;

  // Box background
  html += `<rect x="${bx}" y="${boxY}" width="${boxW}" height="${boxH}" fill="${C.paper}" stroke="${C.ink}" stroke-width="1.2"/>`;

  // Zone fills + dividers
  let cx = bx;
  for (let i = 0; i < zones.length; i++) {
    const z  = zones[i];
    const zw = (z.pct / total) * boxW;
    const op = Math.max(0.07, (z.pct ?? 0) / 100 * 0.6).toFixed(2);
    html += `<rect x="${cx.toFixed(1)}" y="${boxY}" width="${zw.toFixed(1)}" height="${boxH}" fill="${z.fill}" opacity="${op}"/>`;
    if (i < zones.length - 1) {
      const lx = cx + zw;
      html += `<line x1="${lx.toFixed(1)}" y1="${boxY}" x2="${lx.toFixed(1)}" y2="${boxY+boxH}" stroke="${C.ink}" stroke-width="0.5" stroke-dasharray="3,2"/>`;
    }
    cx += zw;
  }

  // Zone labels
  const labelY = boxY + boxH / 2 - 6;
  cx = bx;
  for (const z of zones) {
    const zw   = (z.pct / total) * boxW;
    const midx = (cx + cx + zw) / 2;
    html += `<text x="${midx.toFixed(1)}" y="${labelY}" text-anchor="middle" fill="${z.fill}" font-size="8.5" font-family="Helvetica,sans-serif" font-weight="700">${z.label}</text>`;
    html += `<text x="${midx.toFixed(1)}" y="${labelY+13}" text-anchor="middle" fill="${C.muted}" font-size="7.5" font-family="Helvetica,sans-serif">${z.sub}</text>`;
    cx += zw;
  }

  // Baseline
  const baseY = boxY + boxH + 2;
  html += `<line x1="${bx}" y1="${baseY}" x2="${bx+boxW}" y2="${baseY}" stroke="${C.ink}" stroke-width="1.2"/>`;

  // Court note
  html += `<text x="${bx + boxW/2}" y="${baseY + 16}" text-anchor="middle" fill="${C.muted}" font-size="5.5" font-family="Helvetica,sans-serif" letter-spacing="0.12em">OVERALL · AD+DEUCE COMBINED</text>`;

  svg.innerHTML = html;

  // ── Legend: 1st vs 2nd serve breakdown bars
  legend.innerHTML = "";

  const sections = [
    { heading: "1st Serve", data: sd.first_serve  },
    { heading: "2nd Serve", data: sd.second_serve },
  ];

  const dirZones = [
    { key: "wide_pct",   name: "Wide",   color: C.cobalt     },
    { key: "body_pct",   name: "Body",   color: C.terracotta },
    { key: "centre_pct", name: "Centre", color: C.forest     },
  ];

  for (const sec of sections) {
    if (!sec.data) continue;
    const secDiv = document.createElement("div");
    secDiv.style.cssText = "margin-bottom:14px";
    secDiv.innerHTML = `<div style="font-family:var(--font-sans);font-size:var(--t-micro);letter-spacing:0.18em;text-transform:uppercase;color:var(--ink);font-weight:700;margin-bottom:6px">${sec.heading}</div>`;

    for (const z of dirZones) {
      const val = sec.data[z.key] ?? 0;
      const avg = ta?.[z.key.replace("centre", "centre")] ?? null;
      const rowDiv = document.createElement("div");
      rowDiv.className = "dir-row";
      rowDiv.innerHTML = `
        <div class="dir-label-row">
          <span class="dir-name" style="color:${z.color}">${z.name}</span>
          <span class="dir-pct">${val ?? "—"}%</span>
        </div>
        <div class="dir-track">
          <div class="dir-bar" style="width:${val ?? 0}%;background:${z.color}"></div>
        </div>`;
      secDiv.appendChild(rowDiv);
    }
    legend.appendChild(secDiv);
  }

  // Deuce/Ad not available note
  const note = document.createElement("div");
  note.style.cssText = "font-family:var(--font-serif);font-style:italic;font-size:12px;color:var(--ink-muted);margin-top:6px";
  note.textContent = "Deuce/Ad split: Not Available — ServingTo not populated in this dataset.";
  legend.appendChild(note);
}

// ── Rally Length ─────────────────────────────────────────────────
function renderRally(p, t) {
  const rs = p.rally_shots;
  const container = document.getElementById("rally-viz");

  if (!rs?.available) {
    container.innerHTML = `
      <div class="rally-na">
        <span class="rally-na-label">Not Available (${p.year})</span>
        <p class="rally-na-note">The RallyCount column is not populated for ${p.year}.</p>
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
      <circle cx="7" cy="7" r="6"/>
      <path d="M2.5 4.5 Q7 6 11.5 4.5" fill="none" stroke-width="1"/>
      <path d="M2.5 9.5 Q7 8 11.5 9.5" fill="none" stroke-width="1"/>
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
      ? `<div class="rally-avg-line" style="left:${Math.min(avgCount, shown) / shown * 100}%" title="Tournament avg: ${avg} shots"></div>`
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
    const bb = m.bad_break_match ? `<span class="bb-flag">⚡ Bad Break</span>` : "";
    html += `
      <div class="pts-match-row">
        <span class="pts-opp">vs ${m.opponent}</span>
        <span class="pts-score">${m.points_won}</span>
        <span class="pts-won-frac">/ ${m.points_played} (${mPct}%)</span>
        <span>${bb}</span>
      </div>`;
  }
  document.getElementById("points-viz").innerHTML = html;
}

// ── Resilience ────────────────────────────────────────────────────
function renderResilience(p, t) {
  const pr  = p.pressure;
  const avg = t.pressure.bp_saved_pct ?? 0;
  const val = pr.bp_saved_pct ?? 0;
  document.getElementById("resilience-viz").innerHTML = `
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
  const pr       = p.pressure;
  const avg      = t.pressure.bp_created_per_opp_sg ?? 0;
  const val      = pr.bp_created_per_opp_sg ?? 0;
  const convAvg  = t.pressure.bp_conv_pct ?? 0;
  const convVal  = pr.bp_conv_pct ?? 0;
  const MAX      = Math.max(val, avg, 0.5) * 1.4;

  document.getElementById("enforcer-viz").innerHTML = `
    <div class="enf-big">${val}</div>
    <div class="enf-sub">BP created per opp. service game</div>
    <div class="enf-bar-section">
      <div class="enf-bar-row">
        <div class="enf-bar-label">
          <span>BP creation (per opp. sg)</span>
          <span>${val} vs avg ${avg}</span>
        </div>
        <div class="enf-track">
          <div class="enf-fill enf-player-fill" style="width:${(val/MAX*100).toFixed(1)}%"></div>
        </div>
        <div class="enf-track" style="margin-top:3px">
          <div class="enf-fill enf-avg-fill" style="width:${(avg/MAX*100).toFixed(1)}%"></div>
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
  const net  = ag.winners - ag.unf_err;
  const ratio = ag.unf_err > 0 ? (ag.winners / ag.unf_err).toFixed(2) : "∞";

  document.getElementById("aggression-viz").innerHTML = `
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
        <span class="agg-num" style="color:${net >= 0 ? "var(--cobalt)" : "var(--terracotta)"}">${net >= 0 ? "+" : ""}${net}</span>
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
    { label:"Service games won cleanly", val:cg.srv_clean_pct, avg:avg.srv_clean_pct, note:`${cg.srv_clean} clean / ${cg.srv_games} service games won` },
    { label:"Break games won cleanly",   val:cg.ret_clean_pct, avg:avg.ret_clean_pct, note:`${cg.ret_clean} clean / ${cg.ret_games_won} break games won`  },
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
      <div class="clean-games-note">${r.note}${r.avg != null ? ` · avg ${r.avg}%` : ""}</div>`;
    container.appendChild(div);
  }
}

// ── Streaks ───────────────────────────────────────────────────────
function renderStreaks(p, t) {
  const st  = p.streaks;
  const avg = t.streaks.streaks_per_match ?? 0;
  const val = st.streaks_per_match ?? 0;
  const MAX = Math.max(val, avg, 10) * 1.3;

  document.getElementById("streaks-viz").innerHTML = `
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
      Total streaks across tournament: ${st.total_streaks} · ${p.matches_played} matches played
    </div>`;
}

// ── Match Log ─────────────────────────────────────────────────────
function renderMatchLog(p) {
  let rows = "";
  for (const m of p.match_summaries) {
    const pct = m.points_played > 0
      ? (m.points_won / m.points_played * 100).toFixed(0) + "%"
      : "—";
    const bb = m.bad_break_match ? `<span class="bb-flag">⚡ Bad Break</span>` : "";
    rows += `<tr>
      <td class="td-opp">vs ${m.opponent}</td>
      <td class="td-pts">${m.points_won} / ${m.points_played}</td>
      <td class="td-pct">${pct}</td>
      <td>${bb}</td>
    </tr>`;
  }
  document.getElementById("match-log").innerHTML = `
    <table class="match-log-table">
      <thead><tr>
        <th>Opponent</th><th>Points Won</th><th>Win %</th><th>Flag</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ── Helpers ───────────────────────────────────────────────────────
function naCard(metric, year) {
  return `<div class="rally-na">
    <span class="rally-na-label">Not Available (${year})</span>
    <p class="rally-na-note">${metric} data is not populated in the ${year} dataset.</p>
  </div>`;
}

// ── Start ─────────────────────────────────────────────────────────
boot();
