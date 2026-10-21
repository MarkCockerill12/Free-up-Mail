/* Accounts: connect Outlook / any IMAP account, and choose per account what Free(up)Mail
   may do (the server enforces these too; see perms.py). */
import { $, api, esc, fmt, toast } from "./util.js";
import { ui, loadState } from "./state.js";
import { modal } from "./modal.js";

const PERM_SHORT = { scan: "List emails", read: "Open emails", trash: "Move to Trash", organise: "Sort into folders", server_rules: "Create rules" };
const NEW_DEFAULT = ["scan"];
let providerInfo = null;

/** Server name and app-password page for a typed address, from the known-providers list. */
function providerFor(addr) {
  const domain = (addr.split("@")[1] || "").toLowerCase();
  if (!providerInfo || !domain) return null;
  if (providerInfo.microsoft.some((d) => domain.split(".")[0] === d.split(".")[0])) return { microsoft: true };
  return providerInfo.providers[domain] || (domain.startsWith("yahoo.") ? providerInfo.providers["yahoo.com"] : null);
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

/** Tick boxes for a new account's permissions. */
function permChoices(prefix, outlook) {
  return `<div class="perm-list">${Object.entries(ui.permLabels)
    .filter(([p]) => outlook || p !== "server_rules")
    .map(([p, text]) => `<label><input type="checkbox" name="${prefix}-${p}" ${NEW_DEFAULT.includes(p) ? "checked" : ""}>
      <span><b>${PERM_SHORT[p]}</b><small>${esc(text)}</small></span></label>`).join("")}</div>`;
}

const chosen = (form, prefix) => Object.keys(ui.permLabels).filter((p) => form[`${prefix}-${p}`]?.checked);

function permGrid() {
  if (!ui.accounts.length) return "";
  const perms = Object.keys(ui.permLabels);
  const head = perms.map((p) => `<th class="p" title="${esc(ui.permLabels[p])}">${PERM_SHORT[p]}</th>`).join("");
  const rows = ui.accounts.map((a) => `<tr><td>${esc(a.email)}</td>${perms.map((p) => (p === "server_rules" && a.type !== "outlook"
    ? '<td class="p muted" title="Only Outlook lets apps create rules">—</td>'
    : `<td class="p"><input type="checkbox" data-grant="${esc(a.email)}" data-perm="${p}" ${a.perms.includes(p) ? "checked" : ""}></td>`)).join("")}</tr>`).join("");
  return `<h3>Permissions — what Free(up)Mail may do in each mailbox</h3>
    <table class="perm-grid"><thead><tr><th>Account</th>${head}</tr></thead><tbody>${rows}</tbody></table>
    <p class="note">Nothing outside these ticks can happen, even by mistake: the app checks them before every
      action. Hover a column for details. Untick to take a permission back at any time.</p>`;
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
        : "Signs in on Microsoft's own page; only a sign-in token is stored on this computer, never your password."}</p>
       <details ${hasOutlook ? "" : "open"}><summary>Permissions for this account</summary>${permChoices("ms", true)}</details>
       <button value="outlook">${hasOutlook ? "Reconnect Outlook" : "Connect Outlook"}</button>`;
  const form = modal(`<h2>Accounts</h2>
    ${ui.accounts.length ? `<table><thead><tr><th>Account</th><th>Type</th><th class="num">Emails</th><th>Status</th><th>Scanned (UTC)</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>` : '<p class="note">No accounts yet. Add one below — Free(up)Mail talks to your provider directly from this computer.</p>'}
    ${permGrid()}
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
    <details open><summary>Permissions for this account</summary>${permChoices("im", false)}</details>
    <p class="${msg.startsWith("✓") ? "ok" : "err"}">${esc(msg)}</p>
    <div class="foot"><button value="cancel">Close</button><button value="add" class="primary">Test & save</button></div>`,
  async (v, f) => {
    if (v === "outlook") {
      try {
        const res = await api("/api/outlook/connect", { perms: chosen(f, "ms") });
        ui.watchJob();
        accountsDialog("", res);
      } catch (e) { accountsDialog(e.message); }
      return false;
    }
    if (v === "add") {
      try {
        await api("/api/accounts", {
          email: f.email.value, password: f.pw.value, host: f.host.value.trim(), port: f.port.value, user: f.user.value,
          perms: chosen(f, "im"),
        });
        await loadState();
        accountsDialog(`✓ ${f.email.value} connected.${chosen(f, "im").includes("scan") ? " Click Rescan to load it." : ""}`);
      } catch (e) { accountsDialog(e.message); }
      return false;
    }
    const [cmd, email] = v.split(/:(.*)/);
    if (cmd === "rm") {
      if (confirm(`Remove ${email} from Free(up)Mail? Its mailbox is not touched, and the saved password or sign-in is deleted.`)) {
        await api("/api/accounts/remove", { email });
        await loadState();
        await ui.refresh();
        accountsDialog();
      }
      return false;
    }
    if (cmd === "scan") { ui.startJob("/api/scan", { account: email }); return true; }
    return false;
  }, { wide: true });
  form.email.addEventListener("input", () => { $("#provider-hint").innerHTML = providerHint(form.email.value); });
  form.querySelectorAll("[data-grant]").forEach((box) => box.addEventListener("change", () => saveGrants(form, box.dataset.grant)));
}

async function saveGrants(form, email) {
  const perms = [...form.querySelectorAll(`[data-grant="${CSS.escape(email)}"]`)].filter((b) => b.checked).map((b) => b.dataset.perm);
  try {
    await api("/api/grants", { account: email, perms });
    await loadState();
    toast(`Permissions saved for ${email}`);
  } catch (e) { alert(e.message); }
}

/** One-click grant from elsewhere (e.g. "Allow opening emails" in the preview pane). */
export async function grant(email, perm) {
  const account = ui.accounts.find((a) => a.email === email);
  if (!account) return;
  await api("/api/grants", { account: email, perms: [...new Set([...account.perms, perm])] });
  await loadState();
}
