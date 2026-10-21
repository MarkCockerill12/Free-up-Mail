import { $, api, esc, fmt } from "./util.js";
import { ui, loadState } from "./state.js";

function modal(html, onSubmit) {
  const dlg = $("#modal");
  const form = $("#modal-body");
  form.innerHTML = html;
  // XP window chrome: the <h2> becomes the blue title bar with a close button,
  // everything after it goes into the window body.
  const title = form.querySelector("h2");
  const content = document.createElement("div");
  content.className = "content";
  while (title.nextSibling) content.appendChild(title.nextSibling);
  form.appendChild(content);
  const close = document.createElement("button");
  close.type = "button";
  close.className = "xp-close";
  close.title = "Close";
  close.textContent = "✕";
  close.onclick = () => dlg.close();
  title.appendChild(close);
  form.onsubmit = async (ev) => {
    const btn = ev.submitter;
    if (!btn || btn.value === "cancel") return;
    ev.preventDefault();
    btn.disabled = true;
    try {
      if ((await onSubmit(btn.value, form)) !== false) dlg.close();
    } catch (e) {
      alert(e.message);
    } finally {
      btn.disabled = false;
    }
  };
  dlg.showModal();
  return form;
}

export function settingsDialog() {
  const s = ui.settings;
  const opt = (v, label) => `<option value="${v}" ${s.keep_security === v ? "selected" : ""}>${label}</option>`;
  modal(`<h2>Protection rules</h2>
    <p class="note">When a sender is marked <b>Delete</b>, these emails from them are still kept.
      Starred/flagged emails, emails you pin, and anyone you've ever emailed are always kept.</p>
    <div class="row"><label>Security & sign-in alerts</label><select name="keep_security">
      ${opt("all", "Always keep")}${opt("90d", "Keep only the last 90 days")}${opt("none", "Don't protect")}</select></div>
    <div class="row"><label>Receipts, refunds, payslips, bookings</label><input type="checkbox" name="keep_receipts" ${s.keep_receipts === "1" ? "checked" : ""}></div>
    <div class="row"><label>Delivery & parcel updates</label><input type="checkbox" name="keep_shipping" ${s.keep_shipping === "1" ? "checked" : ""}></div>
    <p class="note">Old sign-in alerts and delivery updates are rarely useful; switching those two off clears the most clutter.</p>
    <div class="foot"><button value="cancel">Cancel</button><button value="save" class="primary">Save</button></div>`,
  async (_, f) => {
    await api("/api/settings", {
      keep_security: f.keep_security.value,
      keep_receipts: f.keep_receipts.checked ? "1" : "0",
      keep_shipping: f.keep_shipping.checked ? "1" : "0",
    });
    await loadState();
    await ui.refresh();
  });
}

let providerInfo = null;

/** Server name and app-password page for a typed address, from the known-providers list. */
function providerFor(addr) {
  const domain = (addr.split("@")[1] || "").toLowerCase();
  if (!providerInfo || !domain) return null;
  if (providerInfo.microsoft.some((d) => domain.split(".")[0] === d.split(".")[0])) return { microsoft: true };
  return providerInfo.providers[domain] || (domain.startsWith("yahoo.") ? providerInfo.providers["yahoo.com"] : null);
}

export async function accountsDialog(msg = "", code = null) {
  if (!providerInfo) providerInfo = await api("/api/providers").catch(() => ({ providers: {}, microsoft: [] }));
  const hasOutlook = ui.accounts.some((a) => a.type === "outlook");
  const rows = ui.accounts.map((a) => `<tr><td>${esc(a.email)}</td><td>${a.type}</td><td class="num">${fmt(a.messages)}</td>
    <td class="${a.status === "ok" ? "ok" : "err"}">${esc(a.status)}</td><td>${esc((a.scanned_at || "").slice(0, 16).replace("T", " "))}</td>
    <td><button value="scan:${esc(a.email)}">Rescan</button> <button value="rm:${esc(a.email)}" class="link">Remove</button></td></tr>`).join("");
  const outlookBlock = code
    ? `<p>Open <a href="${esc(code.verification_uri)}" target="_blank" rel="noopener"><b>${esc(code.verification_uri)}</b></a>, enter this code and sign in:</p>
       <div class="code">${esc(code.user_code)}</div>
       <p class="note">Valid for 15 minutes. Free(up)Mail scans Outlook as soon as you approve (progress bottom-right).</p>`
    : `<p class="note">${hasOutlook ? "Connected. Reconnect if a scan says the sign-in expired."
        : "Signs in on Microsoft's own page; only a sign-in token is stored, never your password."}</p>
       <button value="outlook">${hasOutlook ? "Reconnect Outlook" : "Connect Outlook"}</button>`;
  const form = modal(`<h2>Accounts</h2>
    <table><thead><tr><th>Account</th><th>Type</th><th class="num">Emails</th><th>Status</th><th>Scanned (UTC)</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>
    <h3>Outlook / Hotmail / Live / Microsoft 365</h3>${outlookBlock}
    <h3>Add any other account (Gmail, Yahoo, iCloud, AOL, your own domain…)</h3>
    <div class="row"><input type="email" name="email" placeholder="you@example.com" style="flex:1" autocomplete="off">
      <input type="password" name="pw" placeholder="password or app password" style="flex:1" autocomplete="off"></div>
    <p class="note" id="provider-hint">${GENERIC_HINT}</p>
    <details><summary>Server settings</summary>
      <div class="row"><label>IMAP server</label><input type="text" name="host" placeholder="found automatically" style="flex:1"></div>
      <div class="row"><label>Port</label><input type="number" name="port" value="993" style="width:80px">
        <span class="note">993 = SSL, 143 = STARTTLS</span></div>
      <div class="row"><label>Username</label><input type="text" name="user" placeholder="only if it isn't the email address" style="flex:1"></div>
    </details>
    <p class="${msg.startsWith("✓") ? "ok" : "err"}">${esc(msg)}</p>
    <div class="foot"><button value="cancel">Close</button><button value="add" class="primary">Test & save</button></div>`,
  async (v, f) => {
    if (v === "outlook") {
      try {
        const res = await api("/api/outlook/connect", {});
        ui.watchJob();
        accountsDialog("", res);
      } catch (e) { accountsDialog(e.message); }
      return false;
    }
    if (v === "add") {
      try {
        await api("/api/accounts", {
          email: f.email.value, password: f.pw.value, host: f.host.value.trim(), port: f.port.value, user: f.user.value,
        });
        await loadState();
        accountsDialog(`✓ ${f.email.value} connected. Click Rescan to load it.`);
      } catch (e) { accountsDialog(e.message); }
      return false;
    }
    const [cmd, email] = v.split(/:(.*)/);
    if (cmd === "rm") {
      if (confirm(`Remove ${email} from Free(up)Mail? Its mailbox is not touched.`)) {
        await api("/api/accounts/remove", { email });
        await loadState();
        await ui.refresh();
        accountsDialog();
      }
      return false;
    }
    if (cmd === "scan") { ui.startJob("/api/scan", { account: email }); return true; }
    return false;
  });
  form.email.addEventListener("input", () => { $("#provider-hint").innerHTML = providerHint(form.email.value); });
}

const GENERIC_HINT = "Most providers need an <b>app password</b> (made in your account's security settings), not your normal password.";

function providerHint(addr) {
  const p = providerFor(addr);
  if (p?.microsoft) return "Microsoft addresses connect with the <b>Connect Outlook</b> button above.";
  if (p) {
    const make = p.help
      ? ` Make one at <a href="${esc(p.help)}" target="_blank" rel="noopener">${esc(p.help.replace(/^https:[/][/]/, ""))}</a>.`
      : "";
    return `Server <b>${esc(p.host)}</b> found automatically. Use an <b>app password</b> (2-step sign-in must be on).${make}`;
  }
  if (!addr.includes("@")) return GENERIC_HINT;
  return `Unknown provider: Free(up)Mail tries <b>imap.${esc(addr.split("@")[1])}</b>. If that fails, open
    <b>Server settings</b> and enter the IMAP server from your provider's help pages. ${GENERIC_HINT}`;
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
  modal(`<h2>Clean up</h2>
    <p>These emails go to each account's <b>Trash</b> / <b>Deleted Items</b> folder, where most providers keep
      them for about 30 days. <b>In Trash</b> in the sidebar can restore any of them.</p>
    <table><tbody>${entries.map(([a, c]) => `<tr><td>${esc(a)}</td><td class="num"><b>${fmt(c)}</b> emails</td></tr>`).join("")}</tbody></table>
    <p class="note">Still kept from deleted senders: ${esc(kept || "nothing extra")}, plus starred/flagged and pinned emails.
      Anyone you've emailed is never included. Each email is re-checked before it moves.</p>
    <div class="foot"><button value="cancel">Cancel</button><button value="go" class="danger">Move ${fmt(total)} to Trash</button></div>`,
  async () => { ui.startJob("/api/cleanup", { confirm: true, account: ui.account || null }); });
}

export function filtersDialog() {
  const opts = ui.accounts.map((a) => `<option value="${esc(a.email)}" data-type="${a.type}" ${a.email === ui.account ? "selected" : ""}>${esc(a.email)}</option>`).join("");
  const form = modal(`<h2>Filters for future mail</h2>
    <p class="note">Your mail provider then bins future mail from every sender marked <b>Delete</b> by itself, even on your phone,
      with nothing left running. For Gmail and Outlook, subjects with security, receipt or delivery words
      (per your protection rules) are exempt.</p>
    <div class="row"><label>Account</label><select name="acct">${opts}</select></div>
    <div id="howto"></div>
    <div class="foot"><button value="cancel">Close</button><button value="go" class="primary" id="filters-go"></button></div>`,
  async (_, f) => {
    if (type() === "outlook") { ui.startJob("/api/outlook/rules", {}); return true; }
    location.href = `/api/filters?account=${encodeURIComponent(f.acct.value)}`;
    return false;
  });
  const type = () => form.acct.selectedOptions[0]?.dataset.type;
  const sync = () => {
    $("#filters-go").textContent = { outlook: "Create Outlook rules", gmail: "Download Gmail filter file" }[type()]
      || "Download address list";
    $("#howto").innerHTML = {
      outlook: '<p class="note">Creates inbox rules named "Free(up)Mail 1, 2…" (earlier ones are replaced; your own rules are untouched).</p>',
      gmail: `<ol class="note"><li>Download the file.</li><li>Gmail → ⚙ → See all settings → Filters and Blocked Addresses → <b>Import filters</b>.</li>
         <li>Choose the file → Open file → Create filters. Re-import after big changes (delete the old Free(up)Mail filters first).</li></ol>`,
    }[type()] || `<p class="note">This provider has no filter import. Download the list of senders marked Delete
         and add them to <b>Blocked senders</b> (or a filter) in its webmail settings.</p>`;
  };
  form.acct.addEventListener("change", sync);
  sync();
}

export function helpDialog() {
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
      <kbd>Esc</kbd><span>Clear selection</span>
    </div>
    <fieldset><legend>Panes</legend>
      Drag the dividers either side of the list to resize the panes. Double-click a divider, or use
      <b>Task pane</b> / <b>Preview</b> on the toolbar, to hide or show them. Click a section heading
      in the task pane to fold it. Sizes are remembered.</fieldset>
    <p class="note">Review mode: <kbd>D</kbd> delete, <kbd>K</kbd> keep, <kbd>S</kbd> skip, <kbd>B</kbd> back.</p>
    <div class="foot"><button value="cancel" class="primary">Close</button></div>`, async () => true);
}
