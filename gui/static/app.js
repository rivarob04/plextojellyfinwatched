"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const KINDS = ["plex", "jellyfin", "emby"];
const KIND_LABEL = { plex: "Plex", jellyfin: "Jellyfin", emby: "Emby" };
let cfg = {};
let dirty = false;
let logOffset = 0;
let logLines = [];
let lastRunning = false;
let testResults = {};
let discovery = {}; // server name -> { type, users: [], libraries: [{name,type}], updated }

// Let render code pass null/false for "nothing here" without printing it.
const _replaceChildren = Element.prototype.replaceChildren;
Element.prototype.replaceChildren = function (...kids) {
  return _replaceChildren.apply(this, kids.flat().filter((k) => k != null && k !== false));
};

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || data.detail || res.statusText);
  return data;
}

function toast(msg, kind = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), kind === "bad" ? 9000 : 3500);
}

function markDirty() {
  dirty = true;
  $("#dirty").hidden = false;
}
function markClean() {
  dirty = false;
  $("#dirty").hidden = true;
}

const allServers = () =>
  KINDS.flatMap((k) => (cfg[k] || []).map((s, i) => ({ kind: k, index: i, server: s })));
const serverNames = () => allServers().map((x) => x.server.name).filter(Boolean);

const uniqueCI = (items) => {
  const seen = new Map();
  for (const v of items) if (v && !seen.has(v.toLowerCase())) seen.set(v.toLowerCase(), v);
  return [...seen.values()].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
};
const usersOf = (server) => discovery[server]?.users || [];
const libsOf = (server) => (discovery[server]?.libraries || []).map((l) => l.name);
const libLabel = (server, name) => {
  const lib = (discovery[server]?.libraries || []).find((l) => l.name === name);
  return lib && lib.type ? `${name}  (${lib.type})` : name;
};
const configured = () => new Set(serverNames());
const allUsers = () => uniqueCI(Object.entries(discovery).filter(([n]) => configured().has(n)).flatMap(([, d]) => d.users || []));
const allLibs = () => uniqueCI(Object.entries(discovery).filter(([n]) => configured().has(n)).flatMap(([, d]) => (d.libraries || []).map((l) => l.name)));

// ---------------------------------------------------------------------------
// Generic widgets
// ---------------------------------------------------------------------------
function textField(label, value, onInput, opts = {}) {
  const input = h("input", {
    type: opts.type || "text",
    value: value ?? "",
    placeholder: opts.placeholder,
    autocomplete: "off",
    oninput: (e) => { onInput(e.target.value); if (!opts.noDirty) markDirty(); },
  });
  let control = input;
  if (opts.secret) {
    input.type = "password";
    control = h("div", { class: "pw" }, input,
      h("button", { type: "button", onclick: (e) => {
        input.type = input.type === "password" ? "text" : "password";
        e.target.textContent = input.type === "password" ? "Show" : "Hide";
      } }, "Show"));
  }
  return h("label", { class: "field" }, label, control,
    opts.hint ? h("span", { class: "hint" }, opts.hint) : null);
}

function numberField(label, value, onInput, opts = {}) {
  return textField(label, value, (v) => onInput(v === "" ? null : Number(v)), { ...opts, type: "number" });
}

function checkField(label, value, onChange) {
  return h("label", { class: "switch-row compact" },
    h("input", { type: "checkbox", checked: !!value, onchange: (e) => { onChange(e.target.checked); markDirty(); } }),
    h("span", {}, label));
}

function selectField(label, value, options, onChange, opts = {}) {
  const sel = h("select", { onchange: (e) => { onChange(e.target.value); if (!opts.noDirty) markDirty(); } },
    options.map((o) => h("option", { value: o, selected: o === value ? true : null }, o || "— choose —")));
  sel.value = value ?? "";
  return label ? h("label", { class: "field" }, label, sel) : sel;
}

/**
 * Dropdown of names found on a server, with an escape hatch to type a name.
 * Falls back to a text box when nothing has been discovered for that server.
 */
function pickField(value, options, onChange, opts = {}) {
  const typeIt = "__type__";
  const textBox = (v) => h("input", { type: "text", value: v || "", placeholder: opts.placeholder || "Type a name",
    oninput: (e) => { onChange(e.target.value); markDirty(); } });
  if (!options.length) return textBox(value);
  const known = !value || options.some((o) => o.toLowerCase() === value.toLowerCase());
  const wrap = h("div", { class: "pick" });
  const sel = h("select", {
    onchange: (e) => {
      if (e.target.value === typeIt) {
        const box = textBox("");
        wrap.replaceChildren(box); box.focus();
        onChange(""); markDirty(); return;
      }
      onChange(e.target.value); markDirty();
    },
  },
    h("option", { value: "" }, opts.empty || "— choose —"),
    options.map((o) => h("option", { value: o }, opts.label ? opts.label(o) : o)),
    !known ? h("option", { value }, `${value}  (not found on server)`) : null,
    h("option", { value: typeIt }, "✎ Type another name…"));
  const match = options.find((o) => o.toLowerCase() === (value || "").toLowerCase());
  sel.value = known ? (match || "") : value;
  wrap.append(sel);
  return wrap;
}

/** Tag input editing an array of strings in place. */
function chipField(label, arr, onChange, opts = {}) {
  const list = Array.isArray(arr) ? [...arr] : [];
  const wrap = h("div", { class: "chips" });
  const listId = opts.suggest ? "dl-" + Math.random().toString(36).slice(2) : null;
  const input = h("input", { placeholder: opts.placeholder || "Type and press Enter", list: listId });
  const commit = (v) => {
    v = v.trim().replace(/,$/, "").trim();
    if (!v || list.includes(v)) return;
    list.push(v); onChange([...list]); markDirty(); render();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); commit(input.value); input.value = ""; }
    else if (e.key === "Backspace" && !input.value && list.length) { list.pop(); onChange([...list]); markDirty(); render(); }
  });
  input.addEventListener("blur", () => { if (input.value) { commit(input.value); input.value = ""; } });
  input.addEventListener("change", () => { if (opts.suggest && opts.suggest.includes(input.value)) { commit(input.value); input.value = ""; } });
  function render() {
    wrap.replaceChildren(
      ...list.map((v, i) => h("span", { class: "chip" }, v,
        h("button", { type: "button", "aria-label": `Remove ${v}`, onclick: () => {
          list.splice(i, 1); onChange([...list]); markDirty(); render();
        } }, "×"))),
      input,
      listId ? h("datalist", { id: listId }, opts.suggest.map((s) => h("option", { value: s }))) : null,
    );
  }
  render();
  return h("label", { class: "field" }, label, wrap, opts.hint ? h("span", { class: "hint" }, opts.hint) : null);
}

// ---------------------------------------------------------------------------
// Servers tab
// ---------------------------------------------------------------------------
function renameServer(oldName, newName) {
  if (!oldName || oldName === newName) return;
  const fix = (n) => (n === oldName ? newName : n);
  for (const { server } of allServers()) server.sync_to = (server.sync_to || []).map(fix);
  for (const m of [...(cfg.user_mappings || []), ...(cfg.library_mappings || [])])
    for (const a of m.aliases || []) a.server = fix(a.server);
  for (const r of [...(cfg.user_sync_rules || []), ...(cfg.library_sync_rules || [])]) {
    r.from = fix(r.from); r.to = fix(r.to);
  }
}

function renderServers() {
  const list = $("#server-list");
  list.replaceChildren();
  const entries = allServers();
  if (!entries.length) {
    list.append(h("div", { class: "card empty" }, "No servers yet. Add a Plex, Jellyfin or Emby server above."));
  }
  for (const { kind, index, server } of entries) {
    let currentName = server.name;
    const others = serverNames().filter((n) => n !== server.name);
    const fields = [
      textField("Name", server.name, (v) => { renameServer(currentName, v); server.name = v; currentName = v; scheduleMatrix(); },
        { hint: "Unique, case-sensitive. Used in sync directions and rules." }),
      textField("Base URL", server.baseurl, (v) => (server.baseurl = v),
        { placeholder: kind === "plex" ? "http://plex.lan:32400" : "http://jellyfin.lan:8096" }),
      textField(kind === "plex" ? "Token" : "API key", server.token, (v) => (server.token = v), { secret: true,
        hint: kind === "plex" ? "Preferred. Or use username/password below." : "Dashboard → API Keys" }),
    ];
    const card = h("div", { class: "server", "data-kind": kind },
      h("div", { class: "server-head" },
        h("div", { class: "row gap" }, h("span", { class: "badge" }, KIND_LABEL[kind]), h("h3", {}, server.name || "(unnamed)")),
        h("button", { class: "btn icon", title: "Remove server", onclick: () => {
          if (!confirm(`Remove ${server.name || "this server"}?`)) return;
          cfg[kind].splice(index, 1);
          for (const s of allServers()) s.server.sync_to = (s.server.sync_to || []).filter((n) => n !== server.name);
          markDirty(); renderAll();
        } }, "✕ Remove")),
      h("div", { class: "server-fields" }, fields,
        chipField("Pushes watched status to", server.sync_to || [], (v) => { server.sync_to = v; renderMatrix(); },
          { suggest: others, placeholder: others.length ? "Pick a server…" : "Add another server first" })),
    );
    if (kind === "plex") {
      card.querySelector(".server-head .row").append(
        h("button", { class: "btn plex", onclick: () => plexSignIn(server) }, "Sign in with Plex"));
      card.append(h("details", { class: "alt", open: server.username ? true : null },
        h("summary", {}, "Sign in with a Plex account instead of a token, and SSL options"),
        h("div", { class: "server-fields" },
          textField("Username", server.username, (v) => (server.username = v)),
          textField("Password", server.password, (v) => (server.password = v), { secret: true }),
          textField("Server name", server.servername, (v) => (server.servername = v), { hint: "As shown in Plex" }),
          h("div", { class: "field" }, "SSL", checkField("Skip certificate verification", server.ssl_bypass, (v) => (server.ssl_bypass = v))),
        )));
    }
    list.append(card);
  }
  renderMatrix();
}

let matrixTimer;
function scheduleMatrix() { clearTimeout(matrixTimer); matrixTimer = setTimeout(() => { renderMatrix(); renderSummary(); }, 300); }

function renderMatrix() {
  const wrap = $("#matrix");
  const entries = allServers().filter((x) => x.server.name);
  if (entries.length < 2) {
    wrap.replaceChildren(h("p", { class: "empty" }, "Add at least two servers to choose sync directions."));
    return;
  }
  const table = h("table", { class: "matrix" },
    h("thead", {}, h("tr", {}, h("th", { class: "corner" }, "from ↓ / to →"),
      entries.map((e) => h("th", {}, h("span", { class: `dot ${e.kind}` }), e.server.name)))),
    h("tbody", {}, entries.map((row) => h("tr", {},
      h("th", { class: "from" }, h("span", { class: `dot ${row.kind}` }), row.server.name),
      entries.map((col) => {
        if (col.server.name === row.server.name) return h("td", { class: "self" });
        const on = (row.server.sync_to || []).includes(col.server.name);
        return h("td", {}, h("input", { type: "checkbox", checked: on,
          "aria-label": `${row.server.name} pushes to ${col.server.name}`,
          onchange: (ev) => {
            const set = new Set(row.server.sync_to || []);
            ev.target.checked ? set.add(col.server.name) : set.delete(col.server.name);
            row.server.sync_to = [...set];
            markDirty(); renderServers(); renderSummary();
          } }));
      })))));
  wrap.replaceChildren(table);
}

function renderSummary() {
  const box = $("#server-summary");
  const entries = allServers();
  if (!entries.length) {
    box.replaceChildren(h("p", { class: "empty" }, "No servers configured. Head to the Servers tab to add one."));
    return;
  }
  box.replaceChildren(...entries.map(({ kind, server }) => {
    const r = testResults[server.name];
    return h("div", { class: "sum", "data-kind": kind },
      h("div", { class: "name" }, server.name || "(unnamed)", h("span", { class: "muted small" }, " · " + KIND_LABEL[kind])),
      h("div", { class: "meta" }, server.baseurl || "no URL"),
      h("div", { class: "meta" }, (server.sync_to || []).length ? "→ " + server.sync_to.join(", ") : "pushes to nothing"),
      r ? h("div", { class: "res " + (r.ok ? "ok" : "bad") }, (r.ok ? "✓ " : "✕ ") + r.detail) : null,
      discovery[server.name] ? h("div", { class: "meta" },
        `${discovery[server.name].users.length} users · ${discovery[server.name].libraries.length} libraries`) : null);
  }));
}

function addServer(kind) {
  cfg[kind] = cfg[kind] || [];
  const names = new Set(serverNames());
  let n = 1, name = kind;
  while (names.has(name)) name = `${kind}-${++n}`;
  cfg[kind].push({ name, baseurl: "", token: "", sync_to: [] });
  markDirty(); renderAll();
  setTimeout(() => $("#server-list").lastElementChild?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
}

// ---------------------------------------------------------------------------
// Settings tab
// ---------------------------------------------------------------------------
function renderSettings() {
  const set = (k) => (v) => (cfg[k] = v);
  $("#settings-behaviour").replaceChildren(
    checkField("Dry run by default (CLI & scheduled runs read this too)", cfg.dryrun ?? true, set("dryrun")),
    selectField("Log level", cfg.debug_level || "INFO", ["INFO", "DEBUG", "TRACE"], set("debug_level")),
    numberField("Request timeout (seconds)", cfg.request_timeout ?? 300, set("request_timeout")),
    numberField("Worker threads", cfg.max_threads ?? 1, set("max_threads"), { hint: "1–128. Higher is faster but heavier on your servers." }),
    h("details", { class: "alt" }, h("summary", {}, "Command-line loop options (not used by the GUI scheduler)"),
      h("div", { class: "form", style: "margin-top:.7rem" },
        checkField("Run only once", cfg.run_only_once, set("run_only_once")),
        numberField("Sleep between passes (seconds)", cfg.sleep_duration ?? 3600, set("sleep_duration")))),
  );
  $("#settings-files").replaceChildren(
    checkField("Match items by provider IDs (IMDb, TMDB, TVDB)", cfg.generate_guids ?? true, set("generate_guids")),
    checkField("Match items by file path", cfg.generate_locations ?? true, set("generate_locations")),
    textField("Log file", cfg.log_file ?? "log.log", set("log_file")),
    textField("Marked-items file", cfg.mark_file ?? "mark.log", set("mark_file"), { hint: "Every item marked watched is appended here." }),
  );
  $("#settings-filters").replaceChildren(
    chipField("Whitelist users", cfg.whitelist_users, set("whitelist_users"), { suggest: allUsers(), placeholder: "Pick or type a user…" }),
    chipField("Blacklist users", cfg.blacklist_users, set("blacklist_users"), { suggest: allUsers(), placeholder: "Pick or type a user…" }),
    chipField("Whitelist libraries", cfg.whitelist_libraries, set("whitelist_libraries"), { suggest: allLibs(), placeholder: "Pick or type a library…" }),
    chipField("Blacklist libraries", cfg.blacklist_libraries, set("blacklist_libraries"), { suggest: allLibs(), placeholder: "Pick or type a library…" }),
    chipField("Whitelist library types", cfg.whitelist_library_types, set("whitelist_library_types"),
      { suggest: ["movie", "show", "movies", "tvshows"] }),
    chipField("Blacklist library types", cfg.blacklist_library_types, set("blacklist_library_types"),
      { suggest: ["music", "photos", "artist", "musicvideos", "books"] }),
  );
}

// ---------------------------------------------------------------------------
// Mappings tab
// ---------------------------------------------------------------------------
function renderMappings() {
  renderDiscoveryBar();
  renderMapList("#user-maps", "user_mappings", "username", "Username on that server", "Person (canonical name)", usersOf);
  renderMapList("#lib-maps", "library_mappings", "library", "Library name on that server", "Library (canonical name)", libsOf);
}

function renderDiscoveryBar() {
  const bar = $("#discovery-bar");
  const found = serverNames().filter((n) => discovery[n]);
  const btn = h("button", { class: "btn", onclick: (e) => testConnections(e.target) },
    found.length ? "↻ Refresh lists" : "Load users & libraries");
  if (!serverNames().length) {
    bar.replaceChildren(h("span", { class: "muted" }, "Add servers on the Servers tab first."));
    return;
  }
  const stamps = found.map((n) => discovery[n].updated).sort();
  const msg = found.length
    ? h("span", {}, h("b", {}, "Dropdowns ready"), ` — users and libraries from ${found.length} of ${serverNames().length} server${serverNames().length === 1 ? "" : "s"}`,
        stamps.length ? h("span", { class: "muted small" }, ` · updated ${new Date(stamps[0]).toLocaleString()}`) : "")
    : h("span", {}, "Connect to your servers to pick users and libraries from a list instead of typing them.");
  const auto = sameNameUsers();
  const ideas = suggestMappings();
  bar.replaceChildren(h("div", { class: "row between" }, msg, btn),
    auto.length ? h("p", { class: "muted small", style: "margin:.6rem 0 0" },
      `Already matched automatically (same name on every server): ${auto.join(", ")}`) : null,
    ideas.length ? h("div", { style: "margin-top:.7rem" },
      h("b", {}, `Suggested matches (${ideas.length})`),
      h("span", { class: "muted small" }, " — these look like the same person under different names"),
      ideas.map((idea) => h("div", { class: "suggest" },
        h("b", {}, idea.canonical), h("span", { class: "arrow" }, "="),
        h("span", { class: "grow" }, idea.aliases.map((a) => `${a.username} (${a.server})`).join("  ·  ")),
        h("button", { class: "btn primary", onclick: () => acceptSuggestion(idea) }, "Add mapping"),
        h("button", { class: "btn ghost", onclick: () => { dismissed.add(idea.key); saveDismissed(); renderMappings(); } }, "Not the same")))) : null);
}

/** Users whose name exists on every discovered server need no mapping. */
function sameNameUsers() {
  const lists = serverNames().filter((n) => discovery[n]).map((n) => new Set(usersOf(n).map((u) => u.toLowerCase())));
  if (lists.length < 2) return [];
  return allUsers().filter((u) => lists.every((set) => set.has(u.toLowerCase())));
}

function renderMapList(sel, key, field, fieldLabel, canonLabel, optionsFor) {
  const box = $(sel);
  const maps = (cfg[key] = cfg[key] || []);
  box.replaceChildren();
  if (!maps.length) box.append(h("p", { class: "empty" }, "None — names are matched as-is."));
  maps.forEach((m, mi) => {
    m.aliases = m.aliases || [];
    const aliasRows = m.aliases.map((a, ai) => h("div", { class: "alias" },
      selectField(null, a.server, ["", ...serverNames()], (v) => { a.server = v; renderMappings(); }),
      pickField(a[field], optionsFor(a.server), (v) => {
        a[field] = v;
        if (!m.canonical && v) {
          m.canonical = field === "username" ? v.toLowerCase() : v;
          const canon = box.querySelectorAll(".item")[mi]?.querySelector(".item-head input");
          if (canon) canon.value = m.canonical;
        }
      }, {
        placeholder: fieldLabel,
        empty: a.server ? `— choose ${field} —` : "— pick a server first —",
        label: field === "library" ? (o) => libLabel(a.server, o) : null,
      }),
      h("button", { class: "btn icon", title: "Remove alias", onclick: () => { m.aliases.splice(ai, 1); markDirty(); renderMappings(); } }, "✕")));
    box.append(h("div", { class: "item" },
      h("div", { class: "item-head" },
        h("input", { type: "text", value: m.canonical || "", placeholder: canonLabel, "aria-label": canonLabel,
          oninput: (e) => { m.canonical = e.target.value; markDirty(); } }),
        h("span", { class: "muted small" }, `${m.aliases.length} alias${m.aliases.length === 1 ? "" : "es"}`),
        h("span", { style: "flex:1" }),
        h("button", { class: "btn icon", onclick: () => { maps.splice(mi, 1); markDirty(); renderMappings(); } }, "✕ Remove")),
      aliasRows,
      h("button", { class: "btn ghost", onclick: () => { m.aliases.push({ server: "", [field]: "" }); markDirty(); renderMappings(); } }, "+ Alias")));
  });
}

/** New mapping with one alias row per configured server, ready to pick. */
function newMapping(field) {
  const names = serverNames();
  return { canonical: "", aliases: (names.length ? names : [""]).map((server) => ({ server, [field]: "" })) };
}

// ---------------------------------------------------------------------------
// Rules tab
// ---------------------------------------------------------------------------
function renderRules() {
  renderRuleList("#user-rules", "user_sync_rules", "users", "Users",
    uniqueCI([...(cfg.user_mappings || []).map((m) => m.canonical), ...allUsers()]));
  renderRuleList("#lib-rules", "library_sync_rules", "libraries", "Libraries",
    uniqueCI([...(cfg.library_mappings || []).map((m) => m.canonical), ...allLibs()]));
}

function renderRuleList(sel, key, field, label, suggestions) {
  const box = $(sel);
  const rules = (cfg[key] = cfg[key] || []);
  box.replaceChildren();
  if (!rules.length) box.append(h("p", { class: "empty" }, "No overrides — the sync directions on the Servers tab apply."));
  const servers = ["", ...serverNames()];
  rules.forEach((r, i) => {
    box.append(h("div", { class: "item" },
      chipField(label, r[field] || [], (v) => (r[field] = v), { suggest: ["*", ...suggestions], placeholder: "Name or *" }),
      h("div", { class: "rule" },
        selectField("From", r.from, servers, (v) => (r.from = v)),
        h("span", { class: "arrow" }, "→"),
        selectField("To", r.to, servers, (v) => (r.to = v)),
        h("button", { class: "btn icon", onclick: () => { rules.splice(i, 1); markDirty(); renderRules(); } }, "✕ Remove"))));
  });
}

// ---------------------------------------------------------------------------
// YAML tab
// ---------------------------------------------------------------------------
let yamlRaw = "";
function renderYaml() { $("#yaml-text").value = yamlRaw; }

// ---------------------------------------------------------------------------
// Render all + load/save
// ---------------------------------------------------------------------------
function renderAll() {
  renderServers();
  renderSummary();
  renderSettings();
  renderMappings();
  renderRules();
}

async function loadConfig() {
  discovery = await api("/api/discovery").catch(() => ({}));
  const res = await api("/api/config");
  cfg = res.data;
  yamlRaw = res.raw;
  $("#yaml-path").textContent = res.path + (res.exists ? "" : " (not created yet — Save to create it)");
  $("#run-dryrun").checked = cfg.dryrun ?? true;
  markClean();
  renderAll();
  renderYaml();
  if (!res.exists) toast("No config found yet — add your servers, then Save.");
}

async function saveConfig() {
  const btn = $("#btn-save");
  btn.disabled = true;
  try {
    const check = await api("/api/validate", { method: "POST", body: { data: cfg } });
    if (!check.ok && !confirm(`The configuration has a problem:\n\n${check.error}\n\nSave anyway?`)) return;
    const res = await api("/api/config", { method: "PUT", body: { data: cfg } });
    yamlRaw = res.raw; renderYaml(); markClean();
    toast(check.ok ? "Saved ✓" : "Saved with errors", check.ok ? "ok" : "bad");
  } catch (e) {
    toast(e.message, "bad");
  } finally {
    btn.disabled = false;
  }
}

async function validateConfig() {
  try {
    const r = await api("/api/validate", { method: "POST", body: { data: cfg } });
    r.ok ? toast(`Configuration is valid ✓ (${r.servers.length} server${r.servers.length === 1 ? "" : "s"})`, "ok")
         : toast(r.error, "bad");
  } catch (e) { toast(e.message, "bad"); }
}

async function testConnections(button) {
  const btn = button instanceof HTMLElement ? button : $("#btn-test");
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "Connecting…";
  try {
    const r = await api("/api/test", { method: "POST", body: { data: cfg } });
    if (r.error) { toast(r.error, "bad"); return; }
    testResults = Object.fromEntries((r.results || []).map((x) => [x.name, x]));
    if (r.discovery) discovery = r.discovery;
    renderSummary(); renderSettings(); renderMappings(); renderRules();
    const users = (r.results || []).reduce((n, x) => n + (x.users || []).length, 0);
    const libs = (r.results || []).reduce((n, x) => n + (x.libraries || []).length, 0);
    toast(r.ok ? `All servers reachable ✓ — found ${users} users and ${libs} libraries`
               : "Some servers failed — see the Dashboard for details", r.ok ? "ok" : "bad");
  } catch (e) { toast(e.message, "bad"); }
  finally { btn.disabled = false; btn.textContent = label; }
}

// ---------------------------------------------------------------------------
// Runs, log, schedule, history
// ---------------------------------------------------------------------------
function levelOf(line) {
  const m = line.match(/\|\s*(TRACE|DEBUG|INFO|SUCCESS|WARNING|ERROR|CRITICAL)\s*\|/);
  return m ? m[1] : (/Startup error|Traceback/.test(line) ? "ERROR" : "");
}

function renderLog() {
  const pre = $("#log");
  const filter = $("#log-filter").value.toLowerCase();
  const lines = filter ? logLines.filter((l) => l.toLowerCase().includes(filter)) : logLines;
  if (!lines.length) {
    pre.replaceChildren(h("span", { class: "muted" }, logLines.length ? "No lines match the filter." : "No sync has run yet in this session."));
    return;
  }
  const frag = document.createDocumentFragment();
  for (const l of lines.slice(-3000)) {
    const lvl = levelOf(l);
    frag.append(h("span", { class: (lvl ? "l-" + lvl : "") + (/marked/i.test(l) ? " mark" : "") }, l + "\n"));
  }
  pre.replaceChildren(frag);
  if ($("#log-follow").checked) pre.scrollTop = pre.scrollHeight;
}

function fmtDuration(a, b) {
  if (!a || !b) return "—";
  const s = Math.round((new Date(b) - new Date(a)) / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

function statusPill(s) { return h("span", { class: "pill " + s }, s); }

async function pollRun() {
  try {
    const r = await api(`/api/run?offset=${logOffset}`);
    if (r.reset) logLines = [];
    if (r.lines.length) { logLines.push(...r.lines); if (logLines.length > 5000) logLines = logLines.slice(-5000); renderLog(); }
    logOffset = r.offset;
    $("#btn-run").disabled = r.running;
    $("#btn-stop").disabled = !r.running;
    const c = r.current;
    const st = $("#run-status");
    if (c) {
      st.replaceChildren(statusPill(c.status), " ",
        c.dryrun === false ? "Live run" : "Dry run", " · started ", new Date(c.started).toLocaleTimeString(),
        c.errors ? h("span", { class: "bad" }, ` · ${c.errors} error line${c.errors === 1 ? "" : "s"}`) : "");
    }
    if (lastRunning && !r.running) { loadHistory(); loadMarks(); loadResults(); }
    lastRunning = r.running;
  } catch { /* server restarting */ }
  setTimeout(pollRun, lastRunning ? 1000 : 2500);
}

async function startRun() {
  const dry = $("#run-dryrun").checked;
  if (!dry && !confirm("Run a LIVE sync? Watched status will be written to your servers.")) return;
  if (dirty && !confirm("You have unsaved changes. The run uses the saved config. Continue?")) return;
  try {
    logLines = []; renderLog();
    await api("/api/run", { method: "POST", body: { dryrun: dry } });
    lastRunning = true;
  } catch (e) { toast(e.message, "bad"); }
}

async function loadHistory() {
  const rows = await api("/api/history").catch(() => []);
  const body = $("#history tbody");
  body.replaceChildren(...(rows.length ? rows.slice(0, 20).map((r) => h("tr", {
      class: "clickable", title: "Show this run's results",
      onclick: () => { loadResults(r.id); $("#results-card").scrollIntoView({ behavior: "smooth" }); } },
    h("td", {}, new Date(r.started).toLocaleString()),
    h("td", {}, r.trigger),
    h("td", {}, r.dryrun === false ? "Live" : "Dry run"),
    h("td", {}, statusPill(r.status)),
    h("td", {}, r.summary ? String(r.summary.total) : String(r.marked ?? 0)),
    h("td", {}, String(r.errors ?? 0)),
    h("td", {}, fmtDuration(r.started, r.finished)))) :
    [h("tr", {}, h("td", { colspan: 7, class: "empty" }, "No runs yet."))]));
}

async function loadSchedule() {
  const s = await api("/api/schedule");
  $("#sched-enabled").checked = s.enabled;
  $("#sched-interval").value = s.interval_minutes;
  $("#sched-dryrun").checked = s.dryrun;
  $("#sched-next").textContent = s.next_run ? "Next run " + new Date(s.next_run * 1000).toLocaleTimeString() : "";
}

async function saveSchedule() {
  const enabled = $("#sched-enabled").checked, dryrun = $("#sched-dryrun").checked;
  if (enabled && !dryrun && !confirm("Schedule LIVE syncs? Watched status will be written automatically.")) return;
  try {
    const s = await api("/api/schedule", { method: "PUT", body: {
      enabled, dryrun, interval_minutes: Number($("#sched-interval").value) || 60 } });
    $("#sched-next").textContent = s.next_run ? "Next run " + new Date(s.next_run * 1000).toLocaleTimeString() : "";
    toast(enabled ? "Schedule on ✓" : "Schedule off", "ok");
  } catch (e) { toast(e.message, "bad"); }
}

let marks = [];
async function loadMarks() {
  const r = await api("/api/marks").catch(() => ({ lines: [], path: "" }));
  marks = r.lines;
  $("#marks-path").textContent = r.path ? `${r.path} · newest first · ${marks.length} shown` : "";
  renderMarks();
}
function renderMarks() {
  const f = $("#marks-filter").value.toLowerCase();
  const rows = marks.filter((l) => !f || l.toLowerCase().includes(f)).slice(0, 500);
  $("#marks tbody").replaceChildren(...(rows.length ? rows.map((l) => {
    const [type, server, user, lib, title, ep] = l.split("/");
    return h("tr", {}, h("td", {}, h("span", { class: `dot ${type}` }), server || ""), h("td", {}, user || ""),
      h("td", {}, lib || ""), h("td", {}, title || ""), h("td", {}, ep || ""));
  }) : [h("tr", {}, h("td", { colspan: 5, class: "empty" }, "Nothing marked yet. Live runs record each item here."))]));
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
function switchTab(name) {
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  if (name === "yaml" && dirty) { /* show form state */ }
  history.replaceState(null, "", "#" + name);
}

document.addEventListener("DOMContentLoaded", async () => {
  $$(".tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  $$("[data-add-server]").forEach((b) => b.addEventListener("click", () => addServer(b.dataset.addServer)));
  $("#btn-save").addEventListener("click", saveConfig);
  $("#btn-validate").addEventListener("click", validateConfig);
  $("#btn-test").addEventListener("click", (e) => testConnections(e.target));
  $("#btn-run").addEventListener("click", startRun);
  $("#btn-stop").addEventListener("click", () => api("/api/run/stop", { method: "POST" }).catch((e) => toast(e.message, "bad")));
  $("#btn-sched-save").addEventListener("click", saveSchedule);
  $("#log-filter").addEventListener("input", renderLog);
  $("#marks-filter").addEventListener("input", renderMarks);
  $("#marks-refresh").addEventListener("click", loadMarks);
  $("#add-user-map").addEventListener("click", () => {
    (cfg.user_mappings = cfg.user_mappings || []).push(newMapping("username"));
    markDirty(); renderMappings();
  });
  $("#add-lib-map").addEventListener("click", () => {
    (cfg.library_mappings = cfg.library_mappings || []).push(newMapping("library"));
    markDirty(); renderMappings();
  });
  $("#add-user-rule").addEventListener("click", () => {
    (cfg.user_sync_rules = cfg.user_sync_rules || []).push({ users: [], from: "", to: "" });
    markDirty(); renderRules();
  });
  $("#add-lib-rule").addEventListener("click", () => {
    (cfg.library_sync_rules = cfg.library_sync_rules || []).push({ libraries: [], from: "", to: "" });
    markDirty(); renderRules();
  });
  $("#yaml-save").addEventListener("click", async () => {
    try {
      const r = await api("/api/config/raw", { method: "PUT", body: { raw: $("#yaml-text").value } });
      cfg = r.data; yamlRaw = $("#yaml-text").value; markClean(); renderAll();
      const v = await api("/api/validate", { method: "POST" });
      v.ok ? toast("YAML saved ✓", "ok") : toast("Saved, but: " + v.error, "bad");
    } catch (e) { toast(e.message, "bad"); }
  });
  $("#yaml-reload").addEventListener("click", async () => {
    if (!dirty) { renderYaml(); return toast("YAML matches the saved file"); }
    toast("Save the form changes first to see them as YAML");
  });
  window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

  const start = location.hash.slice(1);
  if (start && $("#tab-" + start)) switchTab(start);

  appInfo = await api("/api/info").catch(() => ({}));
  try { await loadConfig(); } catch (e) { toast("Could not load config: " + e.message, "bad"); }
  loadSchedule().catch(() => {});
  loadResults();
  loadNotifications();
  $("#results-filter").addEventListener("input", renderResults);
  $("#notify-save").addEventListener("click", saveNotifications);
  $("#notify-test").addEventListener("click", testNotifications);
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
  loadHistory();
  loadMarks();
  pollRun();
});


// ---------------------------------------------------------------------------
// Shared bits for the newer features
// ---------------------------------------------------------------------------
let appInfo = {};

function openModal(...content) {
  const box = $("#modal .modal-box");
  box.replaceChildren(...content);
  $("#modal").hidden = false;
}
function closeModal() {
  $("#modal").hidden = true;
  $("#modal .modal-box").replaceChildren();
  plexAbort = true;
}

// ---------------------------------------------------------------------------
// 1. Sign in with Plex
// ---------------------------------------------------------------------------
let plexAbort = false;

async function plexSignIn(server) {
  plexAbort = false;
  // Open the tab synchronously so pop-up blockers allow it (browser / Docker).
  const popup = appInfo.desktop ? null : window.open("about:blank", "_blank");
  const status = h("p", {}, h("span", { class: "spinner" }), " Waiting for you to sign in to Plex in the browser window…");
  const linkSlot = h("p", { class: "muted small" });
  openModal(h("h2", {}, "Sign in with Plex"), status, linkSlot,
    h("div", { class: "modal-actions" }, h("button", { class: "btn", onclick: closeModal }, "Cancel")));
  let pin;
  try {
    pin = await api("/api/plex/pin", { method: "POST" });
  } catch (e) {
    if (popup) popup.close();
    status.replaceChildren(h("span", { class: "bad" }, e.message));
    return;
  }
  if (appInfo.desktop) await api("/api/open", { method: "POST", body: { url: pin.url } }).catch(() => {});
  else if (popup) popup.location = pin.url;
  linkSlot.replaceChildren("Window didn't open? ", h("a", { href: pin.url, target: "_blank", rel: "noopener" }, "Open the Plex sign-in page"), ".");

  const deadline = Date.now() + 10 * 60 * 1000;
  while (!plexAbort && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2000));
    if (plexAbort) return;
    let res;
    try { res = await api(`/api/plex/pin/${pin.id}?code=${encodeURIComponent(pin.code)}`); } catch (e) { continue; }
    if (!res.done) continue;
    if (popup && !popup.closed) try { popup.close(); } catch { /* ignore */ }
    return choosePlexServer(server, res.servers);
  }
  if (!plexAbort) status.replaceChildren(h("span", { class: "bad" }, "Timed out waiting for Plex sign-in. Try again."));
}

function choosePlexServer(server, servers) {
  if (!servers.length) {
    openModal(h("h2", {}, "No Plex servers found"),
      h("p", {}, "You're signed in, but this Plex account has no servers it can reach."),
      h("div", { class: "modal-actions" }, h("button", { class: "btn", onclick: closeModal }, "Close")));
    return;
  }
  const options = servers.flatMap((srv, si) => srv.connections.map((c, ci) => ({ srv, c, id: `${si}-${ci}` })));
  const list = h("div", {}, servers.map((srv, si) => h("div", {},
    h("h3", { style: "margin:.8rem 0 .2rem" }, srv.name, srv.owned ? "" : h("span", { class: "muted small" }, " · shared with you")),
    srv.connections.length ? srv.connections.map((c, ci) => h("label", { class: "choice" },
      h("input", { type: "radio", name: "plexconn", value: `${si}-${ci}`, checked: si === 0 && ci === 0 }),
      h("div", {}, h("div", {}, c.label), h("div", { class: "meta" }, c.url))))
      : h("p", { class: "muted small" }, "No direct connection available (relay only)."))));
  openModal(h("h2", {}, "Choose your Plex server"),
    h("p", { class: "muted" }, "Pick how this app should reach it. If it runs on the same network as Plex, a Local option is best. Use the secure address if the plain IP doesn't connect."),
    list,
    h("div", { class: "modal-actions" },
      h("button", { class: "btn", onclick: closeModal }, "Cancel"),
      h("button", { class: "btn primary", onclick: () => {
        const picked = $("input[name=plexconn]:checked");
        const opt = options.find((o) => o.id === picked?.value);
        if (!opt) return;
        server.baseurl = opt.c.url;
        server.token = opt.srv.token;
        delete server.username; delete server.password; delete server.servername;
        markDirty(); closeModal(); renderAll();
        toast(`Connected ${server.name} to “${opt.srv.name}”. Click Save, then Test connections.`, "ok");
      } }, "Use this server")));
}

// ---------------------------------------------------------------------------
// 2. Readable run results / dry-run preview
// ---------------------------------------------------------------------------
let results = { run: null, changes: [] };

async function loadResults(id) {
  results = await api("/api/run/changes" + (id ? `?id=${id}` : "")).catch(() => ({ run: null, changes: [] }));
  renderResults();
}

function renderResults() {
  const body = $("#results-body");
  const run = results.run;
  if (!run) {
    $("#results-title").textContent = "Last run results";
    body.replaceChildren(h("p", { class: "empty" }, "Run a sync (a dry run is safe) to see what would be marked watched."));
    return;
  }
  const dry = run.dryrun !== false;
  $("#results-title").textContent = (dry ? "Dry run preview" : "Live run results") + " · " + new Date(run.started).toLocaleString();
  const f = $("#results-filter").value.toLowerCase();
  const changes = results.changes.filter((c) => !f ||
    [c.user, c.server, c.library, c.title, c.episode].some((v) => (v || "").toLowerCase().includes(f)));

  const watched = results.changes.filter((c) => c.kind !== "partial").length;
  const partial = results.changes.length - watched;
  const verb = dry ? "Would mark" : "Marked";
  const headline = h("div", { class: "headline" },
    statusPill(run.status),
    h("span", { class: "stat" }, h("b", {}, String(results.changes.length)), ` ${dry ? "would change" : "changed"}`),
    h("span", { class: "stat" }, h("b", {}, String(watched)), " watched"),
    h("span", { class: "stat" }, h("b", {}, String(partial)), " in progress"),
    run.errors ? h("span", { class: "stat bad" }, h("b", {}, String(run.errors)), " errors") : null,
    dry && results.changes.length ? h("span", { class: "muted small" }, "Nothing was changed. Untick Dry run to apply these.") : null);

  if (!results.changes.length) {
    body.replaceChildren(headline, h("p", { class: "empty" },
      run.status === "failed" ? "The run failed before changing anything — check the live log below."
        : "Everything is already in sync. Nothing to change."));
    return;
  }

  // group: user -> destination server -> library -> items
  const groups = new Map();
  for (const c of changes) {
    const key = `${c.user}\u0000${c.server}`;
    if (!groups.has(key)) groups.set(key, { user: c.user, server: c.server, type: (c.server_type || "").toLowerCase(), items: [] });
    groups.get(key).items.push(c);
  }
  const sorted = [...groups.values()].sort((a, b) => b.items.length - a.items.length);
  const blocks = sorted.slice(0, 200).map((g, gi) => {
    const libs = new Map();
    for (const c of g.items) { if (!libs.has(c.library)) libs.set(c.library, []); libs.get(c.library).push(c); }
    const w = g.items.filter((c) => c.kind !== "partial").length;
    const rows = [];
    for (const [lib, items] of libs) {
      rows.push(h("tr", { class: "lib-head" }, h("td", { colspan: 3 }, lib || "—")));
      for (const c of items.slice(0, 500)) rows.push(h("tr", {},
        h("td", {}, c.title), h("td", {}, c.episode || ""),
        h("td", {}, c.kind === "partial" ? h("span", { class: "tag" }, `resume at ${c.minutes} min`) : h("span", { class: "tag" }, "watched"))));
      if (items.length > 500) rows.push(h("tr", {}, h("td", { colspan: 3, class: "muted" }, `…and ${items.length - 500} more`)));
    }
    return h("details", { class: "group", open: gi === 0 && sorted.length <= 3 ? true : null },
      h("summary", {}, h("span", { class: `dot ${g.type}` }), h("span", { class: "who" }, g.user), h("span", { class: "muted" }, "on " + g.server),
        h("span", { class: "counts" }, `${verb} ${w} watched${g.items.length - w ? ` · ${g.items.length - w} in progress` : ""}`)),
      h("table", { class: "table" }, h("tbody", {}, rows)));
  });
  body.replaceChildren(headline, ...blocks,
    !blocks.length ? h("p", { class: "empty" }, "No changes match the filter.") : null);
}

// ---------------------------------------------------------------------------
// 3. Suggested user mappings
// ---------------------------------------------------------------------------
const NOISE = ["jellyfin", "plex", "emby", "jf", "server", "account", "user", "tv"];
let dismissed = new Set();
try { dismissed = new Set(JSON.parse(localStorage.getItem("jpw-dismissed") || "[]")); } catch { /* storage off */ }
function saveDismissed() { try { localStorage.setItem("jpw-dismissed", JSON.stringify([...dismissed])); } catch { /* ignore */ } }

function baseName(name) {
  let b = name.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  let parts = b.split(" ").filter((p) => p && !NOISE.includes(p));
  b = (parts.length ? parts : b.split(" ")).join("");
  for (const n of NOISE) {
    if (b.length > n.length + 2 && b.endsWith(n)) b = b.slice(0, -n.length);
    if (b.length > n.length + 2 && b.startsWith(n)) b = b.slice(n.length);
  }
  return b;
}

function similarity(a, b) {
  if (a === b) return 1;
  const m = a.length, n = b.length;
  if (!m || !n) return 0;
  const d = Array.from({ length: m + 1 }, (_, i) => [i, ...Array(n).fill(0)]);
  for (let j = 1; j <= n; j++) d[0][j] = j;
  for (let i = 1; i <= m; i++) for (let j = 1; j <= n; j++)
    d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return 1 - d[m][n] / Math.max(m, n);
}

function suggestMappings() {
  const servers = serverNames().filter((n) => discovery[n]);
  if (servers.length < 2) return [];
  const mapped = new Set((cfg.user_mappings || []).flatMap((m) => (m.aliases || []).map((a) => `${a.server}\u0000${(a.username || "").toLowerCase()}`)));
  const everywhere = new Set(sameNameUsers().map((u) => u.toLowerCase()));
  const nodes = [];
  for (const server of servers) for (const user of usersOf(server)) {
    if (mapped.has(`${server}\u0000${user.toLowerCase()}`) || everywhere.has(user.toLowerCase())) continue;
    nodes.push({ server, user, base: baseName(user) });
  }
  // union-find over likely-same pairs on different servers
  const parent = nodes.map((_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
    const a = nodes[i], b = nodes[j];
    if (a.server === b.server || a.user.toLowerCase() === b.user.toLowerCase()) continue;
    const short = a.base.length <= b.base.length ? a.base : b.base;
    const long = short === a.base ? b.base : a.base;
    const close = a.base === b.base
      || (short.length >= 3 && long.startsWith(short) && long.length - short.length <= 4)
      || (short.length >= 4 && similarity(a.base, b.base) >= 0.8);
    if (close) parent[find(i)] = find(j);
  }
  const clusters = new Map();
  nodes.forEach((n, i) => { const r = find(i); if (!clusters.has(r)) clusters.set(r, []); clusters.get(r).push(n); });
  const ideas = [];
  for (const group of clusters.values()) {
    const perServer = new Set(group.map((g) => g.server));
    if (perServer.size < 2 || group.length > 6) continue;
    const key = group.map((g) => `${g.server}:${g.user}`).sort().join("|");
    if (dismissed.has(key)) continue;
    const canonical = group.map((g) => g.base).sort((a, b) => a.length - b.length)[0] || group[0].user.toLowerCase();
    ideas.push({ key, canonical, aliases: group.map((g) => ({ server: g.server, username: g.user })) });
  }
  return ideas.slice(0, 20);
}

function acceptSuggestion(idea) {
  const maps = (cfg.user_mappings = cfg.user_mappings || []);
  const existing = maps.find((m) => (m.canonical || "").toLowerCase() === idea.canonical);
  if (existing) existing.aliases.push(...idea.aliases);
  else maps.push({ canonical: idea.canonical, aliases: idea.aliases.map((a) => ({ ...a })) });
  markDirty(); renderMappings(); renderRules();
  toast(`Added mapping for “${idea.canonical}”. Remember to Save.`, "ok");
}

// ---------------------------------------------------------------------------
// 4. Notifications (+ 5. Windows app options)
// ---------------------------------------------------------------------------
let notifyCfg = {};

async function loadNotifications() {
  notifyCfg = await api("/api/notifications").catch(() => ({}));
  renderNotifications();
  if (appInfo.desktop && appInfo.tray) {
    try { renderDesktop(await api("/api/desktop")); } catch { /* not available */ }
  }
}

function renderNotifications() {
  const n = notifyCfg;
  const set = (k) => (v) => { n[k] = v; };
  const chk = (label, k) => h("label", { class: "switch-row compact" },
    h("input", { type: "checkbox", checked: !!n[k], onchange: (e) => { n[k] = e.target.checked; } }), h("span", {}, label));
  const txt = (label, k, opts = {}) => textField(label, n[k], set(k), { ...opts, noDirty: true });
  $("#notify-when").replaceChildren(
    chk("A sync fails or is stopped", "on_failure"),
    chk("A live sync marks something watched", "on_changes"),
    chk("Every finished sync, even with no changes", "on_success"),
    chk("Include dry runs", "include_dryrun"));
  $("#notify-discord").replaceChildren(txt("Webhook URL", "discord_webhook", { placeholder: "https://discord.com/api/webhooks/…", secret: true }));
  $("#notify-ntfy").replaceChildren(
    txt("Topic URL", "ntfy_url", { placeholder: "https://ntfy.sh/my-secret-topic" }),
    txt("Access token (optional)", "ntfy_token", { secret: true, hint: "Only for protected topics or self-hosted servers" }));
  $("#notify-webhook").replaceChildren(txt("URL", "webhook_url", { placeholder: "https://…" }));
  $("#notify-email").replaceChildren(
    h("div", { class: "server-fields" },
      txt("SMTP server", "smtp_host", { placeholder: "smtp.gmail.com" }),
      numberField("Port", n.smtp_port ?? 587, set("smtp_port"), { noDirty: true }),
      selectField("Security", n.smtp_security || "starttls", ["starttls", "ssl", "none"], set("smtp_security"), { noDirty: true }),
      txt("Username", "smtp_user"),
      txt("Password", "smtp_password", { secret: true }),
      txt("From", "email_from", { placeholder: "defaults to username" }),
      txt("To", "email_to", { placeholder: "you@example.com" })));
}

async function saveNotifications() {
  try {
    notifyCfg = await api("/api/notifications", { method: "PUT", body: { settings: notifyCfg } });
    $("#notify-result").replaceChildren(h("span", { class: "ok" }, "Saved ✓"));
  } catch (e) { $("#notify-result").replaceChildren(h("span", { class: "bad" }, e.message)); }
}

async function testNotifications() {
  const out = $("#notify-result");
  out.replaceChildren(h("span", { class: "spinner" }), " Sending…");
  try {
    const r = await api("/api/notifications/test", { method: "POST", body: { settings: notifyCfg } });
    out.replaceChildren(...Object.entries(r).map(([k, v]) =>
      h("span", { class: v === "ok" ? "ok" : "bad", style: "margin-right:.8rem" }, `${k}: ${v === "ok" ? "sent ✓" : v}`)));
  } catch (e) { out.replaceChildren(h("span", { class: "bad" }, e.message)); }
}

function renderDesktop(opts) {
  $("#desktop-card").hidden = false;
  const save = async (patch) => {
    try { renderDesktop(await api("/api/desktop", { method: "PUT", body: { ...opts, ...patch } })); toast("Saved ✓", "ok"); }
    catch (e) { toast(e.message, "bad"); }
  };
  const row = (label, k, hint) => h("div", {},
    h("label", { class: "switch-row compact" },
      h("input", { type: "checkbox", checked: !!opts[k], onchange: (e) => save({ [k]: e.target.checked }) }), h("span", {}, label)),
    hint ? h("p", { class: "muted small", style: "margin:.2rem 0 0 1.7rem" }, hint) : null);
  $("#desktop-opts").replaceChildren(
    row("Keep running in the system tray when the window is closed", "close_to_tray",
      "Scheduled syncs keep running. Quit from the tray icon (bottom-right, near the clock)."),
    opts.autostart_supported ? row("Start with Windows", "start_with_windows", "Starts hidden in the tray when you sign in.") : null,
    row("Start minimized to the tray", "start_minimized"));
}
