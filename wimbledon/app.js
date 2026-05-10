/* ── What Wins on Grass — Player Intelligence · app.js ─────────── */
"use strict";

// ── State ─────────────────────────────────────────────────────────
const state = {
  year:            2019,
  playerName:      null,
  profiles:        null,
  tournament:      null,
  fingerprints:    null,
  eraStats:        null,        // loaded once at boot
  curatedMatchups: null,        // loaded once at boot
  mode:            "profile",   // "profile" | "leaderboard" | "matchup"
  lbMetric:        "fspw_pct",
  matchupA:        null,
  matchupB:        null,
};

const AVAILABLE_YEARS = [2011, 2012, 2013, 2014, 2015, 2016,
                          2017, 2018, 2019,
                          2021, 2022, 2023, 2024];

// ── Boot ──────────────────────────────────────────────────────────
async function boot() {
  // Set year select to match default state
  document.getElementById("year-select").value = state.year;

  const base = location.pathname.includes("github.io") ? "/joshua/wimbledon" : ".";
  const nc   = { cache: "no-store" };

  // Load global (non-year-specific) data alongside the first year
  const [,, eraStats, curated] = await Promise.all([
    loadYear(state.year),
    null,
    fetch(`${base}/data/era_stats.json`, nc).then(r => r.json()).catch(() => ({})),
    fetch(`${base}/data/curated_matchups.json`, nc).then(r => r.json()).catch(() => []),
  ]);
  state.eraStats        = eraStats;
  state.curatedMatchups = curated;
  renderFeatured();
  renderBacktest();

  function onPlayerChange(name) {
    if (!name) { showEmpty(); return; }
    state.playerName = name;
    document.getElementById("player-select").value = name;
    renderProfile(name);
  }

  document.getElementById("player-select")
    .addEventListener("change", e => onPlayerChange(e.target.value));

  // ── Leaderboard toggle ───────────────────────────────────────────
  document.getElementById("lb-toggle").addEventListener("click", () => {
    if (state.mode === "leaderboard") {
      state.mode = "profile";
      document.getElementById("lb-toggle").classList.remove("active");
      document.getElementById("leaderboard").classList.add("hidden");
      if (state.playerName && state.profiles[state.playerName]) {
        document.getElementById("empty-state").classList.add("hidden");
        document.getElementById("profile").classList.remove("hidden");
      } else {
        showEmpty();
      }
    } else {
      enterMatchupMode(false);   // exit matchup if active
      state.mode = "leaderboard";
      document.getElementById("lb-toggle").classList.add("active");
      document.getElementById("mu-toggle").classList.remove("active");
      document.getElementById("empty-state").classList.add("hidden");
      document.getElementById("profile").classList.add("hidden");
      document.getElementById("leaderboard").classList.remove("hidden");
      renderLeaderboard();
    }
  });

  // ── Compare toggle ───────────────────────────────────────────────
  document.getElementById("mu-toggle").addEventListener("click", () => {
    if (state.mode === "matchup") {
      exitMatchupMode();
    } else {
      enterMatchupMode(true);
    }
  });

  // ── Matchup selectors ────────────────────────────────────────────
  document.getElementById("mu-select-a").addEventListener("change", e => {
    state.matchupA = e.target.value || null;
  });
  document.getElementById("mu-select-b").addEventListener("change", e => {
    state.matchupB = e.target.value || null;
  });

  document.getElementById("mu-swap").addEventListener("click", () => {
    const a = state.matchupA, b = state.matchupB;
    state.matchupA = b; state.matchupB = a;
    document.getElementById("mu-select-a").value = b || "";
    document.getElementById("mu-select-b").value = a || "";
    if (state.matchupA && state.matchupB) runComparison(state.matchupA, state.matchupB);
  });

  document.getElementById("mu-analyse").addEventListener("click", () => {
    if (state.matchupA && state.matchupB) runComparison(state.matchupA, state.matchupB);
  });

  // ── Year select ───────────────────────────────────────────────────
  document.getElementById("year-select").addEventListener("change", e => {
    const yr = +e.target.value;
    if (yr === state.year) return;
    state.year = yr;
    loadYear(yr).then(() => {
      if (state.mode === "leaderboard") return;
      if (state.mode === "matchup") {
        if (state.matchupA && state.matchupB) {
          // Snap dropdowns to the active matchup players (now available in new year's list)
          const selA = document.getElementById("mu-select-a");
          const selB = document.getElementById("mu-select-b");
          if ([...selA.options].some(o => o.value === state.matchupA)) selA.value = state.matchupA;
          if ([...selB.options].some(o => o.value === state.matchupB)) selB.value = state.matchupB;
          runComparison(state.matchupA, state.matchupB);
        }
        return;
      }
      const prev = state.playerName;
      if (prev && state.profiles[prev]) {
        document.getElementById("player-select").value = prev;
        renderProfile(prev);
      } else if (prev) {
        showNotCompeting(prev, yr);
      } else {
        showEmpty();
      }
    });
  });
}

async function loadFingerprintsOnly(year) {
  const base = location.pathname.includes("github.io") ? "/joshua/wimbledon" : ".";
  const nc = { cache: "no-store" };
  const [fps, gps] = await Promise.all([
    fetch(`${base}/data/${year}_fingerprints.json`, nc).then(r => r.json()).catch(() => null),
    fetch(`${base}/data/${year}_grass_profiles.json`, nc).then(r => r.json()).catch(() => ({})),
  ]);
  if (!fps) return null;
  return mergeGrassProfiles(fps, gps);
}

async function loadYear(year) {
  const base = location.pathname.includes("github.io") ? "/joshua/wimbledon" : ".";
  const nc   = { cache: "no-store" };
  const [profiles, tourn, fingerprints, grassProfiles, archetypes, archetypesMeta] = await Promise.all([
    fetch(`${base}/data/${year}_men_profiles.json`, nc).then(r => r.json()),
    fetch(`${base}/data/${year}_men_tournament.json`, nc).then(r => r.json()),
    fetch(`${base}/data/${year}_fingerprints.json`, nc).then(r => r.json()).catch(() => ({})),
    fetch(`${base}/data/${year}_grass_profiles.json`, nc).then(r => r.json()).catch(() => ({})),
    fetch(`${base}/data/${year}_archetypes.json`, nc).then(r => r.json()).catch(() => ({})),
    fetch(`${base}/data/archetypes_meta.json`, nc).then(r => r.json()).catch(() => null),
  ]);
  state.profiles      = profiles;
  state.tournament    = tourn;
  state.fingerprints  = mergeGrassProfiles(fingerprints, grassProfiles);
  state.archetypes    = archetypes;       // {playerName: {id, label, blurb, raw, z_scores}}
  state.archetypesMeta = archetypesMeta;  // {anchor_year, k, archetypes:[...]}
  state.year          = year;
  populateDropdown();
  populateMuDropdowns();
  if (state.mode === "leaderboard") renderLeaderboard();
}

/**
 * Merge grass ATP profiles into Wimbledon PBP fingerprints.
 *
 * For players WITH a fingerprint: blend Tier 1 metrics (n_eff-weighted).
 * For players WITHOUT a fingerprint: create a synthetic fingerprint
 * from the grass profile so the MC engine can still run.
 *
 * Wimbledon PBP data gets a 1.5× relevance multiplier per observation
 * since it comes from the actual venue.
 */
function mergeGrassProfiles(fingerprints, grassProfiles) {
  const WIMBLEDON_RELEVANCE = 1.5;
  const T1_KEYS = ["fsp_pct", "fspw_pct", "sspw_pct", "rpw_pct",
                   "rpw_vs_1st_pct", "rpw_vs_2nd_pct",
                   "sgw_pct", "rgw_pct"];
  const merged = { ...fingerprints };

  for (const [name, grass] of Object.entries(grassProfiles)) {
    if (merged[name]) {
      // Blend: fingerprint already exists — enrich Tier 1 with grass data
      const fp = merged[name];
      const fpN = (fp.n_points || fp.n_srv_points || 0) * WIMBLEDON_RELEVANCE;

      for (const key of T1_KEYS) {
        const fpM  = fp.tier1?.[key] || {};
        const gpM  = grass.tier1?.[key] || {};
        const fpVal = fpM?.value;
        const gpVal = gpM?.value;

        if (fpVal == null && gpVal == null) continue;
        if (fpVal == null) {
          if (!fp.tier1) fp.tier1 = {};
          fp.tier1[key] = gpM;
          continue;
        }
        if (gpVal == null) continue;

        // Both available — weighted blend
        const fN = (fpM.n_eff || fpN);
        const gN = (gpM.n_eff || grass.n_srv_points || 0);
        const total = fN + gN;
        const blended = total > 0
          ? (fpVal * fN + gpVal * gN) / total
          : (fpVal + gpVal) / 2;

        fp.tier1[key] = {
          value: Math.round(blended * 10) / 10,
          n_eff: Math.round(total),
          confidence: total >= 60 ? "RELIABLE" : total >= 30 ? "LOW" : "UNRELIABLE",
          ...(fpM.ci_90_lo != null ? { ci_90_lo: fpM.ci_90_lo, ci_90_hi: fpM.ci_90_hi } : {}),
        };
      }
      fp.grass_enriched     = true;
      fp.grass_n_matches    = grass.n_matches || 0;
      fp.grass_n_srv_points = grass.n_srv_points || 0;
    } else {
      // No fingerprint — create synthetic from grass profile
      merged[name] = {
        player:          grass.player || name,
        year:            grass.year,
        surface:         "grass",
        source:          "grass_atp_only",
        confidence:      grass.confidence || "LOW",
        n_points:        grass.n_srv_points || 0,
        n_matches:       grass.n_matches || 0,
        tier2_available: false,
        tier1:           grass.tier1 || {},
        tier2:           {},
        elo_snapshot:    grass.elo_snapshot || null,
        grass_enriched:  true,
        grass_n_matches: grass.n_matches || 0,
        grass_n_srv_points: grass.n_srv_points || 0,
      };
    }
  }
  return merged;
}

function populateDropdown() {
  const sel = document.getElementById("player-select");
  const cur = sel.value;
  sel.innerHTML = '<option value="">Select a player…</option>';
  for (const name of Object.keys(state.profiles).sort()) {
    const opt = document.createElement("option");
    opt.value = opt.textContent = name;
    sel.appendChild(opt);
  }
  if (cur && state.profiles[cur]) sel.value = cur;
}

function showEmpty() {
  state.mode = "profile";
  document.getElementById("lb-toggle").classList.remove("active");
  document.getElementById("mu-toggle").classList.remove("active");
  document.getElementById("leaderboard").classList.add("hidden");
  document.getElementById("matchup-panel").classList.add("hidden");
  document.getElementById("empty-state").classList.remove("hidden");
  document.getElementById("profile").classList.add("hidden");
  document.getElementById("player-select").value = "";
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
  const f = (state.fingerprints || {})[name] || null;

  state.mode = "profile";
  document.getElementById("lb-toggle").classList.remove("active");
  document.getElementById("mu-toggle").classList.remove("active");
  document.getElementById("leaderboard").classList.add("hidden");
  document.getElementById("matchup-panel").classList.add("hidden");
  document.getElementById("empty-state").classList.add("hidden");
  document.getElementById("profile").classList.remove("hidden");

  // Clear any not-competing state
  document.querySelector(".metrics-grid").classList.remove("hidden");
  const nc = document.getElementById("not-competing");
  if (nc) nc.classList.add("hidden");

  // ── Existing point-by-point cards ─────────────────────────────
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
  renderDuration(p, t);
  renderDistance(p, t);
  renderMatchLog(p);

  // ── Fingerprint cards ─────────────────────────────────────────
  renderTier1(f);
  renderEloSnapshot(f);
  // Serve
  renderServeEntropy(f);
  renderServeSpeedCourage(f);
  renderServeSpeedDifferential(f);
  renderServeDepthEntropy(f);
  // Return & Pressure
  // Removed: renderDfPressure (ablated), renderClutch (mostly UNRELIABLE),
  //          renderFirstServePressure (ablated, mostly UNRELIABLE)
  renderBpCreation(f);
  renderTiebreakDifferential(f);
  renderSetTransitionDelta(f);
  // Rally & Shape
  // Removed: renderRallyVolatility (ablated), renderCourtSide (ablated),
  //          renderMomentum (ablated catch-fire mechanic)
  renderRallyCurve(f);
  // Physical
  renderRunEfficiency(f);
  renderAttritionSlope(f);
  renderHoldAfterBreak(f);
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

  // Append archetype label to the player meta line if available
  const archetype = state.archetypes ? state.archetypes[p.player] : null;
  let archHtml = "";
  if (archetype) {
    const blurb = archetype.blurb ? archetype.blurb.replace(/"/g, "&quot;") : "";
    archHtml = ` · <span class="archetype-tag" title="${blurb}">${archetype.label}</span>`;
  }
  document.getElementById("player-meta").innerHTML =
    `${p.year} Wimbledon · ${p.matches_played} match${p.matches_played !== 1 ? "es" : ""}${archHtml}`;
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
// ServeWidth (W/B/C) split by deduced court (even/odd point index within game).
function renderServeDirection(p, t) {
  const sd  = p.serve_direction;
  const svg = document.getElementById("court-svg");
  document.getElementById("direction-legend").innerHTML = "";

  if (!sd?.available) {
    svg.setAttribute("viewBox", "0 0 200 80");
    svg.innerHTML = `<foreignObject x="0" y="0" width="200" height="80">${naCard("Serve Direction", p.year)}</foreignObject>`;
    return;
  }

  const C = { wide:"#002FA7", body:"#D35220", centre:"#01482A",
              ink:"#141414", paper:"#FFFDF8", muted:"#8A857B" };

  // Two boxes: Deuce (left) and Ad (right)
  // Deuce zones L→R: Wide | Body | T  (T on inner/centre side)
  // Ad zones L→R:    T | Body | Wide  (T on inner/centre side, mirrored)
  // Both T zones meet in the middle of the visualisation
  const DEUCE_ZONES = [
    { key:"wide",   label:"Wide", color:C.wide   },
    { key:"body",   label:"Body", color:C.body   },
    { key:"centre", label:"T",    color:C.centre },
  ];
  const AD_ZONES = [
    { key:"centre", label:"T",    color:C.centre },
    { key:"body",   label:"Body", color:C.body   },
    { key:"wide",   label:"Wide", color:C.wide   },
  ];

  const W = 440, H = 232;
  const BOX_W = 200, ZONE_W = BOX_W / 3;
  const GAP = W - 2 * BOX_W;                // 40px between boxes
  const LX = 0, RX = BOX_W + GAP;           // box x origins

  // Y layout
  const TITLE_Y  = 13;
  const NET_Y    = 18,  NET_H  = 10;
  const LBL1_Y   = NET_Y + NET_H;           // 28
  const LBL_H    = 15;
  const Z1_Y     = LBL1_Y + LBL_H;          // 43
  const ZONE_H   = 74;
  const DIV_Y    = Z1_Y + ZONE_H;           // 117
  const LBL2_Y   = DIV_Y + 2;              // 119
  const Z2_Y     = LBL2_Y + LBL_H;         // 134
  const BASE_Y   = Z2_Y + ZONE_H;          // 208
  const NOTE_Y   = BASE_Y + 16;            // 224

  const fmt = v => v != null ? Math.round(v) + "%" : "—";
  const F = "Helvetica,Arial,sans-serif";

  function box(bx, zones, title, d1, d2) {
    let h = "";
    // Court title
    h += `<text x="${bx + BOX_W/2}" y="${TITLE_Y}" text-anchor="middle" fill="${C.muted}" font-size="7" font-family="${F}" letter-spacing="0.30em">${title}</text>`;
    // Net bar
    h += `<rect x="${bx}" y="${NET_Y}" width="${BOX_W}" height="${NET_H}" fill="${C.ink}"/>`;
    h += `<text x="${bx + BOX_W/2}" y="${NET_Y + NET_H - 2}" text-anchor="middle" fill="${C.paper}" font-size="5.5" font-family="${F}" letter-spacing="0.22em">NET</text>`;
    // Box background
    const BOX_TOTAL_H = BASE_Y - (NET_Y + NET_H);
    h += `<rect x="${bx}" y="${LBL1_Y}" width="${BOX_W}" height="${BOX_TOTAL_H}" fill="${C.paper}" stroke="${C.ink}" stroke-width="0.8"/>`;
    // Serve sections
    h += serveSection(bx, LBL1_Y, Z1_Y, zones, d1, "1ST SERVE");
    // Divider between 1st and 2nd
    h += `<line x1="${bx}" y1="${DIV_Y}" x2="${bx+BOX_W}" y2="${DIV_Y}" stroke="${C.ink}" stroke-width="0.8" opacity="0.4"/>`;
    h += serveSection(bx, LBL2_Y, Z2_Y, zones, d2, "2ND SERVE");
    // Baseline
    h += `<line x1="${bx}" y1="${BASE_Y}" x2="${bx+BOX_W}" y2="${BASE_Y}" stroke="${C.ink}" stroke-width="1"/>`;
    return h;
  }

  function serveSection(bx, lblY, zoneY, zones, data, label) {
    let h = "";
    // Section header
    h += `<rect x="${bx}" y="${lblY}" width="${BOX_W}" height="${LBL_H}" fill="${C.ink}" opacity="0.05"/>`;
    h += `<text x="${bx + BOX_W/2}" y="${lblY + LBL_H - 4}" text-anchor="middle" fill="${C.muted}" font-size="6" font-family="${F}" letter-spacing="0.22em">${label}</text>`;
    // Three equal zones
    for (let i = 0; i < 3; i++) {
      const z   = zones[i];
      const zx  = bx + i * ZONE_W;
      const val = data ? data[`${z.key}_pct`] : null;
      const mid = zoneY + ZONE_H / 2;
      // Zone fill (fixed equal area, low opacity tint)
      h += `<rect x="${zx.toFixed(1)}" y="${zoneY}" width="${ZONE_W.toFixed(1)}" height="${ZONE_H}" fill="${z.color}" opacity="0.07"/>`;
      // Zone divider
      if (i < 2) {
        const lx = zx + ZONE_W;
        h += `<line x1="${lx.toFixed(1)}" y1="${zoneY}" x2="${lx.toFixed(1)}" y2="${zoneY+ZONE_H}" stroke="${C.ink}" stroke-width="0.4" stroke-dasharray="2,2" opacity="0.5"/>`;
      }
      // Percentage (large)
      h += `<text x="${(zx + ZONE_W/2).toFixed(1)}" y="${(mid - 2).toFixed(1)}" text-anchor="middle" fill="${z.color}" font-size="16" font-family="${F}" font-weight="700">${fmt(val)}</text>`;
      // Zone name (small, below)
      h += `<text x="${(zx + ZONE_W/2).toFixed(1)}" y="${(mid + 14).toFixed(1)}" text-anchor="middle" fill="${C.muted}" font-size="6.5" font-family="${F}" letter-spacing="0.10em">${z.label}</text>`;
    }
    return h;
  }

  let html = "";
  html += box(LX, DEUCE_ZONES, "DEUCE COURT", sd.deuce?.first_serve,  sd.deuce?.second_serve);
  html += box(RX, AD_ZONES,    "AD COURT",    sd.ad?.first_serve,     sd.ad?.second_serve);
  // Overall note at bottom centre
  html += `<text x="${W/2}" y="${NOTE_Y}" text-anchor="middle" fill="${C.muted}" font-size="5.5" font-family="${F}" letter-spacing="0.12em">COURT DEDUCED FROM GAME POINT SEQUENCE · W=WIDE · B=BODY · C=T</text>`;

  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = html;
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
      Total streaks across tournament: ${st.total_streaks} · ${p.matches_played} matches played · 3+ pts in a row, resets per game
    </div>`;
}

// ── Match Duration ────────────────────────────────────────────────
function renderDuration(p, t) {
  const md  = p.match_duration;
  const container = document.getElementById("duration-viz");

  if (!md?.available) {
    container.innerHTML = naCard("Match Duration", p.year);
    return;
  }

  const avg = t.match_duration?.avg_mins ?? 0;
  const val = md.avg_mins ?? 0;
  const MAX = Math.max(val, avg, 60) * 1.35;

  function fmtMins(m) {
    const h   = Math.floor(m / 60);
    const min = Math.round(m % 60);
    return h > 0 ? `${h}h ${min}m` : `${min}m`;
  }

  // Scoreboard format H:MM (matches the Wimbledon Rolex display)
  function fmtBoard(m) {
    const h   = Math.floor(m / 60);
    const min = Math.round(m % 60);
    return `${h}:${String(min).padStart(2, "0")}`;
  }

  let matchRows = "";
  for (const m of p.match_summaries) {
    const dur = m.duration_mins != null ? fmtMins(m.duration_mins) : "—";
    matchRows += `
      <div class="dur-match-row">
        <span class="dur-opp"><span class="round-chip">${m.round}</span> vs ${m.opponent}</span>
        <span class="dur-val">${dur}</span>
        ${m.duration_mins != null
          ? `<div class="dur-row-track"><div class="dur-row-fill" style="width:${Math.min(m.duration_mins/MAX*100,100).toFixed(1)}%"></div></div>`
          : `<div class="dur-row-track"></div>`}
      </div>`;
  }

  container.innerHTML = `
    <div class="scoreboard-panel">
      <div class="scoreboard-eyebrow">Avg Match Time</div>
      <div class="scoreboard-digits" aria-label="${fmtMins(val)}">
        ${fmtBoard(val).split("").map(ch =>
          ch === ":" ? `<span class="seg-sep">:</span>`
                     : `<span class="seg-cell">${ch}</span>`
        ).join("")}
      </div>
      <div class="scoreboard-foot">H : MM &nbsp;·&nbsp; Tourn. avg ${fmtBoard(avg)}</div>
    </div>
    <div class="dur-bar-section" style="margin-top:var(--s-4)">
      <div class="dur-bar-label">
        <span>0</span>
        <span>Tourn. avg ${fmtMins(avg)}</span>
        <span>${fmtMins(Math.round(MAX))}</span>
      </div>
      <div class="dur-track">
        <div class="dur-fill" style="width:${(val/MAX*100).toFixed(1)}%"></div>
        <div class="dur-avg-tick" style="left:${(avg/MAX*100).toFixed(1)}%"></div>
      </div>
    </div>
    <div class="dur-matches">${matchRows}</div>`;
}

// ── Distance Run ──────────────────────────────────────────────────
function renderDistance(p, t) {
  const dist = p.distance;
  const container = document.getElementById("distance-viz");

  if (!dist?.available) {
    container.innerHTML = naCard("Distance Run", p.year);
    return;
  }

  const avg  = t.distance?.avg_km_per_match ?? 0;
  const val  = dist.avg_km_per_match ?? 0;
  const MAX  = Math.max(val, avg, 1) * 1.4;

  let matchRows = "";
  for (const m of p.match_summaries) {
    const km = m.distance_km != null ? `${m.distance_km.toFixed(2)} km` : "—";
    matchRows += `
      <div class="dur-match-row">
        <span class="dur-opp"><span class="round-chip">${m.round}</span> vs ${m.opponent}</span>
        <span class="dur-val">${km}</span>
        ${m.distance_km != null ? `<div class="dur-row-track"><div class="dur-row-fill dist-fill" style="width:${Math.min(m.distance_km/MAX*100,100).toFixed(1)}%"></div></div>` : `<div class="dur-row-track"></div>`}
      </div>`;
  }

  const untracked = dist.matches_total - dist.matches_tracked;
  const note = untracked > 0
    ? `<p class="card-note" style="margin-top:10px">${untracked} match${untracked > 1 ? "es" : ""} without tracking data excluded from average</p>`
    : "";

  container.innerHTML = `
    <div class="dur-big-row">
      <div class="dur-num">${val.toFixed(2)} km</div>
      <div class="dur-unit">avg per match</div>
    </div>
    <div class="dur-bar-section">
      <div class="dur-bar-label">
        <span>0 km</span>
        <span>Tourn. avg ${avg.toFixed(2)} km</span>
        <span>${MAX.toFixed(1)} km</span>
      </div>
      <div class="dur-track">
        <div class="dur-fill dist-fill" style="width:${(val/MAX*100).toFixed(1)}%"></div>
        <div class="dur-avg-tick" style="left:${(avg/MAX*100).toFixed(1)}%"></div>
      </div>
    </div>
    <div class="dur-matches">${matchRows}</div>
    ${note}`;
}

// ── Match Log ─────────────────────────────────────────────────────
function renderMatchLog(p) {
  function fmtMins(m) {
    if (m == null) return "—";
    const h = Math.floor(m / 60);
    const min = Math.round(m % 60);
    return h > 0 ? `${h}h ${min}m` : `${min}m`;
  }

  let rows = "";
  for (const m of p.match_summaries) {
    const pct = m.points_played > 0
      ? (m.points_won / m.points_played * 100).toFixed(0) + "%"
      : "—";
    const bb  = m.bad_break_match ? `<span class="bb-flag">⚡ Bad Break</span>` : "";
    const dur = fmtMins(m.duration_mins);
    const km  = m.distance_km != null ? `${m.distance_km.toFixed(2)} km` : "—";
    rows += `<tr>
      <td class="td-round"><span class="round-chip">${m.round}</span></td>
      <td class="td-opp">vs ${m.opponent}</td>
      <td class="td-pts">${m.points_won} / ${m.points_played}</td>
      <td class="td-pct">${pct}</td>
      <td class="td-dur">${dur}</td>
      <td class="td-dist">${km}</td>
      <td>${bb}</td>
    </tr>`;
  }
  document.getElementById("match-log").innerHTML = `
    <table class="match-log-table">
      <thead><tr>
        <th>Rnd</th><th>Opponent</th><th>Points Won</th><th>Win %</th><th>Duration</th><th>Distance</th><th>Flag</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ── Fingerprint: Tier 1 Stats ─────────────────────────────────────
function renderTier1(f) {
  const container = document.getElementById("tier1-viz");
  const badge     = document.getElementById("fp-confidence-badge");

  if (!f?.tier1) {
    container.innerHTML = naCard("Core Stats", "this player");
    if (badge) badge.textContent = "";
    return;
  }

  if (badge) badge.textContent = f.confidence === "high" ? "High confidence" : "Low confidence";

  const t1    = f.tier1;
  const COLOR = { cobalt:"var(--cobalt)", forest:"var(--forest)", terracotta:"var(--terracotta)" };
  const stats = [
    // Row 1 — Serve quality
    { key:"fsp_pct",         label:"1st Serve %",         col:"cobalt"     },
    { key:"fspw_pct",        label:"1st Srv Won %",       col:"cobalt"     },
    { key:"sspw_pct",        label:"2nd Srv Won %",       col:"forest"     },
    // Row 2 — Return quality (with serve-type splits)
    { key:"rpw_pct",         label:"Return Pts Won %",    col:"terracotta" },
    { key:"rpw_vs_1st_pct",  label:"RPW vs 1st Srv %",    col:"terracotta" },
    { key:"rpw_vs_2nd_pct",  label:"RPW vs 2nd Srv %",    col:"terracotta" },
    // Row 3 — Game-level outcomes
    { key:"sgw_pct",         label:"Srv Games Won %",     col:"cobalt"     },
    { key:"rgw_pct",         label:"Ret Games Won %",     col:"terracotta" },
  ];

  let grid = `<div class="tier1-grid">`;
  for (const s of stats) {
    const m   = t1[s.key] || {};
    const val = m.value;
    const lo  = m.ci_90_lo;
    const hi  = m.ci_90_hi;
    const c   = COLOR[s.col];
    grid += `
      <div class="t1-stat">
        <div class="t1-label">${s.label}</div>
        <div class="t1-value" style="color:${c}">${val != null ? val + "%" : "—"}</div>
        ${val != null ? `
          <div class="t1-bar-track">
            <div class="t1-bar-ci"  style="left:${lo}%;width:${hi - lo}%;background:${c}"></div>
            <div class="t1-bar-val" style="left:${val}%;background:${c}"></div>
          </div>
          <div class="t1-ci-range">${lo}% – ${hi}%</div>` : `<div class="t1-ci-range">no data</div>`}
      </div>`;
  }
  grid += `</div>`;
  grid += `<div class="t1-footer">${f.n_matches} match${f.n_matches !== 1 ? "es" : ""} · Elo-quality weighted · Beta(2,2) prior · 90% CI</div>`;
  container.innerHTML = grid;
}

// ── Fingerprint: Grass Elo ────────────────────────────────────────
function renderEloSnapshot(f) {
  const container = document.getElementById("elo-viz");
  const snap = f?.elo_snapshot;

  if (!snap) {
    container.innerHTML = naCard("Grass Elo", "this player");
    return;
  }

  const adj  = snap.R_adjusted;
  const surf = snap.R_surface;
  const over = snap.R_overall;
  const n    = snap.n_surface;
  const MIN  = 1400, MAX = 2300;
  const pct  = v => Math.max(0, Math.min(100, (v - MIN) / (MAX - MIN) * 100)).toFixed(1);

  container.innerHTML = `
    <div class="elo-big-row">
      <div class="elo-big">${adj}</div>
      <div class="elo-big-label">adjusted<br>grass Elo</div>
    </div>
    <div class="elo-track-wrap">
      <div class="elo-track">
        <div class="elo-fill" style="width:${pct(adj)}%"></div>
        <div class="elo-tick" style="left:${pct(1500)}%" title="Default 1500"></div>
      </div>
      <div class="elo-track-labels"><span>1400</span><span>↑ 1500 default</span><span>2300+</span></div>
    </div>
    <div class="elo-breakdown">
      <div class="elo-b-item">
        <span class="elo-b-val">${surf}</span>
        <span class="elo-b-label">Grass Elo</span>
      </div>
      <div class="elo-b-item">
        <span class="elo-b-val">${over}</span>
        <span class="elo-b-label">Overall Elo</span>
      </div>
      <div class="elo-b-item">
        <span class="elo-b-val">${n}</span>
        <span class="elo-b-label">Grass Matches</span>
      </div>
    </div>`;
}

// ── Fingerprint: Serve Entropy ────────────────────────────────────
function renderServeEntropy(f) {
  const container = document.getElementById("entropy-viz");
  const ent = f?.tier2?.serve_entropy;

  if (!ent?.available) {
    container.innerHTML = naCard("Serve Entropy", f?.year || "this year");
    return;
  }

  const pct  = ent.pct_of_max;
  const bits = ent.value?.toFixed(3) ?? "—";

  container.innerHTML = `
    <div class="entropy-big-row">
      <div class="entropy-big">${pct}%</div>
      <div class="entropy-label">of max<br>entropy</div>
    </div>
    <div class="entropy-track">
      <div class="entropy-fill" style="width:${pct}%"></div>
    </div>
    <div class="entropy-axis"><span>← Readable</span><span>Random →</span></div>
    <div class="entropy-detail">${bits} bits · max ${ent.max_bits?.toFixed(3)} bits (uniform 3-zone)</div>`;
}

// ── Fingerprint: Serve Courage ────────────────────────────────────
function renderServeSpeedCourage(f) {
  const container = document.getElementById("courage-viz");
  const sc = f?.tier2?.serve_speed_courage;

  if (!sc?.available) {
    container.innerHTML = naCard("Serve Courage", f?.year || "this year");
    return;
  }

  const delta = sc.value;
  const pos   = delta >= 0;
  const bpSpd = sc.bp_speed_kmh;
  const ovSpd = sc.overall_speed_kmh;
  const MAX   = Math.max(bpSpd, ovSpd, 100) * 1.08;

  container.innerHTML = `
    <div class="courage-delta ${pos ? "courage-pos" : "courage-neg"}">${pos ? "+" : ""}${delta} km/h</div>
    <div class="courage-sub">${pos ? "faster" : "slower"} under break-point pressure</div>
    <div class="courage-bars">
      <div class="courage-row">
        <span class="courage-row-label">Overall</span>
        <div class="courage-track">
          <div class="courage-fill courage-overall" style="width:${(ovSpd/MAX*100).toFixed(1)}%">
            <span class="courage-fill-label">${ovSpd} km/h</span>
          </div>
        </div>
      </div>
      <div class="courage-row">
        <span class="courage-row-label">On Break Pt</span>
        <div class="courage-track">
          <div class="courage-fill courage-bp" style="width:${(bpSpd/MAX*100).toFixed(1)}%">
            <span class="courage-fill-label">${bpSpd} km/h</span>
          </div>
        </div>
      </div>
    </div>`;
}

// ── Fingerprint: DF Pressure Delta ────────────────────────────────
function renderDfPressure(f) {
  const container = document.getElementById("df-pressure-viz");
  const dfp = f?.tier2?.df_pressure_delta;

  if (!dfp?.available) {
    container.innerHTML = naCard("DF Pressure Delta", f?.year || "this year");
    return;
  }

  const delta    = dfp.value;
  const worse    = delta > 0;
  const bpRate   = dfp.bp_df_rate;
  const baseRate = dfp.baseline_df_rate;
  const MAX      = Math.max(bpRate, baseRate, 3) * 1.6;

  container.innerHTML = `
    <div class="dfp-big ${worse ? "dfp-worse" : "dfp-better"}">${worse ? "+" : ""}${delta}%</div>
    <div class="dfp-sub">${worse ? "more DFs on break points" : "fewer DFs on break points"}</div>
    <div class="dfp-bars">
      <div class="courage-row">
        <span class="courage-row-label">Overall DF%</span>
        <div class="courage-track">
          <div class="courage-fill dfp-base-fill" style="width:${(baseRate/MAX*100).toFixed(1)}%">
            <span class="courage-fill-label">${baseRate}%</span>
          </div>
        </div>
      </div>
      <div class="courage-row">
        <span class="courage-row-label">On Break Pt</span>
        <div class="courage-track">
          <div class="courage-fill dfp-bp-fill" style="width:${(bpRate/MAX*100).toFixed(1)}%">
            <span class="courage-fill-label">${bpRate}%</span>
          </div>
        </div>
      </div>
    </div>`;
}

// ── Fingerprint: BP Creation ──────────────────────────────────────
function renderBpCreation(f) {
  const container = document.getElementById("bp-creation-viz");
  const bpc = f?.tier2?.bp_creation_profile;

  if (!bpc) {
    container.innerHTML = naCard("Break Point Creation", f?.year || "this year");
    return;
  }

  const bpPerGame = bpc.bp_per_return_game;
  const conv      = bpc.bp_conversion;
  const created   = Math.round(bpc.bp_created   ?? 0);
  const converted = Math.round(bpc.bp_converted  ?? 0);
  const MAX_BPG   = 1.5;

  container.innerHTML = `
    <div class="bpc-big-row">
      <div>
        <div class="bpc-big">${bpPerGame?.toFixed(2) ?? "—"}</div>
        <div class="bpc-big-label">BP / return game</div>
        <div class="bpc-track">
          <div class="bpc-fill" style="width:${bpPerGame != null ? Math.min(bpPerGame/MAX_BPG*100,100).toFixed(1) : 0}%"></div>
        </div>
        <div class="bpc-scale-labels"><span>0</span><span>0.75</span><span>1.5+</span></div>
      </div>
      <div class="bpc-divider"></div>
      <div>
        <div class="bpc-big bpc-secondary">${conv != null ? (conv * 100).toFixed(0) + "%" : "—"}</div>
        <div class="bpc-big-label">Conversion rate</div>
        <div class="bpc-note">Stable skill vs<br>noisy conversion</div>
      </div>
    </div>
    <div class="bpc-footer">${created} BPs created · ${converted} converted</div>`;
}

// ── Fingerprint: Clutch Differential ─────────────────────────────
function renderClutch(f) {
  const container = document.getElementById("clutch-viz");
  const cl = f?.tier2?.clutch_differential;

  if (!cl?.available) {
    container.innerHTML = naCard("Clutch Differential", f?.year || "this year");
    return;
  }

  const val     = cl.value;
  const hlPct   = cl.high_lev_win_pct;
  const basePct = cl.baseline_win_pct;
  const pos     = val >= 0;

  container.innerHTML = `
    <div class="clutch-big ${pos ? "clutch-pos" : "clutch-neg"}">${pos ? "+" : ""}${val}%</div>
    <div class="clutch-sub">on high-leverage points vs baseline</div>
    <div class="clutch-bars">
      <div class="clutch-row">
        <span class="clutch-row-label">Baseline win%</span>
        <div class="clutch-track">
          <div class="clutch-fill clutch-base-fill" style="width:${basePct}%">
            <span class="clutch-fill-label">${basePct}%</span>
          </div>
        </div>
      </div>
      <div class="clutch-row">
        <span class="clutch-row-label">Hi-lev win%</span>
        <div class="clutch-track">
          <div class="clutch-fill clutch-hl-fill" style="width:${hlPct}%">
            <span class="clutch-fill-label">${hlPct}%</span>
          </div>
        </div>
      </div>
    </div>`;
}

// ── Fingerprint: Rally Win Curve ──────────────────────────────────
function renderRallyCurve(f) {
  const container = document.getElementById("rally-curve-viz");
  const rw = f?.tier2?.rally_win_curve;

  if (!rw) {
    container.innerHTML = naCard("Rally Win Curve", f?.year || "this year");
    return;
  }

  const LABELS = { "1_3":"1–3 shots", "4_6":"4–6 shots", "7_9":"7–9 shots", "10+":"10+ shots" };
  const COLORS = ["var(--cobalt)", "#0044c8", "var(--forest)", "var(--forest-mid)"];
  const keys   = ["1_3", "4_6", "7_9", "10+"];

  let html = `<div class="rwc-grid">`;
  keys.forEach((k, i) => {
    const band = rw[k];
    const pct  = band?.win_pct;
    const n    = band?.n ?? 0;
    html += `
      <div class="rwc-row">
        <span class="rwc-label">${LABELS[k]}</span>
        <div class="rwc-bar-wrap">
          <div class="rwc-track">
            <div class="rwc-fill" style="width:${pct ?? 0}%;background:${COLORS[i]}"></div>
          </div>
          <span class="rwc-val">${pct != null ? pct + "%" : "—"}</span>
        </div>
        <span class="rwc-n">n=${n}</span>
      </div>`;
  });
  html += `</div>`;
  container.innerHTML = html;
}

// ── Fingerprint: Court Side Asymmetry ────────────────────────────
function renderCourtSide(f) {
  const container = document.getElementById("court-side-viz");
  const ca = f?.tier2?.court_side_asymmetry;

  if (!ca?.available) {
    container.innerHTML = naCard("Court Side Asymmetry", f?.year || "this year");
    return;
  }

  const deuce    = ca.deuce_win_pct;
  const ad       = ca.ad_win_pct;
  const stronger = ca.stronger_side;
  const asym     = ca.asymmetry?.toFixed(1) ?? "0.0";

  container.innerHTML = `
    <div class="csa-split">
      <div class="csa-side ${stronger === "deuce" ? "csa-stronger" : ""}">
        <div class="csa-side-label">Deuce Court</div>
        <div class="csa-pct">${deuce}%</div>
        <div class="csa-sub">pts won</div>
        ${stronger === "deuce" ? `<div class="csa-badge">Stronger</div>` : ""}
      </div>
      <div class="csa-vs">vs</div>
      <div class="csa-side ${stronger === "ad" ? "csa-stronger" : ""}">
        <div class="csa-side-label">Ad Court</div>
        <div class="csa-pct">${ad}%</div>
        <div class="csa-sub">pts won</div>
        ${stronger === "ad" ? `<div class="csa-badge">Stronger</div>` : ""}
      </div>
    </div>
    <div class="csa-asym-note">${asym}pp asymmetry</div>
    <div class="csa-bars">
      <div class="csa-bar-label-row"><span>Deuce</span><span>${deuce}%</span></div>
      <div class="csa-track"><div class="csa-fill-deuce" style="width:${deuce}%"></div></div>
      <div class="csa-bar-label-row" style="margin-top:var(--s-2)"><span>Ad</span><span>${ad}%</span></div>
      <div class="csa-track"><div class="csa-fill-ad" style="width:${ad}%"></div></div>
    </div>`;
}

// ── Fingerprint: Momentum Profile ────────────────────────────────
function renderMomentum(f) {
  const container = document.getElementById("momentum-viz");
  const mom = f?.tier2?.momentum_profile;

  if (!mom) {
    container.innerHTML = naCard("Momentum Profile", f?.year || "this year");
    return;
  }

  const init = mom.streak_initiation_rate;
  const surv = mom.streak_survival_rate;
  const rec  = mom.streak_recovery_rate;
  const fmtN   = v => v != null ? v.toFixed(2) : "—";
  const fmtPct = v => v != null ? (v * 100).toFixed(1) + "%" : "—";

  container.innerHTML = `
    <div class="mom-stats">
      <div class="mom-stat">
        <div class="mom-val">${fmtN(init)}</div>
        <div class="mom-label">Streak<br>Initiation</div>
        <div class="mom-note">3+ pt runs per match</div>
      </div>
      <div class="mom-stat">
        <div class="mom-val">${fmtPct(surv)}</div>
        <div class="mom-label">Streak<br>Survival</div>
        <div class="mom-note">3+ runs extending to 4+</div>
      </div>
      <div class="mom-stat">
        <div class="mom-val">${fmtPct(rec)}</div>
        <div class="mom-label">Recovery<br>Rate</div>
        <div class="mom-note">Pts won after opp streak</div>
      </div>
    </div>`;
}

// ── Fingerprint: Serve Speed Differential ────────────────────────
function renderServeSpeedDifferential(f) {
  const container = document.getElementById("srv-speed-diff-viz");
  const sd = f?.tier2?.serve_speed_differential;
  if (!sd?.available) {
    container.innerHTML = naCard("Speed Differential", f?.year || "this year");
    return;
  }
  const gap    = sd.value;
  const first  = sd.first_avg_kmh;
  const second = sd.second_avg_kmh;
  const MAX    = Math.max(first, second, 100) * 1.1;
  const tight  = gap < 30;

  container.innerHTML = `
    <div class="courage-delta ${tight ? "courage-pos" : "courage-neg"}">${gap.toFixed(0)} km/h gap</div>
    <div class="courage-sub">${tight ? "tight differential — consistent delivery" : "large gap — 2nd serve attackable"}</div>
    <div class="courage-bars">
      <div class="courage-row">
        <span class="courage-row-label">1st Serve</span>
        <div class="courage-track">
          <div class="courage-fill courage-overall" style="width:${(first / MAX * 100).toFixed(1)}%">
            <span class="courage-fill-label">${first.toFixed(0)} km/h</span>
          </div>
        </div>
      </div>
      <div class="courage-row">
        <span class="courage-row-label">2nd Serve</span>
        <div class="courage-track">
          <div class="courage-fill courage-bp" style="width:${(second / MAX * 100).toFixed(1)}%">
            <span class="courage-fill-label">${second.toFixed(0)} km/h</span>
          </div>
        </div>
      </div>
    </div>`;
}

// ── Fingerprint: Serve Depth Entropy ─────────────────────────────
function renderServeDepthEntropy(f) {
  const container = document.getElementById("srv-depth-viz");
  const sd = f?.tier2?.serve_depth_entropy;
  if (!sd?.available) {
    container.innerHTML = naCard("Serve Depth", f?.year || "this year");
    return;
  }
  const pct  = sd.pct_of_max;
  const deep = sd.pct_deep;
  const bits = sd.value?.toFixed(3) ?? "—";

  container.innerHTML = `
    <div class="entropy-big-row">
      <div class="entropy-big">${pct}%</div>
      <div class="entropy-label">depth<br>mix</div>
    </div>
    <div class="entropy-track">
      <div class="entropy-fill" style="width:${pct}%"></div>
    </div>
    <div class="entropy-axis"><span>← Predictable</span><span>Mixed →</span></div>
    <div class="entropy-detail">${bits} bits · ${deep}% deep (CTL) · ${(100 - deep).toFixed(1)}% short (NCTL)</div>`;
}

// ── Fingerprint: Tiebreak Differential ───────────────────────────
function renderTiebreakDifferential(f) {
  const container = document.getElementById("tiebreak-viz");
  const tb = f?.tier2?.tiebreak_differential;
  if (!tb?.available) {
    container.innerHTML = naCard("Tiebreak Differential", f?.year || "this year");
    return;
  }
  const val     = tb.value;
  const pos     = val >= 0;
  const tbPct   = tb.tb_win_pct;
  const basePct = tb.baseline_win_pct;

  container.innerHTML = `
    <div class="clutch-big ${pos ? "clutch-pos" : "clutch-neg"}">${pos ? "+" : ""}${val.toFixed(1)}%</div>
    <div class="clutch-sub">${pos ? "above" : "below"} baseline in tiebreaks</div>
    <div class="clutch-bars">
      <div class="clutch-row">
        <span class="clutch-row-label">Baseline win%</span>
        <div class="clutch-track">
          <div class="clutch-fill clutch-base-fill" style="width:${basePct}%">
            <span class="clutch-fill-label">${basePct.toFixed(1)}%</span>
          </div>
        </div>
      </div>
      <div class="clutch-row">
        <span class="clutch-row-label">Tiebreak win%</span>
        <div class="clutch-track">
          <div class="clutch-fill clutch-hl-fill" style="width:${tbPct}%">
            <span class="clutch-fill-label">${tbPct.toFixed(1)}%</span>
          </div>
        </div>
      </div>
    </div>`;
}

// ── Fingerprint: Set Transition Delta ────────────────────────────
function renderSetTransitionDelta(f) {
  const container = document.getElementById("set-transition-viz");
  const st = f?.tier2?.set_transition_delta;
  if (!st?.available) {
    container.innerHTML = naCard("Set Transition", f?.year || "this year");
    return;
  }
  const val     = st.value;
  const pos     = val >= 0;
  const first   = st.first_games_win_pct;
  const overall = st.overall_win_pct;

  container.innerHTML = `
    <div class="clutch-big ${pos ? "clutch-pos" : "clutch-neg"}">${pos ? "+" : ""}${val.toFixed(1)}%</div>
    <div class="clutch-sub">${pos ? "stronger" : "weaker"} in the first 2 games of each set</div>
    <div class="clutch-bars">
      <div class="clutch-row">
        <span class="clutch-row-label">Overall win%</span>
        <div class="clutch-track">
          <div class="clutch-fill clutch-base-fill" style="width:${overall}%">
            <span class="clutch-fill-label">${overall.toFixed(1)}%</span>
          </div>
        </div>
      </div>
      <div class="clutch-row">
        <span class="clutch-row-label">Set openers</span>
        <div class="clutch-track">
          <div class="clutch-fill clutch-hl-fill" style="width:${first}%">
            <span class="clutch-fill-label">${first.toFixed(1)}%</span>
          </div>
        </div>
      </div>
    </div>`;
}

// ── Fingerprint: 1st Serve % Under Pressure ───────────────────────
function renderFirstServePressure(f) {
  const container = document.getElementById("fsp-pressure-viz");
  const fsp = f?.tier2?.first_serve_pressure;
  if (!fsp?.available) {
    container.innerHTML = naCard("1st Serve Under Pressure", f?.year || "this year");
    return;
  }
  const delta   = fsp.value;
  const pos     = delta >= 0;
  const atPres  = fsp.fsp_at_pressure;
  const overall = fsp.fsp_overall;
  const MAX     = Math.max(atPres, overall, 50) * 1.2;

  container.innerHTML = `
    <div class="courage-delta ${pos ? "courage-pos" : "courage-neg"}">${pos ? "+" : ""}${delta.toFixed(1)}%</div>
    <div class="courage-sub">${pos ? "more aggressive" : "more cautious"} on deuce / ad points</div>
    <div class="courage-bars">
      <div class="courage-row">
        <span class="courage-row-label">Overall 1st%</span>
        <div class="courage-track">
          <div class="courage-fill courage-overall" style="width:${(overall / MAX * 100).toFixed(1)}%">
            <span class="courage-fill-label">${overall.toFixed(1)}%</span>
          </div>
        </div>
      </div>
      <div class="courage-row">
        <span class="courage-row-label">At deuce / AD</span>
        <div class="courage-track">
          <div class="courage-fill courage-bp" style="width:${(atPres / MAX * 100).toFixed(1)}%">
            <span class="courage-fill-label">${atPres.toFixed(1)}%</span>
          </div>
        </div>
      </div>
    </div>`;
}

// ── Fingerprint: Rally Volatility ────────────────────────────────
function renderRallyVolatility(f) {
  const container = document.getElementById("rally-vol-viz");
  const rv = f?.tier2?.rally_volatility;
  if (!rv?.available) {
    container.innerHTML = naCard("Rally Volatility", f?.year || "this year");
    return;
  }
  const val  = rv.value;
  const mean = rv.mean_rc_won;
  // Typical range 1.5–6.0 shots std dev; normalise bar to 6.0
  const pct  = Math.min(val / 6.0 * 100, 100).toFixed(1);

  container.innerHTML = `
    <div class="entropy-big-row">
      <div class="entropy-big">${val.toFixed(2)}</div>
      <div class="entropy-label">shots<br>std dev</div>
    </div>
    <div class="entropy-track">
      <div class="entropy-fill" style="width:${pct}%"></div>
    </div>
    <div class="entropy-axis"><span>← Specialist</span><span>All-court →</span></div>
    <div class="entropy-detail">Mean rally length (won pts): ${mean?.toFixed(1) ?? "—"} shots</div>`;
}

// ── Fingerprint: Run Efficiency ───────────────────────────────────
function renderRunEfficiency(f) {
  const container = document.getElementById("run-efficiency-viz");
  const dre = f?.tier2?.distance_run_efficiency;
  if (!dre?.available) {
    container.innerHTML = naCard("Run Efficiency", f?.year || "this year");
    return;
  }
  const val = dre.value;
  // Range roughly 1–7 m/shot; invert so a low (efficient) value fills the bar more
  const effPct = Math.max(0, Math.min(100, (1 - (val - 1) / 6) * 100)).toFixed(1);

  container.innerHTML = `
    <div class="entropy-big-row">
      <div class="entropy-big">${val.toFixed(2)}</div>
      <div class="entropy-label">m per<br>shot</div>
    </div>
    <div class="entropy-track">
      <div class="entropy-fill" style="width:${effPct}%"></div>
    </div>
    <div class="entropy-axis"><span>← Efficient</span><span>Heavy runner →</span></div>
    <div class="entropy-detail">Metres covered per rally-length unit (Elo-weighted)</div>`;
}

// ── Fingerprint: Attrition Slope ─────────────────────────────────
function renderAttritionSlope(f) {
  const container = document.getElementById("attrition-viz");
  const at = f?.tier2?.attrition_slope;
  if (!at?.available) {
    container.innerHTML = naCard("Attrition Slope", f?.year || "this year");
    return;
  }
  const slope  = at.value;
  const neg    = slope < 0;
  const avgs   = at.set_avgs_m || {};
  const setNums = Object.keys(avgs).sort((a, b) => +a - +b);
  const vals   = setNums.map(k => avgs[k]);
  const MAX_V  = Math.max(...vals, 1) * 1.15;

  const barsHtml = setNums.map(k => {
    const v   = avgs[k];
    const pct = (v / MAX_V * 100).toFixed(1);
    return `
      <div class="courage-row">
        <span class="courage-row-label">Set ${k}</span>
        <div class="courage-track">
          <div class="courage-fill ${neg ? "courage-overall" : "courage-bp"}" style="width:${pct}%">
            <span class="courage-fill-label">${(v / 1000).toFixed(2)} km</span>
          </div>
        </div>
      </div>`;
  }).join("");

  container.innerHTML = `
    <div class="courage-delta ${neg ? "courage-pos" : "courage-neg"}">${neg ? "" : "+"}${slope.toFixed(1)} m/set</div>
    <div class="courage-sub">${neg ? "fresher in later sets" : "heavier load in later sets"}</div>
    <div class="courage-bars">${barsHtml}</div>`;
}

function renderHoldAfterBreak(f) {
  const container = document.getElementById("hold-after-break-viz");
  const hab = f?.tier2?.hold_after_break;
  if (!hab?.available) {
    container.innerHTML = naCard("Hold After Break", f?.year || "this year");
    return;
  }
  const habPct = hab.habr_pct;
  const sgwPct = hab.sgw_pct;
  const delta  = hab.value;
  const pos    = delta >= 0;
  const MAX_V  = Math.max(habPct, sgwPct, 1) * 1.1;

  container.innerHTML = `
    <div class="clutch-delta ${pos ? "clutch-pos" : "clutch-neg"}">${pos ? "+" : ""}${delta != null ? delta.toFixed(1) : "–"}pp after being broken</div>
    <div class="clutch-sub">${pos ? "Bounces back above normal hold rate" : "Struggles to recover service hold after break"}</div>
    <div class="clutch-bars">
      <div class="clutch-row">
        <span class="clutch-row-label">After Break</span>
        <div class="clutch-track">
          <div class="clutch-fill clutch-fill-hl" style="width:${(habPct / MAX_V * 100).toFixed(1)}%">
            <span class="clutch-fill-label">${habPct.toFixed(1)}%</span>
          </div>
        </div>
      </div>
      <div class="clutch-row">
        <span class="clutch-row-label">Overall Hold</span>
        <div class="clutch-track">
          <div class="clutch-fill clutch-fill-base" style="width:${(sgwPct / MAX_V * 100).toFixed(1)}%">
            <span class="clutch-fill-label">${sgwPct.toFixed(1)}%</span>
          </div>
        </div>
      </div>
    </div>`;
}

// ── Helpers ───────────────────────────────────────────────────────
function naCard(metric, year) {
  return `<div class="rally-na">
    <span class="rally-na-label">Not Available (${year})</span>
    <p class="rally-na-note">${metric} data is not populated in the ${year} dataset.</p>
  </div>`;
}

// ── Leaderboard ───────────────────────────────────────────────────

const LEADERBOARD_METRICS = [
  // Core Stats
  { id:"fspw_pct",  label:"1st Srv Won %",    section:"Core Stats",         fmt:"pct",   dir:"desc", extract:f=>f?.tier1?.fspw_pct?.value },
  { id:"fsp_pct",   label:"1st Serve In %",   section:"Core Stats",         fmt:"pct",   dir:"desc", extract:f=>f?.tier1?.fsp_pct?.value  },
  { id:"sspw_pct",  label:"2nd Srv Won %",     section:"Core Stats",         fmt:"pct",   dir:"desc", extract:f=>f?.tier1?.sspw_pct?.value },
  { id:"rpw_pct",         label:"Return Pts Won",       section:"Core Stats", fmt:"pct", dir:"desc", extract:f=>f?.tier1?.rpw_pct?.value         },
  { id:"rpw_vs_1st_pct",  label:"RPW vs 1st Srv",       section:"Core Stats", fmt:"pct", dir:"desc", extract:f=>f?.tier1?.rpw_vs_1st_pct?.value  },
  { id:"rpw_vs_2nd_pct",  label:"RPW vs 2nd Srv",       section:"Core Stats", fmt:"pct", dir:"desc", extract:f=>f?.tier1?.rpw_vs_2nd_pct?.value  },
  { id:"sgw_pct",         label:"Srv Games Won",        section:"Core Stats", fmt:"pct", dir:"desc", extract:f=>f?.tier1?.sgw_pct?.value         },
  { id:"rgw_pct",   label:"Ret Games Won",     section:"Core Stats",         fmt:"pct",   dir:"desc", extract:f=>f?.tier1?.rgw_pct?.value  },
  { id:"elo",       label:"Grass Elo",         section:"Core Stats",         fmt:"elo",   dir:"desc", extract:f=>f?.elo_snapshot?.R_adjusted },
  // Serve
  { id:"entropy",   label:"Serve Entropy",     section:"Serve",              fmt:"pct",   dir:"desc", extract:f=>f?.tier2?.serve_entropy?.pct_of_max },
  { id:"courage",   label:"Serve Courage",     section:"Serve",              fmt:"kmh",   dir:"desc", extract:f=>f?.tier2?.serve_speed_courage?.value },
  { id:"spd_diff",  label:"Speed Gap 1st–2nd", section:"Serve",              fmt:"dec1",  dir:"asc",  extract:f=>f?.tier2?.serve_speed_differential?.value },
  { id:"depth_ent", label:"Depth Mix",         section:"Serve",              fmt:"pct",   dir:"desc", extract:f=>f?.tier2?.serve_depth_entropy?.pct_of_max },
  // Return & Pressure
  // Removed: Clutch Diff, DF Pressure Δ, 1st Srv @ Pressure, BP Conversion
  //          (all ablated or de-facto dead in the model)
  { id:"bp_per_g",  label:"BPs / Ret Game",   section:"Return & Pressure",  fmt:"dec3",  dir:"desc", extract:f=>f?.tier2?.bp_creation_profile?.bp_per_return_game },
  { id:"tiebreak",  label:"Tiebreak Diff",     section:"Return & Pressure",  fmt:"pp",    dir:"desc", extract:f=>f?.tier2?.tiebreak_differential?.value },
  { id:"set_trans", label:"Set Transition Δ",  section:"Return & Pressure",  fmt:"pp",    dir:"desc", extract:f=>f?.tier2?.set_transition_delta?.value },
  // Rally & Shape
  // Removed: Rally Volatility, Court Asymmetry (both ablated)
  { id:"rw_1_3",    label:"Win % 1–3 shots",  section:"Rally & Shape",      fmt:"pct",   dir:"desc", extract:f=>f?.tier2?.rally_win_curve?.["1_3"]?.win_pct },
  { id:"rw_4_6",    label:"Win % 4–6 shots",  section:"Rally & Shape",      fmt:"pct",   dir:"desc", extract:f=>f?.tier2?.rally_win_curve?.["4_6"]?.win_pct },
  { id:"rw_7_9",    label:"Win % 7–9 shots",  section:"Rally & Shape",      fmt:"pct",   dir:"desc", extract:f=>f?.tier2?.rally_win_curve?.["7_9"]?.win_pct },
  { id:"rw_10",     label:"Win % 10+ shots",  section:"Rally & Shape",      fmt:"pct",   dir:"desc", extract:f=>f?.tier2?.rally_win_curve?.["10+"]?.win_pct },
  // Momentum section removed entirely — catch-fire mechanic ablated, no signal
  // Physical
  { id:"run_eff",   label:"Run Efficiency",    section:"Physical",           fmt:"dec2",  dir:"asc",  extract:f=>f?.tier2?.distance_run_efficiency?.value },
  { id:"attrition", label:"Attrition Slope",   section:"Physical",           fmt:"dec1",  dir:"asc",  extract:f=>f?.tier2?.attrition_slope?.value },
  { id:"hold_ab",   label:"Hold After Break",  section:"Physical",           fmt:"pp",    dir:"desc", extract:f=>f?.tier2?.hold_after_break?.value },
];

function fmtLbVal(val, fmt) {
  if (val == null) return "—";
  switch(fmt) {
    case "pct":   return val.toFixed(1) + "%";
    case "pct1":  return val.toFixed(1) + "%";
    case "elo":   return Math.round(val);
    case "kmh":   return (val >= 0 ? "+" : "") + val.toFixed(1) + " km/h";
    case "pp":    return (val >= 0 ? "+" : "") + val.toFixed(1) + "pp";
    case "dec1":  return val.toFixed(1);
    case "dec2":  return val.toFixed(2);
    case "dec3":  return val.toFixed(3);
    default:      return val;
  }
}

function renderLbMetricNav() {
  const nav = document.getElementById("lb-metric-nav");
  const sections = {};
  LEADERBOARD_METRICS.forEach(m => {
    if (!sections[m.section]) sections[m.section] = [];
    sections[m.section].push(m);
  });
  nav.innerHTML = Object.entries(sections).map(([sec, mets]) => `
    <div class="lb-section-row">
      <span class="lb-section-label">${sec}</span>
      <div class="lb-metric-pills">
        ${mets.map(m => `
          <button class="lb-metric-pill ${m.id === state.lbMetric ? "active" : ""}"
                  data-metric="${m.id}">${m.label}</button>
        `).join("")}
      </div>
    </div>
  `).join("");
  nav.querySelectorAll(".lb-metric-pill").forEach(btn => {
    btn.addEventListener("click", () => {
      state.lbMetric = btn.dataset.metric;
      renderLeaderboard();
    });
  });
}

function renderLbList() {
  const metric = LEADERBOARD_METRICS.find(m => m.id === state.lbMetric);
  if (!metric) return;
  const fps = state.fingerprints || {};

  const entries = Object.entries(fps)
    .map(([name, f]) => ({ name, value: metric.extract(f), n: f.n_matches }))
    .filter(e => e.value != null && isFinite(e.value));

  entries.sort((a, b) => metric.dir === "desc" ? b.value - a.value : a.value - b.value);

  const vals = entries.map(e => e.value);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = hi - lo || 1;

  const header = document.getElementById("lb-list-header");
  header.innerHTML = `
    <span class="lb-col-rank">#</span>
    <span class="lb-col-name">Player</span>
    <span class="lb-col-bar"></span>
    <span class="lb-col-val">${metric.label}</span>
    <span class="lb-col-n">Matches</span>
  `;

  const list = document.getElementById("lb-list");
  list.innerHTML = entries.map((e, i) => {
    const barPct = metric.dir === "desc"
      ? (e.value - lo) / span * 100
      : (hi - e.value) / span * 100;
    const isSelected = e.name === state.playerName;
    return `
      <div class="lb-row ${isSelected ? "lb-row--active" : ""}" data-player="${e.name}">
        <span class="lb-col-rank">${i + 1}</span>
        <span class="lb-col-name">${e.name}</span>
        <div class="lb-col-bar">
          <div class="lb-bar-track"><div class="lb-bar-fill" style="width:${barPct.toFixed(1)}%"></div></div>
        </div>
        <span class="lb-col-val">${fmtLbVal(e.value, metric.fmt)}</span>
        <span class="lb-col-n">${e.n}</span>
      </div>`;
  }).join("");

  list.querySelectorAll(".lb-row").forEach(row => {
    row.addEventListener("click", () => {
      const name = row.dataset.player;
      state.playerName = name;
      state.mode = "profile";
      document.getElementById("player-select").value = name;
      document.getElementById("lb-toggle").classList.remove("active");
      document.getElementById("leaderboard").classList.add("hidden");
      document.getElementById("empty-state").classList.add("hidden");
      document.getElementById("profile").classList.remove("hidden");
      renderProfile(name);
    });
  });
}

function renderLeaderboard() {
  renderLbMetricNav();
  renderLbList();
}

// ── Matchup mode ──────────────────────────────────────────────────

function enterMatchupMode(show) {
  state.mode = "matchup";
  document.getElementById("mu-toggle").classList.add("active");
  document.getElementById("lb-toggle").classList.remove("active");
  document.getElementById("profile").classList.add("hidden");
  document.getElementById("leaderboard").classList.add("hidden");
  document.getElementById("empty-state").classList.add("hidden");
  if (show) {
    const panel = document.getElementById("matchup-panel");
    panel.classList.remove("hidden");
    requestAnimationFrame(() => panel.scrollIntoView({ behavior: "smooth", block: "start" }));
  }
}

function exitMatchupMode() {
  document.getElementById("mu-toggle").classList.remove("active");
  document.getElementById("matchup-panel").classList.add("hidden");
  if (state.playerName && state.profiles && state.profiles[state.playerName]) {
    renderProfile(state.playerName);
  } else {
    showEmpty();
  }
}

function populateMuDropdowns() {
  const names = Object.keys(state.profiles || {}).sort();
  ["mu-select-a", "mu-select-b"].forEach(id => {
    const sel = document.getElementById(id);
    const cur = sel.value;
    sel.innerHTML = '<option value="">Choose player…</option>';
    names.forEach(n => {
      const o = document.createElement("option");
      o.value = o.textContent = n;
      sel.appendChild(o);
    });
    if (cur && state.profiles[cur]) sel.value = cur;
  });
}

// overrideFps: optional fingerprint set from a different year (e.g. prior year
// to avoid within-tournament data leakage when predicting a specific matchup).
function runComparison(nameA, nameB, overrideFps = null) {
  const fps = overrideFps || state.fingerprints || {};
  const fpA = fps[nameA];
  const fpB = fps[nameB];
  const fingerprintYear = overrideFps ? overrideFps._year : null;
  const resultEl = document.getElementById("mu-result");

  if (!fpA || !fpB) {
    resultEl.removeAttribute("hidden");
    resultEl.innerHTML = `<p class="mu-error">One or both players have no fingerprint data for ${state.year}. Try a different year.</p>`;
    return;
  }

  fpA.player = nameA;
  fpB.player = nameB;

  // ── Loading state ────────────────────────────────────────────────
  resultEl.removeAttribute("hidden");
  resultEl.innerHTML = `
    <div class="mu-result-inner">
      <div class="mu-loading">
        <div class="mu-loading-sprite" id="mu-dealer-sprite"></div>
        <div class="mu-loading-text">
          <div class="mu-loading-label">Running match simulations</div>
          <div class="mu-loading-msg" id="mu-loading-msg">Preparing player data…</div>
          <div class="mu-loading-bar-wrap">
            <div class="mu-loading-bar" id="mu-progress-bar" style="width:0%"></div>
          </div>
          <div class="mu-loading-sub">Point-by-point · fingerprint-driven</div>
        </div>
      </div>
    </div>`;

  // ── Sprite animation (8fps, 8×5 grid, 280×158 per frame) ─────────
  if (window._spriteTimer) { clearInterval(window._spriteTimer); window._spriteTimer = null; }
  const spriteEl = document.getElementById("mu-dealer-sprite");
  if (spriteEl) {
    const COLS = 8, TOTAL = 40, FW = 280, FH = 158;
    let frame = 0;
    window._spriteTimer = setInterval(() => {
      spriteEl.style.backgroundPosition =
        `-${(frame % COLS) * FW}px -${Math.floor(frame / COLS) * FH}px`;
      frame = (frame + 1) % TOTAL;
    }, 125);  // 8fps
  }

  // Terminate any prior worker
  if (window._mcWorker) { window._mcWorker.terminate(); window._mcWorker = null; }

  const worker      = new Worker("mc_worker.js");
  const startTime   = Date.now();
  const MIN_LOAD_MS = 5200;   // keep animation visible for a full loop
  window._mcWorker  = worker;

  worker.onmessage = (e) => {
    const { type } = e.data;

    if (type === "progress") {
      const bar = document.getElementById("mu-progress-bar");
      const msg = document.getElementById("mu-loading-msg");
      if (bar) bar.style.width = `${e.data.pct}%`;
      if (msg) msg.textContent = e.data.msg;
      return;
    }

    if (type === "result") {
      const elapsed  = Date.now() - startTime;
      const delay    = Math.max(0, MIN_LOAD_MS - elapsed);

      setTimeout(() => {
      if (window._spriteTimer) { clearInterval(window._spriteTimer); window._spriteTimer = null; }
      worker.terminate();
      window._mcWorker = null;

      // Run structural 5-axis breakdown (still driven by compare.js)
      const structural = compareEngine(fpA, fpB, state.year, state.eraStats || {});

      // Merge: probabilities + score dist + CI come from MC;
      // structural axes + pServe come from compareEngine
      const merged = {
        nameA, nameB,
        year:     state.year,
        fingerprintYear,          // null = current year; set = prior year (leak-free)
        pServeA:  structural.pServeA,
        pServeB:  structural.pServeB,
        axes:     structural.axes,
        pWinA:    e.data.pWinA,
        pWinB:    e.data.pWinB,
        ciLow:    e.data.ciLow,
        ciHigh:   e.data.ciHigh,
        scoreDist: e.data.scoreDist,
        axisContrib:  e.data.axisContrib,
        dominantAxis: e.data.dominantAxis,
        nSims:    e.data.nSims,
        narrative: edgeNarrative(structural.axes, nameA, nameB, e.data.pWinA),
      };

        renderMatchupResult(merged);
      }, delay);
    }
  };

  worker.onerror = (err) => {
    resultEl.innerHTML = `<p class="mu-error">Simulation error: ${err.message || "unknown"}. Check console for details.</p>`;
  };

  worker.postMessage({ fpA, fpB, nSims: 25000 });
}

// ── Render matchup result ─────────────────────────────────────────

const AXIS_DEFS = [
  { key: "serveReturn",   label: "Serve / Return",   weight: "0.35" },
  { key: "rallyShape",    label: "Rally Shape",       weight: "0.15" },
  { key: "pressure",      label: "Pressure",          weight: "0.25" },
  { key: "durability",    label: "Durability",        weight: "0.10" },
  { key: "breakPressure", label: "Break Pressure",    weight: "0.15" },
];

// MC axis name → display label
const MC_AXIS_LABELS = {
  rallyShape:    "Rally Shape",
  pressure:      "Pressure",
  serveEntropy:  "Serve Entropy",
  breakPressure: "Break Pressure",
};

function renderMatchupResult(r) {
  const el = document.getElementById("mu-result");
  el.removeAttribute("hidden");

  const pA = Math.round(r.pWinA * 100);
  const pB = 100 - pA;

  // Confidence interval string (only from MC results)
  const ciA = (r.ciLow != null)
    ? `<span class="mu-prob-ci">${Math.round(r.ciLow * 100)}–${Math.round(r.ciHigh * 100)}% range</span>`
    : "";

  // Probability header label
  const probLabelA = `<span class="mu-prob-label">match win probability</span>`;
  const probLabelB = `<span class="mu-prob-label">match win probability</span>`;

  // Score distribution — sorted A-wins then B-wins
  const aWins = [], bWins = [];
  for (const [k, p] of Object.entries(r.scoreDist)) {
    const [sa] = k.split("-").map(Number);
    (sa === 3 ? aWins : bWins).push([k, p]);
  }
  aWins.sort((a, b) => b[1] - a[1]);
  bWins.sort((a, b) => b[1] - a[1]);
  const allScores = [...aWins, ...bWins];
  const maxP = Math.max(...allScores.map(([, p]) => p));

  const scoreRows = allScores.map(([k, p]) => {
    const pct = Math.round(p * 100);
    const barW = Math.round((p / maxP) * 100);
    const [sa, sb] = k.split("-").map(Number);
    const aWon = sa === 3;
    const cls  = aWon ? "dist-bar-a" : "dist-bar-b";
    // Display "winner sets – loser sets" for readability
    const displayLabel = aWon ? `${sa}-${sb}` : `${sb}-${sa}`;
    return `<div class="mu-dist-row">
      <span class="mu-dist-score">${displayLabel}</span>
      <div class="mu-dist-bar-wrap"><div class="${cls}" style="width:${barW}%"></div></div>
      <span class="mu-dist-pct">${pct}%</span>
    </div>`;
  }).join("");

  // Structural axis bars
  const axisRows = AXIS_DEFS.map(ax => {
    const edge  = r.axes[ax.key];
    const pct   = Math.round(Math.abs(edge) * 45);   // max 45% per side
    const isA   = edge >= 0;
    const fill  = isA
      ? `left:50%;width:${pct}%`
      : `left:${50 - pct}%;width:${pct}%`;
    const cls   = isA ? "mu-axis-fill-a" : "mu-axis-fill-b";
    const label = Math.abs(edge) < 0.03 ? "Even"
                : isA ? `+${(Math.abs(edge)).toFixed(2)} A`
                : `+${(Math.abs(edge)).toFixed(2)} B`;
    return `<div class="mu-axis-row">
      <span class="mu-axis-name">${ax.label}</span>
      <div class="mu-axis-bar-wrap">
        <div class="mu-axis-center"></div>
        <div class="${cls} mu-axis-fill" style="${fill}"></div>
      </div>
      <span class="mu-axis-edge ${isA ? "edge-a" : "edge-b"}">${label}</span>
    </div>`;
  }).join("");

  // Key Factor — incorporate MC dominant axis if available
  let keyFactorHTML = `<p class="mu-narrative">${r.narrative}</p>`;
  if (r.dominantAxis && r.axisContrib) {
    const axLabel   = MC_AXIS_LABELS[r.dominantAxis] || r.dominantAxis;
    const contrib   = r.axisContrib[r.dominantAxis];
    const contribPp = Math.abs(Math.round(contrib * 100));
    const beneficiary = contrib > 0
      ? r.nameA.split(" ").pop()
      : r.nameB.split(" ").pop();
    const contribStr = contribPp > 0
      ? `<span class="mu-mc-axis-contrib">+${contribPp}pp for ${beneficiary}</span>`
      : "";
    keyFactorHTML += `
      <div class="mu-mc-axis">
        <span class="mu-mc-axis-tag">Decisive axis</span>
        <span class="mu-mc-axis-name">${axLabel}</span>
        ${contribStr}
      </div>`;
  }

  const winnerA = pA >= pB;

  function tryApplyPhoto(sideEl, name) {
    const src = `players/${name.replace(/ /g, "_")}.webp`;
    const probe = new Image();
    probe.onload = () => {
      const wrap = sideEl.querySelector(".mu-prob-photo");
      if (!wrap) return;
      const img = document.createElement("img");
      img.src = src;
      img.alt = name;
      wrap.appendChild(img);
      sideEl.classList.add("has-photo");
    };
    probe.src = src;  // onerror = do nothing, stays text-only
  }

  // Archetype tags (if available for both players)
  const archA = state.archetypes ? state.archetypes[r.nameA] : null;
  const archB = state.archetypes ? state.archetypes[r.nameB] : null;
  const archTagA = archA ? `<span class="archetype-tag mu-arch-tag" title="${(archA.blurb||'').replace(/"/g,'&quot;')}">${archA.label}</span>` : "";
  const archTagB = archB ? `<span class="archetype-tag mu-arch-tag" title="${(archB.blurb||'').replace(/"/g,'&quot;')}">${archB.label}</span>` : "";

  el.innerHTML = `
    <div class="mu-result-inner">

      <div class="mu-prob-header">
        <div class="mu-prob-side mu-prob-side-a ${winnerA ? "is-winner" : ""}" id="mu-side-a">
          <div class="mu-prob-photo"></div>
          <div class="mu-prob-text">
            <span class="mu-prob-pct">${pA}%</span>
            ${ciA}
            <span class="mu-prob-name">${r.nameA}</span>
            ${archTagA}
            <span class="mu-prob-serve">Srv win ${Math.round(r.pServeA * 100)}%</span>
            ${probLabelA}
          </div>
        </div>
        <div class="mu-prob-divider"></div>
        <div class="mu-prob-side mu-prob-side-b ${!winnerA ? "is-winner" : ""}" id="mu-side-b">
          <div class="mu-prob-photo"></div>
          <div class="mu-prob-text">
            <span class="mu-prob-pct">${pB}%</span>
            ${ciA ? `<span class="mu-prob-ci">${Math.round((1-r.ciHigh) * 100)}–${Math.round((1-r.ciLow) * 100)}% range</span>` : ""}
            <span class="mu-prob-name">${r.nameB}</span>
            ${archTagB}
            <span class="mu-prob-serve">Srv win ${Math.round(r.pServeB * 100)}%</span>
            ${probLabelB}
          </div>
        </div>
      </div>

      <div class="mu-section-head">
        <span class="mu-section-title">Structural Profile</span>
        <span class="mu-section-sub">← B edge · 0 · A edge →</span>
      </div>
      <div class="mu-axes">${axisRows}</div>

      <div class="mu-bottom-row">
        <div class="mu-dist-col">
          <div class="mu-section-head"><span class="mu-section-title">Score Distribution</span></div>
          <div class="mu-dist">${scoreRows}</div>
        </div>
        <div class="mu-narrative-col">
          <div class="mu-section-head"><span class="mu-section-title">Key Factor</span></div>
          ${keyFactorHTML}
          ${r.fingerprintYear
            ? `<p class="mu-fp-note">Using ${r.fingerprintYear} pre-tournament fingerprints — excludes within-tournament data for a clean prediction.</p>`
            : `<p class="mu-fp-note mu-fp-note--warn">Using ${r.year} fingerprints — includes same-tournament data.</p>`
          }
        </div>
      </div>

    </div>`;

  // Async photo injection — falls back silently if image missing
  tryApplyPhoto(document.getElementById("mu-side-a"), r.nameA);
  tryApplyPhoto(document.getElementById("mu-side-b"), r.nameB);
}

// ── Featured curated matchups ──────────────────────────────────────

function renderFeatured() {
  const row = document.getElementById("mu-featured-row");
  if (!row) return;
  const data = state.curatedMatchups || [];
  if (!data.length) { row.innerHTML = ""; return; }

  row.innerHTML = data.map((m, i) => {
    const nameA = m.player_a.split(" ").pop();
    const nameB = m.player_b.split(" ").pop();
    return `<button class="mu-feat-chip" data-idx="${i}" title="${m.player_a} vs ${m.player_b} · ${m.year}">
      <span class="mu-feat-matchup">${nameA} <span class="mu-feat-vs">vs</span> ${nameB}</span>
      <span class="mu-feat-label">${m.label}</span>
    </button>`;
  }).join("");

  row.querySelectorAll(".mu-feat-chip").forEach(btn => {
    btn.addEventListener("click", async () => {
      const m = data[+btn.dataset.idx];
      state.matchupA = m.player_a;
      state.matchupB = m.player_b;
      enterMatchupMode(true);

      // Load match year data (for dropdowns / display context)
      if (state.year !== m.year) {
        document.getElementById("year-select").value = m.year;
        await loadYear(m.year);
      }

      // Snap player dropdowns to the matchup players
      const selA = document.getElementById("mu-select-a");
      const selB = document.getElementById("mu-select-b");
      if ([...selA.options].some(o => o.value === m.player_a)) selA.value = m.player_a;
      if ([...selB.options].some(o => o.value === m.player_b)) selB.value = m.player_b;

      // Load prior-year fingerprints to avoid within-tournament data leakage.
      // e.g. for the 2019 Final we use 2018 fingerprints — genuinely pre-tournament.
      let overrideFps = null;
      if (m.fingerprint_year) {
        const priorFps = await loadFingerprintsOnly(m.fingerprint_year);
        if (priorFps && priorFps[m.player_a] && priorFps[m.player_b]) {
          priorFps._year = m.fingerprint_year;  // tag so the result can display it
          overrideFps = priorFps;
        }
      }

      runComparison(m.player_a, m.player_b, overrideFps);
    });
  });
}

// ── Backtest panel ────────────────────────────────────────────────

// Feature validation data — this benchmarks the fingerprint features themselves,
// not the simulation engine. A supervised ML classifier was trained on the same
// fingerprint inputs to confirm they carry signal beyond ranking alone.
const BACKTEST_DATA = {
  n_test:       623,
  train_years:  "1991–2018",
  test_years:   "2019–2023",
  // Approaches compared — from weakest to strongest signal
  approaches: [
    { name: "Coin-flip",         desc: "No information",          acc: 0.50, highlight: false },
    { name: "Ranking only",      desc: "ATP seeding alone",       acc: 0.63, highlight: false },
    { name: "Playing-style fingerprints", desc: "The features powering this tool", acc: 0.70, highlight: true  },
  ],
};

function renderBacktest() {
  const el = document.getElementById("mu-backtest-body");
  if (!el) return;

  const d = BACKTEST_DATA;
  const best = d.approaches.find(a => a.highlight);

  const rows = d.approaches.map(a => {
    const barW = Math.round(a.acc * 100);
    const barClass = a.highlight ? "bt-bar-strong" : a.acc >= 0.63 ? "bt-bar-mid" : "bt-bar-weak";
    return `<div class="bt-row ${a.highlight ? "bt-row-highlight" : ""}">
      <span class="bt-label">${a.name}</span>
      <div class="bt-bar-wrap">
        <div class="bt-bar ${barClass}" style="width:${barW}%"></div>
        <div class="bt-baseline" style="left:50%"></div>
      </div>
      <span class="bt-acc ${a.highlight ? "bt-acc-strong" : ""}">${Math.round(a.acc * 100)}%</span>
    </div>`;
  }).join("");

  el.innerHTML = `
    <div class="bt-headline">
      <span class="bt-headline-num">${Math.round(best.acc * 100)}%</span>
      <span class="bt-headline-label">prediction accuracy · ${d.n_test} matches</span>
      <span class="bt-headline-note">Tested on ${d.test_years} · trained on ${d.train_years}</span>
    </div>
    <div class="bt-rows">${rows}</div>
    <p class="bt-caveat">The fingerprint features driving this tool were validated
    against ${d.n_test} out-of-sample Wimbledon matches. Using playing-style data alone —
    no live odds, no ranking — predicts the correct winner 70% of the time,
    vs 63% for ranking alone. The simulation uses these same fingerprints
    to model each match point by point.</p>`;
}

// ── Start ─────────────────────────────────────────────────────────
boot();
