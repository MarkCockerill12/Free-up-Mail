export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const fmt = (n) => Number(n || 0).toLocaleString();
export const short = (account) => (account || "").split("@")[0];
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

/* This run's key: the launcher opens the page with it in the #fragment (never sent
   over the network); it is kept for this window only, then hidden from the address bar. */
function readKey() {
  const fromHash = new URLSearchParams(location.hash.slice(1)).get("t");
  try {
    if (fromHash) sessionStorage.setItem("freeupmail.key", fromHash);
    return fromHash || sessionStorage.getItem("freeupmail.key") || "";
  } catch {
    return fromHash || "";
  } finally {
    if (fromHash) history.replaceState(null, "", location.pathname + location.search);
  }
}
export const KEY = readKey();
export const withKey = (url) => `${url}${url.includes("?") ? "&" : "?"}t=${encodeURIComponent(KEY)}`;

export async function api(path, body) {
  const opts = { headers: { "X-FreeUpMail": KEY } };
  if (body !== undefined) {
    Object.assign(opts, { method: "POST", body: JSON.stringify(body) });
    opts.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({ error: res.statusText }));
  if (!res.ok) {
    const err = new Error(data.error || res.statusText);
    Object.assign(err, { locked: !!data.locked, permission: data.permission });
    throw err;
  }
  return data;
}

export const sizeText = (bytes) => (bytes > 1e9 ? `${(bytes / 1e9).toFixed(1)} GB`
  : bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.ceil(bytes / 1e3)} KB`);

export function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

let toastTimer;
/** Bottom toast; with `undo` it shows an Undo button (also bound to Z). */
export function toast(message, undo) {
  const el = $("#toast");
  el.innerHTML = `<span>${esc(message)}</span>${undo ? "<button>Undo</button>" : ""}`;
  el.hidden = false;
  toast.undo = undo || null;
  if (undo) el.querySelector("button").onclick = () => { el.hidden = true; toast.undo = null; undo(); };
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; toast.undo = null; }, undo ? 9000 : 4000);
}

export const STATUS_LABEL = {
  delete: "✖ Will delete", protected: "🛡 Protected", keep: "✔ Keep", review: "? Review", trashed: "🗑 Deleted",
};
export const ACTION_LABEL = { DELETE: "✖ Delete", REVIEW: "? Review", KEEP: "✔ Keep", MIXED: "Mixed" };
export const KIND_LABEL = {
  security: "security", receipt: "receipt", shipping: "delivery", starred: "starred", pinned: "pinned", rule: "kept by rule",
};

/** Render an email body into a sandboxed iframe. Remote content (tracking pixels,
 *  remote CSS) is blocked by CSP unless the user asks for images. */
export function renderBody(iframe, msg, allowRemote) {
  const csp = allowRemote
    ? "default-src 'none'; img-src * data:; style-src * 'unsafe-inline'; font-src * data:"
    : "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:";
  const head = `<meta http-equiv="Content-Security-Policy" content="${csp}"><meta name="color-scheme" content="light"><base target="_blank">`
    + "<style>body{font:14px/1.5 system-ui,sans-serif;margin:14px;color:#1b2130;background:#fff;overflow-wrap:anywhere}"
    + "img{max-width:100%;height:auto}pre{white-space:pre-wrap;font:inherit}</style>";
  const body = msg.html || `<pre>${esc(msg.text || "(empty email)")}</pre>`;
  iframe.setAttribute("sandbox", "allow-popups allow-popups-to-escape-sandbox");
  iframe.srcdoc = head + body;
}

export function hasRemoteContent(msg) {
  return /(src|href)\s*=\s*["']?https?:|url\(\s*["']?https?:/i.test(msg.html || "");
}
