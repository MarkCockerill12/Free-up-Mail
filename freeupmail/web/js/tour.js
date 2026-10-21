/* First-run welcome (it all runs on this computer) and the guided tutorial. The tutorial
   highlights each part of the window in turn; everything stays clickable while it runs,
   and it can be closed at any time with ✕ or Esc. It is always under the "?" button. */
import { $, api, esc } from "./util.js";
import { ui } from "./state.js";
import { modal } from "./modal.js";
import { setPane } from "./layout.js";
import { themePicker, wireThemePicker } from "./theme.js";

const STEPS = [
  { at: "#nav-accounts", title: "Your accounts",
    text: "Every mailbox you connect is listed here. Click one to see only that account, or <b>All accounts</b> to see everything together." },
  { at: "#btn-accounts", title: "Add accounts and choose permissions",
    text: "Connect Outlook, Gmail, Yahoo, iCloud, AOL or any other mailbox. For each one you decide exactly what Free(up)Mail may do — it starts by only listing your emails.",
    try: "Click <b>Accounts</b> to see the permission ticks." },
  { at: "#btn-scan", title: "Rescan",
    text: "Reads the sender, subject and date of each email so they can be sorted. Email contents are only opened when you click one (and only if allowed)." },
  { at: "#list-wrap", title: "Everything, grouped by sender",
    text: "Each row is a sender and how many emails they've sent you. The <b>Decision</b> column is a suggestion: <b>Delete</b>, <b>Review</b> or <b>Keep</b>. Nothing happens until you clean up.",
    try: "Change a decision with its dropdown, or select a row and press <kbd>D</kbd>, <kbd>K</kbd> or <kbd>R</kbd>." },
  { at: "#nav-status", title: "Filter the list",
    text: "Show only the senders marked Delete, the ones that need review, or the ones you changed. In <b>All emails</b> this lists emails by status, including <b>Deleted</b> ones you can restore." },
  { at: "#nav-views", title: "Senders or single emails",
    text: "Switch to <b>All emails</b> to see every email, <b>pin</b> ones you always want to keep, and restore deleted ones." },
  { at: "#detail", title: "Preview pane", open: () => setPane("right", true),
    text: "Click a row to see that sender's emails here, then click an email to read it. Images that track you are blocked until you allow them.",
    try: "Click any sender in the list." },
  { at: "#btn-review", title: "Review mode",
    text: "Go through the senders that need a decision one at a time, with their latest email next to them. Keys: <kbd>D</kbd> delete, <kbd>K</kbd> keep, <kbd>S</kbd> skip." },
  { at: "#btn-settings", title: "Protection and backups",
    text: "Receipts, security alerts and delivery updates are kept even from senders you delete. <b>Backups</b> save a copy of each email on this computer before it is deleted." },
  { at: "#btn-rules", title: "Rules: send emails wherever you want",
    text: "Make your own rules — move to a folder, archive, junk, bin, mark read, flag, or <b>always keep</b>. Run them now, after every scan, or install them in Gmail and Outlook." },
  { at: "#btn-cleanup", title: "Clean up — only when you say so",
    text: "Nothing is deleted until you press this and confirm. Emails go to Trash with a backup kept here, and <b>Deleted</b> in the sidebar puts them back." },
  { at: "#btn-theme", title: "Make it yours",
    text: "Pick Windows XP, Windows 10, Vista Aero, Minimal or Minimal Dark. Drag the dividers to resize the side panes." },
  { at: "#btn-help", title: "Help is always here",
    text: "This tutorial, the welcome screen, keyboard shortcuts and privacy details all live under <b>?</b>. That's it — enjoy your tidier inbox!" },
];

let step = -1;
let onKey = null;

export function welcome() {
  const form = modal(`<h2>Welcome to Free(up)Mail</h2>
    <div class="welcome">
      <p class="lead">Clear the junk out of all your inboxes at once, without losing the emails that matter.</p>
      <ul>
        <li><span class="ico">💻</span><span><b>Everything runs on this computer.</b> There is no Free(up)Mail server, account or
          tracking: the app talks straight to your email providers, and your passwords, email list and backups stay here.</span></li>
        <li><span class="ico">✋</span><span><b>Nothing happens without your permission.</b> For each account you choose what
          Free(up)Mail may do. To start with it can only list your emails.</span></li>
        <li><span class="ico">🧹</span><span><b>You decide.</b> Everything is just a suggestion until you press <b>Clean up</b> and confirm.</span></li>
        <li><span class="ico">💾</span><span><b>Nothing is lost.</b> Deleted emails go to Trash, and a copy is saved here first so you can put them back.</span></li>
      </ul>
      <h3>Pick a look (you can change it any time with 🎨 Theme)</h3>
      ${themePicker()}
    </div>
    <div class="foot"><button value="skip">Skip the tour</button><button value="tour" class="primary">Show me around</button></div>`,
  async (v) => {
    await api("/api/settings", { welcome_done: "1" }).catch(() => {});
    ui.settings.welcome_done = "1";
    if (v === "tour") setTimeout(startTour, 50);
  }, { cls: "medium" });
  wireThemePicker(form);
}

export function startTour(first = 0) {
  step = first;
  $("#tour").hidden = false;
  onKey = (ev) => {
    if ($("#modal").open) return;
    if (ev.key === "Escape") endTour();
    else if (ev.key === "ArrowRight") go(1);
    else if (ev.key === "ArrowLeft") go(-1);
    else return;
    ev.preventDefault();
    ev.stopPropagation();
  };
  document.addEventListener("keydown", onKey, true);
  window.addEventListener("resize", place);
  show();
}

export function endTour() {
  step = -1;
  $("#tour").hidden = true;
  $("#tour").innerHTML = "";
  document.removeEventListener("keydown", onKey, true);
  window.removeEventListener("resize", place);
}

function go(delta) {
  const next = step + delta;
  if (next < 0) return;
  if (next >= STEPS.length) { endTour(); return; }
  step = next;
  show();
}

function show() {
  const s = STEPS[step];
  s.open?.();
  $("#tour").innerHTML = `<div class="tour-dim"></div><div class="tour-dim"></div><div class="tour-dim"></div><div class="tour-dim"></div>
    <div class="tour-hole"></div>
    <section class="tour-card xp-window" role="dialog" aria-label="${esc(s.title)}">
      <div class="xp-title"><span>${esc(s.title)}</span><button class="xp-close" data-tour="end" title="Close the tutorial (Esc)">✕</button></div>
      <div class="content"><p>${s.text}</p>${s.try ? `<div class="try">👉 ${s.try}</div>` : ""}
        <div class="tour-foot"><span class="step">${step + 1} of ${STEPS.length}</span>
          <button data-tour="back" ${step ? "" : "disabled"}>Back</button>
          <button class="primary" data-tour="next">${step === STEPS.length - 1 ? "Finish" : "Next"}</button></div></div>
    </section>`;
  $("#tour").onclick = (ev) => {
    const act = ev.target.closest("[data-tour]")?.dataset.tour;
    if (act === "end") endTour();
    if (act === "back") go(-1);
    if (act === "next") go(1);
  };
  requestAnimationFrame(place);
}

function place() {
  if (step < 0) return;
  const target = $(STEPS[step].at);
  const hole = $("#tour .tour-hole");
  const card = $("#tour .tour-card");
  if (!hole || !card) return;
  const r = target?.getBoundingClientRect();
  const visible = r && r.width > 0 && r.height > 0;
  hole.hidden = !visible;
  const pad = 4;
  const box = visible ? { l: r.left - pad, t: r.top - pad, r: r.right + pad, b: r.bottom + pad } : { l: 0, t: 0, r: 0, b: 0 };
  Object.assign(hole.style, { left: `${box.l}px`, top: `${box.t}px`, width: `${box.r - box.l}px`, height: `${box.b - box.t}px` });
  // Four panels dim everything around the highlighted part (it stays clickable).
  const [dimTop, dimBottom, dimLeft, dimRight] = document.querySelectorAll("#tour .tour-dim");
  Object.assign(dimTop.style, { left: "0", top: "0", width: "100vw", height: `${Math.max(0, box.t)}px` });
  Object.assign(dimBottom.style, { left: "0", top: `${box.b}px`, width: "100vw", bottom: "0" });
  Object.assign(dimLeft.style, { left: "0", top: `${box.t}px`, width: `${Math.max(0, box.l)}px`, height: `${box.b - box.t}px` });
  Object.assign(dimRight.style, { left: `${box.r}px`, top: `${box.t}px`, right: "0", height: `${box.b - box.t}px` });
  const cw = card.offsetWidth;
  const ch = card.offsetHeight;
  let left = window.innerWidth / 2 - cw / 2;
  let top = window.innerHeight / 2 - ch / 2;
  if (visible) {
    const below = r.bottom + 12;
    const right = r.right + 14;
    if (r.height > window.innerHeight * 0.5) {  // tall areas (list, panes): beside them
      left = right + cw < window.innerWidth ? right : Math.max(10, r.left - cw - 14);
      if (left + cw > r.left && left < r.right) left = r.left + 20;  // no room either side: inside
      top = Math.min(Math.max(10, r.top + 40), window.innerHeight - ch - 10);
    } else {
      top = below + ch < window.innerHeight ? below : Math.max(10, r.top - ch - 12);
      left = Math.min(Math.max(10, r.left + r.width / 2 - cw / 2), window.innerWidth - cw - 10);
    }
  }
  Object.assign(card.style, { left: `${left}px`, top: `${top}px` });
}
