import { api, toast, fmt } from "./util.js";

export const ui = {
  view: "senders",          // senders | emails
  account: "",              // "" = all
  status: "all",            // senders: all/DELETE/REVIEW/KEEP/MANUAL; emails: all/delete/protected/keep/review/pinned/trashed
  cat: "",
  group: "sender",          // sender | domain
  q: "", unsubOnly: false, inboxOnly: false, unreadOnly: false, minCount: 1,
  sort: { key: "count", dir: -1 },
  senders: [], groups: [], accounts: [], settings: {}, trashed: 0, plan: {}, permLabels: {}, backups: null, demo: false,
  emails: { items: [], total: 0, loading: false },
  selected: new Set(), cursor: null, jobRunning: false,
};

export const listeners = new Set();
export const changed = () => listeners.forEach((fn) => fn());

export async function loadState() {
  const st = await api("/api/state");
  Object.assign(ui, {
    accounts: st.accounts, settings: st.settings, trashed: st.trashed, permLabels: st.perm_labels,
    backups: st.backups, demo: st.demo, dataFolder: st.data_folder,
  });
  return st;
}

export async function loadSenders() {
  ui.senders = await api("/api/senders");
  ui.groups = buildGroups();
  refreshPlan();
}

export async function refreshPlan() {
  ui.plan = await api("/api/plan");
  changed();
}

export const planTotal = () => Object.entries(ui.plan)
  .filter(([acc]) => !ui.account || acc === ui.account)
  .reduce((n, [, c]) => n + c, 0);

export function buildGroups() {
  const map = new Map();
  for (const s of ui.senders) {
    const label = ui.group === "domain" ? s.domain : s.from_addr;
    const key = `${s.account}|${label}`;
    let g = map.get(key);
    if (!g) {
      g = {
        key, account: s.account, label, names: new Set(), members: [], count: 0, unread: 0, unsub: 0, inbox: 0,
        would_delete: 0, kinds: { security: 0, receipt: 0, shipping: 0, starred: 0, pinned: 0 }, cats: {},
        actions: new Set(), manual: false, contact: false, reasons: new Set(), last_date: "", sample_subject: "",
        unsub_url: "", spammy: false,
      };
      map.set(key, g);
    }
    if (s.from_name) g.names.add(s.from_name);
    g.members.push(s);
    g.count += s.count; g.unread += s.unread; g.unsub += s.unsub; g.inbox += s.inbox;
    g.would_delete += s.would_delete;
    for (const k in s.kinds) g.kinds[k] += s.kinds[k];
    for (const c in s.cats) g.cats[c] = (g.cats[c] || 0) + s.cats[c];
    g.actions.add(s.action);
    g.manual ||= !!s.override;
    g.contact ||= s.is_contact;
    g.spammy ||= s.spammy;
    g.reasons.add(s.override ? "changed by you" : s.reason);
    g.unsub_url ||= s.unsub_url;
    if (s.last_date > g.last_date) { g.last_date = s.last_date; g.sample_subject = s.sample_subject; }
  }
  for (const g of map.values()) {
    g.action = g.actions.size === 1 ? [...g.actions][0] : "MIXED";
    g.protected = Object.values(g.kinds).reduce((a, b) => a + b, 0);
    g.topCat = Object.entries(g.cats).sort((a, b) => b[1] - a[1])[0]?.[0] || "";
    g.name = [...g.names][0] || "";
  }
  return [...map.values()];
}

/** Groups after every filter except the status one (used for sidebar counts). */
export function baseGroups() {
  const q = ui.q.trim().toLowerCase();
  return ui.groups.filter((g) => {
    if (ui.account && g.account !== ui.account) return false;
    if (g.count < ui.minCount) return false;
    if (ui.cat === "none" ? g.topCat : ui.cat && !g.cats[ui.cat]) return false;
    if (ui.unsubOnly && !g.unsub) return false;
    if (ui.inboxOnly && !g.inbox) return false;
    if (q && !`${g.label} ${[...g.names].join(" ")} ${g.sample_subject}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

export function matchesStatus(g, status) {
  if (status === "all") return true;
  if (status === "MANUAL") return g.manual;
  return g.action === status;
}

export function filteredGroups() {
  const rows = baseGroups().filter((g) => matchesStatus(g, ui.status));
  const { key, dir } = ui.sort;
  const val = (g) => (key === "cats" ? g.topCat : key === "label" ? (g.name || g.label).toLowerCase() : g[key]);
  return rows.sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : 0) * dir);
}

/** Apply sender decisions; returns a function that undoes them. */
export async function setActions(members, action, { silent = false } = {}) {
  const before = members.map((m) => [m.account, m.from_addr, m.override || "AUTO"]);
  const changes = members.map((m) => [m.account, m.from_addr, action]);
  if (!changes.length) return;
  await api("/api/actions", { changes });
  await loadSenders();
  const undo = async () => { await api("/api/actions", { changes: before }); await loadSenders(); toast("Undone"); };
  if (!silent) {
    const what = action === "AUTO" ? "reset to suggestion" : `marked ${action.toLowerCase()}`;
    toast(`${fmt(changes.length)} sender${changes.length > 1 ? "s" : ""} ${what}`, undo);
  }
  return undo;
}

export const accountType = (email) => ui.accounts.find((a) => a.email === email)?.type || "imap";

const FREEMAIL = /^(gmail|googlemail|outlook|hotmail|live|msn|yahoo|icloud|me|aol|proton|protonmail)\./i;

/** Senders a decision applies to. Bulk/domain DELETE never sweeps up people you have
 *  emailed (deleting a single contact still works when chosen on its own). */
export function membersFor(groups, action) {
  const members = groups.flatMap((g) => g.members);
  if (action !== "DELETE" || members.length === 1) return members;
  return members.filter((m) => !m.is_contact);
}

/** Confirmation text when a DELETE would hit a personal-mail domain such as gmail.com. */
export function deleteWarning(groups, action) {
  if (action !== "DELETE" || ui.group !== "domain") return "";
  const hit = groups.filter((g) => FREEMAIL.test(g.label || ""));
  if (!hit.length) return "";
  const n = hit.reduce((a, g) => a + g.members.length, 0);
  return `${hit.map((g) => g.label).join(", ")} is personal email used by real people (${n} senders). `
    + "Mark all of them delete? (People you've emailed are skipped either way.)";
}
