import { toCSV } from "./lib/csv.js";
import { buildPlaylist, makeApi, transferRows, TRANSFER_COLUMNS } from "./lib/engine.js";
import { STRINGS } from "./lib/i18n.js";
import { loadLibrary } from "./lib/library.js";
import { DEFAULT_TRACK_SECONDS, playlistSeconds } from "./lib/match.js";

const api = makeApi();
const app = document.getElementById("app");
let names = null;

// ------------------------------------------------------------ storage
const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
};

const state = {
  lang: store.get("pacemaker.lang", (navigator.language || "en").toLowerCase().startsWith("tr") ? "tr" : "en"),
  cadence: store.get("pacemaker.cadence", 170),
  minutes: store.get("pacemaker.minutes", 60),
  fresh: store.get("pacemaker.fresh", 30),
  songs: null,
  fileError: null,
  result: null,
  shared: null,
  busy: false,
};

const t = (key, ...args) => {
  const v = STRINGS[state.lang][key] ?? STRINGS.en[key];
  return typeof v === "function" ? v(...args) : v;
};

// --------------------------------------------------------------- DOM
function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}

function applyStatic() {
  document.documentElement.lang = state.lang;
  for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  document.querySelector('meta[name="description"]').setAttribute("content", t("tagline"));
}

// ------------------------------------------------------------ router
function navigate(path) {
  if (path !== location.pathname) history.pushState({}, "", path);
  route();
}

function route() {
  const path = location.pathname;
  const view = path.startsWith("/browse") ? "browse" : path.startsWith("/p/") ? "playlist" : "make";
  for (const a of document.querySelectorAll("[data-nav]")) {
    if (a.dataset.nav === view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  if (view === "browse") renderBrowse();
  else if (view === "playlist") renderShared(decodeURIComponent(path.slice(3)));
  else if (state.result) renderResult();
  else renderMake();
  window.scrollTo(0, 0);
}

document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-link]");
  if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
  e.preventDefault();
  if (a.getAttribute("href") === "/") state.result = null;
  navigate(a.getAttribute("href"));
});
window.addEventListener("popstate", route);

document.getElementById("lang").addEventListener("click", () => {
  state.lang = state.lang === "tr" ? "en" : "tr";
  store.set("pacemaker.lang", state.lang);
  applyStatic();
  route();
});

// -------------------------------------------------------------- make
const songsPerRun = () => Math.round((state.minutes * 60) / DEFAULT_TRACK_SECONDS * (state.fresh / 100));

function step(no, title, ...body) {
  return h("section", { class: "step", "aria-labelledby": `step${no}` },
    h("div", { class: "step-no", "aria-hidden": "true", text: String(no) }),
    h("h2", { id: `step${no}`, text: title }),
    h("div", { class: "step-body" }, ...body));
}

function renderMake() {
  app.replaceChildren();

  // 1. cadence
  const note = h("div", { class: "note", hidden: true });
  const cadence = h("input", {
    class: "cadence-input", id: "cadence", type: "number", inputmode: "numeric", min: "60", max: "220",
    value: String(state.cadence), "aria-describedby": "cadence-unit",
  });
  const checkCadence = () => {
    const v = Number(cadence.value);
    state.cadence = v;
    store.set("pacemaker.cadence", v);
    if (v >= 60 && v < 120) {
      note.replaceChildren(h("span", { text: t("cadenceOneFoot", v, v * 2) }),
        h("button", { type: "button", class: "btn ghost", onclick: () => { cadence.value = String(v * 2); checkCadence(); } }, t("cadenceUse", v * 2)));
      note.hidden = false;
    } else note.hidden = true;
    updateBuild();
  };
  cadence.addEventListener("input", checkCadence);
  const help = h("details", {},
    h("summary", { text: t("cadenceHelp") }),
    h("p", { class: "hint", text: t("cadenceHelpIntro") }),
    h("table", { class: "help-table" }, h("tbody", {}, t("cadenceWhere").map(([a, b]) => h("tr", {}, h("td", { text: a }), h("td", { text: b }))))));

  // 2. library
  const fileInput = h("input", { type: "file", id: "library", accept: ".csv,.xml,.txt,text/csv,text/xml" });
  const status = h("p", { class: state.fileError ? "error" : "status", role: "status", "aria-live": "polite" });
  const drop = h("label", { class: "drop", for: "library" },
    h("strong", { text: t("dropTitle") }), h("span", { text: t("dropOr") }), h("small", { text: t("dropFormats") }), fileInput);
  const showLoaded = () => {
    if (state.fileError) { status.className = "error"; status.textContent = state.fileError; drop.classList.remove("is-loaded"); }
    else if (state.songs) {
      status.className = "status";
      status.textContent = t("loaded", state.songs.length, state.songs.filter((s) => s.favorite).length);
      drop.classList.add("is-loaded");
    } else status.textContent = "";
  };
  const readFile = async (file) => {
    if (!file) return;
    try {
      const text = await file.text();
      state.songs = loadLibrary(text, file.name, (x) => new DOMParser().parseFromString(x, "application/xml"));
      state.fileError = null;
    } catch (err) {
      state.songs = null;
      state.fileError = t({ "no-columns": "errNoColumns", "xml-unsupported": "errXml" }[err.message] || "errNoSongs");
    }
    showLoaded();
    updateBuild();
  };
  fileInput.addEventListener("change", () => readFile(fileInput.files[0]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("is-over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("is-over"); readFile(e.dataTransfer.files[0]); });
  const exportHelp = h("details", {},
    h("summary", { text: t("exportHelp") }),
    h("table", { class: "help-table" }, h("tbody", {}, t("exportSteps").map(([a, b]) => h("tr", {}, h("td", { text: a }), h("td", { text: b }))))));

  // 3. length
  const lengths = [30, 45, 60, 90];
  const chips = h("div", { class: "chips", role: "group", "aria-labelledby": "step3" },
    lengths.map((m) => h("button", {
      type: "button", class: "chip", "aria-pressed": String(state.minutes === m),
      onclick: (e) => {
        state.minutes = m; store.set("pacemaker.minutes", m);
        for (const c of chips.children) c.setAttribute("aria-pressed", String(c === e.currentTarget));
        freshOut.textContent = t("freshOut", state.fresh, songsPerRun());
      },
    }, `${m} ${t("minutesUnit")}`)));

  // 4. new to you
  const freshOut = h("p", { class: "fresh-out", id: "fresh-out", "aria-live": "polite", text: t("freshOut", state.fresh, songsPerRun()) });
  const fresh = h("input", { type: "range", id: "fresh", min: "0", max: "100", step: "10", value: String(state.fresh), "aria-describedby": "fresh-out" });
  fresh.addEventListener("input", () => {
    state.fresh = Number(fresh.value); store.set("pacemaker.fresh", state.fresh);
    freshOut.textContent = t("freshOut", state.fresh, songsPerRun());
  });

  const build = h("button", { type: "button", class: "btn build", id: "build", onclick: run }, t("build"));
  function updateBuild() {
    const v = Number(cadence.value);
    build.disabled = !state.songs || !(v >= 120 && v <= 220);
  }

  app.append(
    step(1, t("step1"), h("div", { class: "cadence-row" }, cadence, h("span", { class: "unit", id: "cadence-unit", text: t("cadenceUnit") })), note, help),
    step(2, t("step2"), drop, status, h("p", { class: "privacy", text: t("privacy") }), exportHelp),
    step(3, t("step3"), chips),
    step(4, t("step4"), fresh, freshOut, h("p", { class: "hint", text: t("freshHint") })),
    build,
  );
  showLoaded();
  checkCadence();
}

// ---------------------------------------------------------- progress
async function run() {
  if (state.busy || !state.songs) return;
  state.busy = true;
  const bar = h("span", { style: "width:4%" });
  const items = {
    library: h("li", { class: "is-active" }),
    artists: h("li", { text: t("phaseArtists") }),
    discover: h("li", { text: t("phaseDiscover", 0, 0) }),
  };
  items.library.textContent = t("phaseLibrary", 0, 0, 0, state.cadence);
  const list = h("ul", { class: "phases" }, items.library, state.fresh ? [items.artists, items.discover] : []);
  window.scrollTo(0, 0);
  app.replaceChildren(h("section", { class: "progress", "aria-live": "polite" },
    h("h2", { text: t("progressTitle") }), h("div", { class: "lane-bar", role: "progressbar", "aria-label": t("progressTitle") }, bar),
    list, h("p", { class: "hint", text: t("progressNote") })));

  const order = ["library", "artists", "discover"];
  const onProgress = (p) => {
    if (p.phase === "done") { bar.style.width = "100%"; return; }
    const idx = order.indexOf(p.phase);
    order.forEach((k, i) => {
      items[k].classList.toggle("is-done", i < idx);
      items[k].classList.toggle("is-active", i === idx);
    });
    if (p.phase === "library") items.library.textContent = t("phaseLibrary", p.checked, p.total, p.matches, state.cadence);
    if (p.phase === "discover") items.discover.textContent = t("phaseDiscover", p.checked, p.matches);
    const within = p.total ? Math.min(1, p.checked / p.total) : 0.5;
    bar.style.width = `${Math.round(((idx + within) / (state.fresh ? 3 : 1)) * 100)}%`;
  };

  try {
    names ||= await (await fetch("/lib/names.json")).json();
    const served = store.get("pacemaker.served", {});
    const result = await buildPlaylist({
      songs: state.songs, cadence: state.cadence, minutes: state.minutes, fresh: state.fresh / 100,
      served, api, names, lang: state.lang, onProgress, seed: Math.floor(Math.random() * 2 ** 31),
    });
    for (const tr of result.playlist) served[tr.key] = (served[tr.key] || 0) + 1;
    store.set("pacemaker.served", served);
    state.result = { ...result, cadence: state.cadence, minutes: state.minutes, lang: state.lang };
    state.shared = null;
    renderResult();
    window.scrollTo(0, 0); // the build button sits low on a phone; show the bib, not mid-table
  } catch {
    app.replaceChildren(h("p", { class: "error", text: t("errGeneric") }),
      h("button", { type: "button", class: "btn ghost", onclick: renderMake }, t("build")));
  } finally {
    state.busy = false;
  }
}

// -------------------------------------------------------------- bibs
function bib({ cadence, name, minutes, seconds, count, newCount }) {
  const s = seconds ?? minutes * 60;
  return h("div", { class: "bib" },
    h("div", { class: "bib-strip" }, h("span", { text: t("bibHeader") }), h("span", { text: `${cadence} SPM` })),
    h("div", { class: "bib-number", text: String(Math.round(cadence)) }),
    h("div", { class: "bib-name", text: name }),
    h("p", { class: "bib-meta", text: t("meta", Math.floor(s / 60), s % 60, count, newCount) }));
}

function trackTable(tracks) {
  const rows = tracks.map((tr, i) => {
    const isNew = tr.origin === "discovery" || tr.isNew;
    const q = encodeURIComponent(`${tr.title} ${tr.artist}`);
    return h("tr", {},
      h("td", { class: "n", text: String(i + 1) }),
      h("td", {},
        h("div", { class: "title" }, tr.title, isNew ? h("span", { class: "tag-new", text: t("newTag") }) : null),
        h("div", { class: "artist", text: tr.artist }),
        isNew ? h("div", { class: "listen" },
          h("a", { href: `https://music.apple.com/search?term=${q}`, target: "_blank", rel: "noopener" }, "Apple Music"),
          h("a", { href: `https://open.spotify.com/search/${q}`, target: "_blank", rel: "noopener" }, "Spotify")) : null),
      h("td", { class: "bpm" }, (Math.round(tr.bpm * 10) / 10).toString(),
        tr.match === "half-time" ? h("span", { class: "x2", title: t("halfTimeTitle"), text: t("halfTime") }) : null));
  });
  return h("div", { class: "table-wrap" }, h("table", { class: "tracks" },
    h("thead", {}, h("tr", {}, h("th", {}, h("span", { class: "sr", text: "#" })), h("th", { text: t("colSong") }), h("th", { class: "bpm", text: t("colBpm") }))),
    h("tbody", {}, rows)));
}

function downloadCSV(tracks, name) {
  const blob = new Blob(["﻿" + toCSV(transferRows(tracks, name), TRANSFER_COLUMNS)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: `${name.replace(/[^\p{L}\p{N} .&-]+/gu, "").trim() || "pacemaker"}.csv` });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function importHow() {
  return h("p", { class: "import-how" }, t("importHow"), " ",
    h("a", { href: "https://www.tunemymusic.com/transfer/csv-to-apple-music", target: "_blank", rel: "noopener" }, "TuneMyMusic"), " · ",
    h("a", { href: "https://soundiiz.com", target: "_blank", rel: "noopener" }, "Soundiiz"));
}

// ------------------------------------------------------------ result
function renderResult() {
  const r = state.result;
  if (!r) return renderMake();
  const { seconds } = playlistSeconds(r.playlist);
  const newCount = r.playlist.filter((x) => x.origin === "discovery").length;
  const shareArea = h("div");

  const shareBtn = h("button", { type: "button", class: "btn ghost", onclick: async () => {
    shareBtn.disabled = true;
    try {
      const res = await fetch("/api/playlists", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ lang: r.lang, cadence: r.cadence, minutes: Math.max(10, Math.round(seconds / 60)),
          tracks: r.playlist.map((x) => ({ key: x.key, match: x.match, isNew: x.origin === "discovery" })) }) });
      if (res.status === 429) throw new Error("limit");
      if (!res.ok) throw new Error("share");
      state.shared = await res.json();
      renderShare();
    } catch (err) {
      shareArea.replaceChildren(h("p", { class: "error", text: t(err.message === "limit" ? "errShareLimit" : "errGeneric") }));
      shareBtn.disabled = false;
    }
  } }, t("share"));

  function renderShare() {
    if (!state.shared) return;
    shareBtn.hidden = true;
    const url = `${location.origin}/p/${state.shared.slug}`;
    const copy = h("button", { type: "button", class: "btn ghost", onclick: async () => {
      try { await navigator.clipboard.writeText(url); copy.textContent = t("copied"); } catch { /* select instead */ }
    } }, t("copy"));
    shareArea.replaceChildren(h("div", { class: "share-box" }, h("span", { text: t("shared") }),
      h("a", { href: `/p/${state.shared.slug}`, "data-link": true }, h("code", { text: url })), copy));
  }

  const notes = [];
  if (!r.playlist.length) notes.push(h("p", { class: "note", text: t("noMatches") }));
  else if (seconds < r.minutes * 60 * 0.9) notes.push(h("p", { class: "note", text: t("tooShort") }));

  app.replaceChildren(
    bib({ cadence: r.cadence, name: r.name, seconds, count: r.playlist.length, newCount }),
    ...notes,
    r.playlist.length ? h("div", { class: "btn-row" },
      h("button", { type: "button", class: "btn", onclick: () => downloadCSV(r.playlist, r.name) }, t("download")),
      shareBtn,
      h("button", { type: "button", class: "btn ghost", onclick: run }, t("another"))) : null,
    r.playlist.length ? importHow() : null,
    shareArea,
    r.playlist.length ? trackTable(r.playlist) : null,
  );
  renderShare();
}

// ------------------------------------------------------------ browse
async function renderBrowse() {
  const input = h("input", { type: "number", id: "browse-cadence", inputmode: "numeric", min: "120", max: "220",
    value: String(store.get("pacemaker.browse", "") || ""), placeholder: t("browseAll") });
  const list = h("div", { class: "cards", "aria-live": "polite" });
  const load = async () => {
    const v = Number(input.value) || null;
    store.set("pacemaker.browse", v || "");
    list.replaceChildren();
    try {
      const { playlists } = await api.gallery(v);
      if (!playlists.length) { list.replaceChildren(h("p", { class: "empty", text: t("browseEmpty") })); return; }
      list.replaceChildren(...playlists.map((p) => h("a", { class: "card", href: `/p/${p.slug}`, "data-link": true },
        h("div", { class: "bib" },
          h("div", { class: "bib-strip" }, h("span", { text: t("bibHeader") }), h("span", { text: `${p.cadence} SPM` })),
          h("div", { class: "bib-number", text: String(Math.round(p.cadence)) }),
          h("div", { class: "bib-name", text: p.name }),
          h("p", { class: "bib-meta", text: t("browseMeta", p.minutes, p.count, p.new_count) })))));
    } catch {
      list.replaceChildren(h("p", { class: "error", text: t("errGeneric") }));
    }
  };
  app.replaceChildren(
    h("div", { class: "browse-head" },
      h("h1", { text: t("browseTitle") }),
      h("label", { class: "field", for: "browse-cadence" }, t("browseCadence"), input),
      h("button", { type: "button", class: "btn", onclick: load }, t("browseShow"))),
    list);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });
  load();
}

async function renderShared(slug) {
  app.replaceChildren();
  try {
    const p = await api.playlist(slug);
    const seconds = p.tracks.reduce((a, x) => a + (x.duration || DEFAULT_TRACK_SECONDS), 0);
    app.append(
      bib({ cadence: p.cadence, name: p.name, seconds, count: p.tracks.length, newCount: p.new_count }),
      h("div", { class: "btn-row" },
        h("button", { type: "button", class: "btn", onclick: () => downloadCSV(p.tracks, p.name) }, t("download")),
        h("a", { class: "btn ghost", href: "/", "data-link": true }, t("makeYourOwn"))),
      importHow(),
      trackTable(p.tracks));
  } catch {
    app.append(h("p", { class: "note", text: t("notFound") }), h("a", { class: "btn", href: "/", "data-link": true }, t("makeYourOwn")));
  }
}

applyStatic();
route();
