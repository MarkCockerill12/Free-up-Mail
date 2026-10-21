import { $, api, esc, fmt, short, STATUS_LABEL, KIND_LABEL } from "./util.js";
import { ui } from "./state.js";

const PAGE = 200;
let token = 0;

export const emailKey = (m) => `${m.account}|${m.msg_id}`;

export async function loadEmails(reset = true) {
  const mine = ++token;
  ui.emails.loading = true;
  const res = await api("/api/emails", {
    account: ui.account, q: ui.q, status: ui.status === "all" ? "" : ui.status, cat: ui.cat,
    unread: ui.unreadOnly, inbox: ui.inboxOnly, offset: reset ? 0 : ui.emails.items.length, limit: PAGE,
  });
  if (mine !== token) return;
  ui.emails = {
    items: reset ? res.items : [...ui.emails.items, ...res.items], total: res.total, loading: false,
    counts: res.counts || ui.emails.counts || {},
  };
}

export function emailsHead() {
  return `<tr><th><input type="checkbox" id="check-all" title="Select all shown"></th><th>Status</th>
    <th>From</th><th>Subject</th><th>${ui.status === "trashed" ? "Binned" : "Date"}</th>${ui.account ? "" : "<th>Account</th>"}</tr>`;
}

function why(m) {
  if (m.status !== "protected") return "";
  if (m.pinned) return "pinned by you";
  if (m.starred) return "starred";
  return KIND_LABEL[m.kind] || "";
}

function rowHtml(m) {
  const key = emailKey(m);
  const reason = why(m);
  const picked = ui.selected.has(key);
  return `<tr data-key="${esc(key)}" class="${ui.cursor === key ? "cursor" : ""} ${m.seen ? "" : "unread"} ${picked ? "picked" : ""}">
    <td><input type="checkbox" class="pick" ${picked ? "checked" : ""}></td>
    <td><span class="pill ${m.status}" title="${esc(reason)}">${STATUS_LABEL[m.status] || m.status}</span></td>
    <td class="who"><div class="name">${esc(m.from_name || m.from_addr || "(no sender)")}</div><small>${esc(m.from_addr)}</small></td>
    <td class="subj">${m.kind ? `<span class="chip ${m.kind}">${KIND_LABEL[m.kind]}</span>` : ""}${m.pinned ? '<span class="chip pinned">pinned</span>' : ""}${esc(m.subject || "(no subject)")}</td>
    <td>${esc(m.status === "trashed" ? (m.trashed_at || "").slice(0, 10) : m.date)}</td>
    ${ui.account ? "" : `<td class="muted">${esc(short(m.account))}</td>`}
  </tr>`;
}

export function renderEmails() {
  const { items, total, loading } = ui.emails;
  ui.visible = items;
  $("#list tbody").innerHTML = items.length
    ? items.map(rowHtml).join("")
    : `<tr><td colspan="6" class="empty">${loading ? "Loading…" : ui.status === "trashed"
      ? "Nothing in Trash from Free(up)Mail." : "No emails match these filters."}</td></tr>`;
  const more = $("#more");
  more.innerHTML = `${fmt(items.length)} of ${fmt(total)} emails`
    + (items.length < total ? ' <button id="load-more">Load more</button>' : "");
  const all = $("#check-all");
  if (all) all.checked = items.length > 0 && items.every((m) => ui.selected.has(emailKey(m)));
}

export function emailStats() {
  if (ui.status === "trashed") return [[fmt(ui.emails.total), "in Trash (restorable)"]];
  const c = ui.emails.counts || {};
  return [
    [fmt(c.all), "emails"],
    [fmt(c.delete), "will be deleted", "del"],
    [fmt(c.protected), "protected", "prot"],
    [fmt(c.review), "need review"],
    [fmt(c.unread), "unread"],
  ];
}
