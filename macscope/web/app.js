"use strict";
const $ = id => document.getElementById(id);
let token = "", timer = null, busy = false, rows = [], latest = 0, generation = 0;
let historyMode = false, selectedApp = null, currentStatus = {}, searchTimer = null;
let viewerCaps = null, pollCount = 0;
const pending = new Set();
const names = {
  "agent.started":"Agent gestartet", "agent.stopped":"Agent beendet",
  "app.observed":"Beim Agent-Start vorhanden", "app.launched":"App gestartet",
  "app.terminated":"App beendet", "app.activated":"App aktiviert",
  "system.sleep":"Ruhezustand", "system.wake":"Aufgewacht",
  "session.active":"Benutzersitzung aktiv", "session.inactive":"Benutzersitzung inaktiv",
  "recording.paused":"Aufzeichnung pausiert", "recording.resumed":"Aufzeichnung fortgesetzt",
  "action.queued":"Beenden angefragt", "action.requested":"Beenden-Anfrage übermittelt",
  "action.terminated":"App-Ende bestätigt", "action.not_running":"App-Instanz läuft nicht mehr",
  "action.refused":"Beenden-Anfrage abgelehnt", "action.unconfirmed":"App-Ende nicht bestätigt",
  "action.failed":"Fernaktion fehlgeschlagen", "action.expired":"Fernaktion abgelaufen",
  "viewer.requested":"Live-Bild angefragt", "viewer.refused":"Live-Bild abgelehnt",
  "viewer.opened":"Live-Bild verbunden", "viewer.closed":"Live-Bild getrennt"
};
const modeLabels = {view:"Ansehen", control:"Steuern"};
function element(tag, cls, content) {
  const item = document.createElement(tag);
  if (cls) item.className = cls;
  if (content !== undefined) item.textContent = content;
  return item;
}
function notify(message) { $("message").textContent = message; $("message").hidden = false; }
async function api(path, options = {}) {
  const response = await fetch(path, {...options, cache:"no-store", headers:{
    "Authorization":"Bearer " + token,
    ...(options.body ? {"Content-Type":"application/json"} : {}),
    ...options.headers
  }});
  const data = response.status === 204 ? {} : await response.json();
  if (!response.ok) {
    if (response.status === 401 && !$("dashboard").hidden) disconnect();
    throw new Error(data.error || "Verbindung fehlgeschlagen.");
  }
  return data;
}
function query() {
  return new URLSearchParams({q:$("search").value.trim(), kind:$("kind").value, limit:"100"});
}
function renderEvents() {
  const list = $("events"); list.replaceChildren();
  rows.sort((a,b) => b.id-a.id);
  for (const row of rows) {
    const action = row.kind.startsWith("action.") || row.kind.startsWith("viewer.");
    const li = element("li", "event" + (action ? " action" : ""));
    const date = new Date(row.timestamp * 1000);
    const time = element("time", "", date.toLocaleTimeString("de-AT", {hour:"2-digit", minute:"2-digit", second:"2-digit"}));
    time.dateTime = date.toISOString();
    time.append(element("small", "", date.toLocaleDateString("de-AT", {day:"2-digit", month:"2-digit"})));
    const mark = element("span", "eventmark", row.kind.startsWith("viewer.") ? "◎" : action ? "↗" : row.kind === "app.activated" ? "●" : row.kind.includes("terminated") ? "−" : "+");
    mark.setAttribute("aria-hidden", "true");
    const body = element("div");
    body.append(element("strong", "", row.app_name || "iMac"));
    body.append(element("span", "tag", names[row.kind] || row.kind));
    const meta = [row.bundle_id, row.pid ? "PID " + row.pid : null, row.details.message].filter(Boolean).join(" · ");
    body.append(element("div", "eventmeta", meta || names[row.kind] || row.kind));
    li.append(time, mark, body); list.append(li);
  }
  $("empty").hidden = rows.length > 0;
  $("resultInfo").textContent = rows.length + " Ereignisse angezeigt · " + (historyMode ? "Historische Ansicht" : "Neueste Ereignisse");
  $("liveLabel").replaceChildren(element("span"), document.createTextNode(historyMode ? "Verlauf" : "Live"));
}
function mergeEvents(newRows, limit) {
  const byId = new Map(rows.map(row => [row.id,row]));
  for (const row of newRows) byId.set(row.id,row);
  rows = [...byId.values()].sort((a,b) => b.id-a.id);
  if (rows.length > limit) {
    rows = historyMode ? rows.slice(-limit) : rows.slice(0,limit);
    $("older").hidden = false;
  }
  renderEvents();
}
async function loadLatest() {
  const version = ++generation;
  const result = await api("/api/events?" + query());
  if (version !== generation || !token) return;
  historyMode = false; rows = result.events;
  latest = rows.reduce((max,row) => Math.max(max,row.id),0);
  $("older").hidden = !result.has_more;
  renderEvents();
}
async function loadOlder() {
  if (!rows.length) return;
  const version = generation;
  const params = query(); params.set("before", String(Math.min(...rows.map(row => row.id))));
  $("older").disabled = true;
  try {
    const result = await api("/api/events?" + params);
    if (version !== generation || !token) return;
    historyMode = true;
    mergeEvents(result.events,2000);
    $("older").hidden = !result.has_more;
  } catch(error) { notify(error.message); }
  finally { $("older").disabled = false; }
}
function renderApps(apps) {
  $("apps").replaceChildren();
  $("appcount").textContent = apps.length;
  for (const app of apps) {
    const row = element("div","app"), info = element("div");
    const label = element("div","appname",app.name);
    if (currentStatus.active_app?.pid === app.pid) label.append(element("span","active-dot","●"));
    info.append(label, element("div","small",app.bundle_id || "Ohne Bundle-ID"));
    row.append(info);
    if (app.can_quit && currentStatus.agent_online) {
      const button = element("button","secondary","Beenden");
      button.addEventListener("click", () => {
        selectedApp = app;
        $("quitTitle").textContent = app.name + " beenden?";
        $("confirmQuit").disabled = false;
        $("quitDialog").showModal();
      });
      row.append(button);
    }
    $("apps").append(row);
  }
  if (!apps.length) $("apps").append(element("p","small","Keine geöffneten Anwendungen gemeldet."));
}
function renderViewer() {
  const caps = viewerCaps, buttons = $("viewerButtons");
  buttons.replaceChildren();
  const ready = Boolean(caps?.available && currentStatus.agent_online);
  $("viewerState").textContent = !caps ? "—" : ready ? "Bereit" : "Nicht verfügbar";
  $("viewerState").className = "pill" + (ready ? " online" : "");
  $("viewerReason").textContent = caps?.reason || (caps && !currentStatus.agent_online ? "Agent antwortet nicht." : "");
  $("viewerReason").hidden = !$("viewerReason").textContent;
  const connected = caps?.connected || [];
  $("viewerConnected").textContent = connected.length ? "Gerade verbunden: " + connected.map(c =>
    c.peer + " seit " + new Date(c.since * 1000).toLocaleTimeString("de-AT", {hour:"2-digit", minute:"2-digit"})).join(", ") : "";
  $("viewerConnected").hidden = !connected.length;
  if (!caps || caps.kind === "none") return;
  // Ohne erzwungene Modi (Apple) gibt es nur einen Knopf; der Modus wird in Apples App gewählt.
  const modes = caps.modes_enforced ? caps.modes : ["control"];
  for (const mode of modes) {
    const label = caps.modes_enforced ? modeLabels[mode] : "Bildschirmfreigabe öffnen";
    const button = element("button", mode === "view" ? "secondary" : "", label);
    button.disabled = !ready;
    button.addEventListener("click", () => openViewer(mode, button));
    buttons.append(button);
  }
  $("viewerNote").textContent = caps.kind === "embedded"
    ? "Das Bild erscheint hier im Dashboard. Jede Sitzung erscheint in der Zeitleiste."
    : "Öffnet Apples Bildschirmfreigabe. Ansehen oder Steuern wählst du dort. Jede Anfrage erscheint in der Zeitleiste.";
  $("viewerNote").hidden = !["external", "embedded"].includes(caps.kind);
}
async function loadViewer() {
  viewerCaps = await api("/api/viewer");
  renderViewer();
}
async function openViewer(mode, button) {
  button.disabled = true;
  try {
    const session = await api("/api/viewer/session", {method:"POST", body:JSON.stringify({mode})});
    if (session.kind === "external" && session.url) {
      window.location.assign(session.url);
      notify("Bildschirmfreigabe wird geöffnet …");
    } else if (session.kind === "embedded" && session.stream) {
      openStage(session);
    } else {
      notify("Dieser Betrachter wird in dieser Version noch nicht unterstützt.");
    }
    await poll();
  } catch(error) {
    notify(error.message);
    loadViewer().catch(() => {});
  } finally { button.disabled = false; renderViewer(); }
}
async function poll() {
  if (busy || !token) return;
  busy = true;
  const version = generation;
  try {
    const [status, apps] = await Promise.all([api("/api/status"), api("/api/apps")]);
    if (!token || version !== generation) return;
    currentStatus = status;
    $("connection").textContent = status.agent_online ? "● Verbunden" : "Agent antwortet nicht";
    $("connection").className = "pill" + (status.agent_online ? " online" : "");
    $("hostline").textContent = status.hostname + " · " + status.platform;
    $("demo").hidden = !status.demo; $("paused").hidden = !status.paused;
    $("offline").hidden = status.agent_online;
    $("active").textContent = status.active_app?.name || "—";
    $("activeBundle").textContent = status.active_app?.bundle_id || "Keine aktive Anwendung gemeldet";
    $("retention").textContent = status.retention_days + " Tage";
    $("scope").textContent = status.scope;
    renderApps(apps.apps);
    if (!viewerCaps || ++pollCount % 10 === 0) await loadViewer();
    else renderViewer();
    if (!historyMode) {
      const params = query(); params.set("after", String(latest));
      const result = await api("/api/events?" + params);
      if (!token || version !== generation) return;
      for (const row of result.events) latest = Math.max(latest,row.id);
      const hadRows = rows.length > 0;
      mergeEvents(result.events,500);
      if (!hadRows && result.events.length) $("older").hidden = false;
    }
    for (const id of pending) {
      const command = await api("/api/commands/" + id);
      if (!token || version !== generation) return;
      if (!["queued","requested"].includes(command.status)) {
        pending.delete(id); notify(command.message);
      }
    }
  } catch(error) {
    if (token) {
      $("connection").textContent = "Verbindung unterbrochen";
      $("connection").className = "pill";
      $("offline").hidden = false;
      $("apps").querySelectorAll("button").forEach(button => { button.disabled = true; });
      $("viewerButtons").querySelectorAll("button").forEach(button => { button.disabled = true; });
      notify(error.message);
    }
  } finally { busy = false; }
}
// --- Eingebetteter Betrachter (Viewer-Naht, Schicht 2; Protokoll in macscope/stream.py) ---
const STREAM = {HELLO:0x01, FRAME:0x02, CURSOR:0x03, DISPLAY:0x04, BYE:0x05,
                START:0x10, POINTER:0x11, WHEEL:0x12, KEY:0x13, KEYFRAME:0x14, STOP:0x15};
const CODECS = {1:"image/jpeg", 2:"image/png"};
const FRAME_HEADER = 11;
let stage = null;
function streamMessage(type, payload) {
  const json = new TextEncoder().encode(JSON.stringify(payload || {}));
  const message = new Uint8Array(1 + json.length);
  message[0] = type; message.set(json, 1);
  return message;
}
function sendStream(type, payload) {
  if (stage && stage.ws.readyState === WebSocket.OPEN) stage.ws.send(streamMessage(type, payload));
}
function stageInfo() {
  if (!stage) return;
  const parts = [stage.source === "demo" ? "Demo-Quelle" : null, stage.width ? stage.width + " × " + stage.height : null,
                 stage.frames + " Bilder", stage.mode === "control" ? "Eingaben aktiv" : "nur ansehen"];
  $("stageInfo").textContent = stage.ended ? (stage.bye || "Verbindung beendet") + " · " + stage.frames + " Bilder"
    : parts.filter(Boolean).join(" · ");
}
function openStage(session) {
  closeStage(false);
  const ws = new WebSocket("ws://" + location.host + session.stream);
  ws.binaryType = "arraybuffer";
  stage = {ws, mode:session.mode, frames:0, drawing:false, pending:null, ended:false, bye:null, pointer:null};
  $("stage").hidden = false;
  $("stageTitle").textContent = "Bildschirm · " + modeLabels[session.mode];
  $("screen").classList.toggle("control", session.mode === "control");
  $("stageInfo").textContent = "Verbinde …";
  ws.addEventListener("open", () => sendStream(STREAM.START, {max_fps:10, codecs:["jpeg", "png"]}));
  ws.addEventListener("message", event => onStream(new Uint8Array(event.data), ws));
  ws.addEventListener("close", () => {
    if (stage && stage.ws === ws) { stage.ended = true; stageInfo(); }
    loadViewer().catch(() => {});
  });
  $("stage").scrollIntoView({block:"nearest"});
  if (session.mode === "control") $("screen").focus();
}
function onStream(data, ws) {
  if (!stage || stage.ws !== ws || !data.length) return;
  if (data[0] === STREAM.FRAME) {
    const type = CODECS[data[1]];
    if (!type || data.length <= FRAME_HEADER) return;
    stage.pending = new Blob([data.subarray(FRAME_HEADER)], {type});
    if (!stage.drawing) drawStage(stage);
    return;
  }
  let payload;
  try { payload = JSON.parse(new TextDecoder().decode(data.subarray(1)) || "{}"); } catch { return; }
  if (data[0] === STREAM.HELLO || data[0] === STREAM.DISPLAY) {
    const width = Number(payload.width), height = Number(payload.height);
    if (width > 0 && width <= 16384 && height > 0 && height <= 16384) {
      $("screen").width = stage.width = width; $("screen").height = stage.height = height;
    }
    if (data[0] === STREAM.HELLO) stage.source = String(payload.source || "");
    stageInfo();
  } else if (data[0] === STREAM.BYE) {
    stage.bye = "Beendet: " + String(payload.reason || "ohne Angabe");
  }
}
async function drawStage(current) {
  current.drawing = true;
  while (current.pending && stage === current) {
    const blob = current.pending; current.pending = null;
    try {
      const bitmap = await createImageBitmap(blob);
      const canvas = $("screen");
      canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      bitmap.close();
      current.frames++;
      stageInfo();
    } catch { sendStream(STREAM.KEYFRAME); }
  }
  current.drawing = false;
}
function closeStage(release = true) {
  if (!stage) return;
  const ws = stage.ws;
  sendStream(STREAM.STOP);
  stage = null;
  ws.close();
  $("stage").hidden = true;
  if (release) api("/api/viewer/session", {method:"DELETE"}).catch(() => {}).finally(() => loadViewer().catch(() => {}));
}
function pointerPosition(event) {
  const box = $("screen").getBoundingClientRect();
  return {x: Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)),
          y: Math.min(1, Math.max(0, (event.clientY - box.top) / box.height)), buttons: event.buttons & 31};
}
function controlling() { return stage && stage.mode === "control" && !stage.ended; }
for (const name of ["pointermove", "pointerdown", "pointerup"]) {
  $("screen").addEventListener(name, event => {
    if (!controlling()) return;
    if (name === "pointerdown") $("screen").focus();
    const first = stage.pointer === null;
    stage.pointer = pointerPosition(event);
    if (name !== "pointermove") { sendStream(STREAM.POINTER, stage.pointer); stage.pointer = null; }
    else if (first) requestAnimationFrame(() => { if (stage && stage.pointer) { sendStream(STREAM.POINTER, stage.pointer); stage.pointer = null; } });
  });
}
$("screen").addEventListener("wheel", event => {
  if (!controlling()) return;
  event.preventDefault();
  sendStream(STREAM.WHEEL, {dx: Math.max(-10000, Math.min(10000, event.deltaX)), dy: Math.max(-10000, Math.min(10000, event.deltaY))});
}, {passive:false});
for (const name of ["keydown", "keyup"]) {
  $("screen").addEventListener(name, event => {
    if (!controlling() || !event.code) return;
    event.preventDefault();
    sendStream(STREAM.KEY, {code: event.code.slice(0, 32), down: name === "keydown", alt: event.altKey,
                            ctrl: event.ctrlKey, meta: event.metaKey, shift: event.shiftKey});
  });
}
$("stageClose").addEventListener("click", () => closeStage());

function disconnect() {
  closeStage(false);
  token = ""; generation++; pending.clear(); rows = []; latest = 0; viewerCaps = null; pollCount = 0;
  selectedApp = null; clearTimeout(searchTimer);
  clearInterval(timer); timer = null;
  $("token").value = ""; $("dashboard").hidden = true; $("login").hidden = false;
  $("connection").textContent = "Nicht verbunden"; $("connection").className = "pill";
  $("message").hidden = true; $("events").replaceChildren(); $("apps").replaceChildren();
  $("viewerButtons").replaceChildren();
  if ($("quitDialog").open) $("quitDialog").close();
}
async function connect() {
  token = $("token").value.trim();
  if (!token) { notify("Bitte den Zugriffsschlüssel eingeben."); return; }
  $("connect").disabled = true;
  try {
    await api("/api/status");
    $("token").value = ""; $("login").hidden = true; $("dashboard").hidden = false;
    $("message").hidden = true;
    await loadLatest(); await poll();
    clearInterval(timer); timer = setInterval(poll,3000);
  } catch(error) { disconnect(); notify(error.message); }
  finally { $("connect").disabled = false; }
}
$("connect").addEventListener("click",connect);
$("token").addEventListener("keydown",event => { if (event.key === "Enter") connect(); });
$("disconnect").addEventListener("click",disconnect);
$("older").addEventListener("click",loadOlder);
$("refresh").addEventListener("click",() => loadLatest().catch(error => notify(error.message)));
$("kind").addEventListener("change",() => loadLatest().catch(error => notify(error.message)));
$("search").addEventListener("input",() => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadLatest().catch(error => notify(error.message)),300);
});
$("cancelQuit").addEventListener("click",() => $("quitDialog").close());
$("confirmQuit").addEventListener("click",async () => {
  if (!selectedApp) return;
  $("confirmQuit").disabled = true;
  try {
    const result = await api("/api/commands/quit", {method:"POST",body:JSON.stringify({
      pid:selectedApp.pid,bundle_id:selectedApp.bundle_id,launched_at:selectedApp.launched_at
    })});
    pending.add(result.command_id);
    $("quitDialog").close();
    notify("Beenden angefragt. Das tatsächliche App-Ende wird geprüft …");
    await poll();
  } catch(error) { $("quitDialog").close(); notify(error.message); }
  finally { $("confirmQuit").disabled = false; selectedApp = null; }
});
