(() => {
"use strict";

/* ============================ utilidades ============================ */
const $ = (s, r = document) => r.querySelector(s);
const enc = encodeURIComponent;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const svg = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
const FOLDER = '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>';
const ICONS = Object.fromEntries(Object.entries({
  cloud: '<path d="M7 18a4 4 0 0 1-.5-7.97A5.5 5.5 0 0 1 17 8.5a4.5 4.5 0 0 1 .5 9.5z"/>',
  folder: FOLDER,
  newfolder: FOLDER + '<path d="M12 11v5M9.5 13.5h5"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="m21 16-5-5-9 9"/>',
  video: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/>',
  audio: '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
  archive: '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10h14V9M10 13h4"/>',
  more: '<circle cx="12" cy="5" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="12" cy="19" r="1.2"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>',
  download: '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>',
  grid: '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',
  list: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  logout: '<path d="M9 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M16 8l4 4-4 4M20 12H9"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="m20 20-4-4"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
}).map(([k, v]) => [k, svg(v)]));

function ico(name) {
  const s = document.createElement("span");
  s.className = "ico";
  s.innerHTML = ICONS[name] || ICONS.file; // constantes propias, nunca datos del usuario
  return s;
}

/** Crea elementos sin innerHTML (los nombres de archivo nunca se interpretan como HTML). */
function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const join = (a, b) => (a ? a + "/" + b : b);
const parentOf = (p) => p.split("/").slice(0, -1).join("/");
const dl = (p, inline = false) => `/api/download?path=${enc(p)}${inline ? "&inline=1" : ""}`;

function fmtSize(n) {
  if (n < 1024) return n + " B";
  const u = ["KB", "MB", "GB", "TB"];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return (n < 10 ? n.toFixed(1) : Math.round(n)) + " " + u[i];
}
function fmtDate(ts) {
  const d = new Date(ts * 1000);
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString("es", { day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }) });
}
function kindOf(it) {
  if (it.type === "dir") return "folder";
  const m = it.mime || "";
  if (m.startsWith("image/")) return "image";
  if (m.startsWith("video/")) return "video";
  if (m.startsWith("audio/")) return "audio";
  if (/zip|rar|7z|tar|gzip/.test(m)) return "archive";
  return "file";
}

/* ============================ estado y API ============================ */
const state = { path: "", items: [], view: "list", query: "", me: null };
try { state.view = localStorage.getItem("view") || "list"; } catch {}

async function api(url, opts = {}) {
  const headers = { ...(typeof opts.body === "string" ? { "Content-Type": "application/json" } : {}), ...opts.headers };
  const res = await fetch(url, { credentials: "same-origin", ...opts, headers });
  if (res.status === 401) { showLogin(); throw new Error("Sesión expirada"); }
  if (!res.ok) {
    let msg = "Error " + res.status;
    try { const d = (await res.json()).detail; if (typeof d === "string") msg = d; } catch {}
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

function toast(msg, kind = "info") {
  const t = h("div", { class: "toast " + kind, role: "status" }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.classList.add("out"), 3600);
  setTimeout(() => t.remove(), 4100);
}

/* ============================ temas ============================ */
const THEME_COLORS = { papel: "#faf8f5", niebla: "#f4f6f9", menta: "#f3f8f5", noche: "#121316" };
function setTheme(t) {
  if (!THEME_COLORS[t]) t = "niebla";
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("theme", t); } catch {}
  $('meta[name="theme-color"]').content = THEME_COLORS[t];
  document.querySelectorAll(".swatch").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.theme === t)));
}

/* ============================ diálogo ============================ */
function ask({ title, message = "", input = null, confirm = "Aceptar", danger = false }) {
  return new Promise((resolve) => {
    const d = $("#dlg"), inp = $("#dlg-input"), ok = $("#dlg-ok");
    $("#dlg-title").textContent = title;
    $("#dlg-msg").textContent = message;
    $("#dlg-msg").hidden = !message;
    inp.hidden = input === null;
    inp.value = input ?? "";
    ok.textContent = confirm;
    ok.classList.toggle("danger", danger);
    ok.classList.toggle("primary", !danger);
    d.returnValue = "";
    d.onclose = () => resolve(d.returnValue === "ok" ? (input === null ? true : inp.value.trim()) : null);
    d.showModal();
    if (input !== null) { inp.focus(); inp.select(); }
  });
}

/* ============================ navegación ============================ */
const hashFor = (p) => "#/" + p.split("/").filter(Boolean).map(enc).join("/");
function pathFromHash() {
  try { return location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent).join("/"); }
  catch { return ""; }
}
const go = (p) => { location.hash = hashFor(p); };

window.addEventListener("hashchange", () => {
  document.body.classList.remove("nav-open");
  if ($("#app").classList.contains("hidden")) return;
  state.path = pathFromHash();
  state.query = ""; $("#search").value = "";
  load();
});

/* ============================ carga y render ============================ */
async function load() {
  try {
    const d = await api("/api/files?path=" + enc(state.path));
    state.items = d.items;
    render();
  } catch (e) {
    if (e.message === "Sesión expirada") return;
    toast(e.message, "error");
    if (state.path) go(""); // carpeta inexistente: vuelve a la raíz
  }
}

function renderCrumbs() {
  const nav = $("#crumbs");
  nav.replaceChildren();
  const parts = state.path.split("/").filter(Boolean);
  const crumb = (label, p, current) =>
    h("a", { href: hashFor(p), class: "crumb" + (current ? " current" : ""), "aria-current": current ? "page" : null }, label);
  nav.append(crumb("Mis archivos", "", !parts.length));
  let acc = "";
  parts.forEach((seg, i) => { acc = join(acc, seg); nav.append(h("span", { class: "sep" }, "/"), crumb(seg, acc, i === parts.length - 1)); });
}

function render() {
  renderCrumbs();
  const box = $("#files");
  box.className = "files view-" + state.view;
  box.replaceChildren();
  const q = state.query.trim().toLowerCase();
  const items = q ? state.items.filter((i) => i.name.toLowerCase().includes(q)) : state.items;

  $("#empty").hidden = items.length > 0;
  $("#empty-text").textContent = q ? "Sin resultados" : "Esta carpeta está vacía";

  if (state.view === "list" && items.length) {
    box.append(h("div", { class: "list-head" }, h("span"), h("span", {}, "Nombre"), h("span", { class: "c-size" }, "Tamaño"), h("span", { class: "c-date" }, "Modificado"), h("span")));
  }
  items.forEach((it) => box.append(row(it)));

  const vb = $("#view-btn");
  vb.replaceChildren(ico(state.view === "list" ? "grid" : "list"));
  vb.title = state.view === "list" ? "Ver como cuadrícula" : "Ver como lista";
}

function row(it) {
  const p = join(state.path, it.name);
  const kind = kindOf(it);
  const canThumb = kind === "image" && state.view === "grid" && it.size < 8e6 && !/svg|heic|heif|tiff/.test(it.mime);
  const thumb = canThumb ? h("img", { src: dl(p, true), loading: "lazy", alt: "" }) : ico(kind);

  const el = h("div", {
    class: "item " + (it.type === "dir" ? "dir" : "file"),
    draggable: "true", tabindex: "0", role: "button",
    onclick: (e) => { if (!e.target.closest(".more")) openItem(it); },
    onkeydown: (e) => { if (e.key === "Enter" && !e.target.closest(".more")) openItem(it); },
    ondragstart: (e) => { e.dataTransfer.setData("text/x-nube-path", p); e.dataTransfer.effectAllowed = "move"; },
  },
    h("span", { class: "thumb" }, thumb),
    h("span", { class: "name", title: it.name }, it.name),
    h("span", { class: "meta c-size" }, it.type === "dir" ? "" : fmtSize(it.size)),
    h("span", { class: "meta c-date" }, fmtDate(it.modified)),
    h("button", { class: "icon-btn more", "aria-label": "Más acciones", onclick: (e) => { e.stopPropagation(); openMenu(e.currentTarget, it); } }, ico("more")),
  );

  if (it.type === "dir") { // soltar un elemento sobre una carpeta = moverlo
    const isInternal = (e) => e.dataTransfer.types.includes("text/x-nube-path");
    el.addEventListener("dragover", (e) => { if (isInternal(e)) { e.preventDefault(); el.classList.add("drop"); } });
    el.addEventListener("dragleave", () => el.classList.remove("drop"));
    el.addEventListener("drop", (e) => {
      if (!isInternal(e)) return;
      e.preventDefault(); e.stopPropagation(); el.classList.remove("drop");
      moveItem(e.dataTransfer.getData("text/x-nube-path"), p);
    });
  }
  return el;
}

function openItem(it) {
  const p = join(state.path, it.name);
  if (it.type === "dir") return go(p);
  const m = it.mime || "";
  const inline = /^(image|video|audio)\//.test(m) && m !== "image/svg+xml" || m === "application/pdf" || m === "text/plain";
  window.open(dl(p, inline), "_blank", "noopener");
}

/* ============================ acciones ============================ */
function download(p) {
  const a = h("a", { href: dl(p), download: "" });
  document.body.append(a); a.click(); a.remove();
}

async function newFolder() {
  const name = await ask({ title: "Nueva carpeta", input: "", confirm: "Crear" });
  if (!name) return;
  try { await api("/api/folders", { method: "POST", body: JSON.stringify({ path: state.path, name }) }); await load(); }
  catch (e) { toast(e.message, "error"); }
}
async function renameItem(it) {
  const name = await ask({ title: "Renombrar", input: it.name, confirm: "Guardar" });
  if (!name || name === it.name) return;
  try { await api("/api/rename", { method: "POST", body: JSON.stringify({ path: join(state.path, it.name), new_name: name }) }); await load(); }
  catch (e) { toast(e.message, "error"); }
}
async function removeItem(it) {
  const ok = await ask({
    title: it.type === "dir" ? "Eliminar carpeta" : "Eliminar archivo",
    message: it.type === "dir" ? `«${it.name}» y todo su contenido se borrarán de forma permanente.` : `«${it.name}» se borrará de forma permanente.`,
    confirm: "Eliminar", danger: true,
  });
  if (!ok) return;
  try { await api("/api/files?path=" + enc(join(state.path, it.name)), { method: "DELETE" }); await load(); refreshMe(); }
  catch (e) { toast(e.message, "error"); }
}
async function moveItem(src, destDir) {
  if (src === destDir || parentOf(src) === destDir) return;
  try { await api("/api/move", { method: "POST", body: JSON.stringify({ path: src, dest: destDir }) }); toast("Movido"); await load(); }
  catch (e) { toast(e.message, "error"); }
}

/* ---------- menú ---------- */
let menuEl = null;
function closeMenu() { menuEl?.remove(); menuEl = null; }
function openMenu(btn, it) {
  closeMenu();
  const entries = [
    it.type === "file" && ["download", "Descargar", () => download(join(state.path, it.name))],
    ["edit", "Renombrar", () => renameItem(it)],
    ["trash", "Eliminar", () => removeItem(it), true],
  ].filter(Boolean);
  menuEl = h("div", { class: "menu", role: "menu" },
    entries.map(([i, label, fn, danger]) =>
      h("button", { class: "menu-item" + (danger ? " danger" : ""), role: "menuitem", onclick: () => { closeMenu(); fn(); } }, ico(i), label)));
  document.body.append(menuEl);
  const r = btn.getBoundingClientRect(), w = menuEl.offsetWidth, hh = menuEl.offsetHeight;
  menuEl.style.left = Math.max(8, Math.min(r.right - w, innerWidth - w - 8)) + "px";
  menuEl.style.top = (r.bottom + hh + 12 > innerHeight ? Math.max(8, r.top - hh - 4) : r.bottom + 4) + "px";
}
document.addEventListener("click", (e) => { if (menuEl && !e.target.closest(".menu")) closeMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
addEventListener("scroll", closeMenu, true);

/* ============================ subidas ============================ */
const jobs = [], queue = [];
let active = 0, uid = 0, paintTimer = 0;
const MAX_PARALLEL = 2, MAX_RETRIES = 4;

function enqueue(files) {
  const dir = state.path;
  for (const f of files) { const j = { id: ++uid, file: f, dir, loaded: 0, status: "wait", error: "" }; jobs.push(j); queue.push(j); }
  schedulePaint(); pump();
}
function pump() {
  while (active < MAX_PARALLEL && queue.length) {
    const j = queue.shift();
    active++;
    runJob(j).finally(() => { active--; pump(); });
  }
}
async function runJob(j) {
  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      j.status = "up"; j.loaded = 0; schedulePaint();
      await xhrUpload(j);
      j.status = "done"; schedulePaint();
      if (j.dir === state.path) load();
      refreshMe();
      return;
    } catch (e) {
      if (e.fatal || attempt === MAX_RETRIES) { j.status = "error"; j.error = e.message; schedulePaint(); return; }
      j.status = "retry"; schedulePaint();
      await sleep(1500 * attempt);
    }
  }
}
function xhrUpload(j) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("PUT", `/api/upload?path=${enc(j.dir)}&name=${enc(j.file.name)}&modified=${j.file.lastModified || ""}`);
    x.upload.onprogress = (e) => { j.loaded = e.loaded; schedulePaint(); };
    x.onload = () => {
      if (x.status >= 200 && x.status < 300) return resolve();
      let msg = "Error " + x.status;
      try { const d = JSON.parse(x.responseText).detail; if (typeof d === "string") msg = d; } catch {}
      const err = new Error(msg);
      err.fatal = x.status < 500 && x.status !== 408;
      if (x.status === 401) showLogin();
      reject(err);
    };
    x.onerror = x.ontimeout = () => reject(new Error("Conexión perdida"));
    x.send(j.file);
  });
}
function schedulePaint() { if (!paintTimer) paintTimer = setTimeout(() => { paintTimer = 0; paintUploads(); }, 120); }
function paintUploads() {
  const panel = $("#uploads");
  panel.hidden = !jobs.length;
  if (!jobs.length) return;
  const pending = jobs.filter((j) => ["wait", "up", "retry"].includes(j.status)).length;
  const failed = jobs.filter((j) => j.status === "error").length;
  $("#uploads-title").textContent = pending ? `Subiendo ${pending} ${pending === 1 ? "archivo" : "archivos"}` : failed ? `${failed} con errores` : "Subida completa";
  $("#uploads-list").replaceChildren(...jobs.map(jobRow));
}
function jobRow(j) {
  const pct = j.status === "done" ? 100 : j.file.size ? Math.round((j.loaded / j.file.size) * 100) : 0;
  const label = { wait: "En cola", up: pct + "%", retry: "Reintentando…", done: "Listo", error: j.error || "Error" }[j.status];
  return h("li", { class: "job " + j.status },
    h("div", { class: "job-top" }, h("span", { class: "job-name", title: j.file.name }, j.file.name), h("span", { class: "job-state", title: label }, label)),
    h("div", { class: "bar" }, h("i", { style: `width:${pct}%` })));
}
$("#uploads-clear").addEventListener("click", () => {
  const keep = jobs.filter((j) => ["wait", "up", "retry"].includes(j.status));
  jobs.splice(0, jobs.length, ...keep);
  paintUploads();
});
addEventListener("beforeunload", (e) => { if (active || queue.length) { e.preventDefault(); e.returnValue = ""; } });

/* ---- arrastrar y soltar desde el sistema ---- */
let depth = 0;
const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
const appVisible = () => !$("#app").classList.contains("hidden");
addEventListener("dragenter", (e) => { if (hasFiles(e) && appVisible()) { depth++; $("#dropveil").classList.add("on"); } });
addEventListener("dragleave", (e) => { if (hasFiles(e) && --depth <= 0) { depth = 0; $("#dropveil").classList.remove("on"); } });
addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
addEventListener("drop", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault(); depth = 0; $("#dropveil").classList.remove("on");
  if (!appVisible()) return;
  const files = []; let dirs = false;
  for (const it of e.dataTransfer.items) {
    if (it.kind !== "file") continue;
    if (it.webkitGetAsEntry?.()?.isDirectory) { dirs = true; continue; }
    const f = it.getAsFile(); if (f) files.push(f);
  }
  if (dirs) toast("Subir carpetas completas llegará en la siguiente fase.");
  if (files.length) enqueue(files);
});

/* ============================ sesión ============================ */
function showLogin() {
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  $("#login-pass").value = "";
  $("#login-user").focus();
}
async function refreshMe() {
  try {
    state.me = await api("/api/me");
    const d = state.me.disk;
    $("#meter i").style.width = Math.min(100, Math.round((d.used / d.total) * 100)) + "%";
    $("#meter-text").textContent = `${fmtSize(d.free)} libres de ${fmtSize(d.total)}`;
    $("#user-name").textContent = state.me.username;
    $("#avatar").textContent = state.me.username[0].toUpperCase();
  } catch {}
}
async function boot() {
  try { await api("/api/me"); } catch { return showLogin(); }
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  state.path = pathFromHash();
  refreshMe();
  load();
}

/* ============================ arranque ============================ */
document.querySelectorAll("[data-icon]").forEach((el) => el.replaceChildren(ico(el.dataset.icon)));
setTheme(document.documentElement.dataset.theme);
document.querySelectorAll(".swatch").forEach((b) => b.addEventListener("click", () => setTheme(b.dataset.theme)));

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
      body: JSON.stringify({ username: $("#login-user").value, password: $("#login-pass").value }),
    });
    if (!res.ok) { let m = "No se pudo iniciar sesión"; try { const d = (await res.json()).detail; if (typeof d === "string") m = d; } catch {} throw new Error(m); }
    boot();
  } catch (err) { $("#login-error").textContent = err.message; }
});
$("#logout").addEventListener("click", async () => { try { await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }); } catch {} showLogin(); });
$("#menu-btn").addEventListener("click", () => document.body.classList.toggle("nav-open"));
$("#backdrop").addEventListener("click", () => document.body.classList.remove("nav-open"));
$("#view-btn").addEventListener("click", () => { state.view = state.view === "list" ? "grid" : "list"; try { localStorage.setItem("view", state.view); } catch {} render(); });
$("#search").addEventListener("input", (e) => { state.query = e.target.value; render(); });
$("#new-folder").addEventListener("click", newFolder);
$("#upload-btn").addEventListener("click", () => $("#file-input").click());
$("#file-input").addEventListener("change", (e) => { enqueue([...e.target.files]); e.target.value = ""; });

boot();
})();
