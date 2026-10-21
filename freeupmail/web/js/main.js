import { $, $$, api, debounce, esc, fmt, short, toast } from "./util.js";
import {
  ui, listeners, loadState, loadSenders, buildGroups, baseGroups, matchesStatus, planTotal, setActions, membersFor,
} from "./state.js";
import { sendersHead, renderSenders, senderStats } from "./senders.js";
import { loadEmails, emailsHead, renderEmails, emailStats, emailKey } from "./emails.js";
import { showSender, showEmail, closeDetail, decide } from "./detail.js";
import { openReview } from "./review.js";
import { settingsDialog, accountsDialog, cleanupDialog, filtersDialog, helpDialog } from "./dialogs.js";
import { initLayout, setPane } from "./layout.js";

const narrow = () => window.innerWidth < 900;

const SENDER_STATUS = [["all", "All senders"], ["DELETE", "Delete"], ["REVIEW", "Needs review"], ["KEEP", "Keep"], ["MANUAL", "Changed by me"]];
const EMAIL_STATUS = [["all", "All emails"], ["delete", "Will be deleted"], ["protected", "Protected"], ["review", "Needs review"],
  ["keep", "Keep"], ["pinned", "Pinned by me"], ["trashed", "In Trash"]];
const CATEGORIES = [["", "Any"], ["promotions", "Promotions"], ["social", "Social"], ["updates", "Updates"],
  ["forums", "Forums"], ["other", "Outlook \"Other\""], ["none", "Primary / none"]];

// ---------- rendering ----------

function render() {
  renderSidebar();
  const senders = ui.view === "senders";
  $("#group").hidden = $("#lbl-unsub").hidden = $("#lbl-min").hidden = !senders;
  $("#lbl-unread").hidden = senders;
  $("#list thead").innerHTML = senders ? sendersHead() : emailsHead();
  const stats = senders ? senderStats(renderSenders()) : (renderEmails(), emailStats());
  const status = ui.jobRunning ? "Working…" : ui.selected.size ? `${fmt(ui.selected.size)} selected` : "Ready";
  $("#stats").innerHTML = `<div class="sb-cell grow">${status}</div>`
    + stats.map(([v, l, c]) => `<div class="sb-cell ${c || ""}"><b>${v}</b> ${l}</div>`).join("");
  $$("th[data-sort]").forEach((th) => {
    th.classList.toggle("sorted", th.dataset.sort === ui.sort.key);
    th.classList.toggle("asc", ui.sort.dir === 1);
  });
  const total = planTotal();
  $("#cleanup-label").textContent = total ? `Clean up (${fmt(total)})` : "Clean up";
  $("#btn-cleanup").disabled = !total || ui.jobRunning;
  $("#btn-scan").disabled = ui.jobRunning;
  renderBulk();
}
listeners.add(render);

function navButton(attr, value, label, on, extra = "") {
  return `<button data-${attr}="${esc(value)}" class="${on ? "on" : ""}">${label}${extra}</button>`;
}

function renderSidebar() {
  const totalMsgs = ui.accounts.reduce((n, a) => n + a.messages, 0);
  $("#nav-accounts").innerHTML = navButton("account", "", '<span class="acct-ico all">∗</span>All accounts', !ui.account, `<span class="n">${fmt(totalMsgs)}</span>`)
    + ui.accounts.map((a) => navButton("account", a.email,
      `<span class="acct-ico ${a.type}">${{ outlook: "O", gmail: "G" }[a.type] || a.email.split("@")[1][0].toUpperCase()}</span>${esc(short(a.email))}`
      + (a.status === "ok" ? "" : ' <span class="warn">⚠</span>'), ui.account === a.email, `<span class="n">${fmt(a.messages)}</span>`)).join("");
  $$("#nav-views button").forEach((b) => b.classList.toggle("on", b.dataset.view === ui.view));
  const base = ui.view === "senders" ? baseGroups() : null;
  const items = ui.view === "senders" ? SENDER_STATUS : EMAIL_STATUS;
  $("#nav-status").innerHTML = items.map(([v, label]) => {
    const n = base ? base.filter((g) => matchesStatus(g, v)).length
      : v === "trashed" ? ui.trashed : (ui.emails.counts || {})[v] ?? null;
    return navButton("status", v, `<span class="dot ${v}"></span>${label}`, ui.status === v, n === null ? "" : `<span class="n">${fmt(n)}</span>`);
  }).join("");
  $("#nav-cat").innerHTML = CATEGORIES.map(([v, label]) => navButton("cat", v, label, ui.cat === v)).join("");
}

function renderBulk() {
  const n = ui.selected.size;
  const bar = $("#bulk");
  bar.hidden = n === 0;
  if (!n) return;
  let buttons;
  if (ui.view === "senders") {
    buttons = `<button class="danger" data-bulk="DELETE">Delete</button><button data-bulk="REVIEW">Review</button>
      <button class="good" data-bulk="KEEP">Keep</button><button data-bulk="AUTO">Reset to suggestion</button>`;
  } else if (ui.status === "trashed") {
    buttons = '<button class="good" data-bulk="restore">Restore selected</button>';
  } else {
    buttons = `<button data-bulk="pin">📌 Always keep these emails</button><button data-bulk="unpin">Unpin</button>
      <button class="danger" data-bulk="DELETE">Delete their senders</button><button class="good" data-bulk="KEEP">Keep their senders</button>`;
  }
  bar.innerHTML = `<span class="count">${fmt(n)} selected</span>${buttons}<button class="link" data-bulk="clear">Clear</button>`;
}

// ---------- data refresh ----------

async function refresh() {
  await loadSenders();
  if (ui.view === "emails") await loadEmails();
  render();
}
ui.refresh = refresh;

function resetList() {
  ui.selected.clear();
  ui.cursor = null;
  closeDetail();
  $("#list-wrap").scrollTop = 0;
}

async function reloadList() {
  if (ui.view === "emails") {
    ui.emails.loading = true;
    render();
    await loadEmails();
  }
  render();
}

// ---------- selection & actions ----------

const keyOf = (item) => (ui.view === "senders" ? item.key : emailKey(item));
const findVisible = (key) => (ui.visible || []).find((x) => keyOf(x) === key);

function targets() {
  const keys = ui.selected.size ? [...ui.selected] : ui.cursor ? [ui.cursor] : [];
  return keys.map(findVisible).filter(Boolean);
}

function sendersOfEmails(items) {
  const wanted = new Set(items.map((m) => `${m.account}|${m.from_addr}`));
  return ui.senders.filter((s) => wanted.has(`${s.account}|${s.from_addr}`));
}

async function applyAction(action) {
  const items = targets();
  if (!items.length) return;
  if (ui.view === "senders") await decide(items, action);
  else await setActions(action === "DELETE" ? membersFor([{ members: sendersOfEmails(items) }], action) : sendersOfEmails(items), action);
  if (ui.view === "emails") await loadEmails();
  render();
  reopenCursor();
}

async function pinEmails(pinned) {
  const items = targets().filter((m) => m.msg_id && m.status !== "trashed");
  if (!items.length) return;
  await api("/api/pin", { items: items.map((m) => [m.account, m.msg_id]), pinned });
  toast(`${fmt(items.length)} email${items.length > 1 ? "s" : ""} ${pinned ? "pinned — never cleaned up" : "unpinned"}`);
  await refresh();
}

function restore(items) {
  ui.startJob("/api/restore", { items });
}
ui.restore = restore;

function openItem(item) {
  if (!item) return;
  if (narrow()) setPane("right", true);
  ui.cursor = keyOf(item);
  if (ui.view === "senders") showSender(item);
  else showEmail(item);
  $$("#list tbody tr").forEach((tr) => tr.classList.toggle("cursor", tr.dataset.key === ui.cursor));
}

function reopenCursor() {
  const item = ui.cursor && findVisible(ui.cursor);
  if (item) openItem(item);
}

function moveCursor(delta) {
  const list = ui.visible || [];
  if (!list.length) return;
  const i = list.findIndex((x) => keyOf(x) === ui.cursor);
  const next = list[Math.max(0, Math.min(list.length - 1, i < 0 ? 0 : i + delta))];
  openItem(next);
  document.querySelector(`#list tbody tr[data-key="${CSS.escape(keyOf(next))}"]`)?.scrollIntoView({ block: "nearest" });
}

// ---------- jobs & heartbeat ----------

async function watchJob() {
  const st = await api("/api/state").catch(() => null);
  if (!st) return;
  const { running, log } = st.job;
  const wasRunning = ui.jobRunning;
  ui.jobRunning = !!running;
  if (running || wasRunning) {
    $("#joblog").hidden = false;
    $("#job-title").textContent = running ? `Running: ${running}…` : "Finished";
    const pre = $("#job-lines");
    pre.textContent = log.join("\n");
    pre.scrollTop = pre.scrollHeight;
  }
  if (running) setTimeout(watchJob, 1200);
  else if (wasRunning) {
    await loadState();
    await refresh();
  }
  render();
}
ui.watchJob = watchJob;

async function startJob(path, body) {
  try {
    await api(path, body);
  } catch (e) {
    alert(e.message);
    return;
  }
  ui.jobRunning = true;
  $("#joblog").hidden = false;
  watchJob();
}
ui.startJob = startJob;

let missedPings = 0;
async function ping() {
  try {
    await api("/api/ping", {});
    missedPings = 0;
    $("#offline").hidden = true;
  } catch {
    if (++missedPings >= 2) $("#offline").hidden = false;
  }
}

// ---------- wiring ----------

function wire() {
  $("#sidebar").addEventListener("click", async (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    if ("account" in b.dataset) ui.account = b.dataset.account;
    else if ("view" in b.dataset) { ui.view = b.dataset.view; ui.status = "all"; }
    else if ("status" in b.dataset) ui.status = b.dataset.status;
    else if ("cat" in b.dataset) ui.cat = b.dataset.cat;
    else return;
    if (narrow()) setPane("left", false);
    resetList();
    await reloadList();
  });
  $("#btn-settings").onclick = settingsDialog;
  $("#btn-filters").onclick = filtersDialog;
  $("#btn-accounts").onclick = () => accountsDialog();
  $("#btn-help").onclick = helpDialog;
  $("#btn-review").onclick = () => openReview(ui.status === "DELETE" ? "DELETE" : "REVIEW");
  $("#btn-scan").onclick = () => startJob("/api/scan", { account: ui.account || null });
  $("#btn-cleanup").onclick = cleanupDialog;
  $("#job-close").onclick = () => ($("#joblog").hidden = true);

  const search = debounce(async () => { ui.q = $("#q").value; await reloadList(); }, 220);
  $("#q").addEventListener("input", search);
  $("#min").addEventListener("input", () => { ui.minCount = Number($("#min").value) || 1; render(); });
  $("#unsub-only").addEventListener("change", (e) => { ui.unsubOnly = e.target.checked; render(); });
  $("#inbox-only").addEventListener("change", async (e) => { ui.inboxOnly = e.target.checked; await reloadList(); });
  $("#unread-only").addEventListener("change", async (e) => { ui.unreadOnly = e.target.checked; await reloadList(); });
  $("#group").addEventListener("change", (ev) => {
    ui.group = ev.target.value;
    ui.groups = buildGroups();
    resetList();
    render();
  });

  $("#list thead").addEventListener("click", (ev) => {
    const th = ev.target.closest("th[data-sort]");
    if (!th) return;
    const key = th.dataset.sort;
    ui.sort = { key, dir: ui.sort.key === key ? -ui.sort.dir : key === "label" ? 1 : -1 };
    render();
  });
  $("#list thead").addEventListener("change", (ev) => {
    if (ev.target.id !== "check-all") return;
    for (const item of ui.visible || []) {
      if (ev.target.checked) ui.selected.add(keyOf(item));
      else ui.selected.delete(keyOf(item));
    }
    render();
  });
  const tbody = $("#list tbody");
  tbody.addEventListener("click", (ev) => {
    const tr = ev.target.closest("tr[data-key]");
    if (!tr) return;
    if (ev.target.classList.contains("pick")) {
      if (ev.target.checked) ui.selected.add(tr.dataset.key);
      else ui.selected.delete(tr.dataset.key);
      tr.classList.toggle("picked", ev.target.checked);
      render();
      return;
    }
    if (ev.target.closest("select")) return;
    openItem(findVisible(tr.dataset.key));
  });
  tbody.addEventListener("dblclick", (ev) => {
    if (ev.target.closest("tr[data-key]") && !ev.target.closest("select, input")) setPane("right", true);
  });
  tbody.addEventListener("change", async (ev) => {
    if (!ev.target.classList.contains("act")) return;
    const g = findVisible(ev.target.closest("tr").dataset.key);
    if (!(await decide([g], ev.target.value))) render();
    else if (ui.cursor === g.key) reopenCursor();
  });
  $("#more").addEventListener("click", async (ev) => {
    if (ev.target.id === "load-more") { await loadEmails(false); render(); }
  });
  $("#bulk").addEventListener("click", async (ev) => {
    const act = ev.target.dataset.bulk;
    if (!act) return;
    if (act === "clear") ui.selected.clear();
    else if (act === "pin" || act === "unpin") await pinEmails(act === "pin");
    else if (act === "restore") restore(targets().map((m) => [m.account, m.msg_id]));
    else await applyAction(act);
    if (act !== "pin" && act !== "unpin") ui.selected.clear();
    render();
  });

  document.addEventListener("keydown", (ev) => {
    if ($("#modal").open || !$("#review").hidden || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    if (ev.target.closest("input, select, textarea")) {
      if (ev.key === "Escape") ev.target.blur();
      return;
    }
    const k = ev.key;
    const actions = { d: "DELETE", k: "KEEP", r: "REVIEW", u: "AUTO" };
    if (k === "ArrowDown") moveCursor(1);
    else if (k === "ArrowUp") moveCursor(-1);
    else if (actions[k.toLowerCase()] && ui.status !== "trashed") applyAction(actions[k.toLowerCase()]);
    else if (k === "x" && ui.cursor) {
      if (ui.selected.has(ui.cursor)) ui.selected.delete(ui.cursor); else ui.selected.add(ui.cursor);
      render();
    } else if (k === "p" && ui.view === "emails") {
      const item = targets()[0];
      if (item) pinEmails(!item.pinned);
    } else if (k === "Enter" && ui.cursor) setPane("right", true);
    else if (k === "z") toast.undo?.();
    else if (k === "/") $("#q").focus();
    else if (k === "?") helpDialog();
    else if (k === "Escape") { ui.selected.clear(); ui.cursor = null; closeDetail(); render(); }
    else return;
    ev.preventDefault();
  });

  // The app window has its own browser profile; open links in the user's normal,
  // signed-in browser instead.
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest("a[href]");
    if (!a || !/^(https?:|mailto:)/i.test(a.href)) return;
    ev.preventDefault();
    api("/api/open", { url: a.href }).catch((e) => toast(e.message));
  });

  setInterval(ping, 10000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) ping(); });
  window.addEventListener("pagehide", () => navigator.sendBeacon("/api/bye", "{}"));
}

async function init() {
  initLayout();
  wire();
  closeDetail();
  ping();
  const st = await loadState();
  await loadSenders();
  render();
  if (st.job.running) { ui.jobRunning = true; watchJob(); }
  if (!ui.accounts.length) accountsDialog("Add an account to get started.");
}

init();
