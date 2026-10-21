import { $, api, esc, fmt, short, renderBody, hasRemoteContent, KIND_LABEL, STATUS_LABEL, ACTION_LABEL } from "./util.js";
import { ui, setActions } from "./state.js";

const MODES = { REVIEW: "Needs review", DELETE: "Suggested delete", ALL: "Everyone" };
let queue = [];
let idx = 0;
let mode = "REVIEW";
let token = 0;

const senderKey = (s) => `${s.account}|${s.from_addr}`;
const current = () => ui.senders.find((s) => senderKey(s) === queue[idx]);

function inScope(s, m) {
  return (!ui.account || s.account === ui.account) && (m === "ALL" || s.action === m);
}

function buildQueue() {
  queue = ui.senders.filter((s) => inScope(s, mode)).sort((a, b) => b.count - a.count).map(senderKey);
  idx = 0;
}

export function openReview(startMode = "REVIEW") {
  mode = startMode;
  buildQueue();
  $("#review").hidden = false;
  document.addEventListener("keydown", onKey, true);
  render();
}

function closeReview() {
  $("#review").hidden = true;
  document.removeEventListener("keydown", onKey, true);
  ui.refresh();
}

function onKey(ev) {
  if (ev.target.closest?.("input, select, textarea") || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  const key = ev.key.toLowerCase();
  const map = { d: "DELETE", k: "KEEP" };
  if (map[key]) decide(map[key]);
  else if (key === "s" || key === "arrowright") step(1);
  else if (key === "b" || key === "arrowleft") step(-1);
  else if (key === "escape") closeReview();
  else return;
  ev.preventDefault();
  ev.stopPropagation();
}

function step(delta) {
  idx = Math.max(0, Math.min(queue.length, idx + delta));
  render();
}

async function decide(action) {
  const s = current();
  if (!s) return;
  await setActions([s], action, { silent: true });
  step(1);
}

function render() {
  const counts = Object.fromEntries(Object.keys(MODES).map((m) => [m, ui.senders.filter((s) => inScope(s, m)).length]));
  const done = Math.min(idx, queue.length);
  $("#review").innerHTML = `<div class="xp-window">
    <div class="xp-title"><span>🔎 Review senders${ui.account ? " — " + esc(short(ui.account)) : ""}</span>
      <button class="xp-close" data-close title="Close (Esc)">✕</button></div>
    <div class="r-tabs">${Object.entries(MODES).map(([m, label]) =>
        `<button data-mode="${m}" class="${m === mode ? "on" : ""}">${label} (${fmt(counts[m])})</button>`).join("")}
      <div class="r-progress"><div class="progress"><div style="width:${queue.length ? (done / queue.length) * 100 : 100}%"></div></div>
        <span>${fmt(done)} of ${fmt(queue.length)}</span></div>
    </div>
    <div class="r-body"></div>
    <div class="r-buttons">
      <button data-step="-1">« Back <kbd>B</kbd></button>
      <span class="spacer"></span>
      <button class="danger" data-act="DELETE">✖ Delete sender <kbd>D</kbd></button>
      <button class="good" data-act="KEEP">✔ Keep sender <kbd>K</kbd></button>
      <button data-step="1">Skip » <kbd>S</kbd></button>
      <button data-close>Done</button>
    </div></div>`;
  const root = $("#review");
  root.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeReview));
  root.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => { mode = b.dataset.mode; buildQueue(); render(); }));
  root.querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => decide(b.dataset.act)));
  root.querySelectorAll("[data-step]").forEach((b) => (b.onclick = () => step(Number(b.dataset.step))));
  const s = current();
  const body = root.querySelector(".r-body");
  if (!s) {
    body.innerHTML = `<div class="r-card" style="grid-column:1/-1;align-items:center;justify-content:center">
      <h3>${queue.length ? "All done 🎉" : "Nothing here"}</h3>
      <p class="muted">${queue.length ? "You've been through every sender in this list." : "No senders in this list."}
      Press <b>Clean up</b> when you're happy with the decisions.</p></div>`;
    root.querySelectorAll("[data-act]").forEach((b) => (b.disabled = true));
    return;
  }
  const kept = Object.entries(s.kinds).filter(([, n]) => n)
    .map(([k, n]) => `<span class="chip ${k}">${KIND_LABEL[k]} ${fmt(n)}</span>`).join("");
  body.innerHTML = `<div class="r-card">
      <div><span class="pill ${s.action}">${ACTION_LABEL[s.action]}</span> <span class="muted">${esc(s.override ? "changed by you" : s.reason)}</span></div>
      <h3>${esc(s.from_name || s.from_addr || "(no sender)")}</h3>
      <div class="addr">${esc(s.from_addr)} · ${esc(s.account)}</div>
      <div>${s.is_contact ? '<span class="chip contact">you emailed them</span>' : ""}${s.unsub ? '<span class="chip">mailing list</span>' : ""}${s.spammy ? '<span class="chip">looks like spam</span>' : ""}</div>
      <div class="big"><div><b>${fmt(s.count)}</b><span>emails</span></div>
        <div><b>${fmt(s.deletable)}</b><span>would be deleted</span></div>
        <div><b>${fmt(s.count - s.deletable)}</b><span>protected (stays)</span></div></div>
      ${kept ? `<div>${kept}</div>` : ""}
      ${s.unsub_url ? `<a href="${esc(s.unsub_url)}" target="_blank" rel="noopener noreferrer">Unsubscribe from this sender ↗</a>` : ""}
      <ul class="r-subjects"><li class="muted">Loading…</li></ul>
    </div>
    <div class="r-preview"><div class="muted">Loading latest email…</div></div>`;
  loadSender(s);
}

async function loadSender(s) {
  const mine = ++token;
  const { items } = await api("/api/emails", { account: s.account, from_addrs: [s.from_addr], limit: 12 });
  if (mine !== token) return;
  $("#review .r-subjects").innerHTML = items.map((m) =>
    `<li title="${esc(m.subject)}"><small>${esc(m.date.slice(0, 10))}</small><span>${esc(m.subject || "(no subject)")}</span>
      <span class="pill ${m.status}">${STATUS_LABEL[m.status]}</span></li>`).join("");
  const preview = $("#review .r-preview");
  if (!items.length) { preview.innerHTML = ""; return; }
  let msg;
  try {
    msg = await api(`/api/message?account=${encodeURIComponent(s.account)}&msg_id=${encodeURIComponent(items[0].msg_id)}`);
  } catch (e) {
    msg = { error: e.message };
  }
  if (mine !== token) return;
  if (msg.error) { preview.innerHTML = `<div class="muted">${esc(msg.error)}</div>`; return; }
  preview.innerHTML = `<div class="muted">Latest email: <b>${esc(items[0].subject)}</b>
      ${hasRemoteContent(msg) ? ' · images blocked <button class="link" data-images>Show</button>' : ""}</div><iframe title="Latest email"></iframe>`;
  const frame = preview.querySelector("iframe");
  renderBody(frame, msg, false);
  const btn = preview.querySelector("[data-images]");
  if (btn) btn.onclick = () => { renderBody(frame, msg, true); btn.remove(); };
}
