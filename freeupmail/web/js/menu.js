/* Small popup menus (Theme, ?) anchored under a toolbar button. */
import { $, esc } from "./util.js";

let closeMenu = null;

/** items: [{ label, icon?, on?, run }] or "-" for a separator. */
export function showMenu(anchor, items) {
  if (closeMenu) { closeMenu(); return; }
  const menu = $("#menu");
  menu.innerHTML = items.map((it, i) => (it === "-" ? "<hr>"
    : `<button data-i="${i}" class="${it.on ? "on" : ""}">${it.icon || ""}<span>${esc(it.label)}</span></button>`)).join("");
  menu.hidden = false;
  const r = anchor.getBoundingClientRect();
  menu.style.top = `${r.bottom + 2}px`;
  menu.style.left = `${Math.max(6, Math.min(r.left, window.innerWidth - menu.offsetWidth - 6))}px`;
  const close = () => {
    menu.hidden = true;
    document.removeEventListener("pointerdown", outside, true);
    document.removeEventListener("keydown", onKey, true);
    closeMenu = null;
  };
  const outside = (ev) => { if (!menu.contains(ev.target) && !anchor.contains(ev.target)) close(); };
  const onKey = (ev) => { if (ev.key === "Escape") { ev.stopPropagation(); close(); } };
  menu.onclick = (ev) => {
    const b = ev.target.closest("button[data-i]");
    if (!b) return;
    close();
    items[b.dataset.i].run();
  };
  document.addEventListener("pointerdown", outside, true);
  document.addEventListener("keydown", onKey, true);
  closeMenu = close;
}
