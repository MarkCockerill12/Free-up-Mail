/* Themes: each is a set of CSS variables in themes/*.css, switched with html[data-theme]. */
import { api } from "./util.js";
import { showMenu } from "./menu.js";

export const THEMES = [
  ["xp", "Windows XP"], ["win10", "Windows 10"], ["aero", "Vista / 7 Aero"], ["minimal", "Minimal"], ["dark", "Minimal Dark"],
];

export const currentTheme = () => document.documentElement.dataset.theme || "xp";

export function applyTheme(name, save = true) {
  if (!THEMES.some(([t]) => t === name)) name = "xp";
  document.documentElement.dataset.theme = name;
  try { localStorage.setItem("freeupmail.theme", name); } catch { /* storage unavailable */ }
  if (save) api("/api/settings", { theme: name }).catch(() => {});
}

/** The saved setting wins over this window's remembered one (e.g. a fresh app window). */
export function syncTheme(settings) {
  if (settings.theme && settings.theme !== currentTheme()) applyTheme(settings.theme, false);
}

export function themeMenu(anchor) {
  showMenu(anchor, THEMES.map(([id, label]) => ({
    label, icon: `<span class="swatch ${id}"></span>`, on: id === currentTheme(), run: () => applyTheme(id),
  })));
}

/** Theme buttons for the welcome window. */
export function themePicker() {
  return `<div class="theme-pick">${THEMES.map(([id, label]) => `<button type="button" data-theme-pick="${id}"
    class="${id === currentTheme() ? "on" : ""}"><span class="swatch ${id}"></span>${label}</button>`).join("")}</div>`;
}

export function wireThemePicker(root) {
  root.querySelectorAll("[data-theme-pick]").forEach((b) => (b.onclick = () => {
    applyTheme(b.dataset.themePick);
    root.querySelectorAll("[data-theme-pick]").forEach((x) => x.classList.toggle("on", x === b));
  }));
}
