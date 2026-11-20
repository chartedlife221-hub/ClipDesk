"use strict";
/* ClipDesk UI — vanilla JS, no build step. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers = { "content-type": "application/json" }; o.method = o.method || "POST"; delete o.json; }
  const r = await fetch(path, o);
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}
function toast(msg, isErr = false, ms = 3500) {
  const t = $("#toast"); t.innerHTML = msg; t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.className = "toast"), ms);
}
const fail = (e) => toast(esc(e.message || e), true, 6000);

/* ---------- time ---------- */
function fmt(t, precise = false) {
  if (t == null || isNaN(t)) return "–";
  t = Math.max(0, t);
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  const ss = precise ? s.toFixed(1).padStart(4, "0") : String(Math.floor(s)).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}
function parseTC(str) {
  str = String(str || "").trim().toLowerCase();
  if (!str) return NaN;
  let m = str.match(/^(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?$/);
  if (m && (m[1] || m[2] || m[3])) return (+m[1] || 0) * 3600 + (+m[2] || 0) * 60 + (+m[3] || 0);
  if (/^\d+(\.\d+)?$/.test(str)) return +str;
  const parts = str.split(":");
  if (parts.length < 2 || parts.length > 3 || parts.some((p) => !/^\d+(\.\d+)?$/.test(p))) return NaN;
  return parts.reduce((acc, p) => acc * 60 + +p, 0);
}

/* ---------- global state ---------- */
const S = { system: null, creators: [], view: "", ws: null, q: null, seenJobs: new Map(), dismissed: new Set(), myExports: new Set() };

/* ---------- router ---------- */
window.addEventListener("hashchange", route);
async function route() {
  const h = location.hash.replace(/^#\/?/, "").split("/");
  stopWorkspace();
  $$(".topbar nav a").forEach((a) => a.classList.toggle("on", a.dataset.nav === (h[0] || "home") || (h[0] === "source" && a.dataset.nav === "home")));
  try {
    if (h[0] === "source" && h[1]) await renderWorkspace(h[1], h[2]);
    else if (h[0] === "queue") await renderQueue();
    else if (h[0] === "settings") await renderSettings();
    else await renderHome();
  } catch (e) { $("#main").innerHTML = `<div class="panel err">${esc(e.message)}</div>`; }
}

async function loadSystem() {
  S.system = await api("/api/system");
  const b = $("#banner"), s = S.system, msgs = [];
  if (!s.ffmpeg || !s.ffprobe) msgs.push("FFmpeg/ffprobe not found — imports and exports will fail. See Setup.");
  if (!s.faster_whisper) msgs.push("faster-whisper isn't installed: speech search needs an uploaded .srt/.vtt transcript until it is.");
  else if (!s.whisper_model_cached) msgs.push(`First transcription will download the Whisper “${esc(s.whisper_model)}” model (one time).`);
  if (!s.yt_dlp) msgs.push("yt-dlp isn't installed: URL import is off — upload files instead.");
  b.hidden = !msgs.length; b.innerHTML = msgs.join(" · ") + (msgs.length ? ' <a href="#/settings">Setup</a>' : "");
}

/* ================================================================= HOME */
async function renderHome() {
  S.view = "home";
  const [creators, sources] = await Promise.all([api("/api/creators"), api("/api/sources" + (S.creatorFilter ? `?creator=${S.creatorFilter}` : ""))]);
  S.creators = creators;
  const copts = `<option value="">— no creator —</option>` + creators.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  $("#main").innerHTML = `
  <div class="home">
    <aside class="sidebar panel stack" aria-label="Creator library">
      <h3>Creators</h3>
      <div class="creator ${S.creatorFilter ? "" : "on"}" data-cr="" tabindex="0">All sources</div>
      ${creators.map((c) => `<div class="creator ${S.creatorFilter === c.id ? "on" : ""}" data-cr="${c.id}" tabindex="0">
        <span>${esc(c.name)} <span class="muted small">${esc(c.kind)}</span></span><span class="muted small">${c.sources}</span></div>`).join("")}
      <form id="crForm" class="stack">
        <div><label for="crName">Add creator</label><input id="crName" placeholder="Name" required maxlength="80" style="width:100%"></div>
        <div class="row"><select id="crKind" class="grow" aria-label="Creator type">
          <option value="streamer">Streamer</option><option value="speaker">Speaker</option><option value="motivator">Motivator</option><option value="guest">Interview guest</option><option value="other">Other</option></select>
          <button>Add</button></div>
      </form>
    </aside>
    <section>
      <div class="panel">
        <h2>Add a source</h2>
        <div class="add">
          <form id="urlForm" class="stack">
            <div><label for="url">YouTube or Twitch recording URL</label><input id="url" style="width:100%" placeholder="https://www.youtube.com/watch?v=… · twitch.tv/videos/…" autocomplete="off"></div>
            <div class="row">
              <div class="grow"><label for="urlCreator">Creator</label><select id="urlCreator" style="width:100%">${copts}</select></div>
              <div><label for="urlKind">Content</label><select id="urlKind"><option value="auto">Auto</option><option value="gaming">Gaming stream</option><option value="speech">Talk / interview</option></select></div>
            </div>
            <details><summary class="muted small">Only import part of a long stream</summary>
              <div class="row" style="margin-top:6px"><div><label for="secS">From</label><input id="secS" class="tc" placeholder="1:05:00"></div>
              <div><label for="secE">To</label><input id="secE" class="tc" placeholder="1:25:00"></div></div></details>
            <button class="primary">Import from URL</button>
            <div id="urlErr" class="err" role="alert"></div>
          </form>
          <form id="upForm" class="stack">
            <div id="drop" class="drop"><p>Drop a video here, or</p>
              <label for="file" class="sr">Video file</label><input type="file" id="file" accept="video/*,.mkv,.ts,.flv"></div>
            <div class="row">
              <div class="grow"><label for="upCreator">Creator</label><select id="upCreator" style="width:100%">${copts}</select></div>
              <div><label for="upKind">Content</label><select id="upKind"><option value="auto">Auto</option><option value="gaming">Gaming stream</option><option value="speech">Talk / interview</option></select></div>
            </div>
            <button class="primary" id="upBtn">Upload video</button>
            <div class="progress" hidden id="upProg"><i style="width:0"></i></div>
            <div id="upErr" class="err" role="alert"></div>
          </form>
        </div>
      </div>
      <h2 style="margin-top:18px">Recent projects</h2>
      <div class="sources">${sources.length ? sources.map(sourceCard).join("") : `<div class="empty">No sources yet — paste a link or upload a video above.</div>`}</div>
    </section>
  </div>`;
  $$(".creator").forEach((el) => { const go = () => { S.creatorFilter = el.dataset.cr || null; renderHome(); }; el.onclick = go; el.onkeydown = (e) => e.key === "Enter" && go(); });
  $("#crForm").onsubmit = async (e) => { e.preventDefault(); try { await api("/api/creators", { json: { name: $("#crName").value, kind: $("#crKind").value } }); renderHome(); } catch (er) { fail(er); } };
  $("#urlForm").onsubmit = async (e) => {
    e.preventDefault(); $("#urlErr").textContent = "";
    const body = { url: $("#url").value, creator_id: $("#urlCreator").value, content_kind: $("#urlKind").value };
    if ($("#secS").value || $("#secE").value) {
      body.section_start = parseTC($("#secS").value); body.section_end = parseTC($("#secE").value);
      if (isNaN(body.section_start) || isNaN(body.section_end)) { $("#urlErr").textContent = "Section times must look like 1:05:00"; return; }
    }
    try { const s = await api("/api/sources/url", { json: body }); toast(`Added ${esc(s.url)}`); renderHome(); }
    catch (er) { $("#urlErr").textContent = er.message; }
  };
  const drop = $("#drop");
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) { $("#file").files = e.dataTransfer.files; } });
  $("#upForm").onsubmit = (e) => {
    e.preventDefault(); $("#upErr").textContent = "";
    const f = $("#file").files[0]; if (!f) { $("#upErr").textContent = "Choose a video file."; return; }
    const fd = new FormData(); fd.append("file", f); fd.append("creator_id", $("#upCreator").value); fd.append("content_kind", $("#upKind").value);
    const xhr = new XMLHttpRequest(); const bar = $("#upProg"); bar.hidden = false; $("#upBtn").disabled = true;
    xhr.upload.onprogress = (ev) => ev.lengthComputable && ($("i", bar).style.width = (100 * ev.loaded / ev.total).toFixed(1) + "%");
    xhr.onload = () => { $("#upBtn").disabled = false; let d = {}; try { d = JSON.parse(xhr.responseText); } catch {}
      if (xhr.status >= 300) { $("#upErr").textContent = d.error || "Upload failed"; bar.hidden = true; return; }
      toast("Uploaded — importing…"); location.hash = `#/source/${d.id}`; };
    xhr.onerror = () => { $("#upBtn").disabled = false; $("#upErr").textContent = "Upload failed (connection)"; };
    xhr.open("POST", "/api/sources/upload"); xhr.send(fd);
  };
}
const STATUS_LABEL = { queued: "Queued", importing: "Importing", ready: "Ready", live_waiting: "Live · awaiting replay", inaccessible: "Inaccessible", failed: "Failed" };
function sourceCard(s) {
  const j = s.job && ["queued", "running"].includes(s.job.status) ? s.job : null;
  return `<article class="scard">
    <a href="#/source/${s.id}" class="thumb" style="${s.has_poster ? `background-image:url(/api/sources/${s.id}/poster)` : ""}" aria-label="Open ${esc(s.title)}">${s.has_poster ? "" : esc(STATUS_LABEL[s.status])}</a>
    <div class="body stack">
      <div class="row"><span class="badge b-${s.status}">${STATUS_LABEL[s.status] || s.status}</span><span class="muted small">${s.duration ? fmt(s.duration) : ""}</span>
        <span class="muted small grow" style="text-align:right">${esc(s.origin)}</span></div>
      <a class="title" href="#/source/${s.id}" style="color:inherit;text-decoration:none">${esc(s.title || s.url)}</a>
      <div class="muted small">${esc(s.creator_name || "No creator")} · ${s.clips.n || 0} clips · ${s.clips.approved || 0} approved</div>
      ${j ? `<div class="progress"><i style="width:${(j.progress * 100).toFixed(0)}%"></i></div><div class="muted small">${esc(j.message)}</div>` : ""}
      ${["failed", "inaccessible", "live_waiting"].includes(s.status) ? `<div class="${s.status === "live_waiting" ? "warn" : "err"}">${esc(s.status_detail)}</div>
        ${s.origin !== "upload" || s.status === "failed" ? `<button class="small" data-retry="${s.id}">Retry import</button>` : ""}` : ""}
    </div></article>`;
}
document.addEventListener("click", async (e) => {
  const r = e.target.closest("[data-retry]");
  if (r) { try { await api(`/api/sources/${r.dataset.retry}/retry`, { json: {} }); toast("Retrying import"); route(); } catch (er) { fail(er); } }
});

/* ============================================================== WORKSPACE */
function stopWorkspace() { if (S.ws) { cancelAnimationFrame(S.ws.raf); S.ws.alive = false; S.ws = null; } }

async function renderWorkspace(sid, clipId) {
  S.view = "ws";
  const src = await api(`/api/sources/${sid}`);
  if (!S.creators.length) S.creators = await api("/api/creators");
  const ws = (S.ws = { sid, src, alive: true, clips: [], clip: null, rev: null, sel: { s: 0, e: 0 }, playSel: false, signals: null, saveT: null, lastReq: null });
  const ready = src.status === "ready" && src.has_media;
  $("#main").innerHTML = `
  <div class="wshead">
    <div class="grow"><div class="row"><a href="#/" class="muted small">← Sources</a><span class="badge b-${src.status}">${STATUS_LABEL[src.status]}</span>
      <span class="muted small">${esc(src.creator_name || "No creator")} · ${src.duration ? fmt(src.duration) : "duration unknown"} · ${esc(src.origin)}${src.time_offset ? ` · imported from ${fmt(src.time_offset)}` : ""}</span></div>
      <h1>${esc(src.title || src.url)}</h1>
      <div class="muted small">Transcript: <span class="badge b-${src.transcript_status === "ready" ? "ready" : src.transcript_status === "running" ? "running" : "weak"}">${esc(src.transcript_status)}</span> ${esc(src.transcript_detail)}
        · Chat: <span class="badge b-${src.chat_status === "ready" ? "ready" : "weak"}">${src.chat_status === "ready" ? "loaded" : "none"}</span></div>
    </div>
    <div class="row">
      <select id="srcCreator" aria-label="Creator">${`<option value="">— no creator —</option>` + S.creators.map((c) => `<option value="${c.id}" ${c.id === src.creator_id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
      <input type="file" id="trFile" accept=".srt,.vtt,.json,.json3" hidden><button type="button" id="trBtn">Upload transcript</button>
      <input type="file" id="chatFile" accept=".json,.csv,.jsonl,.txt" hidden><button type="button" id="chatBtn">Upload chat log</button>
      ${S.system.faster_whisper && ready && src.transcript_status !== "running" ? `<button id="whBtn">${src.transcript_status === "ready" ? "Re-transcribe" : "Transcribe"}</button>` : ""}
    </div>
  </div>
  ${!ready ? `<div class="panel stack">${["failed", "inaccessible", "live_waiting"].includes(src.status) ? `<div class="${src.status === "live_waiting" ? "warn" : "err"}"><b>${STATUS_LABEL[src.status]}.</b> ${esc(src.status_detail)}</div>
      <div class="row"><button data-retry="${src.id}">Retry import</button><a href="#/">Upload the file instead</a></div>` :
      `<div>Importing… ${src.job ? esc(src.job.message) : ""}</div><div class="progress"><i style="width:${((src.job?.progress || 0) * 100).toFixed(0)}%"></i></div>`}</div>` : `
  <div class="ws">
    <section class="stack" aria-label="Source">
      <div class="player" id="player"><video id="vid" src="/api/sources/${sid}/media" controls preload="auto" playsinline></video><div class="overlay" id="ovl"></div></div>
      <div class="timeline" id="tlAll" title="Whole source — click to seek"><canvas id="heat"></canvas><div class="sel" id="selAll"></div><div class="head" id="headAll"></div></div>
      <div class="timeline detail" id="tlDet" title="Selection detail — drag the handles">
        <div class="sel" id="selDet"></div><div class="head" id="headDet"></div>
        <div class="handle" id="hS" tabindex="0" role="slider" aria-label="Clip start" aria-valuemin="0"></div>
        <div class="handle" id="hE" tabindex="0" role="slider" aria-label="Clip end"></div>
      </div>
      <div class="tl-labels"><span id="detL"></span><span id="detM" class="muted"></span><span id="detR"></span></div>
      <div class="panel">
        <div class="row">
          <div><label for="goTC">Go to timestamp</label><div class="row"><input id="goTC" class="tc" placeholder="12:05"><button id="goBtn" title="G">Seek</button></div></div>
          <div><label for="selS">Start</label><div class="row"><button class="small" data-nudge="s,-0.5">−</button><input id="selS" class="tc"><button class="small" data-nudge="s,0.5">+</button><button class="small" id="setS" title="I">Set to playhead</button></div></div>
          <div><label for="selE">End</label><div class="row"><button class="small" data-nudge="e,-0.5">−</button><input id="selE" class="tc"><button class="small" data-nudge="e,0.5">+</button><button class="small" id="setE" title="O">Set to playhead</button></div></div>
        </div>
        <div class="row" style="margin-top:8px">
          <button id="playSel" title="P">▶ Replay selection</button>
          <span id="selInfo" class="muted small"></span><span id="selErr" class="err" role="alert"></span>
          <span class="grow"></span>
          <button class="primary" id="mkClip" title="Create a clip from this selection">New clip from selection</button>
        </div>
      </div>
      <div class="panel findbox">
        <h2>Find a moment</h2>
        <form id="findForm" class="stack">
          <label for="findText">Describe what you want</label>
          <textarea id="findText" placeholder="${src.content_kind === "speech" ? "Find the story about the first failed business and keep the lesson." : "Find a surprising play and keep the reaction."}"></textarea>
          <div class="row"><select id="findMode" aria-label="Search mode"><option value="auto">Auto (${src.chat_status === "ready" ? "gaming signals" : src.transcript_status === "ready" ? "transcript" : "signals"})</option><option value="speech">Speech: search transcript</option><option value="gaming">Gaming: loudness/chat reactions</option></select>
            <button class="primary">Find</button><span class="muted small">Ranked by: ${esc(S.system.ranker)}</span></div>
        </form>
        <div id="results" class="results"></div>
      </div>
    </section>
    <section class="editor stack" aria-label="Clip editor">
      <div class="panel">
        <div class="row" style="justify-content:space-between"><h3 style="margin:0">Clips in this source</h3><span class="muted small">J / K</span></div>
        <div class="cliplist" id="clipList"></div>
      </div>
      <div id="editor"></div>
    </section>
  </div>`}`;
  $("#srcCreator").onchange = async (e) => { try { await api(`/api/sources/${sid}`, { method: "PATCH", json: { creator_id: e.target.value } }); toast("Creator updated"); } catch (er) { fail(er); } };
  $("#trBtn").onclick = () => $("#trFile").click();
  $("#trFile").onchange = (e) => attach(sid, "transcript", e.target.files[0]);
  $("#chatBtn").onclick = () => $("#chatFile").click();
  $("#chatFile").onchange = (e) => attach(sid, "chat", e.target.files[0]);
  if ($("#whBtn")) $("#whBtn").onclick = async () => { try { await api(`/api/sources/${sid}/transcribe`, { json: {} }); toast("Transcription queued"); } catch (er) { fail(er); } };
  if (!ready) return;
  ws.video = $("#vid");
  ws.sel = { s: 0, e: Math.min(src.duration, 30) };
  ws.video.addEventListener("loadedmetadata", () => { drawHeat(); layoutOverlay(); });
  window.addEventListener("resize", () => { drawHeat(); layoutOverlay(); });
  api(`/api/sources/${sid}/signals`).then((s) => { ws.signals = s; drawHeat(); });
  bindTimeline(); bindTrim();
  $("#findForm").onsubmit = async (e) => {
    e.preventDefault();
    const text = $("#findText").value.trim() || $("#findText").placeholder;
    try { const r = await api(`/api/sources/${sid}/find`, { json: { text, mode: $("#findMode").value } }); ws.lastReq = r.id; renderResults([r, ...src.requests]); toast("Searching…"); }
    catch (er) { fail(er); }
  };
  renderResults(src.requests);
  syncTrim();
  await loadClips(clipId);
  tick();
}

async function attach(sid, what, file) {
  if (!file) return;
  const fd = new FormData(); fd.append("file", file);
  try { const r = await fetch(`/api/sources/${sid}/attach/${what}`, { method: "POST", body: fd }); const d = await r.json(); if (!r.ok) throw new Error(d.error);
    toast(what === "chat" ? `Chat loaded: ${d.messages} messages` : `Transcript loaded: ${d.words} words (${d.word_timing} timing)`); route(); }
  catch (e) { fail(e); }
}

function renderResults(reqs) {
  const ws = S.ws; if (!ws) return;
  ws.requests = reqs;
  const el = $("#results");
  if (!reqs.length) { el.innerHTML = `<p class="muted small">Results appear here with source timecodes and the reason each was picked. You can clip by timestamp while a search runs.</p>`; return; }
  el.innerHTML = reqs.slice(0, 4).map((r, i) => {
    const running = r.job && ["queued", "running"].includes(r.job.status);
    return `<details ${i === 0 ? "open" : ""} class="stack"><summary><b>“${esc(r.text)}”</b> <span class="muted small">${esc(r.mode)} · ${running ? "searching…" : r.status}</span></summary>
      ${running ? `<div class="progress"><i style="width:${(r.job.progress * 100).toFixed(0)}%"></i></div><div class="muted small">${esc(r.job.message)}</div>` : ""}
      ${r.note ? `<p class="${r.candidates.some((c) => c.strength === "strong") ? "muted" : "warn"} small">${esc(r.note)}</p>` : ""}
      ${r.provider ? `<p class="muted small">Ranked by ${esc(r.provider)}</p>` : ""}
      ${r.candidates.map((c) => resultCard(c)).join("")}
    </details>`;
  }).join("");
  $$(".result", el).forEach((d) => (d.onclick = () => selectClip(d.dataset.clip, true)));
}
function resultCard(c) {
  const sg = c.signals || {};
  const bars = sg.engine === "signals-v1"
    ? [["audio", sg.audio], ["chat", sg.chat_available ? sg.chat : null], ["hype", sg.chat_available ? sg.hype : null], ["request", sg.relevance]]
    : [["coverage", sg.coverage], ["payoff", sg.payoff ? 1 : 0], ["question", sg.question ? 1 : 0]];
  const off = S.ws?.src.time_offset || 0;
  return `<div class="result ${S.ws?.clip?.id === c.clip_id ? "on" : ""}" data-clip="${c.clip_id}" tabindex="0">
    <div class="row"><b>#${c.rank}</b><span class="tcs">${fmt(c.start + off, true)} – ${fmt(c.end + off, true)}</span><span class="muted small">${(c.end - c.start).toFixed(1)}s</span>
      <span class="badge b-${c.strength}">${c.strength}</span>${c.clip_status !== "pending" ? `<span class="badge b-${c.clip_status}">${c.clip_status}</span>` : ""}<span class="grow"></span><span class="muted small">score ${c.score.toFixed(2)}</span></div>
    <div>${esc(c.title)}</div><div class="reason">${esc(c.reason)}</div>
    <div class="bars">${bars.map(([n, v]) => `<div class="bar">${n}${v == null ? " n/a" : ""}<i><b style="width:${((v || 0) * 100).toFixed(0)}%"></b></i></div>`).join("")}</div></div>`;
}

async function loadClips(selectId) {
  const ws = S.ws; if (!ws) return;
  ws.clips = await api(`/api/clips?source=${ws.sid}`);
  ws.clips.sort((a, b) => a.start - b.start);
  drawClipList();
  const id = selectId || ws.clip?.id;
  if (id && ws.clips.some((c) => c.id === id)) await selectClip(id, false);
  else renderEditor();
}
function drawClipList() {
  const ws = S.ws; const el = $("#clipList"); if (!el) return;
  el.innerHTML = ws.clips.length ? ws.clips.map((c) => `<div class="chip ${ws.clip?.id === c.id ? "on" : ""}" data-id="${c.id}" tabindex="0">
    <div class="row"><span class="mono">${fmt(c.start + (c.time_offset || 0))}</span><span class="badge b-${c.status}">${c.status}</span></div>
    <div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(c.title || "Untitled")}</div></div>`).join("")
    : `<span class="muted small">No clips yet. Set a start and end, then “New clip from selection” — or use Find a moment.</span>`;
  $$(".chip", el).forEach((d) => { d.onclick = () => selectClip(d.dataset.id, true); d.onkeydown = (e) => e.key === "Enter" && selectClip(d.dataset.id, true); });
}

async function selectClip(id, seek) {
  const ws = S.ws; if (!ws || !id) return;
  const token = (ws.selSeq = (ws.selSeq || 0) + 1);   // last selection wins if requests overlap
  await flushSave();
  const k = await api(`/api/clips/${id}`);
  if (S.ws !== ws || token !== ws.selSeq) return;
  ws.clip = k; ws.rev = structuredClone(k.rev);
  ws.sel = { s: k.rev.start, e: k.rev.end };
  history.replaceState(null, "", `#/source/${ws.sid}/${id}`);
  if (seek) { ws.video.currentTime = k.rev.start; }
  drawClipList(); renderEditor(); renderResults(ws.requests || []); syncTrim(); layoutOverlay();
}

/* ---------- editor ---------- */
function renderEditor() {
  const ws = S.ws; const el = $("#editor"); if (!el) return;
  const k = ws.clip;
  if (!k) { el.innerHTML = `<div class="panel muted">Select a clip, or make one from the selection, to edit framing, captions and review it.</div>`; $("#mkClip").textContent = "New clip from selection"; return; }
  $("#mkClip").textContent = "New clip from selection";
  const L = ws.rev.layout || {}; const mode = L.mode || "speaker";
  const fin = k.renders.find((r) => r.kind === "final" && r.rev === k.approved_rev);
  const prev = k.renders.find((r) => r.kind === "preview" && r.rev === k.current_rev);
  el.innerHTML = `
  <div class="panel stack">
    <div class="row"><span class="badge b-${k.status}">${k.status}</span>${k.edited_since_approval ? `<span class="warn small">edited since approval — approve again to export this version</span>` : ""}
      <span class="grow"></span><span class="mono small">${fmt(ws.rev.start + (k.time_offset || 0), true)} – ${fmt(ws.rev.end + (k.time_offset || 0), true)}</span></div>
    <div class="tabs" role="tablist" aria-label="Preview">
      <button role="tab" data-pv="live" class="${ws.pv !== "file" ? "on" : ""}">Live preview</button>
      <button role="tab" data-pv="file" class="${ws.pv === "file" ? "on" : ""}" ${prev || fin ? "" : "disabled"}>Rendered file</button></div>
    <div class="vwrap"><canvas id="vprev" class="vprev" width="360" height="640" aria-label="Vertical 9:16 preview"></canvas>
      <video id="vfile" class="vprev" controls hidden playsinline></video></div>
    <div class="muted small" style="text-align:center">1080×1920 · 30 fps · ${(ws.rev.end - ws.rev.start).toFixed(1)}s (max ${S.system.limits.max_clip_s}s)</div>
    <div class="review" role="toolbar" aria-label="Review">
    <button class="ok" id="bApprove" title="A">✓ Approve</button>
    <button class="bad" id="bReject" title="R">✕ Reject</button>
    <button id="bUndo" title="U / Ctrl+Z" ${k.can_undo ? "" : "disabled"}>↶ Undo</button>
    <button id="bRender" title="V">Render preview</button>
    <button class="primary" id="bExport" title="E">${k.status === "approved" && !k.edited_since_approval ? "Export MP4" : "Approve & export MP4"}</button>
    ${fin ? `<a href="/api/clips/${k.id}/download.mp4" class="small">Download MP4</a>` : ""}
    <span id="saveState" class="muted small"></span>
    ${k.job && ["queued", "running"].includes(k.job.status) ? `<div class="progress grow"><i style="width:${(k.job.progress * 100).toFixed(0)}%"></i></div>` : ""}
  </div>
    <div><label>Framing</label><div class="tabs" role="tablist">
      ${["speaker", "fit", "gameplay"].map((m) => `<button data-mode="${m}" class="${mode === m ? "on" : ""}">${{ speaker: "Speaker crop", fit: "Fit to frame", gameplay: "Gameplay + facecam" }[m]}</button>`).join("")}</div></div>
    <div id="layoutCtl" class="sliders"></div>
    <div><label for="capY">Caption position</label><input type="range" id="capY" min="0.15" max="0.95" step="0.01" value="${L.caption_y ?? 0.8}" style="width:100%"></div>
    <div><label for="title">Title</label><input id="title" style="width:100%" value="${esc(ws.rev.title)}" maxlength="120"></div>
    <div><label for="tags">Hashtags</label><input id="tags" style="width:100%" value="${esc((ws.rev.hashtags || []).join(" "))}"></div>
    <div><div class="row" style="justify-content:space-between"><label>Captions (source time)</label><button class="small" id="addCap">+ line at playhead</button></div>
      <div class="caps" id="caps"></div></div>
    ${k.candidate ? `<details><summary class="muted small">Why this was suggested (original, unedited)</summary><p class="small">${esc(k.candidate.reason)}</p>
      <p class="muted small">Original: ${fmt(k.candidate.start + (k.time_offset || 0), true)} – ${fmt(k.candidate.end + (k.time_offset || 0), true)} · “${esc(k.candidate.title)}”</p></details>` : ""}
  </div>`;
  $$("[data-pv]").forEach((b) => (b.onclick = () => { ws.pv = b.dataset.pv; renderEditor(); }));
  const vf = $("#vfile"), cv = $("#vprev");
  if (ws.pv === "file" && (prev || fin)) {
    cv.hidden = true; vf.hidden = false;
    vf.src = prev ? `/api/clips/${k.id}/preview.mp4?r=${prev.rev}` : `/api/clips/${k.id}/download.mp4`;
  }
  ws.canvas = cv;
  $$("[data-mode]").forEach((b) => (b.onclick = () => { setLayout({ mode: b.dataset.mode }, true); }));
  $("#capY").oninput = (e) => setLayout({ caption_y: +e.target.value });
  $("#title").oninput = (e) => { ws.rev.title = e.target.value; queueSave({ title: ws.rev.title }); };
  $("#tags").oninput = (e) => { ws.rev.hashtags = e.target.value.split(/\s+/).filter(Boolean); queueSave({ hashtags: ws.rev.hashtags }); };
  $("#addCap").onclick = () => {
    const t = clamp(ws.video.currentTime, ws.rev.start, ws.rev.end - 0.5);
    ws.rev.captions.push({ s: +t.toFixed(2), e: +Math.min(t + 2, ws.rev.end).toFixed(2), text: "New caption" });
    ws.rev.captions.sort((a, b) => a.s - b.s); drawCaps(); queueSave({ captions: ws.rev.captions });
  };
  $("#bApprove").onclick = () => setStatus("approved");
  $("#bReject").onclick = () => setStatus("rejected");
  $("#bUndo").onclick = undo;
  $("#bRender").onclick = renderPreview;
  $("#bExport").onclick = exportClip;
  drawLayoutCtl(); drawCaps();
}
function drawLayoutCtl() {
  const ws = S.ws; const L = ws.rev.layout || {}; const el = $("#layoutCtl"); if (!el) return;
  const sl = (id, label, v, min, max, step) => `<div><label for="${id}">${label}</label><input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${v}"></div>`;
  if (L.mode === "gameplay") {
    el.innerHTML = sl("lsplit", "Facecam height share", L.split ?? 0.38, 0.15, 0.7, 0.01) +
      `<div class="muted small" style="grid-column:1/-1">Drag the pink (facecam) and blue (action) boxes on the source player; corner grip resizes.</div>`;
    $("#lsplit").oninput = (e) => setLayout({ split: +e.target.value });
  } else if (L.mode === "speaker") {
    el.innerHTML = sl("lcx", "Horizontal position", L.cx ?? 0.5, 0, 1, 0.005) + sl("lzoom", "Zoom", L.zoom ?? 1, 1, 2.5, 0.01) +
      `<div style="grid-column:1/-1" class="row"><button class="small" id="autoC">Auto-center on face</button><span id="autoNote" class="muted small"></span></div>`;
    $("#lcx").oninput = (e) => setLayout({ cx: +e.target.value });
    $("#lzoom").oninput = (e) => setLayout({ zoom: +e.target.value });
    $("#autoC").onclick = async () => { try { await flushSave(); const r = await api(`/api/clips/${ws.clip.id}/autocenter`, { json: {} }); $("#autoNote").textContent = r.note; if (r.clip.rev.id !== ws.clip.rev.id) await selectClip(ws.clip.id, false); } catch (er) { fail(er); } };
  } else el.innerHTML = `<div class="muted small" style="grid-column:1/-1">Whole frame, scaled to width, over a blurred copy.</div>`;
  layoutOverlay();
}
function drawCaps() {
  const ws = S.ws; const el = $("#caps"); if (!el) return;
  const caps = ws.rev.captions || [];
  const inRange = caps.map((c, i) => [c, i]).filter(([c]) => c.e > ws.rev.start && c.s < ws.rev.end);
  el.innerHTML = inRange.length ? inRange.map(([c, i]) => `<div class="cap" data-i="${i}"><span class="t" title="Seek here">${fmt(c.s + (ws.src.time_offset || 0), true)}</span>
    <input value="${esc(c.text)}" aria-label="Caption at ${fmt(c.s, true)}"><button class="small ghost" aria-label="Delete caption">✕</button></div>`).join("")
    : `<div class="muted small" style="padding:8px">${ws.src.transcript_status === "ready" ? "No speech in this range." : "No transcript — add lines manually or upload a transcript."}</div>`;
  $$(".cap", el).forEach((row) => {
    const i = +row.dataset.i;
    $("input", row).oninput = (e) => { caps[i].text = e.target.value; queueSave({ captions: caps }); };
    $("button", row).onclick = () => { caps.splice(i, 1); drawCaps(); queueSave({ captions: caps }); };
    $(".t", row).onclick = () => (ws.video.currentTime = caps[i].s);
  });
}
function setLayout(patch, rerender = false) {
  const ws = S.ws; if (!ws?.clip) return;
  if (patch.mode && patch.mode !== ws.rev.layout.mode) {
    const defaults = { speaker: { mode: "speaker", cx: 0.5, zoom: 1, caption_y: 0.8 }, fit: { mode: "fit", caption_y: 0.8 },
      gameplay: { mode: "gameplay", split: 0.38, caption_y: 0.46, face: { x: 0.7, y: 0.62, w: 0.28, h: 0.36 }, action: { x: 0.18, y: 0, w: 0.64, h: 1 } } };
    ws.rev.layout = { ...defaults[patch.mode] };
  } else ws.rev.layout = { ...ws.rev.layout, ...patch };
  queueSave({ layout: ws.rev.layout });
  if (rerender) renderEditor(); else layoutOverlay();
}

/* ---------- saving (debounced revisions) ---------- */
function queueSave(patch) {
  const ws = S.ws; if (!ws?.clip) return;
  ws.pending = { ...(ws.pending || {}), ...patch };
  const st = $("#saveState"); if (st) st.textContent = "unsaved…";
  clearTimeout(ws.saveT); ws.saveT = setTimeout(flushSave, 700);
}
async function flushSave() {
  const ws = S.ws; if (!ws?.clip || !ws.pending) return;
  clearTimeout(ws.saveT);
  const patch = ws.pending; ws.pending = null;
  try {
    const k = await api(`/api/clips/${ws.clip.id}/revise`, { json: patch });
    const rangeChanged = "start" in patch || "end" in patch;
    ws.clip = k;
    if (rangeChanged) { ws.rev.captions = k.rev.captions; drawCaps(); }
    ws.rev.id = k.rev.id;
    const st = $("#saveState"); if (st) st.textContent = `saved · rev ${k.rev.id}`;
    const b = $("#bUndo"); if (b) b.disabled = !k.can_undo;
    const i = ws.clips.findIndex((c) => c.id === k.id);
    if (i >= 0) { Object.assign(ws.clips[i], { start: k.rev.start, end: k.rev.end, title: k.rev.title, status: k.status }); drawClipList(); }
    if (k.edited_since_approval && !$(".review .warn")) renderEditor();
  } catch (e) { fail(e); const st = $("#saveState"); if (st) st.textContent = "not saved"; }
}

async function setStatus(status) {
  const ws = S.ws; if (!ws?.clip) return;
  await flushSave();
  try { ws.clip = await api(`/api/clips/${ws.clip.id}/status`, { json: { status } }); toast(status === "approved" ? "Approved ✓" : "Rejected"); await loadClips(ws.clip.id); }
  catch (e) { fail(e); }
}
async function undo() {
  const ws = S.ws; if (!ws?.clip) return;
  ws.pending = null; clearTimeout(ws.saveT);
  try { await api(`/api/clips/${ws.clip.id}/undo`, { json: {} }); toast("Undone"); await loadClips(ws.clip.id); }
  catch (e) { fail(e); }
}
async function renderPreview() {
  const ws = S.ws; if (!ws?.clip) return;
  await flushSave();
  try { const j = await api(`/api/clips/${ws.clip.id}/render`, { json: { kind: "preview" } }); watchJob(j.id, () => { ws.pv = "file"; selectClip(ws.clip.id, false); }); toast("Rendering preview with the export pipeline…"); }
  catch (e) { fail(e); }
}
async function exportClip() {
  const ws = S.ws; if (!ws?.clip) return;
  await flushSave();
  try {
    let k = ws.clip;
    if (k.status !== "approved" || k.edited_since_approval) k = await api(`/api/clips/${k.id}/status`, { json: { status: "approved" } });
    const j = await api(`/api/clips/${k.id}/render`, { json: { kind: "final" } });
    toast("Rendering 1080×1920 MP4…");
    watchJob(j.id, () => { download(`/api/clips/${k.id}/download.mp4`); loadClips(k.id); });
    await loadClips(k.id);
  } catch (e) { fail(e); }
}
function download(url) { const a = document.createElement("a"); a.href = url; a.download = ""; document.body.appendChild(a); a.click(); a.remove(); }

/* ---------- selection / trim ---------- */
function selValid(s, e) {
  const d = S.ws.src.duration, L = S.system.limits;
  if (isNaN(s) || isNaN(e)) return "Enter times like 12:05.5";
  if (s < 0) return "Start can't be negative";
  if (e > d + 0.05) return `End is past the source end (${fmt(d)})`;
  if (e <= s) return "Start must be before end";
  if (e - s < L.min_clip_s) return `Minimum ${L.min_clip_s}s`;
  if (e - s > L.max_clip_s) return `${(e - s).toFixed(1)}s is over the ${L.max_clip_s}s export limit`;
  return "";
}
function setSel(s, e, save = true) {
  const ws = S.ws; const d = ws.src.duration;
  s = clamp(+(+s).toFixed(2), 0, d); e = clamp(+(+e).toFixed(2), 0, d);
  ws.sel = { s, e };
  const err = selValid(s, e);
  syncTrim(err);
  if (!err && save && ws.clip) { ws.rev.start = s; ws.rev.end = e; queueSave({ start: s, end: e }); }
}
function syncTrim(err) {
  const ws = S.ws; if (!ws || !$("#selS")) return;
  const off = ws.src.time_offset || 0;
  if (document.activeElement !== $("#selS")) $("#selS").value = fmt(ws.sel.s + off, true);
  if (document.activeElement !== $("#selE")) $("#selE").value = fmt(ws.sel.e + off, true);
  err = err ?? selValid(ws.sel.s, ws.sel.e);
  $("#selErr").textContent = err; $("#selInfo").textContent = `${(ws.sel.e - ws.sel.s).toFixed(1)}s selected`;
  $("#mkClip").disabled = !!err;
}
function bindTrim() {
  const ws = S.ws; const off = () => ws.src.time_offset || 0;
  const commit = () => setSel(parseTC($("#selS").value) - off(), parseTC($("#selE").value) - off());
  ["#selS", "#selE"].forEach((id) => { $(id).addEventListener("change", commit); $(id).addEventListener("keydown", (e) => e.key === "Enter" && (commit(), e.target.blur())); });
  $("#setS").onclick = () => setSel(ws.video.currentTime, Math.max(ws.sel.e, ws.video.currentTime + 1));
  $("#setE").onclick = () => setSel(ws.sel.s, ws.video.currentTime);
  $$("[data-nudge]").forEach((b) => (b.onclick = () => { const [w, d] = b.dataset.nudge.split(","); w === "s" ? setSel(ws.sel.s + +d, ws.sel.e) : setSel(ws.sel.s, ws.sel.e + +d); }));
  const go = () => { const t = parseTC($("#goTC").value) - off(); if (isNaN(t) || t < 0 || t > ws.src.duration) { toast(`Timestamp must be between ${fmt(off())} and ${fmt(ws.src.duration + off())}`, true); return; }
    ws.video.currentTime = t; const len = Math.min(20, ws.src.duration - t); if (!ws.clip && len >= S.system.limits.min_clip_s) setSel(Math.max(0, t - 5), t + len - 5 > t ? t + len - 5 : t + len, false); };
  $("#goBtn").onclick = go; $("#goTC").addEventListener("keydown", (e) => e.key === "Enter" && (go(), e.target.blur()));
  $("#playSel").onclick = playSelection;
  $("#mkClip").onclick = async () => {
    try { const k = await api("/api/clips", { json: { source_id: ws.sid, start: ws.sel.s, end: ws.sel.e } }); toast("Clip created"); await loadClips(k.id); }
    catch (e) { fail(e); }
  };
}
function playSelection() { const ws = S.ws; ws.video.currentTime = ws.sel.s; ws.playSel = true; ws.video.play(); }

function bindTimeline() {
  const ws = S.ws;
  $("#tlAll").addEventListener("pointerdown", (e) => { const r = e.currentTarget.getBoundingClientRect(); ws.video.currentTime = clamp((e.clientX - r.left) / r.width, 0, 1) * ws.src.duration; });
  const det = $("#tlDet");
  const detRange = () => { const pad = Math.max(10, (ws.sel.e - ws.sel.s) * 0.6); let a = Math.max(0, ws.sel.s - pad), b = Math.min(ws.src.duration, ws.sel.e + pad); return [a, b]; };
  ws.detRange = detRange;
  let drag = null;
  det.addEventListener("pointerdown", (e) => {
    const r = det.getBoundingClientRect(), [a, b] = ws.detFrozen || detRange(), t = a + (e.clientX - r.left) / r.width * (b - a);
    if (e.target.id === "hS" || e.target.id === "hE") { drag = e.target.id; ws.detFrozen = [a, b]; det.setPointerCapture(e.pointerId); }
    else ws.video.currentTime = t;
  });
  det.addEventListener("pointermove", (e) => {
    if (!drag) return; const r = det.getBoundingClientRect(), [a, b] = ws.detFrozen;
    const t = a + clamp((e.clientX - r.left) / r.width, 0, 1) * (b - a);
    if (drag === "hS") { setSel(Math.min(t, ws.sel.e - 0.5), ws.sel.e, false); ws.video.currentTime = ws.sel.s; }
    else { setSel(ws.sel.s, Math.max(t, ws.sel.s + 0.5), false); ws.video.currentTime = ws.sel.e; }
  });
  det.addEventListener("pointerup", () => { if (drag) { drag = null; ws.detFrozen = null; setSel(ws.sel.s, ws.sel.e, true); } });
  ["hS", "hE"].forEach((id) => $("#" + id).addEventListener("keydown", (e) => {
    const d = e.key === "ArrowLeft" ? -0.1 : e.key === "ArrowRight" ? 0.1 : 0; if (!d) return; e.preventDefault(); e.stopPropagation();
    const k = e.shiftKey ? 10 : 1; id === "hS" ? setSel(ws.sel.s + d * k, ws.sel.e) : setSel(ws.sel.s, ws.sel.e + d * k);
  }));
}
function drawHeat() {
  const ws = S.ws; const c = $("#heat"); if (!ws || !c) return;
  const w = (c.width = c.clientWidth * devicePixelRatio), h = (c.height = c.clientHeight * devicePixelRatio), g = c.getContext("2d");
  g.clearRect(0, 0, w, h);
  const sg = ws.signals; if (!sg || !sg.audio.length) return;
  const a = sg.audio, mn = Math.min(...a), mx = Math.max(...a), n = a.length;
  g.fillStyle = "rgba(106,168,255,.55)";
  for (let i = 0; i < n; i++) { const v = mx > mn ? (a[i] - mn) / (mx - mn) : 0; const x = i / n * w; g.fillRect(x, h - v * h * 0.9, Math.max(1, w / n), v * h * 0.9); }
  if (sg.chat.length) { const cm = Math.max(1, ...sg.chat); g.fillStyle = "rgba(255,143,216,.7)"; for (let i = 0; i < sg.chat.length; i++) { const v = sg.chat[i] / cm; g.fillRect(i / n * w, 0, Math.max(1, w / n), v * h * 0.35); } }
}

/* ---------- overlay regions on the source player ---------- */
function videoRect() {
  const v = S.ws.video, cw = v.clientWidth, ch = v.clientHeight - 0, vw = v.videoWidth || 16, vh = v.videoHeight || 9;
  const s = Math.min(cw / vw, ch / vh); const w = vw * s, h = vh * s; return { x: (cw - w) / 2, y: (ch - h) / 2, w, h };
}
function layoutOverlay() {
  const ws = S.ws; const o = $("#ovl"); if (!ws || !o) return;
  if (!ws.clip) { o.innerHTML = ""; return; }
  const L = ws.rev.layout, R = videoRect(), v = ws.video;
  const box = (cls, r, label, key) => `<div class="region ${cls}" data-key="${key}" style="left:${R.x + r.x * R.w}px;top:${R.y + r.y * R.h}px;width:${r.w * R.w}px;height:${r.h * R.h}px">${label}${key !== "crop" ? '<span class="grip" data-grip="1"></span>' : ""}</div>`;
  if (L.mode === "gameplay") o.innerHTML = box("face", L.face, "facecam", "face") + box("action", L.action, "action", "action");
  else if (L.mode === "speaker") {
    const W = v.videoWidth || 1920, H = v.videoHeight || 1080, z = L.zoom || 1;
    let ch = H / z, cw = ch * 9 / 16; if (cw > W) { cw = W; ch = Math.min(H, W * 16 / 9); }
    const x = clamp((L.cx ?? 0.5) * W - cw / 2, 0, W - cw), y = clamp(0.5 * H - ch / 2, 0, H - ch);
    o.innerHTML = box("crop", { x: x / W, y: y / H, w: cw / W, h: ch / H }, "9:16", "crop");
  } else o.innerHTML = "";
  $$(".region[data-key=face], .region[data-key=action]", o).forEach((el) => {
    el.onpointerdown = (e) => {
      e.preventDefault(); const key = el.dataset.key, grip = !!e.target.dataset.grip, r0 = { ...L[key] }, x0 = e.clientX, y0 = e.clientY;
      el.setPointerCapture(e.pointerId);
      el.onpointermove = (m) => { const dx = (m.clientX - x0) / R.w, dy = (m.clientY - y0) / R.h; const r = { ...r0 };
        if (grip) { r.w = clamp(r0.w + dx, 0.05, 1 - r0.x); r.h = clamp(r0.h + dy, 0.05, 1 - r0.y); } else { r.x = clamp(r0.x + dx, 0, 1 - r0.w); r.y = clamp(r0.y + dy, 0, 1 - r0.h); }
        L[key] = r; el.style.left = R.x + r.x * R.w + "px"; el.style.top = R.y + r.y * R.h + "px"; el.style.width = r.w * R.w + "px"; el.style.height = r.h * R.h + "px"; };
      el.onpointerup = () => { el.onpointermove = null; setLayout({ [key]: L[key] }); };
    };
  });
}

/* ---------- live 9:16 preview (mirrors render.py's filter graph) ---------- */
function coverDraw(g, v, sx, sy, sw, sh, dx, dy, dw, dh) {
  const s = Math.max(dw / sw, dh / sh), cw = dw / s, ch = dh / s;
  g.drawImage(v, sx + (sw - cw) / 2, sy + (sh - ch) / 2, cw, ch, dx, dy, dw, dh);
}
function drawPreview() {
  const ws = S.ws, cv = ws.canvas; if (!cv || cv.hidden || !ws.clip) return;
  const v = ws.video, g = cv.getContext("2d"), OW = cv.width, OH = cv.height, W = v.videoWidth, H = v.videoHeight;
  g.fillStyle = "#000"; g.fillRect(0, 0, OW, OH);
  if (!W) return;
  const L = ws.rev.layout || {};
  if (L.mode === "fit") {
    g.save(); g.filter = "blur(10px) brightness(0.85)"; coverDraw(g, v, 0, 0, W, H, -10, -10, OW + 20, OH + 20); g.restore();
    const s = Math.min(OW / W, OH / H), dw = W * s, dh = H * s; g.drawImage(v, (OW - dw) / 2, (OH - dh) / 2, dw, dh);
  } else if (L.mode === "gameplay") {
    const fh = Math.round(OH * clamp(L.split ?? 0.38, 0.15, 0.7)), f = L.face, a = L.action;
    coverDraw(g, v, f.x * W, f.y * H, f.w * W, f.h * H, 0, 0, OW, fh);
    coverDraw(g, v, a.x * W, a.y * H, a.w * W, a.h * H, 0, fh, OW, OH - fh);
  } else {
    const z = clamp(L.zoom || 1, 1, 3); let ch = H / z, cw = ch * 9 / 16; if (cw > W) { cw = W; ch = Math.min(H, W * 16 / 9); }
    const x = clamp((L.cx ?? 0.5) * W - cw / 2, 0, W - cw), y = clamp(0.5 * H - ch / 2, 0, H - ch);
    g.drawImage(v, x, y, cw, ch, 0, 0, OW, OH);
  }
  // captions: same font, size and wrapping rule as captions.py (PlayRes 1080x1920)
  const t = v.currentTime, cap = (ws.rev.captions || []).find((c) => t >= c.s && t < c.e && c.s < ws.rev.end && c.e > ws.rev.start);
  if (cap && t >= ws.rev.start && t <= ws.rev.end) {
    const k = OH / 1920, size = (L.caption_size || S.system.settings.caption_size) * k, maxChars = Math.max(10, Math.floor((1080 - 180) / (size / k * 0.52)));
    const lines = wrapText(cap.text, maxChars);
    g.font = `700 ${size}px "Fira Sans", sans-serif`; g.textAlign = "center"; g.textBaseline = "bottom"; g.lineJoin = "round";
    g.lineWidth = 10 * k; g.strokeStyle = "#000"; g.fillStyle = "#fff";
    const bottom = OH * clamp(L.caption_y ?? 0.8, 0.1, 0.97), lh = size * 1.18;
    lines.forEach((ln, i) => { const y = bottom - (lines.length - 1 - i) * lh; g.strokeText(ln, OW / 2, y); g.fillText(ln, OW / 2, y); });
  }
  if (t < ws.rev.start - 0.05 || t > ws.rev.end + 0.05) { g.fillStyle = "rgba(0,0,0,.55)"; g.fillRect(0, 0, OW, OH); g.fillStyle = "#ffcc33"; g.font = `600 ${14}px system-ui`; g.textAlign = "center"; g.fillText("playhead outside the clip", OW / 2, 24); }
}
function wrapText(text, n) {  // mirrors captions.wrap_lines
  const words = text.split(/\s+/).filter(Boolean), lines = []; let cur = "";
  for (const w of words) { if ((cur + " " + w).trim().length > n && cur) { lines.push(cur); cur = w; } else cur = (cur + " " + w).trim(); }
  if (cur) lines.push(cur);
  if (lines.length === 2) {
    let best = 1, bestLen = Infinity;
    for (let k = 1; k < words.length; k++) { const m = Math.max(words.slice(0, k).join(" ").length, words.slice(k).join(" ").length); if (m < bestLen) { bestLen = m; best = k; } }
    return [words.slice(0, best).join(" "), words.slice(best).join(" ")];
  }
  return lines;
}

function tick() {
  const ws = S.ws; if (!ws || !ws.alive) return;
  const v = ws.video, d = ws.src.duration || 1;
  if (ws.playSel && v.currentTime >= ws.sel.e) { v.pause(); ws.playSel = false; v.currentTime = ws.sel.e; }
  if (v.paused) ws.playSel = false;
  const pct = (t) => (100 * t / d) + "%";
  $("#headAll").style.left = pct(v.currentTime);
  const sa = $("#selAll"); sa.style.left = pct(ws.sel.s); sa.style.width = (100 * (ws.sel.e - ws.sel.s) / d) + "%";
  const [a, b] = ws.detFrozen || ws.detRange(), dp = (t) => (100 * (t - a) / (b - a)) + "%";
  $("#selDet").style.left = dp(ws.sel.s); $("#selDet").style.width = (100 * (ws.sel.e - ws.sel.s) / (b - a)) + "%";
  $("#hS").style.left = dp(ws.sel.s); $("#hE").style.left = dp(ws.sel.e); $("#headDet").style.left = dp(clamp(v.currentTime, a, b));
  $("#hS").setAttribute("aria-valuenow", ws.sel.s.toFixed(1)); $("#hE").setAttribute("aria-valuenow", ws.sel.e.toFixed(1));
  const off = ws.src.time_offset || 0;
  $("#detL").textContent = fmt(a + off); $("#detR").textContent = fmt(b + off); $("#detM").textContent = `playhead ${fmt(v.currentTime + off, true)}`;
  drawPreview();
  const caps = $$(".cap"); caps.forEach((row) => { const c = ws.rev?.captions[+row.dataset.i]; row.classList.toggle("now", !!c && v.currentTime >= c.s && v.currentTime < c.e); });
  ws.raf = requestAnimationFrame(tick);
}

/* ================================================================= QUEUE */
async function renderQueue() {
  S.view = "queue";
  const q = (S.q = S.q || { status: "pending", creator: "", idx: 0, checked: new Set() });
  const [clips, creators] = await Promise.all([api(`/api/clips?${q.status ? "status=" + q.status : ""}${q.creator ? "&creator=" + q.creator : ""}`), api("/api/creators")]);
  q.clips = clips; q.idx = clamp(q.idx, 0, Math.max(0, clips.length - 1));
  const seenZip = new Set();
  const exports = (await api("/api/jobs")).filter((j) => j.kind === "export" && j.status === "done" && !seenZip.has(j.result.zip) && seenZip.add(j.result.zip));
  $("#main").innerHTML = `
  <div class="row" style="margin-bottom:12px">
    <h1 class="grow">Review queue</h1>
    <select id="qStatus" aria-label="Status filter">${["pending", "approved", "rejected", ""].map((s) => `<option value="${s}" ${q.status === s ? "selected" : ""}>${s || "all"}</option>`).join("")}</select>
    <select id="qCreator" aria-label="Creator filter"><option value="">All creators</option>${creators.map((c) => `<option value="${c.id}" ${q.creator === c.id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
    <button class="primary" id="qExport">Export ${q.checked.size ? q.checked.size + " selected" : "all approved"} (ZIP)</button>
  </div>
  <div class="queue">
    <div class="panel" style="padding:0" role="listbox" aria-label="Clips" id="qList">
      ${clips.length ? clips.map((c, i) => `<div class="qrow ${i === q.idx ? "on" : ""}" data-i="${i}" role="option" aria-selected="${i === q.idx}">
        <input type="checkbox" data-chk="${c.id}" ${q.checked.has(c.id) ? "checked" : ""} aria-label="Select for export">
        <span class="t">${fmt(c.start + (c.time_offset || 0))}<br><span class="muted">${(c.end - c.start).toFixed(0)}s</span></span>
        <div style="min-width:0"><div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis"><b>${esc(c.title || "Untitled")}</b></div>
          <div class="muted small" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(c.creator_name || "—")} · ${esc(c.source_title)}${c.request_text ? ` · “${esc(c.request_text)}”` : " · manual"}</div></div>
        <div class="row">${c.strength ? `<span class="badge b-${c.strength}">${c.strength}</span>` : ""}<span class="badge b-${c.status}">${c.status}</span></div></div>`).join("")
        : `<div class="empty">Nothing ${esc(q.status || "")} here.</div>`}
    </div>
    <div class="stack" id="qDetail"></div>
  </div>
  ${exports.length ? `<div class="panel" style="margin-top:14px"><h3>Exports</h3>${exports.slice(0, 6).map((j) => `<div><a href="/api/exports/${j.result.zip}">${esc(j.result.zip)}</a> <span class="muted small">${j.result.clips} clips · ${new Date(j.updated * 1000).toLocaleString()}</span></div>`).join("")}</div>` : ""}`;
  $("#qStatus").onchange = (e) => { q.status = e.target.value; q.idx = 0; renderQueue(); };
  $("#qCreator").onchange = (e) => { q.creator = e.target.value; q.idx = 0; renderQueue(); };
  $("#qExport").onclick = queueExport;
  $$(".qrow").forEach((r) => (r.onclick = (e) => { if (e.target.dataset.chk) return; q.idx = +r.dataset.i; q.lastActed = null; qSelect(); }));
  $$("[data-chk]").forEach((c) => (c.onchange = () => { c.checked ? q.checked.add(c.dataset.chk) : q.checked.delete(c.dataset.chk); $("#qExport").textContent = `Export ${q.checked.size ? q.checked.size + " selected" : "all approved"} (ZIP)`; }));
  qDetail();
}
function qSelect() { const q = S.q; $$(".qrow").forEach((r, i) => { r.classList.toggle("on", i === q.idx); r.setAttribute("aria-selected", i === q.idx); if (i === q.idx) r.scrollIntoView({ block: "nearest" }); }); qDetail(); }
async function qDetail() {
  const q = S.q, el = $("#qDetail"); const c = q.clips[q.idx];
  if (!c) { el.innerHTML = ""; return; }
  const k = await api(`/api/clips/${c.id}`);
  q.cur = k;
  const prev = k.renders.find((r) => r.kind === "preview" && r.rev === k.current_rev), fin = k.renders.find((r) => r.kind === "final" && r.rev === k.approved_rev);
  const off = k.time_offset || 0;
  el.innerHTML = `<div class="panel stack">
    <div class="row"><span class="badge b-${k.status}">${k.status}</span><span class="mono small">${fmt(k.rev.start + off, true)} – ${fmt(k.rev.end + off, true)}</span><span class="grow"></span><a href="#/source/${k.source_id}/${k.id}">Open in editor →</a></div>
    <h2 style="margin:0">${esc(k.rev.title || "Untitled")}</h2>
    ${prev || fin ? `<video class="vprev" style="max-width:260px;margin:0 auto;display:block" controls src="${prev ? `/api/clips/${k.id}/preview.mp4?r=${prev.rev}` : `/api/clips/${k.id}/download.mp4`}"></video>`
      : `<video id="qv" style="width:100%;border-radius:8px;background:#000" controls preload="metadata" src="/api/sources/${k.source_id}/media#t=${k.rev.start},${k.rev.end}"></video>
         <div class="muted small">Source playback of the selection — press V to render the vertical preview.</div>`}
    ${k.candidate ? `<p class="small">${esc(k.candidate.reason)}</p>` : `<p class="muted small">Manual clip.</p>`}
    <div class="muted small">${esc((k.rev.hashtags || []).join(" "))}</div>
  </div>
  <div class="review" role="toolbar" aria-label="Review">
    <button class="ok" id="qA" title="A">✓ Approve</button><button class="bad" id="qR" title="R">✕ Reject</button>
    <button id="qU" ${k.can_undo ? "" : "disabled"} title="U">↶ Undo</button><button id="qV" title="V">Render preview</button>
    ${fin ? `<a href="/api/clips/${k.id}/download.mp4">Download MP4</a>` : ""}
    ${k.job && ["queued", "running"].includes(k.job.status) ? `<div class="progress grow"><i style="width:${(k.job.progress * 100).toFixed(0)}%"></i></div>` : ""}
  </div>`;
  $("#qA").onclick = () => qStatus("approved"); $("#qR").onclick = () => qStatus("rejected"); $("#qU").onclick = qUndo; $("#qV").onclick = qRender;
}
async function qStatus(st) {
  const q = S.q; if (!q.cur) return;
  try { await api(`/api/clips/${q.cur.id}/status`, { json: { status: st } }); toast(st === "approved" ? "Approved ✓ — U to undo" : "Rejected — U to undo");
    q.lastActed = q.cur.id;                    // undo targets the clip just reviewed, not the next one
    if (!(q.status && q.status !== st)) q.idx++;
    await renderQueue(); } catch (e) { fail(e); }
}
async function qUndo() {
  const q = S.q; const id = q.lastActed || q.cur?.id; if (!id) return;
  try {
    const k = await api(`/api/clips/${id}/undo`, { json: {} }); q.lastActed = null; toast("Undone");
    await renderQueue();
    const i = q.clips.findIndex((c) => c.id === k.id); if (i >= 0) { q.idx = i; qSelect(); }
  } catch (e) { fail(e); }
}
async function qRender() { const q = S.q; if (!q.cur) return; try { const j = await api(`/api/clips/${q.cur.id}/render`, { json: { kind: "preview" } }); toast("Rendering preview…"); watchJob(j.id, () => S.view === "queue" && qDetail()); } catch (e) { fail(e); } }
async function queueExport() {
  const q = S.q;
  try { const j = await api("/api/export", { json: q.checked.size ? { clip_ids: [...q.checked] } : {} }); toast("Export started — rendering approved clips…");
    S.myExports.add(j.id); watchJob(j.id, (jj) => { download(`/api/exports/${jj.result.zip}`); if (S.view === "queue") renderQueue(); }); }
  catch (e) { fail(e); }
}

/* ================================================================ SETTINGS */
async function renderSettings() {
  S.view = "settings"; await loadSystem(); const s = S.system, st = s.settings;
  const yes = (v) => `<span class="badge b-${v ? "ready" : "failed"}">${v ? "found" : "missing"}</span>`;
  $("#main").innerHTML = `<div class="panel stack" style="max-width:820px">
    <h1>Setup</h1>
    <table class="keys"><tr><td>FFmpeg / ffprobe</td><td>${yes(s.ffmpeg && s.ffprobe)} <span class="muted small mono">${esc(s.ffmpeg || "")}</span></td></tr>
      <tr><td>yt-dlp (URL import)</td><td>${yes(s.yt_dlp)}</td></tr>
      <tr><td>faster-whisper (transcription)</td><td>${yes(s.faster_whisper)} ${s.faster_whisper ? `model “${esc(s.whisper_model)}” ${s.whisper_model_cached ? "cached" : "downloads on first use"}` : ""}</td></tr>
      <tr><td>Face auto-center</td><td>${yes(s.face_model)} <span class="muted small">manual position slider always works</span></td></tr>
      <tr><td>Ranker</td><td>${esc(s.ranker)}</td></tr>
      <tr><td>Data folder</td><td class="mono small">${esc(s.data_dir)}</td></tr>
      <tr><td>Host</td><td class="mono small">${esc(s.host)} · ${esc(s.os)}</td></tr></table>
    <form id="setForm" class="stack">
      <div class="row">
        <div><label for="wm">Whisper model</label><select id="wm">${["tiny", "base", "small", "medium"].map((m) => `<option ${st.whisper_model === m ? "selected" : ""}>${m}</option>`).join("")}</select></div>
        <div><label for="mp">Model scoring</label><select id="mp">${[["auto", "Auto (use an API key if set)"], ["local", "Local only"], ["anthropic", "Anthropic"], ["openai", "OpenAI"]].map(([v, l]) => `<option value="${v}" ${st.model_provider === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
        <div><label for="dl">Default framing</label><select id="dl">${["speaker", "fit", "gameplay"].map((m) => `<option ${st.default_layout === m ? "selected" : ""}>${m}</option>`).join("")}</select></div>
        <div><label for="cs">Caption size (px @1080)</label><input id="cs" type="number" min="40" max="110" value="${st.caption_size}" style="width:90px"></div>
      </div>
      <p class="muted small">API keys are read only from the ANTHROPIC_API_KEY / OPENAI_API_KEY environment variables when the server starts; they're never stored or logged.</p>
      <button class="primary">Save</button></form></div>`;
  $("#setForm").onsubmit = async (e) => { e.preventDefault(); try { await api("/api/settings", { method: "PUT", json: { whisper_model: $("#wm").value, model_provider: $("#mp").value, default_layout: $("#dl").value, caption_size: +$("#cs").value } }); toast("Saved"); renderSettings(); } catch (er) { fail(er); } };
}

/* ================================================================== JOBS */
const watchers = new Map();
function watchJob(id, cb) { watchers.set(id, cb); }
const JOB_LABEL = { import_url: "Import (URL)", import_file: "Import (file)", transcribe: "Transcribe", discover: "Find moments", render: "Render", export: "Export ZIP" };
async function pollJobs() {
  let jobs = [];
  try { jobs = await api("/api/jobs"); } catch { setTimeout(pollJobs, 3000); $("#jobsBar").innerHTML = `<div class="err">Server not reachable — is ClipDesk still running?</div>`; return; }
  const active = jobs.filter((j) => ["queued", "running", "cancelling"].includes(j.status));
  const recentFail = jobs.filter((j) => j.status === "failed" && Date.now() / 1000 - j.updated < 900 && !S.dismissed.has(j.id + j.attempt));
  let changed = false;
  const pollStart = Date.now() / 1000;
  for (const j of jobs) {
    const prev = S.seenJobs.get(j.id);
    const finished = ["done", "failed", "cancelled"].includes(j.status);
    // a job can be queued and finish between two polls: treat "first seen, already finished, updated since
    // our last poll" as a transition too
    const fresh = prev === undefined && finished && S.lastPoll && j.updated >= S.lastPoll - 1;
    if ((prev && prev !== j.status && finished) || fresh) {
      changed = true;
      if (j.status === "failed") toast(`${JOB_LABEL[j.kind]} failed: ${esc(j.error.slice(0, 160))}`, true, 7000);
      else if (j.status === "done" && ["discover", "import_file", "import_url", "transcribe"].includes(j.kind)) toast(`${JOB_LABEL[j.kind]}: ${esc(j.message)}`);
      const cb = watchers.get(j.id); if (cb && j.status === "done") { watchers.delete(j.id); cb(j); }
    }
    S.seenJobs.set(j.id, j.status);
  }
  $("#jobsBar").innerHTML = [...active, ...recentFail].map((j) => `<div class="job"><span><b>${JOB_LABEL[j.kind]}</b> <span class="badge b-${j.status}">${j.status}</span></span>
      <span class="msg" title="${esc(j.error || j.message)}">${esc(j.status === "failed" ? j.error : j.message)}</span>
      <div class="progress"><i style="width:${(j.progress * 100).toFixed(0)}%"></i></div>
      <span class="row">${j.status === "failed" ? `<button class="small" data-jretry="${j.id}">Retry</button><button class="small ghost" data-jdismiss="${j.id + j.attempt}" aria-label="Dismiss">✕</button>` : `<button class="small" data-jcancel="${j.id}">Cancel</button>`}</span></div>`).join("");
  S.lastPoll = pollStart;
  if (changed) refreshView();
  else if (active.length) liveProgress(active);
  updatePending();
  setTimeout(pollJobs, active.length ? 1000 : 3000);
}
function liveProgress(active) {
  if (S.view === "home") { if (active.some((j) => j.kind.startsWith("import"))) refreshView(); }
  if (S.view === "ws" && S.ws) {
    const disc = active.find((j) => j.kind === "discover" && j.source_id === S.ws.sid);
    if (disc) api(`/api/sources/${S.ws.sid}`).then((s) => S.ws && renderResults(s.requests));
    if (!S.ws.src.has_media || S.ws.src.status !== "ready") refreshView();
  }
}
let refreshing = false;
async function refreshView() {
  if (refreshing) return; refreshing = true;
  try {
    if (S.view === "home") await renderHome();
    else if (S.view === "ws" && S.ws) {
      const s = await api(`/api/sources/${S.ws.sid}`);
      if (s.status !== S.ws.src.status || s.transcript_status !== S.ws.src.transcript_status || s.chat_status !== S.ws.src.chat_status || !S.ws.video) await route();
      else { renderResults(s.requests); await loadClips(S.ws.clip?.id); }
    } else if (S.view === "queue") await renderQueue();
  } finally { refreshing = false; }
}
async function updatePending() { try { const p = await api("/api/clips?status=pending"); $("#pendingCount").textContent = p.length || ""; } catch {} }
document.addEventListener("click", async (e) => {
  const c = e.target.closest("[data-jcancel]"), r = e.target.closest("[data-jretry]"), d = e.target.closest("[data-jdismiss]");
  try {
    if (c) { await api(`/api/jobs/${c.dataset.jcancel}/cancel`, { json: {} }); toast("Cancelling…"); }
    if (r) { await api(`/api/jobs/${r.dataset.jretry}/retry`, { json: {} }); toast("Retrying"); }
    if (d) { S.dismissed.add(d.dataset.jdismiss); d.closest(".job").remove(); }
  } catch (er) { fail(er); }
});

/* ============================================================== KEYBOARD */
function typing(el) { return el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) && !(el.type === "checkbox" || el.type === "range")); }
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && typing(document.activeElement)) { document.activeElement.blur(); return; }
  if (typing(e.target) || e.altKey) return;
  const mod = e.ctrlKey || e.metaKey, k = e.key;
  if (mod && k.toLowerCase() === "z") { e.preventDefault(); S.view === "ws" ? undo() : S.view === "queue" && qUndo(); return; }
  if (mod) return;
  if (k === "?") { $("#helpDlg").showModal(); return; }
  if (S.view === "queue" && S.q) {
    const q = S.q;
    if (k === "j" || k === "ArrowDown") { e.preventDefault(); q.idx = clamp(q.idx + 1, 0, q.clips.length - 1); q.lastActed = null; qSelect(); }
    else if (k === "k" || k === "ArrowUp") { e.preventDefault(); q.idx = clamp(q.idx - 1, 0, q.clips.length - 1); q.lastActed = null; qSelect(); }
    else if (k === "a") qStatus("approved"); else if (k === "r") qStatus("rejected"); else if (k === "u") qUndo();
    else if (k === "v") qRender(); else if (k === "e") queueExport();
    else if (k === "x" && q.cur) { const cb = $(`[data-chk="${q.cur.id}"]`); if (cb) { cb.checked = !cb.checked; cb.onchange(); } }
    return;
  }
  const ws = S.ws; if (S.view !== "ws" || !ws?.video) return;
  const v = ws.video;
  const act = {
    " ": () => (v.paused ? v.play() : v.pause()), p: playSelection,
    i: () => setSel(v.currentTime, Math.max(ws.sel.e, v.currentTime + 1)), o: () => setSel(ws.sel.s, v.currentTime),
    ArrowLeft: () => (v.currentTime -= e.shiftKey ? 5 : 1), ArrowRight: () => (v.currentTime += e.shiftKey ? 5 : 1),
    "[": () => setSel(ws.sel.s - 0.2, ws.sel.e), "]": () => setSel(ws.sel.s + 0.2, ws.sel.e),
    "{": () => setSel(ws.sel.s, ws.sel.e - 0.2), "}": () => setSel(ws.sel.s, ws.sel.e + 0.2),
    g: () => $("#goTC").focus(), "/": () => $("#findText").focus(),
    a: () => setStatus("approved"), r: () => setStatus("rejected"), u: undo, v: renderPreview, e: exportClip,
    j: () => stepClip(1), k: () => stepClip(-1),
  }[k.length === 1 && /[a-z]/i.test(k) ? k.toLowerCase() : k];
  if (act) { e.preventDefault(); act(); }
});
function stepClip(d) { const ws = S.ws; if (!ws.clips.length) return; const i = ws.clips.findIndex((c) => c.id === ws.clip?.id); selectClip(ws.clips[clamp(i + d, 0, ws.clips.length - 1)].id, true); }
$("#helpBtn").onclick = () => $("#helpDlg").showModal();

/* ================================================================== BOOT */
(async function boot() {
  try { await loadSystem(); } catch (e) { $("#main").innerHTML = `<div class="panel err">Can't reach the ClipDesk server: ${esc(e.message)}</div>`; return; }
  await route(); pollJobs();
})();
