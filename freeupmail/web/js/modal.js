/* The one modal window. Its first <h2> becomes the title bar with a close button and the
   rest goes into the window body; onSubmit(buttonValue, form) returning false keeps it open. */
import { $ } from "./util.js";

export function modal(html, onSubmit, { wide = false, cls = "" } = {}) {
  const dlg = $("#modal");
  const form = $("#modal-body");
  dlg.className = [wide ? "wide" : "", cls].filter(Boolean).join(" ");
  form.innerHTML = html;
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
  if (!dlg.open) dlg.showModal();
  return form;
}
