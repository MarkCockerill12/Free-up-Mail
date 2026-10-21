import { $, esc, fmt, short, ACTION_LABEL } from "./util.js";
import { ui, filteredGroups } from "./state.js";

const RENDER_LIMIT = 500;

export function sendersHead() {
  const col = (key, label, cls = "") => `<th data-sort="${key}" class="${cls}">${label}</th>`;
  return `<tr><th><input type="checkbox" id="check-all" title="Select all shown"></th>
    ${col("action", "Decision")}${col("label", ui.group === "domain" ? "Domain" : "Sender")}
    ${col("count", "Emails", "num")}${col("would_delete", "Will delete", "num")}
    ${col("protected", "Kept anyway")}${col("last_date", "Latest")}</tr>`;
}

export function renderSenders() {
  const rows = filteredGroups();
  ui.visible = rows;
  $("#list tbody").innerHTML = rows.length
    ? rows.slice(0, RENDER_LIMIT).map(rowHtml).join("")
    : `<tr><td colspan="7" class="empty">No senders match these filters.</td></tr>`;
  $("#more").textContent = rows.length > RENDER_LIMIT
    ? `Showing ${RENDER_LIMIT} of ${fmt(rows.length)} — search or filter to narrow it down.`
    : `${fmt(rows.length)} ${ui.group === "domain" ? "domains" : "senders"}`;
  const all = $("#check-all");
  if (all) all.checked = rows.length > 0 && rows.slice(0, RENDER_LIMIT).every((g) => ui.selected.has(g.key));
  return rows;
}

function rowHtml(g) {
  const sub = ui.group === "domain"
    ? `${g.members.length} sender${g.members.length > 1 ? "s" : ""}${g.name ? " · " + g.name : ""}`
    : g.label;
  const title = ui.group === "domain" ? g.label : (g.name || g.label || "(no sender)");
  const acct = ui.account ? "" : ` · ${short(g.account)}`;
  const kept = Object.entries(g.kinds).filter(([, n]) => n)
    .map(([k, n]) => `<span class="chip ${k}">${k === "shipping" ? "delivery" : k} ${fmt(n)}</span>`).join("");
  const opts = ["DELETE", "REVIEW", "KEEP"]
    .map((a) => `<option value="${a}" ${a === g.action ? "selected" : ""}>${ACTION_LABEL[a]}</option>`).join("");
  const badges = (g.contact ? '<span class="chip contact">contact</span>' : "")
    + (g.unsub ? '<span class="chip">list</span>' : "") + (g.spammy ? '<span class="chip">spam?</span>' : "");
  const picked = ui.selected.has(g.key);
  return `<tr data-key="${esc(g.key)}" class="${ui.cursor === g.key ? "cursor" : ""} ${picked ? "picked" : ""}">
    <td><input type="checkbox" class="pick" ${picked ? "checked" : ""}></td>
    <td><select class="act ${g.action}" title="${esc([...g.reasons].join("; "))}">
      ${g.action === "MIXED" ? '<option value="MIXED" selected>Mixed</option>' : ""}${opts}<option value="AUTO">↺ Suggested</option></select>
      ${g.manual ? '<span class="manual" title="You changed this">●</span>' : ""}</td>
    <td class="who"><div class="name">${esc(title)} ${badges}</div><small>${esc(sub)}${esc(acct)}</small></td>
    <td class="num">${fmt(g.count)}</td>
    <td class="num ${g.would_delete ? "" : "zero"}">${fmt(g.would_delete)}</td>
    <td>${kept || '<span class="muted">—</span>'}</td>
    <td>${esc(g.last_date)}</td>
  </tr>`;
}

export function senderStats(rows) {
  const sum = (f) => rows.reduce((n, g) => n + f(g), 0);
  return [
    [fmt(sum((g) => g.count)), "emails"],
    [fmt(rows.length), ui.group === "domain" ? "domains" : "senders"],
    [fmt(rows.filter((g) => g.action === "DELETE").length), "marked delete"],
    [fmt(sum((g) => g.would_delete)), "will be deleted", "del"],
    [fmt(sum((g) => (g.action === "DELETE" ? g.count - g.would_delete : 0))), "kept from deleted senders", "prot"],
  ];
}
