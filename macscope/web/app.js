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
  "viewer.requested":"Live-Bild angefragt", "viewer.refused":"Live-Bild abgelehnt"
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
  const data = await response.json();
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
  $("viewerNote").hidden = caps.kind !== "external";
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
    } else if (session.demo) {
      notify("Demo: Hier würde sich die Bildschirmfreigabe öffnen.");
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
function disconnect() {
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
