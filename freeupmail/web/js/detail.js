import { $, api, esc, fmt, toast, renderBody, hasRemoteContent, STATUS_LABEL, KIND_LABEL, ACTION_LABEL } from "./util.js";
import { ui, setActions, membersFor, deleteWarning, accountType } from "./state.js";

let token = 0;
const pane = () => $("#detail-body");

function open(title, html) {
  $("#detail").classList.remove("idle");
  $("#detail-title").textContent = title;
  pane().innerHTML = html;
}

export function closeDetail() {
  token++;
  $("#detail").classList.add("idle");
  $("#detail-title").textContent = "Preview";
  pane().innerHTML = `<div class="empty">Click a sender or email to see it here.<br><br>
    Press <kbd>?</kbd> for keyboard shortcuts.</div>`;
}

function unsubLink(url) {
  if (!url) return "";
  const label = url.startsWith("mailto:") ? "Unsubscribe (email)" : "Unsubscribe ↗";
  return `<a class="link" href="${esc(url)}" target="_blank" rel="noopener noreferrer"
    title="Opens the sender's own unsubscribe page">${label}</a>`;
}

const kindChips = (kinds) => Object.entries(kinds).filter(([, n]) => n)
  .map(([k, n]) => `<span class="chip ${k}">${KIND_LABEL[k]} ${fmt(n)}</span>`).join("");

export async function decide(groups, action) {
  const warning = deleteWarning(groups, action);
  if (warning && !confirm(warning)) return false;
  await setActions(membersFor(groups, action), action);
  return true;
}

// ---------- sender ----------

export async function showSender(g) {
  const mine = ++token;
  const title = ui.group === "domain" ? g.label : (g.name || g.label || "(no sender)");
  const sub = ui.group === "domain" ? `${g.members.length} senders` : g.label;
  open(ui.group === "domain" ? "Domain" : "Sender", `<div class="d-head">
      <h2>${esc(title)}</h2>
      <div class="sub">${esc(sub)} · ${esc(g.account)}</div>
      <div class="reason"><span class="pill ${g.action}">${ACTION_LABEL[g.action]}</span> ${esc([...g.reasons].join("; "))}
        ${g.contact ? '<span class="chip contact">contact</span>' : ""}</div>
      <div class="d-actions">
        <button class="danger" data-act="DELETE">✖ Delete <kbd>D</kbd></button>
        <button data-act="REVIEW">? Review <kbd>R</kbd></button>
        <button class="good" data-act="KEEP">✔ Keep <kbd>K</kbd></button>
        <button class="link" data-act="AUTO">Reset</button>
        ${unsubLink(g.unsub_url)}
      </div>
      <div class="reason">${fmt(g.count)} emails · <b>${fmt(g.would_delete)}</b> will be deleted
        ${g.protected ? ` · kept anyway: ${kindChips(g.kinds)}` : ""}</div>
    </div>
    <ul class="d-list"><li class="muted">Loading…</li></ul>`);
  pane().querySelectorAll("[data-act]").forEach((b) => (b.onclick = async () => {
    if (await decide([g], b.dataset.act)) {
      const fresh = ui.groups.find((x) => x.key === g.key);
      if (fresh) showSender(fresh);
    }
  }));
  const { items, total } = await api("/api/emails", {
    account: g.account, from_addrs: g.members.map((m) => m.from_addr), limit: 400,
  });
  if (mine !== token) return;
  const list = pane().querySelector(".d-list");
  list.innerHTML = items.map((m, i) => `<li data-i="${i}" class="${m.status}">
      <div class="s">${esc(m.subject || "(no subject)")}</div>
      <div class="m"><span class="pill ${m.status}">${STATUS_LABEL[m.status]}</span>${esc(m.date)}
        ${m.kind ? `<span class="chip ${m.kind}">${KIND_LABEL[m.kind]}</span>` : ""}
        ${m.pinned ? '<span class="chip pinned">pinned</span>' : ""}${m.seen ? "" : "<b>unread</b>"}</div></li>`).join("")
    + (total > items.length ? `<li class="muted">Showing newest ${fmt(items.length)} of ${fmt(total)}</li>` : "");
  list.onclick = (ev) => {
    const li = ev.target.closest("li[data-i]");
    if (li) showEmail(items[li.dataset.i], g);
  };
}

// ---------- email ----------

async function reloadEmail(m) {
  const { items } = await api("/api/emails", { account: m.account, from_addrs: [m.from_addr], limit: 5000 });
  return items.find((x) => x.msg_id === m.msg_id) || m;
}

export async function showEmail(m, group = null) {
  const mine = ++token;
  const where = { outlook: "Outlook", gmail: "Gmail" }[accountType(m.account)] || "webmail";
  const why = m.pinned ? "pinned by you" : m.starred ? "starred" : KIND_LABEL[m.kind] || "";
  open("Email", `<div class="d-head">
      ${group ? `<button class="link back" data-back>« All from ${esc(group.name || group.label)}</button>` : ""}
      <h2>${esc(m.subject || "(no subject)")}</h2>
      <div class="sub">${esc(m.from_name)} &lt;${esc(m.from_addr)}&gt;</div>
      <div class="sub">${esc(m.date)} · ${esc(m.account)}</div>
      <div class="reason"><span class="pill ${m.status}">${STATUS_LABEL[m.status]}</span>
        ${m.status === "protected" ? `kept because: ${esc(why)}` : ""}
        ${m.status === "delete" ? "sender is marked delete" : ""}</div>
      <div class="d-actions" id="email-actions">
        ${m.status === "trashed" ? '<button class="good" data-restore>Restore to Inbox</button>' : `
        <button data-pin>${m.pinned ? "Unpin" : "📌 Always keep this email"}</button>
        <button class="danger" data-sender="DELETE">✖ Delete sender</button>
        <button class="good" data-sender="KEEP">✔ Keep sender</button>`}
      </div>
    </div>
    <div class="d-body"><div class="note">Loading email…</div></div>`);
  const back = pane().querySelector("[data-back]");
  if (back) back.onclick = () => showSender(ui.groups.find((g) => g.key === group.key) || group);
  const restoreBtn = pane().querySelector("[data-restore]");
  if (restoreBtn) restoreBtn.onclick = () => ui.restore([[m.account, m.msg_id]]);
  const pinBtn = pane().querySelector("[data-pin]");
  if (pinBtn) pinBtn.onclick = async () => {
    await api("/api/pin", { items: [[m.account, m.msg_id]], pinned: !m.pinned });
    toast(m.pinned ? "Unpinned" : "Pinned — this email will never be cleaned up");
    await ui.refresh();
    showEmail(await reloadEmail(m), group);
  };
  pane().querySelectorAll("[data-sender]").forEach((b) => (b.onclick = async () => {
    const g = ui.groups.find((x) => x.account === m.account && x.members.some((s) => s.from_addr === m.from_addr));
    const member = ui.senders.find((s) => s.account === m.account && s.from_addr === m.from_addr);
    if (member) await setActions([member], b.dataset.sender);
    if (g) showSender(ui.groups.find((x) => x.key === g.key) || g);
  }));

  const body = pane().querySelector(".d-body");
  if (m.status === "trashed") {
    body.innerHTML = '<div class="note">This email is in Trash. Restore it to read it here.</div>';
    return;
  }
  let msg;
  try {
    msg = await api(`/api/message?account=${encodeURIComponent(m.account)}&msg_id=${encodeURIComponent(m.msg_id)}`);
  } catch (e) {
    msg = { error: e.message };
  }
  if (mine !== token) return;
  if (msg.error) { body.innerHTML = `<div class="note">${esc(msg.error)}</div>`; return; }
  const link = msg.web_link || m.web_link;
  const extra = [
    link ? `<a href="${esc(link)}" target="_blank" rel="noopener">Open in ${where} ↗</a>` : "",
    unsubLink(msg.unsub),
  ].join("");
  $("#email-actions").insertAdjacentHTML("beforeend", extra);
  const remote = hasRemoteContent(msg);
  body.innerHTML = `<div class="bar">${remote ? '<span data-remote>🛡 Remote images blocked (they tell senders you opened it). <button class="link" data-images>Show images</button></span>' : ""}
      ${msg.attachments?.length ? `<span>📎 ${msg.attachments.map(esc).join(", ")}</span>` : ""}</div>
    <iframe title="Email content"></iframe>`;
  const frame = body.querySelector("iframe");
  renderBody(frame, msg, false);
  const imagesBtn = body.querySelector("[data-images]");
  if (imagesBtn) imagesBtn.onclick = () => { renderBody(frame, msg, true); body.querySelector("[data-remote]").remove(); };
}
