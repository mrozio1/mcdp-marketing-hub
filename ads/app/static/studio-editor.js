/* MCDP Studio editor: a design is a Fabric.js canvas at the real pixel size, shown zoomed to fit.
   The layers are saved as JSON (studio/designs/<id>.json); "Save" also sends a full-size PNG, which becomes
   the library file, so the grid, share links, SharePoint and posting all see the latest version. */
(() => {
  "use strict";
  const C = JSON.parse(document.getElementById("ed-config").textContent);
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const W = C.w, H = C.h, U = Math.min(W, H) / 100;             // U: 1% of the short side, for layouts
  const PROPS = ["name", "role", "frame", "selectable", "evented", "lockMovementX", "lockMovementY", "lockScalingX",
                 "lockScalingY", "lockRotation", "hasControls", "upper", "rawText"];
  const NEUTRALS = ["#1A1A1A", "#FFFFFF", "#F4F1EA", "#5B5852"];
  const P = C.colors[0] || "#0F7471", A = C.colors[1] || "#D9A441";
  let fonts = { ...C.fonts };
  const savedEl = $("[data-saved]");

  fabric.Object.prototype.set({ transparentCorners: false, cornerColor: "#0F7471", cornerStrokeColor: "#fff",
    borderColor: "#0F7471", cornerSize: 12, padding: 2 });
  fabric.Textbox.prototype.set({ splitByGrapheme: false });

  const canvas = new fabric.Canvas("ed-canvas", { width: W, height: H, backgroundColor: "#ffffff",
    preserveObjectStacking: true, selection: C.canEdit, stopContextMenu: true, fireRightClick: false });
  if (!C.canEdit) { canvas.selection = false; canvas.skipTargetFind = true; }

  // ---------------------------------------------------------------- zoom to fit
  const wrap = $("[data-wrap]"), box = $("[data-box]");
  let zoom = 1;
  const setZoom = z => {
    zoom = Math.max(0.05, Math.min(4, z));
    canvas.setZoom(zoom);
    canvas.setDimensions({ width: Math.round(W * zoom), height: Math.round(H * zoom) });
    $("[data-zoom-label]").textContent = `${Math.round(zoom * 100)}%`;
  };
  const fit = () => {
    const r = wrap.getBoundingClientRect();
    setZoom(Math.min((r.width - 48) / W, (r.height - 48) / H, 1.5));
  };
  window.addEventListener("resize", () => fit());
  $$("[data-zoom]").forEach(b => b.addEventListener("click", () => {
    const v = b.dataset.zoom;
    if (v === "fit") fit(); else setZoom(zoom * (v === "+" ? 1.2 : 1 / 1.2));
  }));

  // ---------------------------------------------------------------- fonts
  const fontReady = (family, weight = "400") =>
    (document.fonts && document.fonts.load) ? document.fonts.load(`${weight} 40px "${family}"`).catch(() => null) : Promise.resolve();
  const loadFontsIn = async objs => {
    const seen = new Set();
    for (const o of objs) if (o.fontFamily) seen.add(`${o.fontWeight || 400}|${o.fontFamily}`);
    await Promise.all([...seen].map(k => { const [w, f] = k.split("|"); return fontReady(f, w); }));
  };

  // ---------------------------------------------------------------- history (undo / redo)
  let hist = [], hi = -1, quiet = false;
  const snapshot = () => JSON.stringify(canvas.toJSON(PROPS));
  const record = () => {
    if (quiet) return;
    const s = snapshot();
    if (hist[hi] === s) return;
    hist = hist.slice(0, hi + 1); hist.push(s); if (hist.length > 80) hist.shift();
    hi = hist.length - 1;
    dirty();
  };
  const restore = s => new Promise(res => {
    quiet = true;
    canvas.loadFromJSON(s, () => { canvas.renderAll(); quiet = false; layers(); res(); });
  });
  const undo = async () => { if (hi > 0) { hi--; await restore(hist[hi]); dirty(); } };
  const redo = async () => { if (hi < hist.length - 1) { hi++; await restore(hist[hi]); dirty(); } };
  let recT;
  const recordSoon = () => { clearTimeout(recT); recT = setTimeout(record, 250); };
  canvas.on("object:added", recordSoon);
  canvas.on("object:removed", recordSoon);
  canvas.on("object:modified", recordSoon);
  canvas.on("text:changed", () => { recordSoon(); checkSoon(); });

  // ---------------------------------------------------------------- saving
  let docDirty = false, renderDirty = false, saving = null, saveT;
  const say = t => { savedEl.textContent = t; };
  function dirty() {
    if (!C.canEdit) return;
    docDirty = renderDirty = true; say("Unsaved changes");
    clearTimeout(saveT); saveT = setTimeout(() => save(false), 4000);
    layers();
  }
  const docJSON = () => JSON.stringify({ ...canvas.toJSON(PROPS), w: W, h: H, v: 1 });
  const render = (format = "png", quality = 0.92) => {
    const active = canvas.getActiveObject(), z = zoom;
    canvas.discardActiveObject();
    canvas.setZoom(1); canvas.setDimensions({ width: W, height: H });          // exactly the design's size
    const url = canvas.toDataURL({ format, quality, multiplier: 1, enableRetinaScaling: false });
    setZoom(z);
    if (active) canvas.setActiveObject(active);
    canvas.requestRenderAll();
    return url;
  };
  const toBlob = url => fetch(url).then(r => r.blob());
  async function save(withRender) {
    if (!C.canEdit) return true;
    if (saving) await saving;
    if (!docDirty && !(withRender && renderDirty)) return true;
    const fd = new FormData();
    fd.append("doc", new Blob([docJSON()], { type: "application/json" }), "design.json");
    fd.append("title", $("[data-title]").value.trim());
    if (withRender) { await loadFontsIn(canvas.getObjects()); fd.append("png", await toBlob(render("png")), "design.png"); }
    say("Saving…");
    saving = fetch(`/studio/designs/${C.iid}/save`, { method: "POST", body: fd, headers: { Accept: "application/json" } })
      .then(async r => {
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
        docDirty = false; if (withRender) renderDirty = false;
        say(`Saved ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`);
        return true;
      })
      .catch(e => { say(`Not saved: ${e.message}`); return false; })
      .finally(() => { saving = null; });
    return saving;
  }
  setInterval(() => { if (renderDirty && !docDirty) save(true); }, 120000);   // refresh the library picture now and then
  window.addEventListener("beforeunload", e => {
    if (docDirty && C.canEdit) {
      const fd = new FormData();
      fd.append("doc", new Blob([docJSON()], { type: "application/json" }), "design.json");
      navigator.sendBeacon(`/studio/designs/${C.iid}/save`, fd);
    }
  });
  $("[data-title]").addEventListener("change", () => { docDirty = true; save(false); });

  // ---------------------------------------------------------------- making things
  const textDefaults = kind => ({
    heading: { fontFamily: fonts.heading, fontWeight: 700, fontSize: Math.round(U * 9), lineHeight: 1.08, name: "Heading", role: "headline" },
    subhead: { fontFamily: fonts.body, fontWeight: 600, fontSize: Math.round(U * 4.6), lineHeight: 1.25, name: "Subheading", role: "subhead" },
    body: { fontFamily: fonts.body, fontWeight: 400, fontSize: Math.round(U * 3.4), lineHeight: 1.4, name: "Body text", role: "body" },
    cta: { fontFamily: fonts.body, fontWeight: 700, fontSize: Math.round(U * 3.6), lineHeight: 1.1, name: "Button text", role: "cta" },
  })[kind];
  const textbox = (text, o = {}) => new fabric.Textbox(text, { fill: "#1A1A1A", textAlign: "left", width: W * 0.8,
    left: W * 0.1, top: H * 0.1, ...o });
  const rect = o => new fabric.Rect({ left: 0, top: 0, width: W * 0.3, height: W * 0.3, fill: P, rx: 0, ry: 0, name: "Shape", ...o });
  const frame = o => new fabric.Rect({ left: W * 0.1, top: H * 0.1, width: W * 0.8, height: H * 0.4, fill: "#E6E1D8",
    stroke: "#C9C2B5", strokeWidth: Math.max(2, U * 0.3), strokeDashArray: [U * 1.5, U], name: "Photo frame", role: "photo", ...o });
  const circleFrame = o => new fabric.Circle({ left: W * 0.3, top: H * 0.15, radius: W * 0.2, fill: "#E6E1D8", stroke: "#C9C2B5",
    strokeWidth: Math.max(2, U * 0.3), strokeDashArray: [U * 1.5, U], name: "Round photo frame", role: "photo", ...o });
  const place = (o, center = true) => {
    if (center) { o.set({ left: (W - o.getScaledWidth()) / 2, top: (H - o.getScaledHeight()) / 2 }); }
    canvas.add(o); canvas.setActiveObject(o); canvas.requestRenderAll();
    return o;
  };
  const button = (label, o = {}) => {
    const d = textDefaults("cta");
    const t = textbox(label, { ...d, fill: "#FFFFFF", textAlign: "center", width: W * 0.4, ...o.text });
    const padX = d.fontSize * 1.1, padY = d.fontSize * 0.7;
    const bg = rect({ width: t.width + padX * 2, height: t.height + padY * 2, rx: (t.height + padY * 2) / 2,
                      ry: (t.height + padY * 2) / 2, fill: A, name: "Button", role: "button", ...o.bg });
    return [bg, t, padX, padY];
  };
  const addButton = label => {
    const [bg, t, padX, padY] = button(label || "Book a visit");
    const x = (W - bg.width) / 2, y = H * 0.8;
    bg.set({ left: x, top: y }); t.set({ left: x + padX, top: y + padY });
    canvas.add(bg, t); canvas.setActiveObject(t); canvas.requestRenderAll();
  };

  // images: everything comes from the hub's own address, so the export isn't blocked
  const loadImage = src => new Promise((res, rej) => fabric.Image.fromURL(src, img => img && img.width ? res(img) : rej(new Error("That picture didn't load.")), { crossOrigin: "anonymous" }));
  async function addImage(src, name) {
    const img = await loadImage(src);
    img.set({ name: name || "Photo", role: "image" });
    const target = canvas.getActiveObject();
    const fr = target && (target.role === "photo" ? target : (target.frame ? target : null));
    if (fr) return fillFrame(img, fr);
    const empty = canvas.getObjects().find(o => o.role === "photo" && !o.frame);
    if (empty && !target) return fillFrame(img, empty);
    const s = Math.min((W * 0.6) / img.width, (H * 0.6) / img.height, 1);
    img.scale(s);
    return place(img);
  }
  function frameOf(f) {
    if (f.frame) return f.frame;
    if (f.type === "circle") return { shape: "circle", x: f.left, y: f.top, w: f.radius * 2 * f.scaleX, h: f.radius * 2 * f.scaleY, rx: 0 };
    return { shape: "rect", x: f.left, y: f.top, w: f.getScaledWidth(), h: f.getScaledHeight(), rx: (f.rx || 0) * f.scaleX };
  }
  function clipFor(fr) {
    return fr.shape === "circle"
      ? new fabric.Ellipse({ left: fr.x, top: fr.y, rx: fr.w / 2, ry: fr.h / 2, absolutePositioned: true })
      : new fabric.Rect({ left: fr.x, top: fr.y, width: fr.w, height: fr.h, rx: fr.rx, ry: fr.rx, absolutePositioned: true });
  }
  function fillFrame(img, target) {
    const fr = frameOf(target);
    const s = Math.max(fr.w / img.width, fr.h / img.height);
    img.set({ scaleX: s, scaleY: s, left: fr.x + (fr.w - img.width * s) / 2, top: fr.y + (fr.h - img.height * s) / 2,
              frame: fr, role: "photo", name: target.name && !/frame/i.test(target.name) ? target.name : "Photo",
              clipPath: clipFor(fr), lockRotation: true });
    const idx = canvas.getObjects().indexOf(target);
    quiet = true; canvas.remove(target); quiet = false;
    canvas.insertAt(img, Math.max(0, idx));
    canvas.setActiveObject(img); canvas.requestRenderAll();
    return img;
  }

  // ---------------------------------------------------------------- built-in templates
  const LOGO = () => (C.logo ? `/brand-images/${C.logo}?full=1` : null);
  const LIB = window.MCDPTemplates({ W, H, U, P, A, C, textDefaults, textbox, rect, frame, circleFrame, button, fabric });
  const T = LIB.T;
  // Layouts are drawn for typical words in the brand's fonts; a wider font (or longer words) can make a heading wrap
  // onto more lines than planned. So once the fonts are in, any text that runs into the text below it, or off the
  // page, is made a little smaller until it fits.
  function settle(objs) {
    const tx = objs.filter(o => o.type === "textbox" && !o.keep);
    tx.forEach(o => o.initDimensions());
    const over = (a, b) => a.left < b.left + b.width && b.left < a.left + a.width;
    for (let pass = 0; pass < 3; pass++) {
      tx.forEach(a => {
        const min = a.fontSize * 0.55;
        for (let n = 0; n < 14 && a.fontSize > min; n++) {
          const bottom = a.top + a.height;
          const hit = bottom > H - U * 2 || tx.some(b => b !== a && b.top > a.top + U && over(a, b) && bottom > b.top - U * 0.8);
          if (!hit) break;
          a.set({ fontSize: Math.round(a.fontSize * 0.93) }); a.initDimensions();
        }
      });
    }
  }
  async function applyTemplate(key) {
    const t = T[key].build();
    quiet = true;
    canvas.clear(); canvas.setBackgroundColor(t.bg, () => {});
    t.objs.forEach(o => canvas.add(o));
    if (t.logo && LOGO()) {
      try {
        const img = await loadImage(LOGO());
        const s = (W * t.logo.size) / img.width;
        img.scale(Math.min(s, (H * t.logo.size) / img.height * 1.4));
        const w = img.getScaledWidth(), h = img.getScaledHeight();
        img.set({ left: t.logo.anchor === "tl" ? W * t.logo.x : W * t.logo.x - w,
                  top: t.logo.anchor === "tl" ? H * t.logo.y : H * t.logo.y - h, name: "Logo", role: "logo" });
        canvas.add(img);
      } catch (e) {}
    }
    await loadFontsIn(canvas.getObjects());
    canvas.getObjects().forEach(o => o.initDimensions && o.initDimensions());
    settle(canvas.getObjects());
    quiet = false; canvas.renderAll(); record(); checkSoon();
  }
  // ---- the template panel: categories, search, favourites, live previews in this brand and size
  const builtins = $("[data-builtins]");
  const CAT_NAMES = Object.fromEntries(LIB.CATS);
  const month = new Date().getMonth() + 1;
  const monthsAway = t => t.months ? Math.min(...t.months.map(m => (m - month + 12) % 12)) : 99;
  let favs = new Set(C.favs || []);
  const pw = Math.round(160 * Math.min(1, W / H)), ph = Math.round(160 * Math.min(1, H / W));
  const sections = {};
  ["seasonal", "offers", "facts", "news", "patients", "basics"].forEach(cat => {
    const sec = document.createElement("div");
    sec.className = "ed-tpl-sec"; sec.dataset.tplSec = cat;
    sec.innerHTML = `<h3></h3><div class="ed-tpl-grid"></div>`;
    sec.querySelector("h3").textContent = cat === "seasonal" ? "Holidays & seasonal (coming up first)" : CAT_NAMES[cat];
    builtins.appendChild(sec); sections[cat] = sec.querySelector(".ed-tpl-grid");
  });
  Object.entries(T)
    .sort(([, a], [, b]) => a.cat === "seasonal" && b.cat === "seasonal" ? monthsAway(a) - monthsAway(b) : 0)
    .forEach(([key, t]) => {
      const w = document.createElement("div");
      w.className = "ed-tpl-wrap"; w.dataset.tplKey = "b:" + key; w.dataset.tplCat = t.cat; w.dataset.tplName = t.name.toLowerCase() + " " + (CAT_NAMES[t.cat] || "").toLowerCase();
      if (monthsAway(t) <= 1) w.dataset.soon = "1";
      w.innerHTML = `<button type="button" class="ed-tpl" data-builtin="${key}"><canvas width="${pw}" height="${ph}"></canvas><span></span></button>` +
        `<button type="button" class="ed-tpl-fav" data-fav aria-pressed="false">☆</button>`;
      w.querySelector("span").textContent = t.name;
      if (w.dataset.soon) w.querySelector(".ed-tpl").insertAdjacentHTML("beforeend", `<em class="ed-tpl-soon">Coming up</em>`);
      w.querySelector("[data-fav]").setAttribute("aria-label", `Add ${t.name} to favourites`);
      sections[t.cat].appendChild(w);
    });
  const catBar = $("[data-tpl-cats]"), search = $("[data-tpl-search]"), empty = $("[data-tpl-empty]");
  const CHIPS = [["favorites", "★ Favourites"], ["all", "All"], ["soon", "Coming up"], ...LIB.CATS.filter(([k]) => !["favorites", "all"].includes(k))];
  if (C.hasTeam) CHIPS.push(["team", "Team's"]);
  let view = favs.size ? "favorites" : "all";
  try { view = localStorage.getItem("mcdp_tpl_view") || view; } catch (e) {}
  if (!CHIPS.some(([k]) => k === view)) view = "all";
  CHIPS.forEach(([k, label]) => {
    const c = document.createElement("button");
    c.type = "button"; c.className = "ed-chip"; c.dataset.cat = k; c.textContent = label; c.setAttribute("role", "tab");
    catBar.appendChild(c);
  });
  const tiles = () => $$(".ed-tpl-wrap");
  function showTemplates() {
    const q = (search.value || "").trim().toLowerCase();
    $$("[data-cat]", catBar).forEach(c => { c.classList.toggle("on", c.dataset.cat === view); c.setAttribute("aria-selected", c.dataset.cat === view); });
    let shown = 0;
    tiles().forEach(w => {
      const fav = favs.has(w.dataset.tplKey), star = w.querySelector("[data-fav]");
      star.textContent = fav ? "★" : "☆"; star.setAttribute("aria-pressed", fav); star.classList.toggle("on", fav);
      star.title = fav ? "Remove from favourites" : "Add to favourites";
      const ok = (q ? w.dataset.tplName.includes(q) : true) && (q || view === "all" || (view === "favorites" ? fav : view === "soon" ? w.dataset.soon : w.dataset.tplCat === view));
      w.hidden = !ok; if (ok) shown++;
    });
    $$(".ed-tpl-sec").forEach(s => { s.hidden = !$$(".ed-tpl-wrap", s).some(w => !w.hidden); });
    empty.hidden = shown > 0;
    empty.textContent = q ? `No templates match “${search.value.trim()}”.` : view === "favorites" ? "No favourites yet. Tap the ☆ on any template to keep it here." : "Nothing here yet.";
    drawVisible();
  }
  catBar.addEventListener("click", e => {
    const c = e.target.closest("[data-cat]"); if (!c) return;
    view = c.dataset.cat; search.value = "";
    try { localStorage.setItem("mcdp_tpl_view", view); } catch (err) {}
    showTemplates();
  });
  search.addEventListener("input", showTemplates);
  $("#ed-p-templates").addEventListener("click", async e => {
    const st = e.target.closest("[data-fav]"); if (!st) return;
    e.preventDefault(); e.stopPropagation();
    const key = st.closest(".ed-tpl-wrap").dataset.tplKey, on = !favs.has(key);
    if (on) favs.add(key); else favs.delete(key);
    showTemplates();
    const fd = new FormData(); fd.append("key", key); fd.append("on", on ? "1" : "0");
    try {
      const r = await fetch("/studio/template-favs", { method: "POST", body: fd, headers: { Accept: "application/json" } });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
      favs = new Set(j.favs); say(on ? "Added to favourites" : "Removed from favourites");
    } catch (err) {
      if (on) favs.delete(key); else favs.add(key);
      say(`Favourite not saved: ${err.message}`);
    }
    showTemplates();
  });
  // previews are drawn as they come into view (there are a lot of them)
  let logoImg = null, logoTried = false, drawGen = 0;
  async function previewLogo() {
    if (logoTried) return logoImg;
    logoTried = true;
    try { if (LOGO()) logoImg = await loadImage(LOGO()); } catch (e) {}
    return logoImg;
  }
  function drawOne(b) {
    const el = b.querySelector("canvas"), t = T[b.dataset.builtin].build();
    if (el._sc) el._sc.dispose();
    const sc = new fabric.StaticCanvas(el, { width: pw, height: ph, renderOnAddRemove: false, enableRetinaScaling: false });
    el._sc = sc;
    sc.setBackgroundColor(t.bg, () => {}); sc.setZoom(pw / W);
    settle(t.objs);
    t.objs.forEach(o => sc.add(o));
    if (t.logo && logoImg) {
      const img = new fabric.Image(logoImg.getElement());
      const s = Math.min((W * t.logo.size) / logoImg.width, (H * t.logo.size) / logoImg.height * 1.4);
      img.set({ scaleX: s, scaleY: s });
      const w = logoImg.width * s, h = logoImg.height * s;
      img.set({ left: t.logo.anchor === "tl" ? W * t.logo.x : W * t.logo.x - w, top: t.logo.anchor === "tl" ? H * t.logo.y : H * t.logo.y - h });
      sc.add(img);
    }
    sc.renderAll();
    b.dataset.drawn = drawGen;
  }
  const io = "IntersectionObserver" in window ? new IntersectionObserver(es => es.forEach(en => {
    if (en.isIntersecting && en.target.dataset.drawn != drawGen) drawOne(en.target);
  }), { root: null, rootMargin: "300px" }) : null;
  function drawVisible() {
    if (!builtins.dataset.drawn) return;
    $$("[data-builtin]").forEach(b => { if (io) { io.unobserve(b); io.observe(b); } else if (b.dataset.drawn != drawGen) drawOne(b); });
  }
  async function drawPreviews() {          // (re)draw the previews, e.g. after the brand fonts change
    drawGen++;
    await previewLogo();
    await Promise.all([fonts.heading, fonts.body].map(f => fontReady(f, 700).catch(() => {})));
    drawVisible();
  }
  showTemplates();

  async function applyTeamTemplate(tid) {
    const r = await fetch(`/studio/templates/${tid}/doc`); const doc = await r.json();
    await loadDoc({ ...doc, resize_from: (doc.w !== W || doc.h !== H) ? { w: doc.w, h: doc.h } : null });
    record();
  }

  // ---------------------------------------------------------------- loading (and re-fitting a resized copy)
  // Fit a layout made for one size onto another: things keep their size (scaled to the shorter side), full-width
  // or full-height things stretch, and things touching an edge stay on that edge.
  function placeBox(b, from, sx, sy, s) {
    const wide = b.w >= from.w * 0.9, tall = b.h >= from.h * 0.9, e = 0.02;
    const w = wide ? b.w * sx : b.w * s, h = tall ? b.h * sy : b.h * s;
    let x, y;
    if (wide || b.x <= from.w * e) x = b.x * s;
    else if (b.x + b.w >= from.w * (1 - e)) x = W - (from.w - b.x) * s;
    else x = (b.x + b.w / 2) * sx - w / 2;
    if (tall || b.y <= from.h * e) y = b.y * s;
    else if (b.y + b.h >= from.h * (1 - e)) y = H - (from.h - b.y) * s;
    else y = (b.y + b.h / 2) * sy - h / 2;
    return { x, y, w, h, wide, tall };
  }
  function refit(from) {
    const sx = W / from.w, sy = H / from.h, s = Math.min(sx, sy), e = 0.02;
    const kx = s + (sx - s) * 0.5, ky = s + (sy - s) * 0.5;      // spread the layout half as much as the page grew
    const objs = canvas.getObjects();
    const box = o => o.frame ? { x: o.frame.x, y: o.frame.y, w: o.frame.w, h: o.frame.h }
                             : { x: o.left, y: o.top, w: o.getScaledWidth(), h: o.getScaledHeight() };
    const pinned = b => b.w >= from.w * 0.9 || b.h >= from.h * 0.9 || b.x <= from.w * e || b.y <= from.h * e
                        || b.x + b.w >= from.w * (1 - e) || b.y + b.h >= from.h * (1 - e);
    const old = new Map(objs.map(o => [o, box(o)]));
    const done = new Map();
    // 1. bands, backgrounds and anything touching an edge keep to that edge
    objs.forEach(o => { const b = old.get(o); if (pinned(b) && o.type !== "textbox") done.set(o, placeBox(b, from, sx, sy, s)); });
    // 2. everything else: inside a pinned band it moves with the band, otherwise it keeps its place on the page
    objs.forEach(o => {
      if (done.has(o)) return;
      const b = old.get(o), cx = b.x + b.w / 2, cy = b.y + b.h / 2, w = b.w * s, h = b.h * s;
      if (pinned(b)) { done.set(o, placeBox(b, from, sx, sy, s)); return; }
      let host = null;
      for (const [c, nb] of done) {
        const cb = old.get(c);
        if (c.type !== "textbox" && cx >= cb.x && cx <= cb.x + cb.w && cy >= cb.y && cy <= cb.y + cb.h
            && cb.w * cb.h < from.w * from.h * 0.95 && (!host || cb.w * cb.h < old.get(host).w * old.get(host).h)) host = c;
      }
      let x = W / 2 + (cx - from.w / 2) * kx - w / 2, y = H / 2 + (cy - from.h / 2) * ky - h / 2;
      if (host) {
        const cb = old.get(host), nb = done.get(host);
        y = nb.y + (b.y - cb.y) * s;
        if (!(cb.w >= from.w * 0.9)) x = nb.x + (b.x - cb.x) * s;
      }
      done.set(o, { x, y, w, h, wide: false, tall: false });
    });
    objs.forEach(o => {
      const b = done.get(o);
      if (o.type === "textbox") {
        o.set({ fontSize: o.fontSize * s, width: Math.min(b.w / (o.scaleX || 1), W) });
        o.initDimensions();
      } else if (!o.frame) {
        o.set({ scaleX: o.scaleX * (b.wide ? sx : s), scaleY: o.scaleY * (b.tall ? sy : s) });
      }
      o.set({ left: b.x, top: b.y });
      if (o.frame) {
        o.frame = { ...o.frame, x: b.x, y: b.y, w: b.w, h: b.h, rx: (o.frame.rx || 0) * s };
        const k = Math.max(o.frame.w / o.width, o.frame.h / o.height);
        o.set({ scaleX: k, scaleY: k, left: o.frame.x + (o.frame.w - o.width * k) / 2, top: o.frame.y + (o.frame.h - o.height * k) / 2,
                clipPath: clipFor(o.frame) });
      }
      o.setCoords();
    });
  }
  function loadDoc(doc) {
    return new Promise(res => {
      quiet = true;
      const data = { ...doc };
      delete data.w; delete data.h; delete data.resize_from; delete data.v;
      canvas.loadFromJSON(data, async () => {
        await loadFontsIn(canvas.getObjects());
        canvas.getObjects().forEach(o => o.initDimensions && o.initDimensions());
        if (doc.resize_from && doc.resize_from.w) { refit(doc.resize_from); docDirty = renderDirty = true; }
        canvas.renderAll(); quiet = false; layers(); checkSoon(); res();
      });
    });
  }

  // ---------------------------------------------------------------- the panels
  const panelBtns = $$("[data-tab]");
  const openTab = key => {
    panelBtns.forEach(b => b.classList.toggle("on", b.dataset.tab === key));
    $$("[data-panel]").forEach(p => { p.hidden = p.dataset.panel !== key; });
    if (key === "templates" && !builtins.dataset.drawn) { builtins.dataset.drawn = 1; drawPreviews(); }
    if ((key === "photos" || key === "uploads" || key === "brand") && !media) loadMedia();
  };
  panelBtns.forEach(b => b.addEventListener("click", () => openTab(b.dataset.tab)));

  // colours
  const sw = $("[data-swatches]");
  [...C.colors, ...NEUTRALS].forEach(c => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "ed-swatch"; b.style.background = c; b.title = c; b.dataset.color = c;
    sw.appendChild(b);
  });
  sw.addEventListener("click", e => {
    const c = e.target.dataset.color; if (!c || !C.canEdit) return;
    const o = canvas.getActiveObject();
    if (o) { (o.type === "activeSelection" ? o.getObjects() : [o]).forEach(x => x.set(x.type === "line" ? "stroke" : "fill", c)); }
    else canvas.setBackgroundColor(c, () => {});
    canvas.requestRenderAll(); record(); syncCtx();
  });

  // brand fonts
  $$("[data-brand-font]").forEach(s => s.addEventListener("change", async () => {
    const heading = $("[data-brand-font=heading]").value, body = $("[data-brand-font=body]").value;
    const pid = C.pid === null ? 0 : C.pid;
    const r = await fetch(`/studio/brand/${pid}/fonts`, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ heading, body }) });
    const j = await r.json().catch(() => ({}));
    $("[data-font-note]").textContent = r.ok ? "Saved. New headings and text use these; click a template to restyle." : (j.error || "Not saved.");
    if (r.ok) { fonts = { heading, body }; if (builtins.dataset.drawn) drawPreviews(); }
  }));

  // text
  $$("[data-add-text]").forEach(b => b.addEventListener("click", async () => {
    const k = b.dataset.addText;
    if (k === "cta") { await fontReady(fonts.body, 700); return addButton(); }
    const d = textDefaults(k);
    await fontReady(d.fontFamily, d.fontWeight);
    const t = textbox({ heading: "Add a heading", subhead: "Add a subheading", body: "Add a little bit of body text" }[k], { ...d, width: W * 0.7 });
    place(t); checkSoon();
  }));

  // shapes
  $$("[data-shape]").forEach(b => b.addEventListener("click", () => {
    const k = b.dataset.shape, s = Math.min(W, H) * 0.3;
    const o = k === "rect" ? rect({ width: s, height: s })
      : k === "round" ? rect({ width: s * 1.4, height: s, rx: s * 0.15, ry: s * 0.15, fill: A })
      : k === "circle" ? new fabric.Circle({ radius: s / 2, fill: A, name: "Circle" })
      : k === "triangle" ? new fabric.Triangle({ width: s, height: s * 0.87, fill: P, name: "Triangle" })
      : k === "line" ? new fabric.Line([0, 0, s * 1.6, 0], { stroke: "#1A1A1A", strokeWidth: Math.max(2, U * 0.5), name: "Line" })
      : k === "frame" ? frame({ width: s * 1.6, height: s * 1.2 })
      : circleFrame({ radius: s / 2 });
    place(o);
  }));

  // photos and uploads
  let media = null;
  const tile = (m, onClick) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "ed-media"; b.title = m.name || "";
    b.innerHTML = `<img alt="" loading="lazy">`; b.querySelector("img").src = m.thumb;
    b.addEventListener("click", () => onClick(m));
    return b;
  };
  const use = async m => {
    if (!C.canEdit) return;
    try { await addImage(m.src, m.name); } catch (e) { alertBar(e.message); }
  };
  async function loadMedia() {
    const r = await fetch(`/studio/media?pid=${C.pid === null ? "" : C.pid}`);
    media = await r.json();
    const ph = $("[data-photos]"); ph.innerHTML = "";
    const group = (title, list) => {
      if (!list || !list.length) return;
      const h = document.createElement("h3"); h.textContent = title; ph.appendChild(h);
      const g = document.createElement("div"); g.className = "ed-media-grid"; list.forEach(m => g.appendChild(tile(m, use))); ph.appendChild(g);
    };
    group("Practice photos", media.photos); group("AI images", media.ai);
    group("Library pictures", media.library.filter(m => !/design uploads/i.test(m.folder || "")));
    if (!ph.children.length) ph.innerHTML = `<p class="small muted">No photos yet. Add some on the practice page (Brand kit → Ad images) or under Uploads.</p>`;
    const lg = $("[data-logos]"); lg.innerHTML = "";
    (media.logo || []).forEach(m => lg.appendChild(tile(m, use)));
    if (!lg.children.length) lg.innerHTML = `<p class="small muted">No logo saved for this brand yet${C.isPractice ? " (add it on the practice page)" : ""}.</p>`;
    const up = $("[data-uploads]"); up.innerHTML = "";
    media.library.slice(0, 40).forEach(m => up.appendChild(tile(m, use)));
  }
  async function upload(files) {
    for (const f of files) {
      const fd = new FormData(); fd.append("file", f);
      say(`Adding ${f.name}…`);
      const r = await fetch(`/studio/designs/${C.iid}/upload`, { method: "POST", body: fd, headers: { Accept: "application/json" } });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { alertBar(j.error || "That picture didn't go."); continue; }
      say(""); media = null; await loadMedia();
      await use(j);
    }
  }
  const upIn = $("[data-upload]");
  if (upIn) upIn.addEventListener("change", () => { upload([...upIn.files]); upIn.value = ""; });
  if (C.canEdit) {
    wrap.addEventListener("dragover", e => { if ([...e.dataTransfer.types].includes("Files")) { e.preventDefault(); wrap.classList.add("drop"); } });
    wrap.addEventListener("dragleave", () => wrap.classList.remove("drop"));
    wrap.addEventListener("drop", e => { e.preventDefault(); wrap.classList.remove("drop"); upload([...e.dataTransfer.files].filter(f => f.type.startsWith("image/"))); });
    const dz = $("[data-upload-drop]");
    if (dz) { dz.addEventListener("dragover", e => e.preventDefault()); dz.addEventListener("drop", e => { e.preventDefault(); upload([...e.dataTransfer.files]); }); }
  }

  // AI picture
  const aiImg = $("[data-ai-image]");
  if (aiImg) aiImg.addEventListener("submit", async e => {
    e.preventDefault();
    const btn = aiImg.querySelector("button"); btn.disabled = true; btn.textContent = "Making it (about 20 s)…";
    try {
      const r = await fetch("/studio/ai/image", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ pid: C.pid, idea: aiImg.idea.value }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || "It didn't work.");
      media = null; await loadMedia(); await use(j);
    } catch (err) { alertBar(err.message); }
    btn.disabled = false; btn.textContent = "Make it";
  });

  // AI words
  const aiCopy = $("[data-ai-copy]");
  if (aiCopy) aiCopy.addEventListener("submit", async e => {
    e.preventDefault();
    const btn = aiCopy.querySelector("button"), out = $("[data-ai-options]");
    btn.disabled = true; btn.textContent = "Writing…"; out.innerHTML = "";
    try {
      const r = await fetch("/studio/ai/copy", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ pid: C.pid, brief: aiCopy.brief.value, size: `${W} × ${H}` }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || "It didn't work.");
      j.options.forEach(o => {
        const d = document.createElement("div"); d.className = "ed-ai-option";
        d.innerHTML = `<strong></strong><p></p><span class="tag"></span><ul class="small"></ul><button type="button" class="btn btn-small btn-quiet">Use</button>`;
        d.querySelector("strong").textContent = o.headline; d.querySelector("p").textContent = o.subhead;
        const tg = d.querySelector(".tag"); if (o.cta) tg.textContent = o.cta; else tg.remove();
        (o.notes || []).forEach(n => { const li = document.createElement("li"); li.textContent = n; d.querySelector("ul").appendChild(li); });
        d.querySelector("button").addEventListener("click", () => useCopy(o));
        out.appendChild(d);
      });
    } catch (err) { out.innerHTML = ""; alertBar(err.message); }
    btn.disabled = false; btn.textContent = "Write 3 more";
  });
  async function useCopy(o) {
    const objs = canvas.getObjects();
    const set = async (role, text, kind) => {
      if (!text) return;
      const t = objs.find(x => x.type === "textbox" && x.role === role);
      if (t) { t.set({ text }); t.initDimensions(); }
      else if (kind === "cta") addButton(text);
      else { const d = textDefaults(kind); await fontReady(d.fontFamily, d.fontWeight); place(textbox(text, d)); }
    };
    await set("headline", o.headline, "heading"); await set("subhead", o.subhead, "subhead"); await set("cta", o.cta, "cta");
    canvas.requestRenderAll(); record(); checkSoon();
  }

  // ---------------------------------------------------------------- the context bar and properties
  const ctx = $("[data-ctx]"), props = $("[data-props]");
  const sel = () => canvas.getActiveObject();
  const items = () => { const o = sel(); return o ? (o.type === "activeSelection" ? o.getObjects() : [o]) : []; };
  const isText = o => o && (o.type === "textbox" || o.type === "i-text" || o.type === "text");
  const hex = c => {
    if (!c || typeof c !== "string" || c === "transparent") return "#ffffff";
    if (/^#[0-9a-f]{6}$/i.test(c)) return c;
    if (/^#[0-9a-f]{3}$/i.test(c)) return "#" + [...c.slice(1)].map(x => x + x).join("");
    const m = c.match(/\d+/g); return m ? "#" + m.slice(0, 3).map(v => (+v).toString(16).padStart(2, "0")).join("") : "#000000";
  };
  function syncCtx() {
    const o = sel(), its = items(), any = !!o && C.canEdit;
    $("[data-ctx-empty]").hidden = any;
    $("[data-ctx-text]").hidden = !(any && its.every(isText));
    $("[data-ctx-fill]").hidden = !(any && its.every(x => x.type !== "image"));
    $("[data-ctx-common]").hidden = !any;
    props.hidden = !any || its.length !== 1;
    if (!any) return;
    const f = its[0];
    if (isText(f)) {
      $("[data-prop=fontFamily]").value = f.fontFamily;
      $("[data-prop=fontSize]").value = Math.round(f.fontSize * (f.scaleY || 1));
      $("[data-toggle=bold]").classList.toggle("on", +f.fontWeight >= 600 || f.fontWeight === "bold");
      $("[data-toggle=italic]").classList.toggle("on", f.fontStyle === "italic");
      $("[data-toggle=underline]").classList.toggle("on", !!f.underline);
      $("[data-toggle=upper]").classList.toggle("on", !!f.upper);
      $$("[data-align]").forEach(b => b.classList.toggle("on", b.dataset.align === f.textAlign));
    }
    $("[data-prop=fill]").value = hex(f.type === "line" ? f.stroke : f.fill);
    if (its.length === 1) {
      $("[data-props-title]").textContent = f.name || ({ textbox: "Text", image: "Picture", rect: "Shape", circle: "Circle" })[f.type] || "Selected";
      $("[data-prop=opacity]").value = Math.round((f.opacity ?? 1) * 100);
      $("[data-props-text]").hidden = !isText(f);
      if (isText(f)) {
        $("[data-prop=lineHeight]").value = Math.round((f.lineHeight || 1.16) * 100);
        $("[data-prop=charSpacing]").value = f.charSpacing || 0;
        $("[data-prop=textBackgroundColor]").value = hex(f.textBackgroundColor || "#ffff00");
      }
      $("[data-props-shape]").hidden = !(f.type === "rect" || f.type === "circle" || f.type === "triangle" || f.type === "image");
      if (f.type === "rect") $("[data-prop=rx]").value = Math.round(((f.rx || 0) / Math.max(1, Math.min(f.width, f.height))) * 100);
      $("[data-prop=rx]").closest("label").hidden = f.type !== "rect";
      $("[data-prop=stroke]").value = hex(f.stroke || "#000000");
      $("[data-prop=strokeWidth]").value = Math.round(f.strokeWidth || 0);
      $("[data-prop=shadow]").value = f.shadow ? Math.round(f.shadow.blur / U * 4) : 0;
    }
  }
  canvas.on("selection:created", () => { syncCtx(); layers(); });
  canvas.on("selection:updated", () => { syncCtx(); layers(); });
  canvas.on("selection:cleared", () => { syncCtx(); layers(); });
  canvas.on("object:scaling", syncCtx);

  const apply = (fn, rec = true) => { items().forEach(fn); canvas.requestRenderAll(); if (rec) record(); syncCtx(); };
  $("[data-prop=fontFamily]").addEventListener("change", async e => {
    const f = e.target.value; await fontReady(f, 700); await fontReady(f, 400);
    apply(o => { if (isText(o)) { o.set("fontFamily", f); o.initDimensions(); } });
  });
  $("[data-prop=fontSize]").addEventListener("change", e => apply(o => { if (isText(o)) { o.set({ fontSize: +e.target.value / (o.scaleY || 1) }); o.initDimensions(); } }));
  $("[data-toggle=bold]").addEventListener("click", async () => {
    const on = !items().every(o => +o.fontWeight >= 600 || o.fontWeight === "bold");
    await Promise.all(items().map(o => fontReady(o.fontFamily, on ? 700 : 400)));
    apply(o => { o.set("fontWeight", on ? 700 : 400); o.initDimensions && o.initDimensions(); });
  });
  $("[data-toggle=italic]").addEventListener("click", () => apply(o => o.set("fontStyle", o.fontStyle === "italic" ? "normal" : "italic")));
  $("[data-toggle=underline]").addEventListener("click", () => apply(o => o.set("underline", !o.underline)));
  $("[data-toggle=upper]").addEventListener("click", () => apply(o => {
    if (!isText(o)) return;
    if (o.upper) { o.set({ text: o.rawText || o.text, upper: false }); }
    else { o.set({ rawText: o.text, text: o.text.toUpperCase(), upper: true }); }
    o.initDimensions();
  }));
  $$("[data-align]").forEach(b => b.addEventListener("click", () => apply(o => o.set("textAlign", b.dataset.align))));
  $("[data-prop=fill]").addEventListener("input", e => apply(o => o.set(o.type === "line" ? "stroke" : "fill", e.target.value), false));
  $("[data-prop=fill]").addEventListener("change", () => record());
  $("[data-prop=opacity]").addEventListener("input", e => apply(o => o.set("opacity", e.target.value / 100), false));
  $("[data-prop=lineHeight]").addEventListener("input", e => apply(o => { o.set("lineHeight", e.target.value / 100); o.initDimensions(); }, false));
  $("[data-prop=charSpacing]").addEventListener("input", e => apply(o => { o.set("charSpacing", +e.target.value); o.initDimensions(); }, false));
  $("[data-prop=textBackgroundColor]").addEventListener("input", e => apply(o => o.set("textBackgroundColor", e.target.value), false));
  $("[data-clear=textBackgroundColor]").addEventListener("click", () => apply(o => o.set("textBackgroundColor", "")));
  $("[data-prop=rx]").addEventListener("input", e => apply(o => { if (o.type === "rect") { const r = Math.min(o.width, o.height) * e.target.value / 100; o.set({ rx: r, ry: r }); } }, false));
  $("[data-prop=stroke]").addEventListener("input", e => apply(o => { o.set({ stroke: e.target.value, strokeDashArray: null }); if (!o.strokeWidth) o.set("strokeWidth", Math.max(2, U * 0.4)); }, false));
  $("[data-prop=strokeWidth]").addEventListener("change", e => apply(o => o.set("strokeWidth", +e.target.value)));
  $("[data-prop=shadow]").addEventListener("input", e => apply(o => o.set("shadow", +e.target.value ? new fabric.Shadow({ color: "rgba(0,0,0,0.28)", blur: U * e.target.value / 4, offsetX: 0, offsetY: U * e.target.value / 12 }) : null), false));
  $$("[data-props] input[type=range]").forEach(r => r.addEventListener("change", () => record()));

  // actions
  const act = {
    undo, redo,
    "center-h": () => apply(o => { o.set("left", (W - o.getScaledWidth()) / 2); o.setCoords(); }),
    "center-v": () => apply(o => { o.set("top", (H - o.getScaledHeight()) / 2); o.setCoords(); }),
    forward: () => apply(o => canvas.bringForward(o)),
    backward: () => apply(o => canvas.sendBackwards(o)),
    duplicate: () => {
      const o = sel(); if (!o) return;
      o.clone(c => { c.set({ left: c.left + U * 3, top: c.top + U * 3 });
        if (c.type === "activeSelection") { c.canvas = canvas; c.forEachObject(x => canvas.add(x)); c.setCoords(); } else canvas.add(c);
        canvas.setActiveObject(c); canvas.requestRenderAll(); }, PROPS);
    },
    delete: () => { const its = items(); canvas.discardActiveObject(); its.forEach(o => canvas.remove(o)); canvas.requestRenderAll(); checkSoon(); },
    done: async () => { if (await save(true)) location.href = C.back; },
    review: async () => {                                   // pick who approves it on the file's page
      if (await save(true)) location.href = C.back + "#approve";
    },
    template: () => { const d = $("[data-tpl-dialog]"); d.showModal(); d.querySelector("input[name=name]").focus(); },
  };
  $$("[data-act]").forEach(b => b.addEventListener("click", () => act[b.dataset.act] && act[b.dataset.act]()));
  $("[data-tpl-form]").addEventListener("submit", async e => {
    const f = e.target;
    if (e.submitter && e.submitter.value === "cancel") return;
    const fd = new FormData();
    fd.append("name", f.name.value); fd.append("scope", f.scope.value); fd.append("pid", C.pid === null ? "" : C.pid);
    fd.append("doc", new Blob([docJSON()], { type: "application/json" }), "design.json");
    fd.append("png", await toBlob(render("png")), "t.png");
    const r = await fetch("/studio/templates", { method: "POST", body: fd, headers: { Accept: "application/json" } });
    const j = await r.json().catch(() => ({}));
    alertBar(r.ok ? `Saved “${f.name.value}”. It's under Templates for new designs.` : (j.error || "Not saved."), r.ok);
  });

  // resize: a copy in another size
  $$("[data-resize]").forEach(b => b.addEventListener("click", async () => {
    await save(true);
    const r = await fetch(`/studio/designs/${C.iid}/copy`, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ size: b.dataset.resize }) });
    const j = await r.json().catch(() => ({}));
    if (r.ok) location.href = j.url; else alertBar(j.error || "Couldn't make the copy.");
  }));

  // download
  $$("[data-download]").forEach(b => b.addEventListener("click", async () => {
    await loadFontsIn(canvas.getObjects());
    const fmt = b.dataset.download === "jpg" ? "jpeg" : "png";
    const a = document.createElement("a");
    a.href = render(fmt, 0.92); a.download = `${($("[data-title]").value || "design").replace(/[\\/:*?"<>|]+/g, "-")}.${b.dataset.download}`;
    document.body.appendChild(a); a.click(); a.remove();
    b.closest("details").open = false;
    if (C.canEdit && renderDirty) save(true);
  }));
  document.addEventListener("click", e => $$(".ed-menu[open]").forEach(d => { if (!d.contains(e.target)) d.open = false; }));

  // templates
  builtins.addEventListener("click", e => { const b = e.target.closest("[data-builtin]"); if (b && C.canEdit) applyTemplate(b.dataset.builtin); });
  $$("[data-team-tpl]").forEach(b => b.addEventListener("click", () => C.canEdit && applyTeamTemplate(b.dataset.teamTpl)));

  // keyboard
  document.addEventListener("keydown", e => {
    if (!C.canEdit) return;
    const o = sel(), typing = o && o.isEditing, inField = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); save(true); return; }
    if (typing || inField) return;
    if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); }
    else if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); }
    else if (mod && e.key.toLowerCase() === "d") { e.preventDefault(); act.duplicate(); }
    else if ((e.key === "Delete" || e.key === "Backspace") && o) { e.preventDefault(); act.delete(); }
    else if (o && e.key.startsWith("Arrow")) {
      e.preventDefault();
      const d = (e.shiftKey ? 10 : 1) / zoom;
      items().forEach(x => x.set(e.key === "ArrowLeft" ? { left: x.left - d } : e.key === "ArrowRight" ? { left: x.left + d }
        : e.key === "ArrowUp" ? { top: x.top - d } : { top: x.top + d }));
      o.setCoords(); canvas.requestRenderAll(); recordSoon();
    }
  });

  // snap to the page's centre lines while dragging
  let guides = [];
  canvas.on("object:moving", e => {
    const o = e.target, t = 8 / zoom; guides = [];
    const cx = o.left + o.getScaledWidth() / 2, cy = o.top + o.getScaledHeight() / 2;
    if (Math.abs(cx - W / 2) < t) { o.set("left", W / 2 - o.getScaledWidth() / 2); guides.push(["v", W / 2]); }
    if (Math.abs(cy - H / 2) < t) { o.set("top", H / 2 - o.getScaledHeight() / 2); guides.push(["h", H / 2]); }
  });
  canvas.on("mouse:up", () => { guides = []; canvas.requestRenderAll(); });
  canvas.on("after:render", () => {
    if (!guides.length) return;
    const c = canvas.getSelectionContext(); c.save(); c.strokeStyle = "#B83A5E"; c.lineWidth = 1;
    guides.forEach(([k, v]) => { c.beginPath(); if (k === "v") { c.moveTo(v * zoom, 0); c.lineTo(v * zoom, H * zoom); } else { c.moveTo(0, v * zoom); c.lineTo(W * zoom, v * zoom); } c.stroke(); });
    c.restore();
  });

  // ---------------------------------------------------------------- layers
  const layerList = $("[data-layers]");
  function layers() {
    const objs = canvas.getObjects().slice().reverse(), active = items();
    layerList.innerHTML = "";
    objs.forEach(o => {
      const li = document.createElement("li");
      li.className = active.includes(o) ? "on" : "";
      const label = o.name || ({ textbox: "Text", image: "Picture", rect: "Shape", circle: "Circle", line: "Line", triangle: "Triangle" })[o.type] || o.type;
      const extra = isText(o) ? `: ${o.text.slice(0, 28)}` : "";
      li.innerHTML = `<button type="button" class="ed-layer-name"></button>` + (C.canEdit ? `<button type="button" class="ed-icon" data-l="vis" title="Show or hide">${o.visible === false ? "◌" : "●"}</button><button type="button" class="ed-icon" data-l="lock" title="Lock or unlock">${o.selectable === false ? "🔒" : "🔓"}</button>` : "");
      li.querySelector(".ed-layer-name").textContent = label + extra;
      li.querySelector(".ed-layer-name").addEventListener("click", () => { if (o.selectable !== false && C.canEdit) { canvas.setActiveObject(o); canvas.requestRenderAll(); } });
      const v = li.querySelector("[data-l=vis]"), l = li.querySelector("[data-l=lock]");
      if (v) v.addEventListener("click", () => { o.set("visible", o.visible === false); canvas.discardActiveObject(); canvas.requestRenderAll(); record(); });
      if (l) l.addEventListener("click", () => { const lock = o.selectable !== false; o.set({ selectable: !lock, evented: !lock }); canvas.discardActiveObject(); canvas.requestRenderAll(); record(); });
      layerList.appendChild(li);
    });
    if (!objs.length) layerList.innerHTML = `<li class="small muted">Nothing on the page yet.</li>`;
  }

  // ---------------------------------------------------------------- brand check
  let checkT;
  function checkSoon() { clearTimeout(checkT); checkT = setTimeout(check, 900); }
  async function check() {
    const texts = canvas.getObjects().filter(isText).map(o => o.text).filter(Boolean);
    const list = $("[data-check]");
    if (!texts.length) { list.innerHTML = `<li class="small muted">Nothing to check yet.</li>`; return; }
    try {
      const r = await fetch(`/studio/designs/${C.iid}/check`, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ texts }) });
      const j = await r.json();
      list.innerHTML = "";
      if (!j.problems.length) { list.innerHTML = `<li class="ed-ok">Looks good: nothing against the brand kit or health-ad rules.</li>`; return; }
      j.problems.forEach(p => { const li = document.createElement("li"); li.className = "ed-warn"; li.textContent = p; list.appendChild(li); });
    } catch (e) {}
  }

  // ---------------------------------------------------------------- messages
  let barT;
  function alertBar(msg, ok) {
    let b = $(".ed-alert");
    if (!b) { b = document.createElement("div"); b.className = "ed-alert"; b.setAttribute("role", "status"); document.body.appendChild(b); }
    b.textContent = msg; b.classList.toggle("ok", !!ok); b.hidden = false;
    clearTimeout(barT); barT = setTimeout(() => { b.hidden = true; }, 6000);
  }

  // ---------------------------------------------------------------- start
  (async () => {
    fit();
    const r = await fetch(`/studio/designs/${C.iid}/doc`, { headers: { Accept: "application/json" } });
    const doc = await r.json();
    await loadDoc(doc);
    hist = [snapshot()]; hi = 0;
    if (doc.resize_from) { say("Re-fitted to the new size: check it over"); save(true); } else say("");
    openTab(C.fresh && C.canEdit ? "templates" : (C.canEdit ? "text" : "brand"));
    if (C.fresh && !canvas.getObjects().length) say("Pick a template, or start from blank");
  })();
})();
