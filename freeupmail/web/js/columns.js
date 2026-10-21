/* Resizable list columns: drag the edge of a column heading, double-click it to reset.
   Widths are remembered per view; columns without a width share the spare space. */
import { $, $$ } from "./util.js";

const KEY = "freeupmail.columns";
// [column key, default width in px (null = takes the spare space), minimum]
export const COLUMNS = {
  senders: [["pick", 30, 30], ["action", 114, 96], ["label", null, 140], ["count", 62, 50],
    ["would_delete", 74, 56], ["protected", null, 70], ["last_date", 88, 76]],
  emails: [["pick", 30, 30], ["status", 112, 80], ["from", null, 110], ["subject", null, 120],
    ["date", 118, 80], ["account", 100, 70]],
};
let widths = {};
try { widths = JSON.parse(localStorage.getItem(KEY)) || {}; } catch { /* storage unavailable */ }
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(widths)); } catch { /* storage unavailable */ } };

/** After the table head is drawn: set the <colgroup> widths and add the drag handles. */
export function applyColumns(view) {
  const table = $("#list");
  const cols = COLUMNS[view].filter(([key]) => table.querySelector(`th[data-col="${key}"]`));
  const mine = widths[view] || {};
  table.querySelector("colgroup")?.remove();
  table.insertAdjacentHTML("afterbegin", `<colgroup>${cols.map(([key, def]) => {
    const w = mine[key] ?? def;
    return `<col data-col="${key}"${w ? ` style="width:${w}px"` : ""}>`;
  }).join("")}</colgroup>`);
  // Keep the table at least as wide as its fixed columns, so nothing gets squashed to nothing.
  const fixed = cols.reduce((n, [key, def, min]) => n + (mine[key] ?? def ?? min), 0); // flexible ones at their minimum
  table.style.minWidth = `${fixed}px`;
  $$("#list th[data-col]").forEach((th) => {
    if (th.dataset.col === "pick" || th.querySelector(".col-grip")) return;
    th.insertAdjacentHTML("beforeend", '<span class="col-grip" title="Drag to resize · double-click to reset"></span>');
  });
}

export function initColumns(getView) {
  const head = $("#list thead");
  head.addEventListener("click", (ev) => { if (ev.target.closest(".col-grip")) ev.stopPropagation(); }, true);
  head.addEventListener("dblclick", (ev) => {
    const grip = ev.target.closest(".col-grip");
    if (!grip) return;
    const view = getView();
    delete (widths[view] || {})[grip.parentElement.dataset.col];
    save();
    applyColumns(view);
  });
  head.addEventListener("pointerdown", (ev) => {
    const grip = ev.target.closest(".col-grip");
    if (!grip || ev.button !== 0) return;
    ev.preventDefault();
    const view = getView();
    const key = grip.parentElement.dataset.col;
    const spec = COLUMNS[view].find(([k]) => k === key);
    const col = $(`#list col[data-col="${key}"]`);
    const startX = ev.clientX;
    const startW = grip.parentElement.getBoundingClientRect().width;
    grip.setPointerCapture(ev.pointerId);
    document.body.classList.add("dragging");
    const move = (e) => {
      const w = Math.round(Math.max(spec[2], startW + e.clientX - startX));
      col.style.width = `${w}px`;
      (widths[view] ||= {})[key] = w;
    };
    const up = () => {
      document.body.classList.remove("dragging");
      grip.removeEventListener("pointermove", move);
      grip.removeEventListener("pointerup", up);
      save();
      applyColumns(view);
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up);
  });
}
