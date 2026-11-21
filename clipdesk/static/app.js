"use strict";
/* ClipDesk UI v0.2 — vanilla JS, no build step. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const icon = (n, cls = "") => `<svg class="ic ${cls}" aria-hidden="true"><use href="#i-${n}"/></svg>`;
const isPhone = () => matchMedia("(max-width: 899px)").matches;
const store = { get: (k, d) => { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } } };

async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers = { "content-type": "application/json" }; o.method = o.method || "POST"; delete o.json; }
  const r = await fetch(path, o);
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  if (!r.ok) throw new Error((data && data.error) || `The server answered ${r.status}`);
  return data;
}
function toast(msg, { err = false, action = null, ms = 4200 } = {}) {
  const t = $("#toast");
  t.innerHTML = `${icon(err ? "alert" : "check")}<span>${msg}</span>${action ? `<button type="button">${esc(action.label)}</button>` : ""}`;
  t.className = "toast show" + (err ? " err" : "");
  if (action) $("button", t).onclick = () => { t.className = "toast"; action.fn(); };
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.className = "toast"), action ? Math.max(ms, 6000) : ms);
}
const fail = (e) => toast(esc(e.message || e), { err: true, ms: 7000 });
async function busy(btn, fn) {  // loading state on the pressed button
  if (!btn || btn.disabled) return fn();
  const html = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="spin" aria-hidden="true"></span>${btn.dataset.busy || "Working…"}`;
  try { return await fn(); } finally { if (btn.isConnected) { btn.disabled = false; btn.innerHTML = html; } }
}

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
  const m = str.match(/^(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?$/);
  if (m && (m[1] || m[2] || m[3])) return (+m[1] || 0) * 3600 + (+m[2] || 0) * 60 + (+m[3] || 0);
  if (/^\d+(\.\d+)?$/.test(str)) return +str;
  const parts = str.split(":");
  if (parts.length < 2 || parts.length > 3 || parts.some((p) => !/^\d+(\.\d+)?$/.test(p))) return NaN;
  return parts.reduce((acc, p) => acc * 60 + +p, 0);
}

const S = { system: null, creators: [], view: "", ws: null, q: null, seenJobs: new Map(), dismissed: new Set(), trayOpen: false };
const STATUS = { queued: "Queued", importing: "Importing", ready: "Ready", live_waiting: "Live, waiting for replay", inaccessible: "Can't access", failed: "Import failed" };
const CLIP = { pending: "To review", approved: "Approved", rejected: "Rejected" };
const pill = (cls, text, ic) => `<span class="pill ${cls}">${ic ? icon(ic) : ""}${esc(text)}</span>`;
const clipPill = (st) => pill(st, CLIP[st] || st, st === "approved" ? "check" : st === "rejected" ? "x" : null);

/* ---------- router ---------- */
window.addEventListener("hashchange", route);
async function route() {
  const h = location.hash.replace(/^#\/?/, "").split("/");
  stopWorkspace();
  const nav = h[0] === "queue" ? "queue" : h[0] === "settings" ? "settings" : "home";
  $$(".mainnav a").forEach((a) => { if (a.dataset.nav === nav) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  document.body.classList.toggle("in-ws", h[0] === "source");
  try {
    if (h[0] === "source" && h[1]) await renderWorkspace(h[1], h[2]);
    else if (h[0] === "queue") await renderQueue();
    else if (h[0] === "settings") await renderSettings();
    else await renderHome();
  } catch (e) { $("#main").innerHTML = `<div class="panel empty">${icon("alert")}<h2>Something went wrong</h2><p>${esc(e.message)}</p><p style="margin-top:12px"><a href="#/">Back to sources</a></p></div>`; }
}

async function loadSystem() {
  S.system = await api("/api/system");
  const b = $("#banner"), s = S.system, off = [];
  if (!s.ffmpeg || !s.ffprobe) off.push("video processing (FFmpeg missing)");
  if (!s.yt_dlp) off.push("link import");
  if (!s.faster_whisper) off.push("automatic transcripts");
  const msg = off.length ? `Not available on this computer: ${off.join(", ")}.` : (!s.whisper_model_cached ? `The first transcript downloads the Whisper “${esc(s.whisper_model)}” model once.` : "");
  b.hidden = !msg;
  b.innerHTML = msg ? `${icon("alert")}<span>${msg} <a href="#/settings">What this means</a></span>` : "";
}

/* ================================================================= SOURCES */
async function renderHome() {
  S.view = "home";
  const [creators, sources] = await Promise.all([api("/api/creators"), api("/api/sources" + (S.creatorFilter ? `?creator=${S.creatorFilter}` : ""))]);
  S.creators = creators;
  const copts = `<option value="">No creator</option>` + creators.map((c) => `<option value="${c.id}" ${c.id === S.lastCreator ? "selected" : ""}>${esc(c.name)}</option>`).join("");
  const kinds = `<option value="auto">Auto-detect</option><option value="gaming">Gaming stream</option><option value="speech">Talk or interview</option>`;
  $("#main").innerHTML = `
  <div class="home">
    <aside class="creators panel" aria-labelledby="crTitle">
      <div class="panel-head"><h2 id="crTitle">Creators</h2></div>
      <button class="cr" aria-pressed="${!S.creatorFilter}" data-cr="">All sources</button>
      ${creators.map((c) => `<button class="cr" aria-pressed="${S.creatorFilter === c.id}" data-cr="${c.id}"><span>${esc(c.name)}</span><span class="n">${c.sources}</span></button>`).join("")}
      <form id="crForm" style="margin-top:16px">
        <label for="crName">Add a creator</label>
        <input id="crName" placeholder="Name" required maxlength="80" autocomplete="off">
        <div class="row" style="margin-top:8px">
          <select id="crKind" class="grow" aria-label="Creator type"><option value="streamer">Streamer</option><option value="speaker">Speaker</option><option value="motivator">Motivator</option><option value="guest">Interview guest</option><option value="other">Other</option></select>
          <button class="btn">${icon("plus")}Add</button>
        </div>
      </form>
    </aside>
    <section aria-labelledby="addTitle">
      <div class="panel">
        <div class="panel-head"><h1 id="addTitle">Add a recording</h1></div>
        <div class="intake">
          <form id="upForm">
            <label class="drop" id="drop" for="file">
              ${icon("upload")}
              <b>Drop a video here or choose a file</b>
              <span class="small" id="fileName">MP4, MOV, MKV or WebM from your device</span>
              <input type="file" id="file" class="sr" accept="video/*,.mkv,.ts,.flv">
            </label>
            <div class="row" style="margin-top:12px">
              <div class="grow"><label for="upCreator">Creator</label><select id="upCreator">${copts}</select></div>
              <div class="grow"><label for="upKind">Content</label><select id="upKind">${kinds}</select></div>
            </div>
            <div class="progress" hidden id="upProg" style="margin-top:12px"><i style="width:0"></i></div>
            <div id="upErr" class="err" role="alert"></div>
            <button class="btn primary" id="upBtn" data-busy="Uploading…" style="margin-top:12px;width:100%">${icon("upload")}Upload and import</button>
          </form>
          <form id="urlForm">
            <div class="field"><label for="url">Or paste a YouTube or Twitch replay link</label>
              <input id="url" inputmode="url" autocomplete="off" placeholder="youtube.com/watch?v=…  or  twitch.tv/videos/…">
              <div id="urlErr" class="err" role="alert"></div></div>
            <div class="row">
              <div class="grow"><label for="urlCreator">Creator</label><select id="urlCreator">${copts}</select></div>
              <div class="grow"><label for="urlKind">Content</label><select id="urlKind">${kinds}</select></div>
            </div>
            <details style="margin-top:12px"><summary class="small muted" style="cursor:pointer;min-height:32px">Only import part of a long stream</summary>
              <div class="row" style="margin-top:8px"><div class="grow"><label for="secS">From</label><input id="secS" placeholder="1:05:00"></div>
              <div class="grow"><label for="secE">To</label><input id="secE" placeholder="1:25:00"></div></div></details>
            <button class="btn" id="urlBtn" data-busy="Checking link…" style="margin-top:12px;width:100%">${icon("link")}Import from link</button>
          </form>
        </div>
      </div>
      <div class="panel-head" style="margin-top:24px"><h2>Your recordings</h2><span class="muted small">${sources.length} total</span></div>
      ${sources.length ? `<div class="shelf">${sources.map(reel).join("")}</div>`
        : `<div class="panel empty">${icon("film")}<h2>No recordings yet</h2><p>Upload a video or paste a replay link above to start clipping.</p></div>`}
    </section>
  </div>`;
  $$(".cr").forEach((b) => (b.onclick = () => { S.creatorFilter = b.dataset.cr || null; renderHome(); }));
  $("#crForm").onsubmit = async (e) => { e.preventDefault(); try { const c = await api("/api/creators", { json: { name: $("#crName").value, kind: $("#crKind").value } }); S.lastCreator = c.id; toast(`Added ${esc(c.name)}`); renderHome(); } catch (er) { fail(er); } };
  ["#upCreator", "#urlCreator"].forEach((id) => ($(id).onchange = (e) => (S.lastCreator = e.target.value)));
  $("#urlForm").onsubmit = async (e) => {
    e.preventDefault(); $("#urlErr").textContent = "";
    const body = { url: $("#url").value, creator_id: $("#urlCreator").value, content_kind: $("#urlKind").value };
    if ($("#secS").value || $("#secE").value) {
      body.section_start = parseTC($("#secS").value); body.section_end = parseTC($("#secE").value);
      if (isNaN(body.section_start) || isNaN(body.section_end)) { $("#urlErr").innerHTML = `${icon("alert")}Write the times like 1:05:00.`; return; }
    }
    await busy($("#urlBtn"), async () => {
      try { await api("/api/sources/url", { json: body }); toast("Link added. Importing in the background."); renderHome(); }
      catch (er) { $("#urlErr").innerHTML = `${icon("alert")}${esc(er.message)}`; $("#url").focus(); }
    });
  };
  const drop = $("#drop"), file = $("#file");
  const showFile = () => { const f = file.files[0]; $("#fileName").innerHTML = f ? `<span class="chosen">${esc(f.name)}</span> ${(f.size / 1e6).toFixed(1)} MB` : "MP4, MOV, MKV or WebM from your device"; };
  file.onchange = showFile;
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) { file.files = e.dataTransfer.files; showFile(); } });
  $("#upForm").onsubmit = (e) => {
    e.preventDefault(); $("#upErr").textContent = "";
    const f = file.files[0]; if (!f) { $("#upErr").innerHTML = `${icon("alert")}Choose a video file first.`; return; }
    const fd = new FormData(); fd.append("file", f); fd.append("creator_id", $("#upCreator").value); fd.append("content_kind", $("#upKind").value);
    const xhr = new XMLHttpRequest(), bar = $("#upProg"), btn = $("#upBtn"); bar.hidden = false; btn.disabled = true;
    btn.innerHTML = `<span class="spin" aria-hidden="true"></span>Uploading…`;
    xhr.upload.onprogress = (ev) => { if (ev.lengthComputable) { const p = (100 * ev.loaded / ev.total).toFixed(0); $("i", bar).style.width = p + "%"; btn.lastChild.textContent = `Uploading ${p}%`; } };
    xhr.onload = () => { let d = {}; try { d = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 300) { $("#upErr").innerHTML = `${icon("alert")}${esc(d.error || "Upload failed.")}`; bar.hidden = true; btn.disabled = false; btn.innerHTML = `${icon("upload")}Upload and import`; return; }
      toast("Uploaded. Importing now."); location.hash = `#/source/${d.id}`; };
    xhr.onerror = () => { btn.disabled = false; btn.innerHTML = `${icon("upload")}Upload and import`; $("#upErr").innerHTML = `${icon("alert")}The upload lost its connection. Try again.`; };
    xhr.open("POST", "/api/sources/upload"); xhr.send(fd);
  };
}
function reel(s) {
  const j = s.job && ["queued", "running"].includes(s.job.status) ? s.job : null;
  const bad = ["failed", "inaccessible", "live_waiting"].includes(s.status);
  return `<article class="reel">
    <a href="#/source/${s.id}" class="thumb" style="${s.has_poster ? `background-image:url(/api/sources/${s.id}/poster)` : ""}" aria-label="Open ${esc(s.title || s.url)}" tabindex="-1">
      ${s.has_poster ? "" : icon("film")}${pill(s.status, STATUS[s.status] || s.status)}${s.duration ? `<span class="dur">${fmt(s.duration)}</span>` : ""}</a>
    <div class="body">
      <a class="title" href="#/source/${s.id}">${esc(s.title || s.url)}</a>
      <div class="meta"><span>${icon("user")}${esc(s.creator_name || "No creator")}</span><span>${icon("scissors")}${s.clips.n || 0} clips</span>${s.clips.approved ? `<span>${icon("check")}${s.clips.approved} approved</span>` : ""}</div>
      ${j ? `<div class="progress" role="progressbar" aria-valuenow="${Math.round(j.progress * 100)}" aria-valuemin="0" aria-valuemax="100"><i style="width:${(j.progress * 100).toFixed(0)}%"></i></div><div class="small muted">${esc(j.message)}</div>` : ""}
      ${bad ? `<p class="detail ${s.status === "live_waiting" ? "warn" : "err"}">${esc(s.status_detail)}</p><button class="btn sm" data-retry="${s.id}" style="align-self:flex-start">${icon("refresh")}Try import again</button>` : ""}
    </div></article>`;
}
document.addEventListener("click", async (e) => {
  const r = e.target.closest("[data-retry]");
  if (r) { try { await api(`/api/sources/${r.dataset.retry}/retry`, { json: {} }); toast("Trying the import again."); route(); } catch (er) { fail(er); } }
});

/* ============================================================== WORKSPACE */
function stopWorkspace() {
  if (!S.ws) return;
  cancelAnimationFrame(S.ws.raf); S.ws.alive = false; clearTimeout(S.ws.stripT); clearTimeout(S.ws.saveT);
  if (S.ws.onResize) window.removeEventListener("resize", S.ws.onResize);
  if (S.ws.pending) flushSave();  // don't lose an edit made just before leaving
  S.ws = null;
}
function steps(src, clips) {
  const has = (st) => clips.some((c) => c.status === st);
  const state = [src.status === "ready", src.transcript_status === "ready" || src.chat_status === "ready", clips.length > 0, has("approved"), clips.some((c) => c.status === "approved" && c.exported)];
  const names = ["Import", src.content_kind === "gaming" || src.chat_status === "ready" ? "Add chat log" : "Transcript", "Make clips", "Approve", "Export"];
  const now = state.findIndex((x) => !x);
  return `<ol class="steps" aria-label="Progress">${names.map((n, i) => `<li class="${state[i] ? "done" : i === now ? "now" : ""}" ${i === now ? 'aria-current="step"' : ""}>${n}</li>`).join("")}</ol>`;
}

async function renderWorkspace(sid, clipId) {
  S.view = "ws";
  const src = await api(`/api/sources/${sid}`);
  if (!S.creators.length) S.creators = await api("/api/creators");
  const ws = (S.ws = { sid, src, alive: true, clips: [], clip: null, rev: null, sel: { s: 0, e: 0 }, playSel: false, signals: null, saveT: null,
    snap: store.get("cd.snap", true), mtab: store.get("cd.mtab", "find") });
  const ready = src.status === "ready" && src.has_media;
  const off = src.time_offset || 0;
  $("#main").innerHTML = `
  <div class="ws-head">
    <div class="grow">
      <div class="crumbs"><a href="#/">${icon("back")}Sources</a>${pill(src.status, STATUS[src.status])}<span>${src.duration ? fmt(src.duration) : ""}</span>
        <span>${icon("mic", "faint")} ${src.transcript_status === "ready" ? "Transcript ready" : src.transcript_status === "running" ? "Transcribing…" : "No transcript"}</span>
        <span class="hide-phone">${icon("chat", "faint")} ${src.chat_status === "ready" ? "Chat log loaded" : "No chat log"}</span>${off ? `<span>Imported from ${fmt(off)}</span>` : ""}</div>
      <h1>${esc(src.title || src.url)}</h1>
      <div id="stepsBox"></div>
    </div>
    <div class="tools">
      <select id="srcCreator" aria-label="Creator">${`<option value="">No creator</option>` + S.creators.map((c) => `<option value="${c.id}" ${c.id === src.creator_id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
      <input type="file" id="trFile" accept=".srt,.vtt,.json,.json3" class="sr" tabindex="-1"><button class="btn sm" id="trBtn">${icon("type")}Add transcript</button>
      <input type="file" id="chatFile" accept=".json,.csv,.jsonl,.txt" class="sr" tabindex="-1"><button class="btn sm" id="chatBtn">${icon("chat")}Add chat log</button>
      ${S.system.faster_whisper && ready && src.transcript_status !== "running" ? `<button class="btn sm" id="whBtn" data-busy="Queuing…">${icon("mic")}${src.transcript_status === "ready" ? "Transcribe again" : "Transcribe"}</button>` : ""}
    </div>
  </div>
  ${!ready ? `<div class="panel empty">${["failed", "inaccessible", "live_waiting"].includes(src.status)
      ? `${icon("alert")}<h2>${STATUS[src.status]}</h2><p>${esc(src.status_detail)}</p><div class="row" style="justify-content:center;margin-top:16px"><button class="btn" data-retry="${src.id}">${icon("refresh")}Try again</button><a class="btn quiet" href="#/">Upload the file instead</a></div>`
      : `${icon("film")}<h2>Importing</h2><p>${src.job ? esc(src.job.message) : "Waiting to start…"}</p><div class="progress" style="max-width:360px;margin:16px auto 0"><i style="width:${((src.job?.progress || 0) * 100).toFixed(0)}%"></i></div>`}</div>` : `
  <div class="mtabs"><div class="seg" role="tablist" aria-label="Workspace">
    <button role="tab" data-mt="find" aria-selected="${ws.mtab === "find"}">${icon("spark")}Find moments</button>
    <button role="tab" data-mt="edit" aria-selected="${ws.mtab === "edit"}">${icon("crop")}Edit clip</button></div></div>
  <div class="ws" data-mtab="${ws.mtab}">
    <section aria-label="Source">
      <div class="monitor" id="player"><video id="vid" src="/api/sources/${sid}/media" controls preload="auto" playsinline></video><div class="overlay" id="ovl"></div></div>
      <div class="panel trim">
        <div class="slate">
          <div class="tc s"><small>Start</small><span id="slS">0:00.0</span></div>
          <div class="len" id="slLen"><b>0.0s</b>of ${S.system.limits.max_clip_s}s max</div>
          <div class="tc e"><small>End</small><span id="slE">0:00.0</span></div>
        </div>
        <div class="overview" id="tlAll" aria-label="Whole recording. Tap to jump."><canvas id="heat"></canvas><div class="win" id="winAll"></div><div id="marks"></div><div class="head" id="headAll"></div></div>
        <div class="filmstrip" id="strip">
          <div class="dim" id="dimL"></div><div class="dim" id="dimR"></div><div class="sel" id="selDet"></div><div class="head" id="headDet"></div>
          <div class="handle s" id="hS" tabindex="0" role="slider" aria-label="Clip start" aria-valuemin="0"></div>
          <div class="handle e" id="hE" tabindex="0" role="slider" aria-label="Clip end" aria-valuemin="0"></div>
        </div>
        <div class="tl-scale"><span id="detL"></span><span id="detM"></span><span id="detR"></span></div>
        <div class="trimctl">
          <div class="go"><label for="goTC">Go to time</label><div class="tcin"><input id="goTC" placeholder="12:05" inputmode="decimal"><button class="btn" id="goBtn" aria-label="Go to time">${icon("chevron")}</button></div></div>
          <div><label for="selS">Start</label><div class="tcin"><button class="btn" data-nudge="s,-0.5" aria-label="Start earlier">−</button><input id="selS" inputmode="decimal"><button class="btn" data-nudge="s,0.5" aria-label="Start later">+</button></div></div>
          <div><label for="selE">End</label><div class="tcin"><button class="btn" data-nudge="e,-0.5" aria-label="End earlier">−</button><input id="selE" inputmode="decimal"><button class="btn" data-nudge="e,0.5" aria-label="End later">+</button></div></div>
        </div>
        <div class="trimbar">
          <button class="btn sm" id="setS" title="Shortcut I">Start here</button><button class="btn sm" id="setE" title="Shortcut O">End here</button>
          <button class="btn sm" id="playSel" title="Shortcut P">${icon("replay")}Replay</button>
          <label class="toggle"><input type="checkbox" id="snapT" ${ws.snap ? "checked" : ""}>${icon("magnet")}Snap to pauses</label>
          <span class="grow"></span>
          <button class="btn sm" id="mkClip">${icon("scissors")}New clip</button>
        </div>
        <p class="err" id="selErr" role="alert"></p><p class="note" id="snapNote" aria-live="polite"></p>
      </div>
      <div class="panel find pane-find">
        <div class="panel-head"><h2>Find a moment</h2><span class="small faint">${esc(S.system.ranker.split(" (")[0])}</span></div>
        <form id="findForm">
          <label for="findText">Describe what you want</label>
          <textarea id="findText" placeholder="${src.content_kind === "speech" || (src.transcript_status === "ready" && src.chat_status !== "ready") ? "Find the story about the first failed business and keep the lesson." : "Find a surprising play and keep the reaction."}"></textarea>
          <div class="row" style="margin-top:8px">
            <select id="findMode" aria-label="How to search" class="grow"><option value="auto">Search automatically</option><option value="speech">Search what's said (transcript)</option><option value="gaming">Find reactions (loudness and chat)</option></select>
            <button class="btn primary" id="findBtn" data-busy="Searching…">${icon("search")}Find</button>
          </div>
        </form>
        <div id="results"></div>
      </div>
    </section>
    <section class="pane-edit" aria-label="Clip editor">
      <div class="panel">
        <div class="panel-head"><h2>Clips</h2><span class="small faint">J and K to move</span></div>
        <div class="cliplist" id="clipList"></div>
      </div>
      <div id="editor" style="margin-top:16px"></div>
    </section>
  </div>`}`;
  $("#srcCreator").onchange = async (e) => { try { await api(`/api/sources/${sid}`, { method: "PATCH", json: { creator_id: e.target.value } }); toast("Creator updated"); } catch (er) { fail(er); } };
  $("#trBtn").onclick = () => $("#trFile").click();
  $("#trFile").onchange = (e) => attach(sid, "transcript", e.target.files[0]);
  $("#chatBtn").onclick = () => $("#chatFile").click();
  $("#chatFile").onchange = (e) => attach(sid, "chat", e.target.files[0]);
  if ($("#whBtn")) $("#whBtn").onclick = (e) => busy(e.currentTarget, async () => { try { await api(`/api/sources/${sid}/transcribe`, { json: {} }); toast("Transcription started"); } catch (er) { fail(er); } });
  $("#stepsBox").innerHTML = steps(src, []);
  if (!ready) return;
  $$("[data-mt]").forEach((b) => (b.onclick = () => setMtab(b.dataset.mt)));
  ws.video = $("#vid");
  ws.sel = { s: 0, e: Math.min(src.duration, 30) };
  ws.video.addEventListener("loadedmetadata", () => { drawHeat(); layoutOverlay(); });
  ws.onResize = () => { drawHeat(); layoutOverlay(); queueStrip(); };
  window.addEventListener("resize", ws.onResize);
  api(`/api/sources/${sid}/signals`).then((s) => { if (S.ws === ws) { ws.signals = s; drawHeat(); } });
  bindTimeline(); bindTrim();
  $("#snapT").onchange = (e) => { ws.snap = e.target.checked; store.set("cd.snap", ws.snap); };
  $("#findForm").onsubmit = async (e) => {
    e.preventDefault();
    const text = $("#findText").value.trim() || $("#findText").placeholder;
    await busy($("#findBtn"), async () => {
      try { const r = await api(`/api/sources/${sid}/find`, { json: { text, mode: $("#findMode").value } }); renderResults([r, ...(ws.requests || src.requests)]); }
      catch (er) { fail(er); }
    });
  };
  renderResults(src.requests);
  syncTrim();
  await loadClips(clipId);
  tick();
}
function setMtab(t) {
  const ws = S.ws; if (!ws) return;
  ws.mtab = t; store.set("cd.mtab", t);
  const g = $(".ws"); if (g) g.dataset.mtab = t;
  $$("[data-mt]").forEach((b) => b.setAttribute("aria-selected", b.dataset.mt === t));
  if (t === "edit") setTimeout(layoutOverlay, 0);
}

async function attach(sid, what, file) {
  if (!file) return;
  const fd = new FormData(); fd.append("file", file);
  try {
    const r = await fetch(`/api/sources/${sid}/attach/${what}`, { method: "POST", body: fd }); const d = await r.json(); if (!r.ok) throw new Error(d.error);
    toast(what === "chat" ? `Chat log added: ${d.messages} messages` : `Transcript added: ${d.words} words`); route();
  } catch (e) { fail(e); }
}

function renderResults(reqs) {
  const ws = S.ws; if (!ws) return;
  ws.requests = reqs;
  const el = $("#results"); if (!el) return;
  if (!reqs.length) { el.innerHTML = `<p class="help" style="margin-top:12px">Results show up here, ranked, with the reason for each. You can keep trimming by time while a search runs.</p>`; drawMarks(); return; }
  // keep the newest search open; if it found nothing, also open the latest search that did
  const firstWith = reqs.findIndex((r) => r.candidates.length);
  el.innerHTML = reqs.slice(0, 4).map((r, i) => {
    const running = r.job && ["queued", "running"].includes(r.job.status);
    const strong = r.candidates.some((c) => c.strength === "strong");
    return `<details class="req" ${i === 0 || i === firstWith ? "open" : ""}><summary class="reqhead">${icon("chevron", "chev")}<span class="q grow">“${esc(r.text)}”</span>
        ${running ? pill("running", "Searching") : r.candidates.length ? pill(strong ? "strong" : "weak", `${r.candidates.length} found`) : pill("weak", "None")}</summary>
      ${running ? `<div class="progress" style="margin-top:8px"><i style="width:${(r.job.progress * 100).toFixed(0)}%"></i></div><p class="small muted" style="margin-top:6px">${esc(r.job.message)}</p>`
        : ""}
      ${r.note ? `<p class="reqnote ${strong ? "" : "warn"}">${icon(strong ? "check" : "alert")}<span>${esc(r.note)}</span></p>` : ""}
      ${r.candidates.map(resultCard).join("")}
    </details>`;
  }).join("");
  $$(".result", el).forEach((d) => (d.onclick = () => { selectClip(d.dataset.clip, true); if (isPhone()) setMtab("edit"); }));
  drawMarks();
}
function resultCard(c) {
  const sg = c.signals || {}, off = S.ws?.src.time_offset || 0;
  const bars = sg.engine === "signals-v1"
    ? [["Loudness", sg.audio], ["Chat", sg.chat_available ? sg.chat : null], ["Hype", sg.chat_available ? sg.hype : null], ["Match", sg.relevance]]
    : [["Match", sg.coverage], ["Payoff", sg.payoff ? 1 : 0], ["Question", sg.question ? 1 : 0], ["Score", c.score]];
  return `<button class="result" data-clip="${c.clip_id}" aria-current="${S.ws?.clip?.id === c.clip_id}">
    <div class="top"><span class="rank">${c.rank}</span><span class="when"><span class="s">${fmt(c.start + off, true)}</span> to <span class="e">${fmt(c.end + off, true)}</span></span>
      <span class="small faint">${(c.end - c.start).toFixed(0)}s</span><span class="grow"></span>${pill(c.strength, c.strength === "strong" ? "Strong match" : "Weak match")}${c.clip_status !== "pending" ? clipPill(c.clip_status) : ""}</div>
    <div class="ttl">${esc(c.title)}</div><div class="why">${esc(c.reason)}</div>
    <div class="meters">${bars.map(([n, v]) => `<div class="meter">${n}${v == null ? ": none" : ""}<i><b style="width:${((v || 0) * 100).toFixed(0)}%"></b></i></div>`).join("")}</div></button>`;
}

async function loadClips(selectId) {
  const ws = S.ws; if (!ws) return;
  const clips = await api(`/api/clips?source=${ws.sid}`);
  if (S.ws !== ws) return;
  ws.clips = clips.sort((a, b) => a.start - b.start);
  $("#stepsBox").innerHTML = steps(ws.src, ws.clips);
  drawClipList();
  const id = selectId || ws.clip?.id;
  if (id && ws.clips.some((c) => c.id === id)) await selectClip(id, false);
  else renderEditor();
}
function drawClipList() {
  const ws = S.ws, el = $("#clipList"); if (!el) return;
  const off = ws.src.time_offset || 0;
  el.innerHTML = ws.clips.length ? ws.clips.map((c) => `<button class="chip" data-id="${c.id}" aria-current="${ws.clip?.id === c.id}">
      <div class="t"><span>${fmt(c.start + off)}</span>${clipPill(c.status)}</div><div class="n">${esc(c.title || "Untitled")}</div></button>`).join("")
    : `<p class="help">No clips yet. Set a start and end and press New clip, or use Find a moment.</p>`;
  $$(".chip", el).forEach((d) => (d.onclick = () => selectClip(d.dataset.id, true)));
  drawMarks();
}

async function selectClip(id, seek) {
  const ws = S.ws; if (!ws || !id) return;
  const token = (ws.selSeq = (ws.selSeq || 0) + 1);
  await flushSave();
  const k = await api(`/api/clips/${id}`);
  if (S.ws !== ws || token !== ws.selSeq) return;
  ws.clip = k; ws.rev = structuredClone(k.rev);
  ws.sel = { s: k.rev.start, e: k.rev.end };
  history.replaceState(null, "", `#/source/${ws.sid}/${id}`);
  if (seek) ws.video.currentTime = k.rev.start;
  drawClipList(); renderEditor(); renderResults(ws.requests || []); syncTrim(); layoutOverlay(); queueStrip();
}

/* ---------- editor (program monitor + inspector) ---------- */
function renderEditor() {
  const ws = S.ws, el = $("#editor"); if (!el) return;
  const k = ws.clip;
  if (!k) { el.innerHTML = `<div class="panel empty">${icon("crop")}<h2>No clip selected</h2><p>Pick a result or a clip, or trim a part of the recording and press New clip.</p></div>`; return; }
  const L = ws.rev.layout || {}, mode = L.mode || "speaker", off = k.time_offset || 0;
  const fin = k.renders.find((r) => r.kind === "final" && r.rev === k.approved_rev);
  const prev = k.renders.find((r) => r.kind === "preview" && r.rev === k.current_rev);
  const running = k.job && ["queued", "running"].includes(k.job.status);
  ws.itab = ws.itab || "frame";
  const exportLabel = k.status === "approved" && !k.edited_since_approval ? "Export MP4" : "Approve and export";
  el.innerHTML = `
  <div class="panel">
    <div class="state">${clipPill(k.status)}<span class="small muted">${fmt(ws.rev.start + off, true)} to ${fmt(ws.rev.end + off, true)}</span>
      <span class="grow"></span><div class="seg" role="tablist" aria-label="Preview">
      <button role="tab" data-pv="live" aria-selected="${ws.pv !== "file"}">${icon("eye")}Live</button>
      <button role="tab" data-pv="file" aria-selected="${ws.pv === "file"}" ${prev || fin ? "" : "disabled"}>${icon("film")}Rendered</button></div></div>
    ${k.edited_since_approval ? `<p class="reqnote warn" style="margin:0 0 12px">${icon("alert")}<span>Changed after approval. Approve again to export this version.</span></p>` : ""}
    <div class="vwrap"><canvas id="vprev" class="vprev" width="360" height="640" role="img" aria-label="Vertical 9 by 16 preview"></canvas><video id="vfile" class="vprev" controls hidden playsinline></video></div>
    <p class="specs">1080 × 1920, 30 fps, ${(ws.rev.end - ws.rev.start).toFixed(1)}s</p>
    ${running ? `<div class="progress" style="margin-top:8px"><i style="width:${(k.job.progress * 100).toFixed(0)}%"></i></div><p class="small muted" style="margin-top:4px;text-align:center">${esc(k.job.message)}</p>` : ""}
    <div class="reviewbar ${isPhone() ? "dock" : ""}" role="toolbar" aria-label="Review">
      <button class="btn approve" id="bApprove" title="Shortcut A">${icon("check")}Approve</button>
      <button class="btn reject" id="bReject" title="Shortcut R">${icon("x")}Reject</button>
      <button class="btn" id="bUndo" title="Shortcut U" ${k.can_undo ? "" : "disabled"} aria-label="Undo">${icon("undo")}<span class="lbl-long">Undo</span></button>
      <button class="btn primary" id="bExport" title="Shortcut E" data-busy="Rendering…">${icon("download")}${exportLabel}</button>
    </div>
    <div class="row" style="margin-top:8px;justify-content:center">
      <button class="btn sm quiet" id="bRender" title="Shortcut V" data-busy="Rendering…">${icon("film")}Render preview file</button>
      ${fin ? `<a class="btn sm quiet" href="/api/clips/${k.id}/download.mp4">${icon("download")}Download again</a>` : ""}
      <span id="saveState" class="small faint" aria-live="polite"></span>
    </div>
    <div class="tabs" role="tablist" aria-label="Clip settings">
      ${[["frame", "crop", "Framing"], ["caps", "type", "Captions"], ["info", "sliders", "Title and tags"]].map(([id, ic, lb]) => `<button role="tab" data-it="${id}" aria-selected="${ws.itab === id}">${icon(ic)}${lb}</button>`).join("")}
    </div>
    <div data-ip="frame" ${ws.itab === "frame" ? "" : "hidden"}>
      <div class="seg" role="radiogroup" aria-label="Framing" style="display:flex;margin-bottom:12px">
        ${[["speaker", "user", "Speaker"], ["fit", "film", "Full frame"], ["gameplay", "gamepad", "Game + cam"]].map(([m, ic, lb]) => `<button role="radio" data-mode="${m}" aria-checked="${mode === m}" aria-pressed="${mode === m}" style="flex:1;justify-content:center">${icon(ic)}${lb}</button>`).join("")}
      </div>
      <div id="layoutCtl" class="sliders"></div>
      <div style="margin-top:12px"><label for="capY">Caption height</label><input type="range" id="capY" min="0.15" max="0.95" step="0.01" value="${L.caption_y ?? 0.8}"></div>
    </div>
    <div data-ip="caps" ${ws.itab === "caps" ? "" : "hidden"}>
      <div class="row" style="justify-content:space-between;margin-bottom:8px"><span class="small muted">Times are in the recording. Tap a time to jump.</span><button class="btn sm" id="addCap">${icon("plus")}Line at playhead</button></div>
      <div class="caps" id="caps"></div>
    </div>
    <div data-ip="info" ${ws.itab === "info" ? "" : "hidden"}>
      <div class="field"><label for="title">Title</label><input id="title" value="${esc(ws.rev.title)}" maxlength="120"></div>
      <div class="field"><label for="tags">Hashtags</label><input id="tags" value="${esc((ws.rev.hashtags || []).join(" "))}"><p class="help">Separate with spaces.</p></div>
      ${k.candidate ? `<details class="why-orig"><summary>Why this was suggested</summary><p class="small" style="margin-top:6px">${esc(k.candidate.reason)}</p>
        <p class="small faint" style="margin-top:6px">Original suggestion: ${fmt(k.candidate.start + off, true)} to ${fmt(k.candidate.end + off, true)}, “${esc(k.candidate.title)}”</p></details>` : `<p class="small faint">Made by hand.</p>`}
    </div>
  </div>`;
  $$("[data-pv]").forEach((b) => (b.onclick = () => { ws.pv = b.dataset.pv; renderEditor(); }));
  $$("[data-it]").forEach((b) => (b.onclick = () => { ws.itab = b.dataset.it; renderEditor(); }));
  const vf = $("#vfile"), cv = $("#vprev");
  if (ws.pv === "file" && (prev || fin)) { cv.hidden = true; vf.hidden = false; vf.src = prev ? `/api/clips/${k.id}/preview.mp4?r=${prev.rev}` : `/api/clips/${k.id}/download.mp4`; }
  ws.canvas = cv;
  $$("[data-mode]").forEach((b) => (b.onclick = () => setLayout({ mode: b.dataset.mode }, true)));
  $("#capY").oninput = (e) => setLayout({ caption_y: +e.target.value });
  if ($("#title")) $("#title").oninput = (e) => { ws.rev.title = e.target.value; queueSave({ title: ws.rev.title }); };
  if ($("#tags")) $("#tags").oninput = (e) => { ws.rev.hashtags = e.target.value.split(/\s+/).filter(Boolean); queueSave({ hashtags: ws.rev.hashtags }); };
  $("#addCap").onclick = () => {
    const t = clamp(ws.video.currentTime, ws.rev.start, ws.rev.end - 0.5);
    ws.rev.captions.push({ s: +t.toFixed(2), e: +Math.min(t + 2, ws.rev.end).toFixed(2), text: "New caption" });
    ws.rev.captions.sort((a, b) => a.s - b.s); drawCaps(); queueSave({ captions: ws.rev.captions });
    const inp = $$("#caps input").find((x) => x.value === "New caption"); if (inp) { inp.focus(); inp.select(); }
  };
  $("#bApprove").onclick = () => setStatus("approved");
  $("#bReject").onclick = () => setStatus("rejected");
  $("#bUndo").onclick = undo;
  $("#bRender").onclick = (e) => renderPreview(e.currentTarget);
  $("#bExport").onclick = (e) => exportClip(e.currentTarget);
  drawLayoutCtl(); drawCaps();
}
function drawLayoutCtl() {
  const ws = S.ws, L = ws.rev.layout || {}, el = $("#layoutCtl"); if (!el) return;
  const sl = (id, label, v, min, max, step) => `<div><label for="${id}">${label}</label><input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${v}"></div>`;
  if (L.mode === "gameplay") {
    el.innerHTML = sl("lsplit", "Camera height", L.split ?? 0.38, 0.15, 0.7, 0.01) +
      `<p class="help" style="grid-column:1/-1;margin:0">Drag the pink camera box and the blue game box on the video. The corner square resizes them.</p>`;
    $("#lsplit").oninput = (e) => setLayout({ split: +e.target.value });
  } else if (L.mode === "speaker") {
    el.innerHTML = sl("lcx", "Left and right", L.cx ?? 0.5, 0, 1, 0.005) + sl("lzoom", "Zoom", L.zoom ?? 1, 1, 2.5, 0.01) +
      `<div style="grid-column:1/-1" class="row">${S.system.face_model ? `<button class="btn sm" id="autoC" data-busy="Looking for a face…">${icon("user")}Center on face</button>` : ""}<span id="autoNote" class="small faint">${S.system.face_model ? "" : "You can also drag the purple frame on the video. New clips from this recording reuse this position."}</span></div>`;
    $("#lcx").oninput = (e) => setLayout({ cx: +e.target.value });
    $("#lzoom").oninput = (e) => setLayout({ zoom: +e.target.value });
    if ($("#autoC")) $("#autoC").onclick = (e) => busy(e.currentTarget, async () => { try { await flushSave(); const r = await api(`/api/clips/${ws.clip.id}/autocenter`, { json: {} }); $("#autoNote").textContent = r.note; if (r.clip.rev.id !== ws.clip.rev.id) await selectClip(ws.clip.id, false); } catch (er) { fail(er); } });
  } else el.innerHTML = `<p class="help" style="grid-column:1/-1;margin:0">The whole picture fits the width, over a blurred copy of itself.</p>`;
  layoutOverlay();
}
function drawCaps() {
  const ws = S.ws, el = $("#caps"); if (!el) return;
  const caps = ws.rev.captions || [], off = ws.src.time_offset || 0;
  const inRange = caps.map((c, i) => [c, i]).filter(([c]) => c.e > ws.rev.start && c.s < ws.rev.end);
  el.innerHTML = inRange.length ? inRange.map(([c, i]) => `<div class="cap" data-i="${i}"><button class="t" aria-label="Jump to ${fmt(c.s + off, true)}">${fmt(c.s + off, true)}</button>
      <input value="${esc(c.text)}" aria-label="Caption at ${fmt(c.s + off, true)}"><button class="iconbtn" aria-label="Delete this caption">${icon("x")}</button></div>`).join("")
    : `<p class="help" style="padding:12px">${ws.src.transcript_status === "ready" ? "Nobody speaks in this part." : "No transcript yet. Add lines by hand or add a transcript file."}</p>`;
  $$(".cap", el).forEach((row) => {
    const i = +row.dataset.i;
    $("input", row).oninput = (e) => { caps[i].text = e.target.value; queueSave({ captions: caps }); };
    $(".iconbtn", row).onclick = () => { caps.splice(i, 1); drawCaps(); queueSave({ captions: caps }); };
    $(".t", row).onclick = () => (ws.video.currentTime = caps[i].s);
  });
}
const DEFAULT_LAYOUTS = { speaker: { mode: "speaker", cx: 0.5, zoom: 1, caption_y: 0.8 }, fit: { mode: "fit", caption_y: 0.8 },
  gameplay: { mode: "gameplay", split: 0.38, caption_y: 0.46, face: { x: 0.7, y: 0.62, w: 0.28, h: 0.36 }, action: { x: 0.18, y: 0, w: 0.64, h: 1 } } };
function setLayout(patch, rerender = false) {
  const ws = S.ws; if (!ws?.clip) return;
  if (patch.mode && patch.mode !== ws.rev.layout.mode) ws.rev.layout = { ...DEFAULT_LAYOUTS[patch.mode] };
  else ws.rev.layout = { ...ws.rev.layout, ...patch };
  queueSave({ layout: ws.rev.layout });
  if (rerender) { renderEditor(); } else layoutOverlay();
}

/* ---------- saving ---------- */
function queueSave(patch) {
  const ws = S.ws; if (!ws?.clip) return;
  ws.pending = { ...(ws.pending || {}), ...patch };
  const st = $("#saveState"); if (st) st.textContent = "Saving…";
  clearTimeout(ws.saveT); ws.saveT = setTimeout(flushSave, 600);
}
async function flushSave() {
  const ws = S.ws; if (!ws?.clip || !ws.pending) return;
  clearTimeout(ws.saveT);
  const patch = ws.pending; ws.pending = null;
  try {
    const k = await api(`/api/clips/${ws.clip.id}/revise`, { json: patch });
    if (S.ws !== ws) return;
    const ranged = "start" in patch || "end" in patch;
    ws.clip = k; ws.rev.id = k.rev.id;
    if (ranged) {
      ws.rev.captions = k.rev.captions; ws.rev.start = k.rev.start; ws.rev.end = k.rev.end;
      if (!ws.dragging) { ws.sel = { s: k.rev.start, e: k.rev.end }; syncTrim(); }
      $("#snapNote").textContent = k.snap_note || "";
      drawCaps();
    }
    const st = $("#saveState"); if (st) st.textContent = "Saved";
    const b = $("#bUndo"); if (b) b.disabled = !k.can_undo;
    const i = ws.clips.findIndex((c) => c.id === k.id);
    if (i >= 0) { Object.assign(ws.clips[i], { start: k.rev.start, end: k.rev.end, title: k.rev.title, status: k.status }); drawClipList(); }
    if (k.edited_since_approval && !$(".panel .reqnote.warn", $("#editor"))) renderEditor();
  } catch (e) { fail(e); const st = $("#saveState"); if (st) st.textContent = "Not saved"; }
}

async function setStatus(status) {
  const ws = S.ws; if (!ws?.clip) return;
  await flushSave();
  const id = ws.clip.id;
  try {
    await api(`/api/clips/${id}/status`, { json: { status } });
    toast(status === "approved" ? "Approved" : "Rejected", { action: { label: "Undo", fn: () => undoClip(id) } });
    await loadClips(id);
  } catch (e) { fail(e); }
}
async function undoClip(id) {
  try { await api(`/api/clips/${id}/undo`, { json: {} }); toast("Undone"); if (S.view === "ws") await loadClips(id); else if (S.view === "queue") await renderQueue(); }
  catch (e) { fail(e); }
}
async function undo() {
  const ws = S.ws; if (!ws?.clip) return;
  ws.pending = null; clearTimeout(ws.saveT);
  await undoClip(ws.clip.id);
}
async function renderPreview(btn) {
  const ws = S.ws; if (!ws?.clip) return;
  await flushSave();
  await busy(btn || $("#bRender"), async () => {
    try { const j = await api(`/api/clips/${ws.clip.id}/render`, { json: { kind: "preview" } }); toast("Rendering a preview file. It uses the same settings as the export.");
      watchJob(j.id, () => { if (S.ws === ws) { ws.pv = "file"; selectClip(ws.clip.id, false); } }); }
    catch (e) { fail(e); }
  });
}
async function exportClip(btn) {
  const ws = S.ws; if (!ws?.clip) return;
  await flushSave();
  await busy(btn || $("#bExport"), async () => {
    try {
      let k = ws.clip;
      if (k.status !== "approved" || k.edited_since_approval) k = await api(`/api/clips/${k.id}/status`, { json: { status: "approved" } });
      const j = await api(`/api/clips/${k.id}/render`, { json: { kind: "final" } });
      toast("Rendering your 1080 × 1920 MP4. It downloads when ready.");
      watchJob(j.id, () => { download(`/api/clips/${k.id}/download.mp4`); toast("Exported. The MP4 is downloading."); if (S.ws === ws) loadClips(k.id); });
      await loadClips(k.id);
    } catch (e) { fail(e); }
  });
}
function download(url) { const a = document.createElement("a"); a.href = url; a.download = ""; document.body.appendChild(a); a.click(); a.remove(); }

/* ---------- selection / trim ---------- */
function selValid(s, e) {
  const d = S.ws.src.duration, L = S.system.limits;
  if (isNaN(s) || isNaN(e)) return "Write times like 12:05.5";
  if (s < 0) return "The start can't be before 0:00.";
  if (e > d + 0.05) return `The end is after the recording ends (${fmt(d)}).`;
  if (e <= s) return "The start has to come before the end.";
  if (e - s < L.min_clip_s) return `Clips need at least ${L.min_clip_s} seconds.`;
  if (e - s > L.max_clip_s) return `This is ${(e - s).toFixed(1)} seconds. Exports can be ${L.max_clip_s} at most.`;
  return "";
}
function setSel(s, e, save = true) {
  const ws = S.ws, d = ws.src.duration;
  s = clamp(+(+s).toFixed(2), 0, d); e = clamp(+(+e).toFixed(2), 0, d);
  ws.sel = { s, e };
  const err = selValid(s, e);
  syncTrim(err);
  if (save) $("#snapNote").textContent = "";
  if (!err && save && ws.clip) {
    const patch = { start: s, end: e }; if (ws.snap) patch.snap = true;
    ws.rev.start = s; ws.rev.end = e; queueSave(patch);
  }
}
function syncTrim(err) {
  const ws = S.ws; if (!ws || !$("#selS")) return;
  const off = ws.src.time_offset || 0, len = ws.sel.e - ws.sel.s;
  if (document.activeElement !== $("#selS")) $("#selS").value = fmt(ws.sel.s + off, true);
  if (document.activeElement !== $("#selE")) $("#selE").value = fmt(ws.sel.e + off, true);
  $("#slS").textContent = fmt(ws.sel.s + off, true); $("#slE").textContent = fmt(ws.sel.e + off, true);
  const lenEl = $("#slLen"); lenEl.classList.toggle("over", len > S.system.limits.max_clip_s); $("b", lenEl).textContent = `${len.toFixed(1)}s`;
  err = err ?? selValid(ws.sel.s, ws.sel.e);
  $("#selErr").innerHTML = err ? `${icon("alert")}${esc(err)}` : "";
  $("#mkClip").disabled = !!err;
  queueStrip();
}
function bindTrim() {
  const ws = S.ws, off = () => ws.src.time_offset || 0;
  const commit = () => setSel(parseTC($("#selS").value) - off(), parseTC($("#selE").value) - off());
  ["#selS", "#selE"].forEach((id) => { $(id).addEventListener("change", commit); $(id).addEventListener("keydown", (e) => e.key === "Enter" && (commit(), e.target.blur())); });
  $("#setS").onclick = () => setSel(ws.video.currentTime, Math.max(ws.sel.e, ws.video.currentTime + 1));
  $("#setE").onclick = () => setSel(ws.sel.s, ws.video.currentTime);
  $$("[data-nudge]").forEach((b) => (b.onclick = () => { const [w, d] = b.dataset.nudge.split(","); w === "s" ? setSel(ws.sel.s + +d, ws.sel.e) : setSel(ws.sel.s, ws.sel.e + +d); }));
  const go = () => {
    const t = parseTC($("#goTC").value) - off();
    if (isNaN(t) || t < 0 || t > ws.src.duration) { toast(`Pick a time between ${fmt(off())} and ${fmt(ws.src.duration + off())}`, { err: true }); return; }
    ws.video.currentTime = t;
    if (!ws.clip) { const a = Math.max(0, t - 5); setSel(a, Math.min(ws.src.duration, a + 20), false); }
  };
  $("#goBtn").onclick = go; $("#goTC").addEventListener("keydown", (e) => e.key === "Enter" && (go(), e.target.blur()));
  $("#playSel").onclick = playSelection;
  $("#mkClip").onclick = (e) => busy(e.currentTarget, async () => {
    try {
      const k = await api("/api/clips", { json: { source_id: ws.sid, start: ws.sel.s, end: ws.sel.e, snap: ws.snap } });
      toast(k.snap_note ? `Clip made. ${esc(k.snap_note)}` : "Clip made");
      await loadClips(k.id); if (isPhone()) setMtab("edit");
    } catch (er) { fail(er); }
  });
}
function playSelection() { const ws = S.ws; ws.video.currentTime = ws.sel.s; ws.playSel = true; ws.video.play(); }
function detRange() {
  const ws = S.ws; if (ws.detFrozen) return ws.detFrozen;
  const pad = Math.max(8, (ws.sel.e - ws.sel.s) * 0.5);
  return [Math.max(0, ws.sel.s - pad), Math.min(ws.src.duration, ws.sel.e + pad)];
}
function queueStrip() {  // filmstrip thumbnails for the current detail range (debounced, cached by the server)
  const ws = S.ws; if (!ws || ws.detFrozen || !$("#strip")) return;
  clearTimeout(ws.stripT);
  ws.stripT = setTimeout(() => {
    if (S.ws !== ws) return;
    const el = $("#strip"), [a, b] = detRange();
    const key = `${a.toFixed(1)}_${b.toFixed(1)}`;
    if (key === ws.stripKey || b - a < 0.5) return;
    ws.stripKey = key;
    const n = clamp(Math.round(el.clientWidth / 112), 4, 16);
    const img = new Image();
    img.onload = () => { if (ws.stripKey === key) el.style.backgroundImage = `url(${img.src})`; };
    img.src = `/api/sources/${ws.sid}/strip.jpg?s=${a.toFixed(1)}&e=${b.toFixed(1)}&n=${n}&h=72`;
  }, 300);
}
function bindTimeline() {
  const ws = S.ws, all = $("#tlAll"), strip = $("#strip");
  const seekAll = (e) => { const r = all.getBoundingClientRect(); ws.video.currentTime = clamp((e.clientX - r.left) / r.width, 0, 1) * ws.src.duration; };
  all.addEventListener("pointerdown", (e) => { seekAll(e); all.setPointerCapture(e.pointerId); all.onpointermove = seekAll; });
  all.addEventListener("pointerup", () => (all.onpointermove = null));
  let drag = null;
  const tAt = (x) => { const r = strip.getBoundingClientRect(), [a, b] = ws.detFrozen || detRange(); return a + clamp((x - r.left) / r.width, 0, 1) * (b - a); };
  strip.addEventListener("pointerdown", (e) => {
    const h = e.target.closest(".handle");
    if (h) { drag = h.id; ws.dragging = true; ws.detFrozen = detRange(); strip.setPointerCapture(e.pointerId); ws.video.pause(); }
    else ws.video.currentTime = tAt(e.clientX);
  });
  strip.addEventListener("pointermove", (e) => {
    if (!drag) return; const t = tAt(e.clientX);
    if (drag === "hS") { setSel(Math.min(t, ws.sel.e - 0.5), ws.sel.e, false); ws.video.currentTime = ws.sel.s; }
    else { setSel(ws.sel.s, Math.max(t, ws.sel.s + 0.5), false); ws.video.currentTime = ws.sel.e; }
  });
  const end = () => { if (drag) { drag = null; ws.dragging = false; ws.detFrozen = null; setSel(ws.sel.s, ws.sel.e, true); } };
  strip.addEventListener("pointerup", end); strip.addEventListener("pointercancel", end);
  ["hS", "hE"].forEach((id) => $("#" + id).addEventListener("keydown", (e) => {
    const d = e.key === "ArrowLeft" ? -0.1 : e.key === "ArrowRight" ? 0.1 : 0; if (!d) return; e.preventDefault(); e.stopPropagation();
    const k = e.shiftKey ? 10 : 1; id === "hS" ? setSel(ws.sel.s + d * k, ws.sel.e) : setSel(ws.sel.s, ws.sel.e + d * k);
  }));
}
function drawHeat() {
  const ws = S.ws, c = $("#heat"); if (!ws || !c) return;
  const w = (c.width = c.clientWidth * devicePixelRatio), h = (c.height = c.clientHeight * devicePixelRatio), g = c.getContext("2d");
  g.clearRect(0, 0, w, h);
  const sg = ws.signals; if (!sg || !sg.audio.length) return;
  const a = sg.audio, n = a.length; let mn = Infinity, mx = -Infinity; for (const v of a) { mn = Math.min(mn, v); mx = Math.max(mx, v); }
  g.fillStyle = "rgba(143,184,255,.5)";
  for (let i = 0; i < n; i++) { const v = mx > mn ? (a[i] - mn) / (mx - mn) : 0; g.fillRect(i / n * w, h - v * h * .9, Math.max(1, w / n - .5), v * h * .9); }
  if (sg.chat.length) { const cm = Math.max(1, ...sg.chat); g.fillStyle = "rgba(255,158,219,.75)"; for (let i = 0; i < sg.chat.length; i++) { const v = sg.chat[i] / cm; g.fillRect(i / n * w, 0, Math.max(1, w / n - .5), v * h * .4); } }
}
function drawMarks() {
  const ws = S.ws, el = $("#marks"); if (!ws || !el) return;
  const d = ws.src.duration || 1;
  el.innerHTML = ws.clips.map((c) => `<span class="mk" style="left:${(100 * c.start / d).toFixed(2)}%" title="Clip at ${fmt(c.start)}"></span>`).join("");
}

/* ---------- regions drawn on the source video ---------- */
function videoRect() {
  const v = S.ws.video, cw = v.clientWidth, ch = v.clientHeight, vw = v.videoWidth || 16, vh = v.videoHeight || 9;
  const s = Math.min(cw / vw, ch / vh); const w = vw * s, h = vh * s; return { x: (cw - w) / 2, y: (ch - h) / 2, w, h };
}
function speakerRect(L, W, H) {
  const z = clamp(L.zoom || 1, 1, 3); let ch = H / z, cw = ch * 9 / 16; if (cw > W) { cw = W; ch = Math.min(H, W * 16 / 9); }
  return { x: clamp((L.cx ?? 0.5) * W - cw / 2, 0, W - cw), y: clamp((L.cy ?? 0.5) * H - ch / 2, 0, H - ch), w: cw, h: ch };
}
function layoutOverlay() {
  const ws = S.ws, o = $("#ovl"); if (!ws || !o) return;
  if (!ws.clip) { o.innerHTML = ""; return; }
  const L = ws.rev.layout, R = videoRect(), v = ws.video;
  const box = (cls, r, label, key, grip) => `<div class="region ${cls}" data-key="${key}" style="left:${R.x + r.x * R.w}px;top:${R.y + r.y * R.h}px;width:${r.w * R.w}px;height:${r.h * R.h}px">${label}${grip ? '<span class="grip" data-grip="1"></span>' : ""}</div>`;
  if (L.mode === "gameplay") o.innerHTML = box("face", L.face, "Camera", "face", true) + box("action", L.action, "Game", "action", true);
  else if (L.mode === "speaker") {
    const W = v.videoWidth || 1920, H = v.videoHeight || 1080, r = speakerRect(L, W, H);
    o.innerHTML = box("crop", { x: r.x / W, y: r.y / H, w: r.w / W, h: r.h / H }, "9:16", "crop", false);
  } else o.innerHTML = "";
  $$(".region", o).forEach((el) => {
    el.onpointerdown = (e) => {
      e.preventDefault(); const key = el.dataset.key, grip = !!e.target.dataset.grip, x0 = e.clientX, y0 = e.clientY;
      const r0 = key === "crop" ? { cx: L.cx ?? 0.5 } : { ...L[key] };
      el.setPointerCapture(e.pointerId);
      el.onpointermove = (m) => {
        const dx = (m.clientX - x0) / R.w, dy = (m.clientY - y0) / R.h;
        if (key === "crop") { L.cx = clamp(r0.cx + dx, 0, 1); const W = v.videoWidth || 1920, H = v.videoHeight || 1080, r = speakerRect(L, W, H); el.style.left = R.x + r.x / W * R.w + "px"; const s = $("#lcx"); if (s) s.value = L.cx; return; }
        const r = { ...r0 };
        if (grip) { r.w = clamp(r0.w + dx, 0.05, 1 - r0.x); r.h = clamp(r0.h + dy, 0.05, 1 - r0.y); } else { r.x = clamp(r0.x + dx, 0, 1 - r0.w); r.y = clamp(r0.y + dy, 0, 1 - r0.h); }
        L[key] = r; el.style.left = R.x + r.x * R.w + "px"; el.style.top = R.y + r.y * R.h + "px"; el.style.width = r.w * R.w + "px"; el.style.height = r.h * R.h + "px";
      };
      el.onpointerup = () => { el.onpointermove = null; setLayout(key === "crop" ? { cx: +L.cx.toFixed(3) } : { [key]: L[key] }); };
    };
  });
}

/* ---------- live 9:16 preview (same maths as render.py) ---------- */
function coverDraw(g, v, sx, sy, sw, sh, dx, dy, dw, dh) { const s = Math.max(dw / sw, dh / sh), cw = dw / s, ch = dh / s; g.drawImage(v, sx + (sw - cw) / 2, sy + (sh - ch) / 2, cw, ch, dx, dy, dw, dh); }
function drawPreview() {
  const ws = S.ws, cv = ws.canvas; if (!cv || cv.hidden || !cv.isConnected || !ws.clip) return;
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
  } else { const r = speakerRect(L, W, H); g.drawImage(v, r.x, r.y, r.w, r.h, 0, 0, OW, OH); }
  const t = v.currentTime, cap = (ws.rev.captions || []).find((c) => t >= c.s && t < c.e && c.s < ws.rev.end && c.e > ws.rev.start);
  if (cap && t >= ws.rev.start && t <= ws.rev.end) {
    const k = OH / 1920, size = (L.caption_size || S.system.settings.caption_size) * k, maxChars = Math.max(10, Math.floor((1080 - 180) / (size / k * 0.52)));
    const lines = wrapText(cap.text, maxChars);
    g.font = `700 ${size}px "Fira Sans", sans-serif`; g.textAlign = "center"; g.textBaseline = "bottom"; g.lineJoin = "round";
    g.lineWidth = 10 * k; g.strokeStyle = "#000"; g.fillStyle = "#fff";
    const bottom = OH * clamp(L.caption_y ?? 0.8, 0.1, 0.97), lh = size * 1.18;
    lines.forEach((ln, i) => { const y = bottom - (lines.length - 1 - i) * lh; g.strokeText(ln, OW / 2, y); g.fillText(ln, OW / 2, y); });
  }
  if (t < ws.rev.start - 0.05 || t > ws.rev.end + 0.05) {
    g.fillStyle = "rgba(26,31,43,.72)"; g.fillRect(0, 0, OW, OH); g.fillStyle = "#EEF0F5"; g.font = `600 15px "DM Sans", sans-serif`; g.textAlign = "center";
    g.fillText("The playhead is outside this clip", OW / 2, OH / 2 - 10); g.fillStyle = "#A9B1C3"; g.fillText("Press P to play the clip", OW / 2, OH / 2 + 14);
  }
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
  const v = ws.video, d = ws.src.duration || 1, off = ws.src.time_offset || 0;
  if (ws.playSel && v.currentTime >= ws.sel.e) { v.pause(); ws.playSel = false; v.currentTime = ws.sel.e; }
  if (v.paused) ws.playSel = false;
  const pct = (t) => (100 * t / d) + "%";
  $("#headAll").style.left = pct(v.currentTime);
  const wa = $("#winAll"); wa.style.left = pct(ws.sel.s); wa.style.width = (100 * (ws.sel.e - ws.sel.s) / d) + "%";
  const [a, b] = detRange(), dp = (t) => (100 * (t - a) / (b - a)) + "%";
  $("#selDet").style.left = dp(ws.sel.s); $("#selDet").style.width = (100 * (ws.sel.e - ws.sel.s) / (b - a)) + "%";
  $("#dimL").style.left = "0"; $("#dimL").style.width = dp(ws.sel.s); $("#dimR").style.left = dp(ws.sel.e); $("#dimR").style.right = "0";
  $("#hS").style.left = dp(ws.sel.s); $("#hE").style.left = dp(ws.sel.e);
  const hd = $("#headDet"); const inside = v.currentTime >= a && v.currentTime <= b; hd.hidden = !inside; if (inside) hd.style.left = dp(v.currentTime);
  $("#hS").setAttribute("aria-valuenow", ws.sel.s.toFixed(1)); $("#hE").setAttribute("aria-valuenow", ws.sel.e.toFixed(1));
  $("#hS").setAttribute("aria-valuetext", fmt(ws.sel.s + off, true)); $("#hE").setAttribute("aria-valuetext", fmt(ws.sel.e + off, true));
  $("#detL").textContent = fmt(a + off); $("#detR").textContent = fmt(b + off); $("#detM").textContent = `Playhead ${fmt(v.currentTime + off, true)}`;
  drawPreview();
  $$(".cap").forEach((row) => { const c = ws.rev?.captions[+row.dataset.i]; row.classList.toggle("now", !!c && v.currentTime >= c.s && v.currentTime < c.e); });
  ws.raf = requestAnimationFrame(tick);
}

/* ================================================================= REVIEW QUEUE */
async function renderQueue() {
  S.view = "queue";
  const q = (S.q = S.q || { status: "pending", creator: "", idx: 0, checked: new Set() });
  const [clips, creators, jobs] = await Promise.all([api(`/api/clips?${q.status ? "status=" + q.status : ""}${q.creator ? "&creator=" + q.creator : ""}`), api("/api/creators"), api("/api/jobs")]);
  q.clips = clips; q.idx = clamp(q.idx, 0, Math.max(0, clips.length - 1));
  const seen = new Set();
  const exports = jobs.filter((j) => j.kind === "export" && j.status === "done" && !seen.has(j.result.zip) && seen.add(j.result.zip));
  const exportLabel = q.checked.size ? `Export ${q.checked.size} selected` : "Export all approved";
  $("#main").innerHTML = `
  <div class="qtools">
    <h1>Review</h1>
    <div class="seg" role="radiogroup" aria-label="Show">${[["pending", "To review"], ["approved", "Approved"], ["rejected", "Rejected"], ["", "All"]].map(([v, l]) => `<button role="radio" data-qs="${v}" aria-checked="${q.status === v}" aria-pressed="${q.status === v}">${l}</button>`).join("")}</div>
    <select id="qCreator" aria-label="Creator"><option value="">All creators</option>${creators.map((c) => `<option value="${c.id}" ${q.creator === c.id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
    <button class="btn primary" id="qExport" data-busy="Exporting…">${icon("download")}${exportLabel} (ZIP)</button>
  </div>
  <div class="queue">
    <div class="panel qlist" role="listbox" aria-label="Clips" id="qList">
      ${clips.length ? clips.map((c, i) => `<div class="qrow" data-i="${i}" role="option" aria-selected="${i === q.idx}" tabindex="${i === q.idx ? 0 : -1}">
        <input type="checkbox" data-chk="${c.id}" ${q.checked.has(c.id) ? "checked" : ""} aria-label="Include in export">
        <div><div class="when">${fmt(c.start + (c.time_offset || 0))}</div><div class="len">${(c.end - c.start).toFixed(0)}s</div></div>
        <div style="min-width:0"><div class="ttl">${esc(c.title || "Untitled")}</div>
          <div class="sub">${esc(c.creator_name || "No creator")}, ${esc(c.source_title)}${c.request_text ? `, found for “${esc(c.request_text)}”` : ", made by hand"}</div></div>
        <div class="pills">${c.strength ? pill(c.strength, c.strength === "strong" ? "Strong" : "Weak") : ""}${clipPill(c.status)}</div></div>`).join("")
        : `<div class="empty">${icon("queue")}<h2>${q.status === "pending" ? "Nothing to review" : "No clips here"}</h2><p>${q.status === "pending" ? "Make clips from a recording and they'll wait here for a decision." : "Try another filter."}</p></div>`}
    </div>
    <div class="qdetail stack" id="qDetail"></div>
  </div>
  ${exports.length ? `<div class="panel exports" style="margin-top:24px"><div class="panel-head"><h2>Exports</h2></div>${exports.slice(0, 6).map((j) => `<div><a href="/api/exports/${j.result.zip}">${icon("download")}${esc(j.result.zip)}</a> <span class="small faint">${j.result.clips} clips, ${new Date(j.updated * 1000).toLocaleString()}</span></div>`).join("")}</div>` : ""}`;
  $$("[data-qs]").forEach((b) => (b.onclick = () => { q.status = b.dataset.qs; q.idx = 0; q.lastActed = null; renderQueue(); }));
  $("#qCreator").onchange = (e) => { q.creator = e.target.value; q.idx = 0; renderQueue(); };
  $("#qExport").onclick = (e) => queueExport(e.currentTarget);
  $$(".qrow").forEach((r) => (r.onclick = (e) => { if (e.target.dataset.chk) return; q.idx = +r.dataset.i; q.lastActed = null; qSelect(); }));
  $$("[data-chk]").forEach((c) => (c.onchange = () => { c.checked ? q.checked.add(c.dataset.chk) : q.checked.delete(c.dataset.chk);
    $("#qExport").innerHTML = `${icon("download")}${q.checked.size ? `Export ${q.checked.size} selected` : "Export all approved"} (ZIP)`; }));
  qDetail();
}
function qSelect() {
  const q = S.q;
  $$(".qrow").forEach((r, i) => { const on = i === q.idx; r.setAttribute("aria-selected", on); r.tabIndex = on ? 0 : -1; if (on) r.scrollIntoView({ block: "nearest" }); });
  qDetail();
  if (isPhone()) $("#qDetail").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}
async function qDetail() {
  const q = S.q, el = $("#qDetail"), c = q.clips[q.idx];
  if (!c) { el.innerHTML = ""; return; }
  const k = await api(`/api/clips/${c.id}`);
  if (S.view !== "queue") return;
  q.cur = k;
  const prev = k.renders.find((r) => r.kind === "preview" && r.rev === k.current_rev), fin = k.renders.find((r) => r.kind === "final" && r.rev === k.approved_rev);
  const off = k.time_offset || 0, running = k.job && ["queued", "running"].includes(k.job.status);
  el.innerHTML = `<div class="panel">
    <div class="state">${clipPill(k.status)}<span class="small muted">${fmt(k.rev.start + off, true)} to ${fmt(k.rev.end + off, true)}</span><span class="grow"></span><a class="btn sm" href="#/source/${k.source_id}/${k.id}">${icon("crop")}Edit</a></div>
    <h2>${esc(k.rev.title || "Untitled")}</h2>
    <div style="margin-top:12px">${prev || fin ? `<video class="vert" controls playsinline src="${prev ? `/api/clips/${k.id}/preview.mp4?r=${prev.rev}` : `/api/clips/${k.id}/download.mp4`}"></video>`
      : `<video controls preload="metadata" playsinline src="/api/sources/${k.source_id}/media#t=${k.rev.start},${k.rev.end}"></video><p class="help">This plays the original. Render a preview to see the vertical version.</p>`}</div>
    ${running ? `<div class="progress" style="margin-top:8px"><i style="width:${(k.job.progress * 100).toFixed(0)}%"></i></div>` : ""}
    <p class="small muted" style="margin-top:12px">${k.candidate ? esc(k.candidate.reason) : "Made by hand."}</p>
    ${(k.rev.hashtags || []).length ? `<p class="small faint" style="margin-top:6px">${esc(k.rev.hashtags.join(" "))}</p>` : ""}
    <div class="reviewbar ${isPhone() ? "dock" : ""}" role="toolbar" aria-label="Review">
      <button class="btn approve" id="qA" title="Shortcut A">${icon("check")}Approve</button><button class="btn reject" id="qR" title="Shortcut R">${icon("x")}Reject</button>
      <button class="btn" id="qU" title="Shortcut U" ${k.can_undo || q.lastActed ? "" : "disabled"} aria-label="Undo">${icon("undo")}<span class="lbl-long">Undo</span></button>
      <button class="btn" id="qV" title="Shortcut V" data-busy="Rendering…" aria-label="Render preview">${icon("film")}<span class="lbl-long">Preview</span></button>
    </div>
    ${fin ? `<a class="btn sm quiet" style="margin-top:8px" href="/api/clips/${k.id}/download.mp4">${icon("download")}Download MP4</a>` : ""}
  </div>`;
  $("#qA").onclick = () => qStatus("approved"); $("#qR").onclick = () => qStatus("rejected"); $("#qU").onclick = qUndo; $("#qV").onclick = (e) => qRender(e.currentTarget);
}
async function qStatus(st) {
  const q = S.q; if (!q.cur) return;
  const id = q.cur.id;
  try {
    await api(`/api/clips/${id}/status`, { json: { status: st } });
    q.lastActed = id;
    if (!(q.status && q.status !== st)) q.idx++;
    toast(st === "approved" ? "Approved" : "Rejected", { action: { label: "Undo", fn: () => qUndo() } });
    await renderQueue();
  } catch (e) { fail(e); }
}
async function qUndo() {
  const q = S.q; const id = q.lastActed || q.cur?.id; if (!id) return;
  try {
    const k = await api(`/api/clips/${id}/undo`, { json: {} }); q.lastActed = null; toast("Undone");
    await renderQueue();
    const i = q.clips.findIndex((c) => c.id === k.id); if (i >= 0) { q.idx = i; qSelect(); }
  } catch (e) { fail(e); }
}
async function qRender(btn) { const q = S.q; if (!q.cur) return; await busy(btn, async () => { try { const j = await api(`/api/clips/${q.cur.id}/render`, { json: { kind: "preview" } }); toast("Rendering a preview file."); watchJob(j.id, () => S.view === "queue" && qDetail()); } catch (e) { fail(e); } }); }
async function queueExport(btn) {
  const q = S.q;
  await busy(btn || $("#qExport"), async () => {
    try { const j = await api("/api/export", { json: q.checked.size ? { clip_ids: [...q.checked] } : {} }); toast("Rendering your approved clips into a ZIP.");
      watchJob(j.id, (jj) => { download(`/api/exports/${jj.result.zip}`); toast(`Exported ${jj.result.clips} clips. The ZIP is downloading.`); if (S.view === "queue") renderQueue(); }); }
    catch (e) { fail(e); }
  });
}

/* ================================================================ SETUP */
async function renderSettings() {
  S.view = "settings"; await loadSystem(); const s = S.system, st = s.settings;
  const ck = (on, title, desc) => `<div class="ck ${on ? "on" : "off"}">${icon(on ? "check" : "alert")}<div><b>${title}</b><span class="small muted">${desc}</span></div></div>`;
  $("#main").innerHTML = `<div style="max-width:760px">
    <h1>Setup</h1>
    <div class="panel" style="margin-top:16px">
      <div class="panel-head"><h2>What's installed</h2></div>
      <div class="checks">
        ${ck(s.ffmpeg && s.ffprobe, "Video engine (FFmpeg)", s.ffmpeg ? "Imports, previews and exports work." : "Install FFmpeg, then restart ClipDesk.")}
        ${ck(s.yt_dlp, "Link import (yt-dlp)", s.yt_dlp ? "YouTube and Twitch replays can be imported." : "Off. Upload files instead, or install yt-dlp.")}
        ${ck(s.faster_whisper, "Automatic transcripts (faster-whisper)", s.faster_whisper ? `Uses the “${esc(s.whisper_model)}” model${s.whisper_model_cached ? "" : ", downloaded on first use"}.` : "Off. Upload .srt or .vtt transcripts to search speech.")}
        ${ck(s.face_model, "Center on face", s.face_model ? "Speaker crops can find the face." : "Off. Drag the frame by hand; each recording remembers it.")}
        ${ck(true, "Ranking", esc(s.ranker))}
      </div>
      <p class="small faint">Data folder: ${esc(s.data_dir)}</p>
    </div>
    <form id="setForm" class="panel" style="margin-top:16px">
      <div class="panel-head"><h2>Preferences</h2></div>
      <div class="row" style="align-items:flex-start;gap:16px">
        <div class="grow"><label for="wm">Transcript accuracy</label><select id="wm">${[["tiny", "Fastest (tiny)"], ["base", "Balanced (base)"], ["small", "Better (small)"], ["medium", "Best, slow (medium)"]].map(([m, l]) => `<option value="${m}" ${st.whisper_model === m ? "selected" : ""}>${l}</option>`).join("")}</select></div>
        <div class="grow"><label for="mp">Ranking</label><select id="mp">${[["auto", "Use an AI key if one is set"], ["local", "Local only"], ["anthropic", "Anthropic"], ["openai", "OpenAI"]].map(([v, l]) => `<option value="${v}" ${st.model_provider === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
      </div>
      <div class="row" style="align-items:flex-start;gap:16px;margin-top:12px">
        <div class="grow"><label for="dl">Default framing</label><select id="dl">${[["speaker", "Speaker"], ["fit", "Whole frame"], ["gameplay", "Game + cam"]].map(([m, l]) => `<option value="${m}" ${st.default_layout === m ? "selected" : ""}>${l}</option>`).join("")}</select></div>
        <div class="grow"><label for="cs">Caption size</label><input id="cs" type="number" min="40" max="110" value="${st.caption_size}" inputmode="numeric"><p class="help">Pixels on the 1080-wide video.</p></div>
      </div>
      <p class="help">AI keys are read from ANTHROPIC_API_KEY or OPENAI_API_KEY when ClipDesk starts. They're never saved or logged.</p>
      <button class="btn primary" id="setBtn" data-busy="Saving…" style="margin-top:16px">${icon("check")}Save preferences</button>
    </form></div>`;
  $("#setForm").onsubmit = (e) => { e.preventDefault(); busy($("#setBtn"), async () => { try { await api("/api/settings", { method: "PUT", json: { whisper_model: $("#wm").value, model_provider: $("#mp").value, default_layout: $("#dl").value, caption_size: +$("#cs").value } }); toast("Preferences saved"); renderSettings(); } catch (er) { fail(er); } }); };
}

/* ================================================================== JOBS */
const watchers = new Map();
function watchJob(id, cb) { watchers.set(id, cb); }
const JOB = { import_url: "Importing link", import_file: "Importing file", transcribe: "Transcribing", discover: "Finding moments", render: "Rendering", export: "Exporting ZIP" };
async function pollJobs() {
  let jobs = [];
  try { jobs = await api("/api/jobs"); }
  catch { $("#tray").innerHTML = `<button class="tray-btn" type="button"><span class="dot bad"></span>Can't reach ClipDesk. Is it still running?</button>`; setTimeout(pollJobs, 3000); return; }
  const active = jobs.filter((j) => ["queued", "running", "cancelling"].includes(j.status));
  const failed = jobs.filter((j) => j.status === "failed" && Date.now() / 1000 - j.updated < 900 && !S.dismissed.has(j.id + j.attempt));
  let changed = false;
  const pollStart = Date.now() / 1000;
  for (const j of jobs) {
    const prev = S.seenJobs.get(j.id), finished = ["done", "failed", "cancelled"].includes(j.status);
    const fresh = prev === undefined && finished && S.lastPoll && j.updated >= S.lastPoll - 1;
    if ((prev && prev !== j.status && finished) || fresh) {
      changed = true;
      if (j.status === "failed") toast(`${JOB[j.kind]} failed: ${esc(j.error.slice(0, 160))}`, { err: true, ms: 8000 });
      else if (j.status === "done" && ["discover", "import_file", "import_url", "transcribe"].includes(j.kind)) toast(`${JOB[j.kind]} finished. ${esc(j.message)}`);
      const cb = watchers.get(j.id); if (cb && j.status === "done") { watchers.delete(j.id); cb(j); }
    }
    S.seenJobs.set(j.id, j.status);
  }
  S.lastPoll = pollStart;
  drawTray(active, failed);
  if (changed) refreshView(); else if (active.length) liveProgress(active);
  updatePending();
  setTimeout(pollJobs, active.length ? 1000 : 3000);
}
function drawTray(active, failed) {
  const tray = $("#tray");
  if (!active.length && !failed.length) { tray.innerHTML = ""; S.trayOpen = false; return; }
  const label = active.length ? `${active.length} ${active.length === 1 ? "task" : "tasks"} running` : `${failed.length} failed`;
  tray.innerHTML = `${S.trayOpen ? `<div class="tray-list" id="trayList">${[...active, ...failed].map((j) => `<div class="job ${j.status}">
      <div class="top">${JOB[j.kind]}<span class="grow"></span>${pill(j.status, j.status === "failed" ? "Failed" : j.status === "queued" ? "Waiting" : "Working")}</div>
      <p class="msg">${esc(j.status === "failed" ? j.error : j.message)}</p>
      ${j.status !== "failed" ? `<div class="progress"><i style="width:${(j.progress * 100).toFixed(0)}%"></i></div>` : ""}
      <div class="row" style="margin-top:8px">${j.status === "failed" ? `<button class="btn sm" data-jretry="${j.id}">${icon("refresh")}Try again</button><button class="btn sm quiet" data-jdismiss="${j.id + j.attempt}">Dismiss</button>` : `<button class="btn sm quiet" data-jcancel="${j.id}">${icon("x")}Cancel</button>`}</div></div>`).join("")}</div>` : ""}
    <button class="tray-btn" id="trayBtn" type="button" aria-expanded="${S.trayOpen}" aria-controls="trayList"><span class="dot ${active.length ? "" : "bad"}"></span>${label}</button>`;
  $("#trayBtn").onclick = () => { S.trayOpen = !S.trayOpen; drawTray(active, failed); };
}
function liveProgress(active) {
  if (S.view === "home" && active.some((j) => j.kind.startsWith("import"))) refreshView();
  if (S.view === "ws" && S.ws) {
    const ws = S.ws;
    if (active.some((j) => j.kind === "discover" && j.source_id === ws.sid)) api(`/api/sources/${ws.sid}`).then((s) => S.ws === ws && renderResults(s.requests));
    if (!ws.src.has_media || ws.src.status !== "ready") refreshView();
  }
}
let refreshing = false;
async function refreshView() {
  if (refreshing) return; refreshing = true;
  try {
    if (S.view === "home") await renderHome();
    else if (S.view === "ws" && S.ws) {
      const ws = S.ws, s = await api(`/api/sources/${ws.sid}`);
      if (S.ws !== ws) return;
      if (s.status !== ws.src.status || s.transcript_status !== ws.src.transcript_status || s.chat_status !== ws.src.chat_status || !ws.video) await route();
      else { renderResults(s.requests); await loadClips(ws.clip?.id); }
    } else if (S.view === "queue") await renderQueue();
  } finally { refreshing = false; }
}
async function updatePending() { try { const p = await api("/api/clips?status=pending"); $("#pendingCount").textContent = p.length || ""; } catch { /* offline */ } }
document.addEventListener("click", async (e) => {
  const c = e.target.closest("[data-jcancel]"), r = e.target.closest("[data-jretry]"), d = e.target.closest("[data-jdismiss]");
  try {
    if (c) { await api(`/api/jobs/${c.dataset.jcancel}/cancel`, { json: {} }); toast("Cancelling"); }
    if (r) { await api(`/api/jobs/${r.dataset.jretry}/retry`, { json: {} }); toast("Trying again"); }
    if (d) { S.dismissed.add(d.dataset.jdismiss); d.closest(".job").remove(); }
  } catch (er) { fail(er); }
});

/* ============================================================== KEYBOARD */
function typing(el) { return el && (el.isContentEditable || (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) && !["checkbox", "range", "radio"].includes(el.type))); }
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && typing(document.activeElement)) { document.activeElement.blur(); return; }
  if (typing(e.target) || e.altKey) return;
  const mod = e.ctrlKey || e.metaKey, k = e.key;
  if (mod && k.toLowerCase() === "z") { e.preventDefault(); S.view === "ws" ? undo() : S.view === "queue" && qUndo(); return; }
  if (mod) return;
  if (k === "?") { $("#helpDlg").showModal(); return; }
  if (S.view === "queue" && S.q) {
    const q = S.q, key = k.length === 1 ? k.toLowerCase() : k;
    if (!q.clips?.length) return;
    if (key === "j" || key === "ArrowDown") { e.preventDefault(); q.idx = clamp(q.idx + 1, 0, q.clips.length - 1); q.lastActed = null; qSelect(); $(".qrow[aria-selected=true]")?.focus(); }
    else if (key === "k" || key === "ArrowUp") { e.preventDefault(); q.idx = clamp(q.idx - 1, 0, q.clips.length - 1); q.lastActed = null; qSelect(); $(".qrow[aria-selected=true]")?.focus(); }
    else if (key === "a") qStatus("approved"); else if (key === "r") qStatus("rejected"); else if (key === "u") qUndo();
    else if (key === "v") qRender($("#qV")); else if (key === "e") queueExport();
    else if (key === "x" && q.cur) { const cb = $(`[data-chk="${q.cur.id}"]`); if (cb) { cb.checked = !cb.checked; cb.onchange(); } }
    return;
  }
  const ws = S.ws; if (S.view !== "ws" || !ws?.video) return;
  if (e.target.closest && e.target.closest(".handle") && (k === "ArrowLeft" || k === "ArrowRight")) return;
  if (k === " " && e.target.tagName === "BUTTON") return;
  const v = ws.video;
  const act = {
    " ": () => (v.paused ? v.play() : v.pause()), p: playSelection,
    i: () => setSel(v.currentTime, Math.max(ws.sel.e, v.currentTime + 1)), o: () => setSel(ws.sel.s, v.currentTime),
    ArrowLeft: () => (v.currentTime -= e.shiftKey ? 5 : 1), ArrowRight: () => (v.currentTime += e.shiftKey ? 5 : 1),
    "[": () => setSel(ws.sel.s - 0.2, ws.sel.e), "]": () => setSel(ws.sel.s + 0.2, ws.sel.e),
    "{": () => setSel(ws.sel.s, ws.sel.e - 0.2), "}": () => setSel(ws.sel.s, ws.sel.e + 0.2),
    g: () => $("#goTC").focus(), "/": () => { if (isPhone()) setMtab("find"); $("#findText").focus(); },
    a: () => setStatus("approved"), r: () => setStatus("rejected"), u: undo, v: () => renderPreview(), e: () => exportClip(),
    j: () => stepClip(1), k: () => stepClip(-1),
  }[k.length === 1 && /[a-z]/i.test(k) ? k.toLowerCase() : k];
  if (act) { e.preventDefault(); act(); }
});
function stepClip(d) { const ws = S.ws; if (!ws.clips.length) return; const i = ws.clips.findIndex((c) => c.id === ws.clip?.id); selectClip(ws.clips[clamp(i + d, 0, ws.clips.length - 1)].id, true); }
$("#helpBtn").onclick = () => $("#helpDlg").showModal();
matchMedia("(max-width: 899px)").addEventListener("change", () => { if (S.view === "ws" && S.ws?.clip) renderEditor(); });

/* ================================================================== BOOT */
(async function boot() {
  try { await loadSystem(); } catch (e) { $("#main").innerHTML = `<div class="panel empty">${icon("alert")}<h2>Can't reach ClipDesk</h2><p>${esc(e.message)}. Start it with python run.py and reload this page.</p></div>`; return; }
  await route(); pollJobs();
})();
