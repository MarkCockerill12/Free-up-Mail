/* Rules: "emails matching these conditions go here". Run them on existing mail, after every
   scan, and/or install them in Gmail / Outlook so new mail is sorted with the app closed. */
import { $, api, esc, fmt, toast, withKey } from "./util.js";
import { ui } from "./state.js";
import { modal } from "./modal.js";

const FIELD_LABEL = {
  from: "Sender", domain: "Sender's domain", subject: "Subject", category: "Category", kind: "Type",
  list: "Is a mailing list", unread: "Is unread", starred: "Is starred / flagged", inbox: "Is in the inbox", age: "Age",
};
const OP_LABEL = {
  contains: "contains", not_contains: "doesn't contain", is: "is", starts: "starts with", ends: "ends with",
  is_not: "is not", yes: "yes", no: "no", older: "older than (days)", newer: "newer than (days)",
};
const CHOICE_LABEL = { shipping: "delivery", other: "Outlook \"Other\"" };
let meta = null;

async function loadRules() {
  meta = await api("/api/rules");
  return meta.rules;
}

const badge = (ok, label) => `<span class="chip ${ok ? "ok" : ""}" title="${ok ? "Can be installed in" : "Can't be expressed as"} a ${label} filter">${label} ${ok ? "✓" : "–"}</span>`;

export async function rulesDialog(note = "") {
  const rules = await loadRules();
  const list = rules.length ? rules.map((r) => `<li class="${r.enabled ? "" : "off"}">
      <input type="checkbox" data-toggle="${r.id}" ${r.enabled ? "checked" : ""} title="On / off">
      <div class="what"><b>${esc(r.name)}</b><small title="${esc(r.summary)}">${esc(r.summary)}</small></div>
      ${r.auto ? '<span class="chip ok" title="Runs after every scan">auto</span>' : ""}
      ${badge(r.exports.gmail, "Gmail")}${badge(r.exports.outlook, "Outlook")}
      <button type="button" data-edit="${r.id}">Edit</button>
      <button type="button" data-run="${r.id}" ${r.action === "keep" ? "disabled title=\"Always-keep rules work by themselves\"" : ""}>Run now</button>
      <button type="button" class="link" data-del="${r.id}">Delete</button></li>`).join("")
    : '<li class="muted">No rules yet. Try "receipts go to a Receipts folder" or "always keep anything from my bank".</li>';
  const opts = ui.accounts.map((a) => `<option value="${esc(a.email)}" data-type="${a.type}" ${a.email === ui.account ? "selected" : ""}>${esc(a.email)}</option>`).join("");
  const form = modal(`<h2>Rules</h2>
    <p class="note">Send emails wherever you want: move them to a folder or label, archive, junk, bin, mark as read,
      flag, or <b>always keep</b> them. Rules that bin mail never touch protected emails (people you've emailed,
      starred, pinned, restored, and the kinds kept under Protection).</p>
    <ul class="rule-list">${list}</ul>
    <div class="row"><button type="button" class="primary" data-new>+ New rule</button>
      <button type="button" data-run-all ${rules.some((r) => r.enabled && r.action !== "keep") ? "" : "disabled"}>Run all rules now</button>
      <span class="ok">${esc(note)}</span></div>
    <h3>Sort new mail even when Free(up)Mail is closed</h3>
    <p class="note">Install your rules, plus the senders marked <b>Delete</b>, in the mailbox itself.
      Rules marked "Gmail ✓" / "Outlook ✓" can be installed there; the rest run inside Free(up)Mail after each scan.</p>
    ${ui.accounts.length ? `<div class="row"><label>Account</label><select name="acct">${opts}</select>
      <button type="button" data-install></button></div><div id="howto"></div>` : '<p class="muted">Add an account first.</p>'}
    <div class="foot"><button value="cancel">Close</button></div>`, async () => true, { wide: true });

  form.querySelector("[data-new]").onclick = () => editRule();
  form.querySelector("[data-run-all]").onclick = () => runRules(null, "all enabled rules");
  form.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => editRule(rules.find((r) => r.id === Number(b.dataset.edit)))));
  form.querySelectorAll("[data-run]").forEach((b) => (b.onclick = () => {
    const r = rules.find((x) => x.id === Number(b.dataset.run));
    runRules([r.id], `"${r.name}"`, r);
  }));
  form.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
    const r = rules.find((x) => x.id === Number(b.dataset.del));
    if (!confirm(`Delete the rule "${r.name}"? Emails it already moved stay where they are.`)) return;
    await api("/api/rules/delete", { id: r.id });
    await ui.refresh();
    rulesDialog("Rule deleted.");
  }));
  form.querySelectorAll("[data-toggle]").forEach((box) => box.addEventListener("change", async () => {
    const r = rules.find((x) => x.id === Number(box.dataset.toggle));
    await api("/api/rules/save", { ...r, enabled: box.checked });
    await ui.refresh();
    rulesDialog(`"${r.name}" turned ${box.checked ? "on" : "off"}.`);
  }));
  if (form.acct) wireInstall(form);
}

function wireInstall(form) {
  const type = () => form.acct.selectedOptions[0]?.dataset.type;
  const button = form.querySelector("[data-install]");
  const sync = () => {
    const t = type();
    const acct = ui.accounts.find((a) => a.email === form.acct.value);
    button.textContent = { outlook: "Create rules in Outlook", gmail: "Download Gmail filter file" }[t] || "Download blocked-senders list";
    $("#howto").innerHTML = {
      outlook: `<p class="note">Creates inbox rules named "Free(up)Mail…" (earlier ones are replaced; your own Outlook rules are untouched).
        ${acct?.perms.includes("server_rules") ? "" : '<br><span class="warn">Needs the "Create rules" permission for this account (Accounts → Permissions).</span>'}</p>`,
      gmail: `<ol class="note"><li>Download the file.</li><li>Gmail → ⚙ → See all settings → Filters and Blocked Addresses → <b>Import filters</b>.</li>
         <li>Choose the file → Open file → Create filters. Re-import after big changes (delete the old Free(up)Mail filters first).</li></ol>`,
    }[t] || `<p class="note">This provider has no filter import. The list holds the senders marked Delete: add them to
         <b>Blocked senders</b> in its webmail settings. Your rules run inside Free(up)Mail after each scan instead.</p>`;
  };
  button.onclick = () => {
    if (type() === "outlook") { $("#modal").close(); ui.startJob("/api/outlook/rules", {}); return; }
    location.href = withKey(`/api/filters?account=${encodeURIComponent(form.acct.value)}`);
  };
  form.acct.addEventListener("change", sync);
  sync();
}

function missingPerm(rule) {
  const perm = meta.action_perm[rule.action];
  if (!perm) return [];
  return ui.accounts.filter((a) => (!rule.accounts?.length || rule.accounts.includes(a.email)) && !a.perms.includes(perm))
    .map((a) => a.email);
}

async function runRules(ids, what, rule = null) {
  if (rule) {
    const preview = await api("/api/rules/preview", rule).catch((e) => ({ error: e.message }));
    if (preview.error) { alert(preview.error); return; }
    if (!preview.total) { alert(`"${rule.name}" matches no emails right now.`); return; }
    const blocked = missingPerm(rule);
    const warn = blocked.length ? `\n\nSkipped (not allowed — Accounts → Permissions): ${blocked.join(", ")}` : "";
    const verb = meta.actions[rule.action].toLowerCase();
    if (!confirm(`${what}: ${verb}${rule.folder ? ` "${rule.folder}"` : ""} for ${fmt(preview.total)} emails?${warn}`)) return;
  } else if (!confirm(`Run ${what} on the emails in your mailboxes now? Accounts without the needed permission are skipped.`)) return;
  $("#modal").close();
  ui.startJob("/api/rules/run", { ids: ids || [] });
}

// ---------- editor ----------

function condRow(c = { field: "subject", op: "contains", value: "" }) {
  const kind = meta.fields[c.field];
  const fields = Object.keys(meta.fields).map((f) => `<option value="${f}" ${f === c.field ? "selected" : ""}>${FIELD_LABEL[f]}</option>`).join("");
  const ops = meta.ops[kind].map((o) => `<option value="${o}" ${o === c.op ? "selected" : ""}>${OP_LABEL[o]}</option>`).join("");
  let value = '<span class="muted"></span>';
  if (kind === "text") value = `<input type="text" data-c="value" value="${esc(c.value)}" placeholder="${c.field === "domain" ? "shop.example" : "text"}">`;
  if (kind === "days") value = `<input type="number" data-c="value" min="0" value="${esc(c.value || "30")}">`;
  if (kind === "choice") {
    value = `<select data-c="value">${meta.choices[c.field].map((v) => `<option value="${v}" ${v === c.value ? "selected" : ""}>${CHOICE_LABEL[v] || v}</option>`).join("")}</select>`;
  }
  return `<div class="cond"><select data-c="field">${fields}</select><select data-c="op">${ops}</select>${value}
    <button type="button" class="link" data-remove title="Remove this condition">✕</button></div>`;
}

function readRule(form, base) {
  const conditions = [...form.querySelectorAll(".cond")].map((row) => ({
    field: row.querySelector("[data-c=field]").value, op: row.querySelector("[data-c=op]").value,
    value: row.querySelector("[data-c=value]")?.value || "",
  }));
  const accounts = form.all_accounts.checked ? [] : [...form.querySelectorAll("[name=acct]:checked")].map((x) => x.value);
  return {
    ...(base?.id ? { id: base.id } : {}), name: form.rname.value, enabled: base?.enabled ?? true, accounts,
    match: form.match.value, conditions, action: form.ract.value, folder: form.folder.value,
    mark_read: form.mark_read.checked, auto: form.auto.checked,
  };
}

export async function editRule(base = null, prefill = null) {
  if (!meta) await loadRules();
  const r = base || {
    name: "", accounts: [], match: "all", action: "move", folder: "", mark_read: false, auto: false,
    conditions: [{ field: "subject", op: "contains", value: "" }], ...prefill,
  };
  const actions = Object.entries(meta.actions).map(([a, l]) => `<option value="${a}" ${a === r.action ? "selected" : ""}>${l}</option>`).join("");
  const accts = ui.accounts.map((a) => `<label><input type="checkbox" name="acct" value="${esc(a.email)}" ${r.accounts.includes(a.email) ? "checked" : ""}> ${esc(a.email)}</label>`).join(" ");
  const form = modal(`<h2>${base ? "Edit rule" : "New rule"}</h2>
    <div class="row"><label>Name</label><input type="text" name="rname" value="${esc(r.name)}" style="flex:1" placeholder="e.g. Receipts to a folder"></div>
    <div class="row"><label>Accounts</label><label><input type="checkbox" name="all_accounts" ${r.accounts.length ? "" : "checked"}> All</label>
      <span id="acct-list" ${r.accounts.length ? "" : "hidden"}>${accts}</span></div>
    <h3>When an email matches <select name="match"><option value="all" ${r.match === "all" ? "selected" : ""}>all</option>
      <option value="any" ${r.match === "any" ? "selected" : ""}>any</option></select> of these:</h3>
    <div id="conds">${r.conditions.map(condRow).join("")}</div>
    <button type="button" class="link" data-add>+ Add condition</button>
    <h3>Then</h3>
    <div class="row"><select name="ract">${actions}</select>
      <input type="text" name="folder" value="${esc(r.folder)}" placeholder="Folder or Gmail label (created if missing)" style="flex:1"></div>
    <div class="row"><label><input type="checkbox" name="mark_read" ${r.mark_read ? "checked" : ""}> Also mark as read</label>
      <label><input type="checkbox" name="auto" ${r.auto ? "checked" : ""}> Run automatically after each scan</label></div>
    <p class="note" id="action-note"></p>
    <div class="row"><button type="button" data-preview>Preview matches</button><span id="preview-count" class="note"></span></div>
    <div class="preview-box" id="preview-list" hidden></div>
    <div class="foot"><button type="button" class="left link" data-back>« All rules</button><button value="cancel">Cancel</button>
      <button value="save" class="primary">Save rule</button></div>`,
  async (_, f) => {
    await api("/api/rules/save", readRule(f, base));
    await ui.refresh();
    toast("Rule saved");
    rulesDialog(`Saved "${f.rname.value}".`);
    return false;
  }, { wide: true });

  const syncAction = () => {
    const a = form.ract.value;
    form.folder.hidden = a !== "move";
    form.mark_read.parentElement.hidden = !["move", "archive", "junk"].includes(a);
    form.auto.parentElement.hidden = a === "keep";
    const blocked = missingPerm({ action: a, accounts: readRule(form, base).accounts });
    $("#action-note").innerHTML = (a === "keep" ? "Matching emails are protected: no cleanup or rule will ever bin them. "
      : a === "trash" ? "Emails go to Trash with a backup saved first. Protected emails are never binned by rules. " : "")
      + (blocked.length ? `<span class="warn">Not allowed yet for ${esc(blocked.join(", "))} — those accounts are skipped
        (Accounts → Permissions).</span>` : "");
  };
  form.ract.addEventListener("change", syncAction);
  form.all_accounts.addEventListener("change", () => { $("#acct-list").hidden = form.all_accounts.checked; syncAction(); });
  $("#conds").addEventListener("change", (ev) => {
    const row = ev.target.closest(".cond");
    if (ev.target.dataset.c === "field") {
      const field = ev.target.value;
      row.outerHTML = condRow({ field, op: meta.ops[meta.fields[field]][0], value: meta.choices[field]?.[0] || "" });
    }
  });
  $("#conds").addEventListener("click", (ev) => {
    if (ev.target.closest("[data-remove]") && form.querySelectorAll(".cond").length > 1) ev.target.closest(".cond").remove();
  });
  form.querySelector("[data-add]").onclick = () => $("#conds").insertAdjacentHTML("beforeend", condRow());
  form.querySelector("[data-back]").onclick = () => rulesDialog();
  form.querySelector("[data-preview]").onclick = async () => {
    const box = $("#preview-list");
    try {
      const p = await api("/api/rules/preview", readRule(form, base));
      $("#preview-count").textContent = `${fmt(p.total)} emails match right now`
        + (p.total ? ` (${Object.entries(p.accounts).map(([a, n]) => `${a}: ${fmt(n)}`).join(", ")})` : "");
      box.innerHTML = p.sample.map((m) => `<div><span class="muted">${esc(m.date)}</span> <b>${esc(m.from)}</b> — ${esc(m.subject)}</div>`).join("")
        + (p.total > p.sample.length ? `<div class="muted">…and ${fmt(p.total - p.sample.length)} more</div>` : "");
      box.hidden = !p.total;
    } catch (e) {
      $("#preview-count").textContent = e.message;
      box.hidden = true;
    }
  };
  syncAction();
}

/** "Make a rule" from a sender in the preview pane. */
export function ruleForSender(group) {
  const exact = group.members.length === 1;
  editRule(null, {
    name: `${group.name || group.label}`,
    conditions: [exact ? { field: "from", op: "is", value: group.members[0].from_addr } : { field: "domain", op: "is", value: group.label }],
  });
}
