/* Updates: a banner when GitHub has a newer release, and "Check for updates" in the ? menu.
   The standalone app installs it itself; runs from source or the .pyz get the download page. */
import { $, api, esc } from "./util.js";
import { ui } from "./state.js";
import { modal } from "./modal.js";

const SKIP = "freeupmail.skipUpdate";

const check = () => api("/api/update/check", {}).catch((e) => ({ error: e.message }));

export async function checkOnStart(settings) {
  if (settings.check_updates === "0") return;
  const info = await check();
  if (info.error || !info.newer) return;
  let skipped = null;
  try { skipped = localStorage.getItem(SKIP); } catch { /* storage blocked: just ask again */ }
  if (skipped !== info.latest) showBanner(info);
}

export async function checkNow() {
  updateDialog(await check());
}

function showBanner(info) {
  const bar = $("#update-banner");
  bar.innerHTML = `🎉 <b>Free(up)Mail ${esc(info.latest)}</b> is available (you have ${esc(info.current)}).
    <button data-u="more">What's new</button>
    <button class="primary" data-u="more">${info.can_install ? "Update now" : "Download"}</button>
    <button class="link" data-u="later">Not now</button>`;
  bar.hidden = false;
  bar.onclick = (ev) => {
    const what = ev.target.dataset.u;
    if (what === "later") {
      try { localStorage.setItem(SKIP, info.latest); } catch { /* only hides it for this session */ }
      bar.hidden = true;
    } else if (what === "more") updateDialog(info);
  };
}

function updateDialog(info) {
  const close = '<div class="foot"><button value="cancel" class="primary">Close</button></div>';
  if (info.error) {
    modal(`<h2>Updates</h2><p>${esc(info.error)}</p>${close}`, async () => true);
    return;
  }
  if (!info.newer) {
    modal(`<h2>Updates</h2><p>✔️ You have the latest version (${esc(info.current)}).</p>${close}`, async () => true);
    return;
  }
  const how = info.can_install
    ? "Free(up)Mail downloads it, checks it is exactly the published release, puts it in place of this copy and restarts. "
      + "The old copy is deleted. Your accounts, settings and backups stay as they are."
    : info.source
      ? "You're running Free(up)Mail from its source code: update it with <code>git pull</code>, or download the new release."
      : "Download the new version from its release page and use it in place of this file.";
  modal(`<h2>Free(up)Mail ${esc(info.latest)}</h2>
    <p>You have ${esc(info.current)}.</p>
    ${info.notes ? `<pre class="notes">${esc(info.notes)}</pre>` : ""}
    <p class="note">${how}</p>
    <div class="foot"><button value="cancel">Later</button>
      ${info.can_install ? '<button value="install" class="primary">Update now</button>'
        : '<button value="page" class="primary">Open the release page</button>'}</div>`,
  async (v) => {
    if (v === "install") {
      $("#update-banner").hidden = true;
      ui.startJob("/api/update/install", {});
    }
    if (v === "page") await api("/api/open", { url: info.page });
    return true;
  });
}
