/* Protection & backups, the Clean up confirmation, and the "?" help menu. */
import { api, esc, fmt, sizeText, toast } from "./util.js";
import { ui, loadState } from "./state.js";
import { modal } from "./modal.js";
import { showMenu } from "./menu.js";

export function settingsDialog() {
  const s = ui.settings;
  const b = ui.backups || { count: 0, bytes: 0 };
  const opt = (v, label) => `<option value="${v}" ${s.keep_security === v ? "selected" : ""}>${label}</option>`;
  const keepFor = [["0", "Forever"], ["30", "30 days"], ["90", "90 days"], ["180", "6 months"], ["365", "1 year"]]
    .map(([v, label]) => `<option value="${v}" ${(s.backup_days || "0") === v ? "selected" : ""}>${label}</option>`).join("");
  modal(`<h2>Protection & backups</h2>
    <p class="note">When a sender is marked <b>Delete</b>, these emails from them are still kept.
      Starred/flagged emails, emails you pin or restore, "always keep" rules and anyone you've ever emailed are always kept.</p>
    <div class="row"><label>Security & sign-in alerts</label><select name="keep_security">
      ${opt("all", "Always keep")}${opt("90d", "Keep only the last 90 days")}${opt("none", "Don't protect")}</select></div>
    <div class="row"><label>Receipts, refunds, payslips, bookings</label><input type="checkbox" name="keep_receipts" ${s.keep_receipts === "1" ? "checked" : ""}></div>
    <div class="row"><label>Delivery & parcel updates</label><input type="checkbox" name="keep_shipping" ${s.keep_shipping === "1" ? "checked" : ""}></div>
    <h3>Backups</h3>
    <div class="row"><label>Save a copy before deleting</label><input type="checkbox" name="backup" ${s.backup === "1" ? "checked" : ""}>
      <span class="note">Recommended. Each email is saved on this computer before it goes to Trash, so it can be
      put back even after the Trash is emptied.</span></div>
    <div class="row"><label>Backups so far</label><span><b>${fmt(b.count)}</b> emails, ${sizeText(b.bytes)}</span>
      <button type="button" data-open="backups">Open folder</button></div>
    <div class="row"><label>Keep backups for</label><select name="backup_days">${keepFor}</select>
      <span class="note">Older copies are deleted each time Free(up)Mail starts (and now, when you save).</span></div>
    <p class="note">To put emails back, open <b>Deleted (restorable)</b> in the sidebar, select them and press
      <b>Restore</b>. Ones still in Trash are moved back; the rest are uploaded from these backups.
      <button type="button" class="link" data-show-deleted>Show deleted emails</button></p>
    <h3>Rules</h3>
    <div class="row"><label>Run automatic rules after each scan</label><input type="checkbox" name="auto_rules" ${s.auto_rules === "1" ? "checked" : ""}>
      <span class="note">Only rules you marked "run automatically".</span></div>
    <div class="foot"><button value="cancel">Cancel</button><button value="save" class="primary">Save</button></div>`,
  async (_, f) => {
    if (f.backup.checked === false && s.backup === "1"
        && !confirm("Turn off backups? Emails deleted from now on can only be restored while they are still in Trash.")) return false;
    const days = Number(f.backup_days.value);
    const shorter = days && (!Number(s.backup_days) || days < Number(s.backup_days));
    if (shorter && !confirm(`Delete backup copies older than ${f.backup_days.selectedOptions[0].text}, now and from now on? `
      + "Emails in your mailboxes are not affected, but older deleted emails can then only be restored while they are in Trash.")) return false;
    await api("/api/settings", {
      keep_security: f.keep_security.value, keep_receipts: f.keep_receipts.checked ? "1" : "0",
      keep_shipping: f.keep_shipping.checked ? "1" : "0", backup: f.backup.checked ? "1" : "0",
      auto_rules: f.auto_rules.checked ? "1" : "0", backup_days: f.backup_days.value,
    });
    if (days) {
      const { deleted } = await api("/api/backups/prune", { days });
      if (deleted) toast(`${fmt(deleted)} old backups deleted`);
    }
    await loadState();
    await ui.refresh();
  });
  document.querySelector("[data-open=backups]").onclick = () => api("/api/open-folder", { what: "backups" }).catch((e) => alert(e.message));
  document.querySelector("[data-show-deleted]").onclick = () => { document.querySelector("#modal").close(); ui.showDeleted(); };
}

export async function cleanupDialog() {
  const plan = await api("/api/plan");
  const entries = Object.entries(plan).filter(([acc]) => !ui.account || acc === ui.account);
  const total = entries.reduce((n, [, c]) => n + c, 0);
  if (!total) { alert("Nothing is marked for deletion yet."); return; }
  const s = ui.settings;
  const kept = [
    s.keep_security === "all" ? "all security alerts" : s.keep_security === "90d" ? "security alerts from the last 90 days" : "",
    s.keep_receipts === "1" ? "receipts" : "", s.keep_shipping === "1" ? "delivery updates" : "",
  ].filter(Boolean).join(", ");
  const perms = Object.fromEntries(ui.accounts.map((a) => [a.email, a.perms]));
  const backups = s.backup === "1";
  const needs = (acc) => !perms[acc]?.includes("trash") || (backups && !perms[acc]?.includes("read"));
  const rows = entries.map(([a, c]) => `<tr><td>${esc(a)}</td><td class="num"><b>${fmt(c)}</b> emails</td>
    <td>${needs(a) ? `<label><input type="checkbox" name="allow" value="${esc(a)}"> Allow Free(up)Mail to move these to Trash
      ${backups ? "(and read them to save backups)" : ""}</label>` : '<span class="chip ok">allowed</span>'}</td></tr>`).join("");
  modal(`<h2>Clean up</h2>
    <p>These emails go to each account's <b>Trash</b> / <b>Deleted Items</b> folder. ${backups
      ? "A copy of each is saved on this computer first, so <b>Deleted</b> in the sidebar can put any of them back — even after the Trash is emptied."
      : "<b>Backups are off</b>: they can only be put back while they are still in Trash (usually about 30 days)."}</p>
    <table><tbody>${rows}</tbody></table>
    <p class="note">Still kept from deleted senders: ${esc(kept || "nothing extra")}, plus starred/flagged, pinned and restored
      emails and "always keep" rules. Anyone you've emailed is never included. Each email is re-checked before it moves.</p>
    <div class="foot"><button value="cancel">Cancel</button><button value="go" class="danger">Move ${fmt(total)} to Trash</button></div>`,
  async (_, f) => {
    const allow = [...f.querySelectorAll("input[name=allow]:checked")].map((x) => x.value);
    const blocked = entries.filter(([a]) => needs(a) && !allow.includes(a));
    if (blocked.length === entries.length) { alert("Tick at least one account to allow, or press Cancel."); return false; }
    if (blocked.length && !confirm(`${blocked.map(([a]) => a).join(", ")} will be skipped (not allowed). Carry on with the rest?`)) return false;
    ui.startJob("/api/cleanup", { confirm: true, account: ui.account || null, allow, allow_read: backups });
  });
}

export function shortcutsDialog() {
  modal(`<h2>Keyboard shortcuts</h2>
    <div class="keys">
      <kbd>↑</kbd><kbd>↓</kbd><span>Move through the list (opens the item on the right)</span>
      <kbd>X</kbd><span>Select / unselect the current row</span>
      <kbd>D</kbd><span>Delete sender (selected rows, or the current one)</span>
      <kbd>K</kbd><span>Keep sender</span>
      <kbd>R</kbd><span>Back to review</span>
      <kbd>U</kbd><span>Reset to the suggestion</span>
      <kbd>P</kbd><span>Pin / unpin email (All emails view)</span>
      <kbd>Z</kbd><span>Undo the last change</span>
      <kbd>/</kbd><span>Search</span>
      <kbd>Enter</kbd><span>Show the preview pane for the current row (or double-click it)</span>
      <kbd>?</kbd><span>This help menu</span>
      <kbd>Esc</kbd><span>Clear selection</span>
    </div>
    <fieldset><legend>Panes</legend>
      Drag the dividers either side of the list to resize the panes. Double-click a divider, or use
      <b>Task pane</b> / <b>Preview</b> on the toolbar, to hide or show them. Click a section heading
      in the task pane to fold it. Drag the edge of a column heading to resize that column (double-click it to reset). Sizes are remembered.</fieldset>
    <p class="note">Review mode: <kbd>D</kbd> delete, <kbd>K</kbd> keep, <kbd>S</kbd> skip, <kbd>B</kbd> back.</p>
    <div class="foot"><button value="cancel" class="primary">Close</button></div>`, async () => true);
}

export function privacyDialog() {
  modal(`<h2>Your privacy</h2>
    <p class="lead"><b>Everything runs on this computer.</b> There is no Free(up)Mail server, account, analytics or tracking.</p>
    <ul>
      <li>Free(up)Mail connects straight from this computer to your email providers (Google, Microsoft, Yahoo…) and nowhere else.</li>
      <li>Passwords, sign-ins, the email list and backups are stored only in <code>${esc(ui.dataFolder || "")}</code>.</li>
      <li>It only does what you allow per account under <b>Accounts → Permissions</b>, and checks that before every action.</li>
      <li>Nothing is ever permanently deleted: emails go to Trash, with a backup copy saved here first.</li>
      <li>Remote images in emails are blocked until you click to show them, so senders can't see you opened them.</li>
      <li>The window only works with a secret key made each time the app starts, so other programs can't read your mail through it.</li>
    </ul>
    <div class="foot"><button type="button" data-open="data" class="left">Open data folder</button>
      <button value="cancel" class="primary">Close</button></div>`, async () => true);
  document.querySelector("[data-open=data]").onclick = () => api("/api/open-folder", { what: "data" }).catch((e) => alert(e.message));
}

export function helpMenu(anchor, startTour, showWelcome) {
  showMenu(anchor, [
    { label: "Tutorial — show me around", icon: "🎓", run: startTour },
    { label: "Welcome screen", icon: "👋", run: showWelcome },
    "-",
    { label: "Keyboard shortcuts", icon: "⌨️", run: shortcutsDialog },
    { label: "Privacy — what stays on this computer", icon: "🔒", run: privacyDialog },
  ]);
}
