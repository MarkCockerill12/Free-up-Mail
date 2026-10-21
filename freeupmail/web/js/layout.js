/* Resizable, collapsible side panes and task-pane sections. Remembered per browser. */
import { $, $$ } from "./util.js";

const KEY = "freeupmail.layout";
const LIMITS = { left: [170, 480], right: [300, 1000] };
const MIN_CENTRE = 460; // the list always keeps at least this much room
// lw / rw stay null until you drag a divider, so the panes start in proportion to the window.
const state = { lw: null, rw: null, left: true, right: true, panels: {} };

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/** Pane widths that fit the current window: your sizes when there is room, smaller ones when not. */
function fitted() {
  const W = window.innerWidth;
  let lw = state.lw ?? clamp(Math.round(W * 0.17), 180, 260);
  let rw = state.rw ?? clamp(Math.round(W * 0.32), 320, 540);
  const room = W - MIN_CENTRE - 12;
  const used = (state.left ? lw : 0) + (state.right ? rw : 0);
  if (used > room) {
    const scale = Math.max(0, room) / used;
    lw = Math.max(LIMITS.left[0], Math.round(lw * scale));
    rw = Math.max(LIMITS.right[0], Math.round(rw * scale));
  }
  return { lw, rw };
}

function load() {
  try { Object.assign(state, JSON.parse(localStorage.getItem(KEY)) || {}); } catch { /* storage unavailable */ }
}
function save() {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* storage unavailable */ }
}

function apply() {
  const app = $("#app");
  const { lw, rw } = fitted();
  app.style.setProperty("--lw-open", `${lw}px`);
  app.style.setProperty("--rw-open", `${rw}px`);
  app.classList.toggle("left-collapsed", !state.left);
  app.classList.toggle("right-collapsed", !state.right);
  $("#btn-pane-left").classList.toggle("on", state.left);
  $("#btn-pane-right").classList.toggle("on", state.right);
  $$(".tp-panel").forEach((p) => p.classList.toggle("closed", !!state.panels[p.dataset.panel]));
}

export function setPane(side, open) {
  state[side] = open ?? !state[side];
  apply();
  save();
}
export const paneOpen = (side) => state[side];

function startDrag(ev, side) {
  if (ev.button !== 0 || ev.target.closest("button") || !state[side]) return;
  const splitter = ev.currentTarget;
  const startX = ev.clientX;
  const startW = fitted()[side === "left" ? "lw" : "rw"];
  const [min, max] = LIMITS[side];
  splitter.setPointerCapture(ev.pointerId);
  document.body.classList.add("dragging");
  const move = (e) => {
    const dx = e.clientX - startX;
    const other = side === "left" ? (state.right ? fitted().rw : 0) : (state.left ? fitted().lw : 0);
    const limit = Math.min(max, window.innerWidth - MIN_CENTRE - other);
    const w = Math.round(Math.max(min, Math.min(limit, side === "left" ? startW + dx : startW - dx)));
    state[side === "left" ? "lw" : "rw"] = w;
    apply();
  };
  const up = () => {
    document.body.classList.remove("dragging");
    splitter.removeEventListener("pointermove", move);
    splitter.removeEventListener("pointerup", up);
    save();
  };
  splitter.addEventListener("pointermove", move);
  splitter.addEventListener("pointerup", up);
}

export function initLayout() {
  load();
  if (window.innerWidth < 900) Object.assign(state, { left: false, right: false });
  apply();
  window.addEventListener("resize", apply);
  $$(".splitter").forEach((sp) => {
    const side = sp.dataset.split;
    sp.addEventListener("pointerdown", (ev) => startDrag(ev, side));
    sp.addEventListener("dblclick", (ev) => { if (!ev.target.closest("button")) setPane(side); });
  });
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-collapse], [data-expand]");
    if (!b) return;
    if (b.dataset.collapse) setPane(b.dataset.collapse, false);
    else setPane(b.dataset.expand, true);
  });
  $("#btn-pane-left").addEventListener("click", () => setPane("left"));
  $("#btn-pane-right").addEventListener("click", () => setPane("right"));
  $$(".tp-panel").forEach((p) => p.querySelector(".tp-head").addEventListener("click", () => {
    state.panels[p.dataset.panel] = !state.panels[p.dataset.panel];
    apply();
    save();
  }));
}
