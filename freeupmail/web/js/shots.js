/* Demo-only hooks used to take the README screenshots (main.js ignores them outside --demo). */
import { api } from "./util.js";
import { ui } from "./state.js";
import { openReview } from "./review.js";
import { accountsDialog } from "./accounts.js";
import { rulesDialog, editRule } from "./rules.js";
import { applyTheme } from "./theme.js";
import { welcome, startTour } from "./tour.js";

/** Demo-only hooks for README screenshots: ?shot=rules, ?shot=tour-3, ?shot=email ... */
export async function screenshot(shots, helpers) {
  for (const shot of shots.split(",")) await one(shot, helpers);
}

async function one(shot, { openItem, reloadList }) {
  const [what, arg] = shot.split("-");
  if (what === "theme") applyTheme(arg, false);
  if (what === "welcome") welcome();
  if (what === "tour") startTour(Number(arg || 0));
  if (what === "rules") rulesDialog();
  if (what === "rule") editRule((await api("/api/rules")).rules[Number(arg || 0)]);
  if (what === "accounts") accountsDialog();
  if (what === "review") openReview("REVIEW");
  if (what === "sender" || what === "email") {
    const g = (ui.visible || [])[Number(arg || 0)];
    if (g) openItem(g);
    if (what === "email") setTimeout(() => document.querySelector(".d-list li[data-i]")?.click(), 400);
  }
  if (what === "deleted") { ui.view = "emails"; ui.status = "trashed"; await reloadList(); }
}
