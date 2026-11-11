/* MCDP Studio video editor, version 2 (CapCut-style).
   The preview is a canvas that combines every layer frame by frame with the same arithmetic the NAS uses
   to make the MP4 (app/video2.py): main track with transitions, layers on tracks above it, crop, position,
   rotation, colour filters, motion, background removal, text, captions and music. */
(() => {
  "use strict";
  const C = JSON.parse(document.getElementById("vd-config").textContent);
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const fmt = t => { t = Math.max(0, t || 0); const m = Math.floor(t / 60); return `${m}:${(t - m * 60).toFixed(1).padStart(4, "0")}`; };
  const esc = s => String(s).replace(/[<&"]/g, c => ({ "<": "&lt;", "&": "&amp;", '"': "&quot;" })[c]);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const uid = () => Math.random().toString(36).slice(2, 10);
  const EPS = 2 / 30, ANIM_T = 0.35;
  const PRESET = Object.fromEntries(C.presets.map(([k, name, base]) => [k, { name, base }]));
  const TRANS = Object.fromEntries(C.transitions);

  let P = null, items = {}, sel = null, t = 0, playing = false, cropMode = false;
  const mq = window.matchMedia("(max-width: 760px)");
  let M = mq.matches;                       // phone layout: centre playhead, bottom toolbar, sheets
  const ICON = {
    media: '<path d="M7 18a4 4 0 0 1-.5-8 6 6 0 0 1 11.5 1.5A3.5 3.5 0 0 1 17.5 18"/><path d="M12 12v8M9 15l3-3 3 3"/>',
    stock: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 5v14M17 5v14M3 9h4M3 15h4M17 9h4M17 15h4"/>',
    audio: '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
    text: '<path d="M5 7V5h14v2M12 5v14M9 19h6"/>',
    captions: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 11h4M13 11h4M7 14h10"/>',
    effects: '<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/><path d="M18 15l.9 2.1L21 18l-2.1.9L18 21l-.9-2.1L15 18l2.1-.9z"/>',
    transitions: '<path d="M4 5l8 7-8 7zM20 5l-8 7 8 7z"/>',
    filters: '<circle cx="12" cy="8.5" r="4.5"/><circle cx="8.5" cy="14.5" r="4.5"/><circle cx="15.5" cy="14.5" r="4.5"/>',
    brand: '<path d="M4 20h16M6 20V9l6-5 6 5v11M10 20v-5h4v5"/>',
    ratio: '<rect x="4" y="6" width="16" height="12" rx="1" stroke-dasharray="3 2"/>',
    split: '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><path d="M20 4L8 16M14.5 14.5L20 20M8 8l4 4"/>',
    del: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/>',
    dup: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M4 16V6a2 2 0 0 1 2-2h10"/>',
    crop: '<path d="M6 2v14a2 2 0 0 0 2 2h14M2 6h14a2 2 0 0 1 2 2v14"/>',
    volume: '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
    mute: '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M17 9l5 6M22 9l-5 6"/>',
    speed: '<path d="M4.5 17a8.5 8.5 0 1 1 15 0"/><path d="M12 13l4-4"/>',
    anim: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
    edit: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    bg: '<circle cx="12" cy="9" r="3.2"/><path d="M5.5 20a6.5 6.5 0 0 1 13 0"/><path d="M3 3h3M18 3h3M3 3v3M21 3v3" />',
    up: '<path d="M12 4l8 4-8 4-8-4z"/><path d="M4 12l8 4 8-4" opacity=".45"/><path d="M12 22v-5" />',
    down: '<path d="M12 12l8 4-8 4-8-4z"/><path d="M4 8l8-4 8 4" opacity=".45"/><path d="M12 2v5"/>',
    back: '<path d="M15 5l-7 7 7 7"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    typing: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
    undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
    redo: '<path d="M15 14l5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h3"/>',
    zin: '<circle cx="11" cy="11" r="7"/><path d="M8 11h6M11 8v6M20 20l-4-4"/>',
    zout: '<circle cx="11" cy="11" r="7"/><path d="M8 11h6M20 20l-4-4"/>',
    fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    play: '<path d="M7 4v16l13-8z" fill="currentColor" stroke="none"/>',
    pause: '<path d="M6 4h4v16H6zM14 4h4v16h-4z" fill="currentColor" stroke="none"/>',
  };
  const ico = (k, s = 20) => `<svg viewBox="0 0 24 24" width="${s}" height="${s}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[k] || ""}</svg>`;
  [["[data-split]", "split"], ["[data-del]", "del"], ["[data-dup]", "dup"], ["[data-crop-btn]", "crop"], ["[data-undo]", "undo"], ["[data-redo]", "redo"],
   ["[data-zoom-in]", "zin"], ["[data-zoom-out]", "zout"], ["[data-zoom-fit]", "fit"], ["[data-play]", "play"]]
    .forEach(([q, k]) => { const b = document.querySelector(q); if (b) b.innerHTML = ico(k, q === "[data-play]" ? 18 : 19); });
  let hist = [], hi = -1;

  // ================================================================ timing (same as video2.layout)
  const clipLen = c => c.still ? c.out : (c.out - c.in) / (c.speed || 1);
  const layerLen = L => L.kind === "video" ? (L.out - L.in) / (L.speed || 1) : L.dur;
  function layout() {
    const segs = []; let prev = null;
    const list = P.main.map(c => ["clip", c]);
    if (P.outro.on && P.main.length) list.push(["outro", { out: P.outro.dur, still: true, trans: P.outro.trans || { type: "none" } }]);
    list.forEach(([kind, c]) => {
      const len = clipLen(c); let d = 0;
      const tr = c.trans || {};
      if (prev && (tr.type || "none") !== "none") d = Math.min(tr.dur || 0.5, prev.len / 2, len / 2);
      const start = prev ? prev.end - d : 0;
      const seg = { kind, c, start, len, end: start + len, d, trans: d ? tr.type : "none" };
      segs.push(seg); prev = seg;
    });
    return { segs, end: segs.length ? segs[segs.length - 1].end : 0 };
  }
  const total = () => layout().end;

  // ================================================================ colour (same as video2.grade)
  function grade(f) {
    f = f || {};
    const base = (PRESET[f.preset || "none"] || PRESET.none).base;
    const v = {};
    ["brightness", "contrast", "saturation", "warmth", "vignette"].forEach(k => { v[k] = clamp((base[k] || 0) + (f[k] || 0), -1, 1); });
    v.sepia = base.sepia || 0; v.vignette = Math.max(0, v.vignette);
    const I = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
    const mul = (a, b) => a.map((r, i) => [0, 1, 2].map(j => r[0] * b[0][j] + r[1] * b[1][j] + r[2] * b[2][j]));
    const m = v.sepia, sep = [[.393, .769, .189], [.349, .686, .168], [.272, .534, .131]];
    const S = I.map((r, i) => r.map((x, j) => (1 - m) * x + m * sep[i][j]));
    const k = 1 + v.saturation, lw = [0.2126, 0.7152, 0.0722];
    const Sat = I.map((r, i) => r.map((x, j) => (1 - k) * lw[j] + (i === j ? k : 0)));
    const w = v.warmth, Wm = [[1 + 0.12 * w, 0, 0], [0, 1 + 0.02 * w, 0], [0, 0, 1 - 0.12 * w]];
    const b = 1 + 0.5 * v.brightness, B = [[b, 0, 0], [0, b, 0], [0, 0, b]];
    const M = mul(B, mul(Wm, mul(Sat, S)));
    const neutral = M.every((r, i) => r.every((x, j) => Math.abs(x - I[i][j]) < 1e-6)) && v.contrast === 0 && v.vignette === 0;
    return { M, c: 1 + v.contrast, vig: v.vignette, neutral };
  }
  function applyGrade(cv, g) {
    if (!g || g.neutral) return;
    const x = cv.getContext("2d", { willReadFrequently: true });
    let d; try { d = x.getImageData(0, 0, cv.width, cv.height); } catch (e) { return; }
    const a = d.data, M = g.M, k = g.c;
    for (let i = 0; i < a.length; i += 4) {
      const r = a[i], gg = a[i + 1], bb = a[i + 2];
      let R = M[0][0] * r + M[0][1] * gg + M[0][2] * bb, G = M[1][0] * r + M[1][1] * gg + M[1][2] * bb, Bv = M[2][0] * r + M[2][1] * gg + M[2][2] * bb;
      if (k !== 1) { R = (R - 128) * k + 128; G = (G - 128) * k + 128; Bv = (Bv - 128) * k + 128; }
      a[i] = R; a[i + 1] = G; a[i + 2] = Bv;
    }
    x.putImageData(d, 0, 0);
    if (g.vig > 0) {
      const gr = x.createRadialGradient(cv.width / 2, cv.height / 2, Math.min(cv.width, cv.height) * (0.55 - 0.25 * g.vig),
                                        cv.width / 2, cv.height / 2, Math.hypot(cv.width, cv.height) / 2);
      gr.addColorStop(0, "rgba(0,0,0,0)"); gr.addColorStop(1, `rgba(0,0,0,${0.35 + 0.45 * g.vig})`);
      x.fillStyle = gr; x.fillRect(0, 0, cv.width, cv.height);
    }
  }

  // ================================================================ media elements
  const pool = new Map();         // key -> <video>/<img>
  function media(key, src, kind) {
    let el = pool.get(key);
    if (el && el.dataset.src === src) return el;
    if (el) { try { el.pause(); } catch (e) {} el.remove(); }
    if (kind === "image") { el = new Image(); el.decoding = "async"; el.onload = () => requestDraw(); }
    else {
      el = document.createElement("video"); el.preload = "auto"; el.playsInline = true; el.muted = true;
      el.addEventListener("seeked", () => requestDraw()); el.addEventListener("loadeddata", () => requestDraw());
      el.style.display = "none"; document.body.appendChild(el);
    }
    el.dataset.src = src; el.src = src; pool.set(key, el);
    return el;
  }
  const ready = el => el && (el.tagName === "IMG" ? el.complete && el.naturalWidth : el.readyState >= 2);
  const dims = el => el.tagName === "IMG" ? [el.naturalWidth, el.naturalHeight] : [el.videoWidth, el.videoHeight];
  const itemEl = (key, id) => { const it = items[id]; return it ? media(key, it.src, it.kind === "video" ? "video" : "image") : null; };
  const maskEl = (key, id) => { const it = items[id]; return it && it.mask ? media("m:" + key, it.mask, "video") : null; };
  let outroImg = null;
  function loadOutro() { outroImg = new Image(); outroImg.onload = () => requestDraw(); outroImg.src = `/studio/video/${C.iid}/outro.png?text=${encodeURIComponent(P.outro.text)}&t=${Date.now()}`; }
  const music = new Audio(); music.preload = "auto"; let musicFresh = true;
  music.addEventListener("error", () => { if (music.dataset.src) say("The music file couldn't be loaded for the preview"); });

  // ================================================================ the preview canvas
  const cv = $("[data-cv]"), ctx = cv.getContext("2d");
  const screen = $("[data-screen]"), wrap = $("[data-screen-wrap]");
  let K = 1, DPR = Math.min(2, window.devicePixelRatio || 1);
  const off = () => { const c = document.createElement("canvas"); return c; };
  const offA = off(), offB = off(), tmp = off(), tmp2 = off();
  function fit() {
    const r = wrap.getBoundingClientRect();
    K = Math.max(0.05, Math.min((r.width - 24) / P.w, (r.height - 24) / P.h));
    if (Math.abs(K * P.w - parseFloat(screen.style.width || 0)) < 1 && cv.width) return;
    const cw = Math.round(P.w * K), ch = Math.round(P.h * K);
    screen.style.width = `${cw}px`; screen.style.height = `${ch}px`; screen.style.setProperty("--short", `${Math.min(P.w, P.h) * K}px`);
    cv.width = Math.round(cw * DPR); cv.height = Math.round(ch * DPR); cv.style.width = `${cw}px`; cv.style.height = `${ch}px`;
    [offA, offB].forEach(o => { o.width = cv.width; o.height = cv.height; });
    requestDraw(); placeSel();
  }
  window.addEventListener("resize", () => P && fit());
  mq.addEventListener("change", () => { M = mq.matches; if (!P) return; closeSheets(); cv.width = 0; fit(); drawTimeline(); props(); followHead(true); });
  if (window.ResizeObserver) new ResizeObserver(() => P && fit()).observe(wrap);
  const S = () => K * DPR;                 // design px -> canvas px

  function cropBox(sw, sh, cr) {
    const cw = Math.max(2, sw * (1 - cr.l - cr.r)), ch = Math.max(2, sh * (1 - cr.t - cr.b));
    return { cw, ch, cx: sw * cr.l, cy: sh * cr.t };
  }
  function mainBox(c, sw, sh) {
    const { cw, ch } = cropBox(sw, sh, c.crop);
    const s0 = (c.fit === "fill" ? Math.max : Math.min)(P.w / cw, P.h / ch), s = s0 * (c.zoom || 1);
    const fw = cw * s, fh = ch * s, cx = P.w / 2 + (c.dx || 0) * P.w, cy = P.h / 2 + (c.dy || 0) * P.h;
    return { x: cx - fw / 2, y: cy - fh / 2, w: fw, h: fh };
  }
  function cutout(src, mask, sx, sy, sw, sh, w, h) {
    // the source, with the matte's brightness as its transparency
    tmp.width = Math.max(1, Math.round(w)); tmp.height = Math.max(1, Math.round(h));
    const a = tmp.getContext("2d", { willReadFrequently: true });
    a.clearRect(0, 0, tmp.width, tmp.height); a.drawImage(src, sx, sy, sw, sh, 0, 0, tmp.width, tmp.height);
    tmp2.width = tmp.width; tmp2.height = tmp.height;
    const b = tmp2.getContext("2d", { willReadFrequently: true });
    const [mw, mh] = dims(mask), [vw, vh] = dims(src);
    b.drawImage(mask, sx * mw / vw, sy * mh / vh, sw * mw / vw, sh * mh / vh, 0, 0, tmp.width, tmp.height);
    try {
      const d = a.getImageData(0, 0, tmp.width, tmp.height), m = b.getImageData(0, 0, tmp.width, tmp.height);
      for (let i = 3; i < d.data.length; i += 4) d.data[i] = m.data[i - 3];
      a.putImageData(d, 0, 0);
    } catch (e) {}
    return tmp;
  }
  function drawMain(seg, o, tl) {
    const x = o.getContext("2d", { willReadFrequently: true });
    x.setTransform(1, 0, 0, 1, 0, 0); x.fillStyle = "#000"; x.fillRect(0, 0, o.width, o.height);
    x.setTransform(S(), 0, 0, S(), 0, 0);
    if (seg.kind === "outro") { if (outroImg && outroImg.complete) x.drawImage(outroImg, 0, 0, P.w, P.h); return; }
    const c = seg.c, el = itemEl(c.id, c.item);
    if (!ready(el)) return;
    const [sw, sh] = dims(el), cb = cropBox(sw, sh, c.crop), box = mainBox(c, sw, sh);
    const mode = c.bg.mode;
    if (mode === "color") { x.fillStyle = c.bg.color; x.fillRect(0, 0, P.w, P.h); }
    else if (mode === "photo" && c.bg.item && ready(itemEl("bg:" + c.id, c.bg.item))) {
      const im = itemEl("bg:" + c.id, c.bg.item), [iw, ih] = dims(im), s = Math.max(P.w / iw, P.h / ih);
      x.drawImage(im, (P.w - iw * s) / 2, (P.h - ih * s) / 2, iw * s, ih * s);
    } else if (mode === "blur") {
      const s = Math.max(P.w / cb.cw, P.h / cb.ch);
      tmp2.width = 24; tmp2.height = Math.max(1, Math.round(24 * P.h / P.w));
      const b = tmp2.getContext("2d"); b.imageSmoothingQuality = "low";
      b.drawImage(el, cb.cx + (cb.cw - P.w / s) / 2, cb.cy + (cb.ch - P.h / s) / 2, P.w / s, P.h / s, 0, 0, tmp2.width, tmp2.height);
      x.imageSmoothingQuality = "high"; x.drawImage(tmp2, 0, 0, P.w, P.h);
      x.fillStyle = "rgba(0,0,0,0.12)"; x.fillRect(0, 0, P.w, P.h);
    }
    const mk = c.bg.cut ? maskEl(c.id, c.item) : null;
    if (mk && ready(mk)) x.drawImage(cutout(el, mk, cb.cx, cb.cy, cb.cw, cb.ch, box.w * S(), box.h * S()), box.x, box.y, box.w, box.h);
    else x.drawImage(el, cb.cx, cb.cy, cb.cw, cb.ch, box.x, box.y, box.w, box.h);
    applyGrade(o, grade(c.filter));
  }
  function motionT(seg, tl) {
    const m = seg.c.motion || "none", L = seg.len;
    if (m === "none" || seg.kind === "outro") return null;
    const z = m === "zoomin" ? 1 + 0.15 * tl / L : m === "zoomout" ? 1.15 - 0.15 * tl / L : 1.12;
    const iw = P.w * z, ih = P.h * z;
    const px = m === "panleft" ? (iw - P.w) * (1 - tl / L) : m === "panright" ? (iw - P.w) * tl / L : (iw - P.w) / 2;
    return { z, px, py: (ih - P.h) / 2 };
  }
  function blitMain(o, seg, tl, alpha = 1) {
    ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = alpha;
    const mt = motionT(seg, tl);
    if (mt) ctx.drawImage(o, 0, 0, o.width, o.height, -mt.px * S(), -mt.py * S(), o.width * mt.z, o.height * mt.z);
    else ctx.drawImage(o, 0, 0);
    ctx.restore();
  }
  function blend(type, A, B, segA, segB, p) {
    const W = cv.width, H = cv.height, tA = t - segA.start, tB = t - segB.start;
    const draw = (o, seg, tl, fn) => { ctx.save(); fn && fn(); blitMain(o, seg, tl); ctx.restore(); };
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    if (type === "fadeblack" || type === "fadewhite") {
      ctx.fillStyle = type === "fadeblack" ? "#000" : "#fff"; ctx.fillRect(0, 0, W, H);
      if (p < 0.5) blitMain(A, segA, tA, 1 - p * 2); else blitMain(B, segB, tB, p * 2 - 1);
      return;
    }
    const clipRect = (x, y, w, h) => () => { ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip(); };
    switch (type) {
      case "wipeleft": draw(A, segA, tA); draw(B, segB, tB, clipRect(W * (1 - p), 0, W * p, H)); break;
      case "wiperight": draw(A, segA, tA); draw(B, segB, tB, clipRect(0, 0, W * p, H)); break;
      case "wipeup": draw(A, segA, tA); draw(B, segB, tB, clipRect(0, H * (1 - p), W, H * p)); break;
      case "wipedown": draw(A, segA, tA); draw(B, segB, tB, clipRect(0, 0, W, H * p)); break;
      case "slideleft": draw(A, segA, tA, () => ctx.translate(-W * p, 0)); draw(B, segB, tB, () => ctx.translate(W * (1 - p), 0)); break;
      case "slideright": draw(A, segA, tA, () => ctx.translate(W * p, 0)); draw(B, segB, tB, () => ctx.translate(-W * (1 - p), 0)); break;
      case "slideup": draw(A, segA, tA, () => ctx.translate(0, -H * p)); draw(B, segB, tB, () => ctx.translate(0, H * (1 - p))); break;
      case "slidedown": draw(A, segA, tA, () => ctx.translate(0, H * p)); draw(B, segB, tB, () => ctx.translate(0, -H * (1 - p))); break;
      case "circleopen": draw(A, segA, tA); draw(B, segB, tB, () => { ctx.beginPath(); ctx.arc(W / 2, H / 2, Math.hypot(W, H) / 2 * p, 0, 7); ctx.clip(); }); break;
      case "circleclose": draw(B, segB, tB); draw(A, segA, tA, () => { ctx.beginPath(); ctx.arc(W / 2, H / 2, Math.hypot(W, H) / 2 * (1 - p), 0, 7); ctx.clip(); }); break;
      case "zoomin": draw(A, segA, tA, () => { const z = 1 + p; ctx.translate(W / 2, H / 2); ctx.scale(z, z); ctx.translate(-W / 2, -H / 2); }); blitMain(B, segB, tB, p); break;
      default: blitMain(A, segA, tA); blitMain(B, segB, tB, p);
    }
  }

  // text: one drawing routine for the preview and for the picture sent to the renderer
  function textLines(x, L) {
    const pad = L.size * 0.3, maxw = Math.max(10, L.w - pad * 2), out = [];
    x.font = `${L.bold ? 700 : 400} ${L.size}px "${L.font}", "DejaVu Sans", sans-serif`;
    String(L.text).split("\n").forEach(par => {
      let line = "";
      par.split(/\s+/).forEach(wd => {
        const tryl = line ? line + " " + wd : wd;
        if (x.measureText(tryl).width > maxw && line) { out.push(line); line = wd; } else line = tryl;
      });
      out.push(line);
    });
    return out;
  }
  function drawText(x, L) {
    const lines = textLines(x, L), lh = L.size * 1.2, pad = L.size * 0.3;
    if (L.fill) {
      x.fillStyle = L.fill; const r = Math.min(L.w, L.h) * 0.12;
      x.beginPath(); x.roundRect ? x.roundRect(0, 0, L.w, L.h, r) : x.rect(0, 0, L.w, L.h); x.fill();
    }
    x.fillStyle = L.color; x.textBaseline = "top"; x.textAlign = L.align === "left" ? "left" : L.align === "right" ? "right" : "center";
    let y = (L.h - lh * lines.length) / 2 + (lh - L.size) / 2;
    const tx = L.align === "left" ? pad : L.align === "right" ? L.w - pad : L.w / 2;
    lines.forEach(line => { x.fillText(line, tx, y); y += lh; });
  }
  function fitTextBox(L) {
    const x = tmp.getContext("2d");
    const n = textLines(x, L).length;
    L.h = Math.round(Math.max(L.size * 1.3, n * L.size * 1.2 + L.size * 0.5));
  }
  function anim(L, tl) {
    const len = layerLen(L); let a = L.opacity, sc = 1, dx = 0, dy = 0;
    const pin = clamp(tl / ANIM_T, 0, 1);
    if (L.anim.in !== "none") a *= pin;
    if (L.anim.in === "pop") sc = Math.min(1, 0.7 + 0.3 * tl / ANIM_T);
    if (L.anim.in === "slideleft") dx = Math.max(0, 1 - tl / ANIM_T) * P.w * 0.25;
    if (L.anim.in === "slideup") dy = Math.max(0, 1 - tl / ANIM_T) * P.h * 0.12;
    if (L.anim.out === "fade") a *= clamp((len - tl) / ANIM_T, 0, 1);
    return { a, sc, dx, dy };
  }
  const lc = off();
  function drawLayer(L) {
    const tl = t - L.start;
    if (tl < 0 || tl >= layerLen(L)) return;
    lc.width = Math.max(1, Math.round(L.w * S())); lc.height = Math.max(1, Math.round(L.h * S()));
    const x = lc.getContext("2d", { willReadFrequently: true }); x.clearRect(0, 0, lc.width, lc.height);
    if (L.kind === "text") { x.setTransform(S(), 0, 0, S(), 0, 0); drawText(x, L); x.setTransform(1, 0, 0, 1, 0, 0); }
    else {
      const el = itemEl(L.id, L.item); if (!ready(el)) return;
      const [sw, sh] = dims(el), cb = cropBox(sw, sh, L.crop);
      const mk = L.cut ? maskEl(L.id, L.item) : null;
      if (mk && ready(mk)) x.drawImage(cutout(el, mk, cb.cx, cb.cy, cb.cw, cb.ch, lc.width, lc.height), 0, 0);
      else x.drawImage(el, cb.cx, cb.cy, cb.cw, cb.ch, 0, 0, lc.width, lc.height);
      const g = grade(L.filter);
      if (!g.neutral) {
        // keep the transparency while colouring
        tmp2.width = lc.width; tmp2.height = lc.height; const b = tmp2.getContext("2d", { willReadFrequently: true });
        b.clearRect(0, 0, tmp2.width, tmp2.height); b.drawImage(lc, 0, 0);
        const al = x.getImageData(0, 0, lc.width, lc.height);
        applyGrade(lc, { ...g, vig: 0 });
        const d = x.getImageData(0, 0, lc.width, lc.height);
        for (let i = 3; i < d.data.length; i += 4) d.data[i] = al.data[i];
        x.putImageData(d, 0, 0);
      }
    }
    const an = anim(L, tl);
    ctx.save(); ctx.setTransform(S(), 0, 0, S(), 0, 0);
    ctx.translate(L.x + L.w / 2 + an.dx, L.y + L.h / 2 + an.dy); ctx.rotate(L.rot * Math.PI / 180); ctx.scale(an.sc, an.sc);
    ctx.globalAlpha = an.a; ctx.drawImage(lc, -L.w / 2, -L.h / 2, L.w, L.h);
    ctx.restore();
  }
  function overlays() {
    const cap = $("[data-pcap]"), low = $("[data-plower]");
    const c = P.captions.find(x => t >= x.start && t < x.end);
    cap.hidden = !c; if (c) cap.querySelector("span").textContent = c.text;
    cap.className = `vd-cap vd-cap-${P.style.captions} vd-pos-${P.style.position}`;
    const l = P.lowers.find(x => t >= x.start && t < x.end);
    low.hidden = !l; if (l) { low.querySelector("strong").textContent = l.text; low.querySelector("span").textContent = l.sub || ""; }
  }
  let drawQueued = false;
  function requestDraw() { if (!drawQueued) { drawQueued = true; requestAnimationFrame(() => { drawQueued = false; draw(); }); } }
  function draw() {
    if (!P) return;
    const lay = layout();
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.fillStyle = "#111"; ctx.fillRect(0, 0, cv.width, cv.height);
    $("[data-pempty]").hidden = !!P.main.length;
    const act = lay.segs.filter(s => t >= s.start && t < s.end);
    if (act.length === 1 || (act.length === 2 && act[1].d === 0)) {
      const s = act[act.length - 1]; drawMain(s, offA, t - s.start); blitMain(offA, s, t - s.start);
    } else if (act.length >= 2) {
      const A = act[0], B = act[1], p = clamp((t - B.start) / B.d, 0, 1);
      drawMain(A, offA, t - A.start); drawMain(B, offB, t - B.start); blend(B.trans, offA, offB, A, B, p);
    }
    [...P.layers].sort((a, b) => a.track - b.track || a.start - b.start).forEach(drawLayer);
    overlays();
    $("[data-time]").textContent = fmt(t); $("[data-time-total]").textContent = fmt(lay.end);
    placeHead();
  }

  // ================================================================ playback (one clock; videos follow it)
  function wanted() {
    const lay = layout(), want = [];
    lay.segs.forEach(s => {
      if (s.kind !== "clip" || s.c.still || t < s.start - 0.5 || t >= s.end) return;
      const local = s.c.in + Math.max(0, t - s.start) * s.c.speed;
      let vol = s.c.volume;
      const nx = lay.segs[lay.segs.indexOf(s) + 1];
      if (s.d && t < s.start + s.d) vol *= (t - s.start) / s.d;
      if (nx && nx.d && t > nx.start) vol *= Math.max(0, 1 - (t - nx.start) / nx.d);
      want.push({ key: s.c.id, id: s.c.item, local, rate: s.c.speed, vol, on: t >= s.start });
      if (s.c.bg.cut) want.push({ key: "m:" + s.c.id, id: s.c.item, mask: true, local, rate: s.c.speed, vol: 0, on: t >= s.start });
    });
    P.layers.forEach(L => {
      if (L.kind !== "video" || t < L.start - 0.5 || t >= L.start + layerLen(L)) return;
      const local = L.in + Math.max(0, t - L.start) * L.speed;
      want.push({ key: L.id, id: L.item, local, rate: L.speed, vol: L.volume, on: t >= L.start });
      if (L.cut) want.push({ key: "m:" + L.id, id: L.item, mask: true, local, rate: L.speed, vol: 0, on: t >= L.start });
    });
    return want;
  }
  function syncMedia() {
    const want = wanted(), keys = new Set(want.map(w => w.key));
    pool.forEach((el, key) => { if (el.tagName === "VIDEO" && !keys.has(key) && !el.paused) el.pause(); });
    want.forEach(w => {
      const el = w.mask ? maskEl(w.key.slice(2), w.id) : itemEl(w.key, w.id);
      if (!el || el.tagName !== "VIDEO") return;
      el.playbackRate = w.rate; el.muted = !(w.vol > 0) || w.mask; el.volume = clamp(w.vol, 0, 1);
      const drift = Math.abs(el.currentTime - w.local);
      if (playing && w.on) { if (drift > 0.3) el.currentTime = w.local; if (el.paused) el.play().catch(() => {}); }
      else { if (!el.paused) el.pause(); if (drift > 0.04) el.currentTime = w.local; }
    });
    if (P.music && items[P.music.item]) {
      const msrc = items[P.music.item].src;
      if (music.dataset.src !== msrc) { music.src = msrc; music.dataset.src = msrc; music.loop = true; music.load(); }
      const end = total(); music.volume = clamp(P.music.volume * clamp((end - t) / 1.5, 0, 1), 0, 1);
      if (playing) {
        const d = isFinite(music.duration) && music.duration > 0 ? music.duration : 0, want = d ? t % d : t;
        if (!music.seeking && music.readyState >= 1 && (musicFresh || Math.abs(music.currentTime - want) > 0.8)) { try { music.currentTime = want; } catch (e) {} }
        musicFresh = false;
        if (music.paused) music.play().catch(err => say(err && err.name === "NotAllowedError" ? "Tap play again to hear the music" : "The music couldn't play"));
      } else if (!music.paused) music.pause();
    } else if (!music.paused) music.pause();
  }
  let clock0 = 0, t0 = 0;
  function loop() {
    if (!playing) return;
    t = t0 + (performance.now() - clock0) / 1000;
    if (t >= total()) { t = total(); stop(); }
    syncMedia(); draw(); placeSel(); followHead();
    if (playing) requestAnimationFrame(loop);
  }
  function play() {
    if (!P.main.length) return;
    if (t >= total() - 0.05) t = 0;
    playing = true; t0 = t; clock0 = performance.now(); hideTyping(); musicFresh = true;
    $("[data-play]").innerHTML = ico("pause", 18);
    syncMedia(); requestAnimationFrame(loop);
  }
  function stop() {
    playing = false; pool.forEach(el => { if (el.tagName === "VIDEO") el.pause(); }); music.pause();
    $("[data-play]").innerHTML = ico("play", 18);
    syncMedia(); requestDraw();
  }
  function seek(nt, fromScroll) {
    t = clamp(nt, 0, total()); musicFresh = true;
    if (playing) { t0 = t; clock0 = performance.now(); }
    syncMedia(); requestDraw(); placeSel(); if (!fromScroll) followHead();
  }
  $("[data-play]").addEventListener("click", () => playing ? stop() : play());

  // ================================================================ saving and undo
  let saveT, saving = Promise.resolve();
  const say = m => { $("[data-saved]").textContent = m; const sm = $("[data-saved-m]"); if (sm) sm.textContent = m; };
  function commit(opts = {}) {
    if (!C.canEdit) return;
    const snap = JSON.stringify(P);
    if (hist[hi] !== snap) { hist = hist.slice(0, hi + 1); hist.push(snap); if (hist.length > 100) hist.shift(); hi = hist.length - 1; }
    say("Unsaved changes"); clearTimeout(saveT); saveT = setTimeout(save, 1000);
    if (!opts.quiet) { drawTimeline(); props(); }
    requestDraw(); placeSel();
  }
  function save() {
    saving = saving.then(async () => {
      say("Saving…");
      try {
        const r = await fetch(`/studio/video/${C.iid}/project`, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ project: P, title: $("[data-title]").value.trim() }) });
        const j = await r.json(); if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
        say(`Saved ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`);
      } catch (e) { say(`Not saved: ${e.message}`); }
    });
    return saving;
  }
  function undo(dir) {
    const j = hi + dir; if (j < 0 || j >= hist.length) return;
    hi = j; P = JSON.parse(hist[hi]); sel = null; say("Unsaved changes"); clearTimeout(saveT); saveT = setTimeout(save, 1000);
    drawTimeline(); props(); requestDraw(); placeSel();
  }
  $("[data-title]").addEventListener("change", () => save());
  const ub = $("[data-undo]"), rb_ = $("[data-redo]");
  if (ub) ub.addEventListener("click", () => undo(-1));
  if (rb_) rb_.addEventListener("click", () => undo(1));

  // ================================================================ adding things
  const defFilter = () => ({ preset: "none", brightness: 0, contrast: 0, saturation: 0, warmth: 0, vignette: 0 });
  const noCrop = () => ({ l: 0, t: 0, r: 0, b: 0 });
  function newMain(it) {
    const base = { id: uid(), item: it.id, fit: it.kind === "video" ? "fill" : "fit", crop: noCrop(), zoom: 1, dx: 0, dy: 0, filter: defFilter(),
                   motion: it.kind === "video" ? "none" : "zoomin", bg: { mode: "blur", cut: false, color: C.primary, item: null }, trans: { type: "none", dur: 0.5 } };
    return it.kind === "video" ? { ...base, in: 0, out: +Math.min(it.duration || 5, 60).toFixed(2), speed: 1, volume: 1 }
                               : { ...base, in: 0, out: 3, speed: 1, volume: 0, still: true };
  }
  function newLayer(it, start, track) {
    const ar = (it.w && it.h) ? it.w / it.h : 16 / 9, w = Math.min(P.w * 0.45, P.h * 0.45 * ar), h = w / ar;
    const L = { id: uid(), kind: it.kind === "video" ? "video" : "image", item: it.id, track: track || nextTrack(start), start: +start.toFixed(2),
                x: P.w - w - P.w * 0.06, y: P.h * 0.08, w, h, rot: 0, crop: noCrop(), opacity: 1, filter: defFilter(), anim: { in: "fade", out: "fade" }, cut: false };
    if (L.kind === "video") Object.assign(L, { in: 0, out: +Math.min(it.duration || 4, Math.max(1, total() - start), 60).toFixed(2), speed: 1, volume: 0 });
    else L.dur = +Math.min(3, Math.max(1, total() - start)).toFixed(2);
    return L;
  }
  function nextTrack(start, len = 3) {
    for (let tr = 1; tr < 12; tr++) {
      if (!P.layers.some(L => L.track === tr && L.start < start + len && start < L.start + layerLen(L))) return tr;
    }
    return 12;
  }
  async function ensureItem(id) {
    if (items[id]) return items[id];
    const r = await fetch(`/studio/video-info/${id}`); const info = await r.json();
    if (!r.ok) throw new Error(info.error || "That file can't be used.");
    const lib = mediaIndex[id] || {};
    items[id] = { ...lib, id, title: info.title, kind: info.kind, duration: info.duration, w: info.w, h: info.h,
                  src: lib.src || `/studio/media/${id}`, thumb: lib.thumb || `/studio/t/${id}` };
    return items[id];
  }
  async function addMain(id, at) {
    try {
      const it = await ensureItem(id), c = newMain(it);
      if (at === undefined || at >= P.main.length) P.main.push(c); else P.main.splice(at, 0, c);
      sel = { kind: "main", id: c.id }; if (M) closeSheets(); commit(); seek(layout().segs.find(s => s.c.id === c.id).start + 0.01);
    } catch (e) { say(e.message); }
  }
  async function addLayer(id, start, track) {
    try {
      const it = await ensureItem(id), L = newLayer(it, start ?? t, track);
      P.layers.push(L); sel = { kind: "layer", id: L.id }; if (M) closeSheets(); commit(); seek(L.start + 0.01);
    } catch (e) { say(e.message); }
  }
  const TEXT_PRESETS = {
    title: { text: "Your title", size: 96, bold: true, color: "#FFFFFF", fill: "", font: "Montserrat", y: 0.2, anim: "pop" },
    subtitle: { text: "A line of detail", size: 54, bold: false, color: "#FFFFFF", fill: "", font: "Public Sans", y: 0.3, anim: "fade" },
    cta: { text: "Book a free consult", size: 60, bold: true, color: "#FFFFFF", fill: C.accent, font: "Montserrat", y: 0.72, anim: "pop" },
    plain: { text: "Text", size: 64, bold: false, color: "#FFFFFF", fill: "", font: "DejaVu Sans", y: 0.45, anim: "none" },
  };
  function addText(kind) {
    const start = t, len = Math.max(1, Math.min(4, total() - t || 4));
    const mk = (pr, y, w) => {
      const L = { id: uid(), kind: "text", track: nextTrack(start, len), start: +start.toFixed(2), dur: +len.toFixed(2), text: pr.text, font: pr.font,
                  size: pr.size, color: pr.color, fill: pr.fill, bold: pr.bold, align: "center", x: (P.w - w) / 2, y: P.h * y, w, h: 100,
                  rot: 0, crop: noCrop(), opacity: 1, filter: defFilter(), anim: { in: pr.anim, out: "fade" } };
      fitTextBox(L); return L;
    };
    if (kind === "name") {
      const a = mk({ text: "Dr. Name", size: 58, bold: true, color: "#FFFFFF", fill: C.accent, font: "Montserrat", anim: "slideleft" }, 0.58, P.w * 0.5);
      a.align = "left"; a.x = P.w * 0.07; fitTextBox(a); P.layers.push(a);
      const b = mk({ text: "Orthodontist", size: 38, bold: false, color: "#FFFFFF", fill: C.primary, font: "Public Sans", anim: "slideleft" }, 0.58, P.w * 0.38);
      b.align = "left"; b.x = P.w * 0.07; b.y = a.y + a.h + 8; b.track = nextTrack(start, len); fitTextBox(b); P.layers.push(b);
      sel = { kind: "layer", id: a.id };
    } else {
      const pr = TEXT_PRESETS[kind], L = mk(pr, pr.y, kind === "cta" ? P.w * 0.62 : P.w * 0.84);
      P.layers.push(L); sel = { kind: "layer", id: L.id };
    }
    if (M) closeSheets(); commit(); requestDraw();
  }
  $$("[data-addtext]").forEach(b => b.addEventListener("click", () => { if (!P.main.length) return say("Add a clip to the main track first."); addText(b.dataset.addtext); }));

  // ================================================================ selection on the video: move, resize, rotate, crop
  const selbox = $("[data-selbox]");
  const curLayer = () => sel && sel.kind === "layer" ? P.layers.find(L => L.id === sel.id) : null;
  const curMain = () => sel && sel.kind === "main" ? P.main.find(c => c.id === sel.id) : null;
  function selGeom() {
    const L = curLayer();
    if (L) return { x: L.x, y: L.y, w: L.w, h: L.h, rot: L.rot, kind: "layer" };
    const c = curMain();
    if (c) {
      const el = pool.get(c.id); if (!ready(el)) return null;
      const [sw, sh] = dims(el), b = mainBox(c, sw, sh); return { ...b, rot: 0, kind: "main" };
    }
    return null;
  }
  function placeSel() {
    const g = selGeom();
    const visible = g && (() => {
      if (g.kind === "layer") { const L = curLayer(); return t >= L.start && t < L.start + layerLen(L); }
      const s = layout().segs.find(s_ => s_.kind === "clip" && s_.c.id === sel.id); return s && t >= s.start && t < s.end;
    })();
    selbox.hidden = !visible || playing || !C.canEdit;
    if (selbox.hidden) return;
    selbox.style.left = `${(g.x) * K}px`; selbox.style.top = `${g.y * K}px`; selbox.style.width = `${g.w * K}px`; selbox.style.height = `${g.h * K}px`;
    selbox.style.transform = `rotate(${g.rot}deg)`;
    selbox.classList.toggle("vd-cropping", cropMode); selbox.classList.toggle("vd-main", g.kind === "main");
    selbox.classList.toggle("vd-small", !cropMode && (g.w * K < 120 || g.h * K < 70));
    selbox.querySelector("[data-hd=rot]").hidden = g.kind !== "layer" || cropMode;
  }
  function hitLayer(px, py) {
    const now = [...P.layers].filter(L => t >= L.start && t < L.start + layerLen(L)).sort((a, b) => b.track - a.track);
    for (const L of now) {
      const cx = L.x + L.w / 2, cy = L.y + L.h / 2, a = -L.rot * Math.PI / 180;
      const dx = px - cx, dy = py - cy, rx = dx * Math.cos(a) - dy * Math.sin(a), ry = dx * Math.sin(a) + dy * Math.cos(a);
      if (Math.abs(rx) <= L.w / 2 && Math.abs(ry) <= L.h / 2) return L;
    }
    return null;
  }
  const toDesign = e => { const r = screen.getBoundingClientRect(); return [(e.clientX - r.left) / K, (e.clientY - r.top) / K]; };
  screen.addEventListener("pointerdown", e => {
    if (!C.canEdit || e.target.closest("[data-hd]") || e.target === $("[data-typing]")) return;
    const [px, py] = toDesign(e);
    const L = hitLayer(px, py);
    if (L) sel = { kind: "layer", id: L.id };
    else { const s = layout().segs.find(s_ => s_.kind === "clip" && t >= s_.start && t < s_.end); sel = s ? { kind: "main", id: s.c.id } : null; }
    if (!sel || cropMode) { cropMode = false; }
    drawTimeline(); props(); placeSel();
    if (sel) dragSel(e, "move");
  });
  screen.addEventListener("dblclick", e => { const L = curLayer(); if (L && L.kind === "text") showTyping(L); });
  selbox.addEventListener("pointerdown", e => { const h = e.target.closest("[data-hd]"); if (h) { e.stopPropagation(); dragSel(e, h.dataset.hd); } });
  function dragSel(e, how) {
    e.preventDefault();
    const L = curLayer(), c = curMain(); if (!L && !c) return;
    const [x0, y0] = toDesign(e), orig = JSON.parse(JSON.stringify(L || c));
    const el = c ? pool.get(c.id) : (L.kind !== "text" ? pool.get(L.id) : null);
    const [sw, sh] = el && ready(el) ? dims(el) : [1, 1];
    const box0 = c ? mainBox(orig, sw, sh) : null;
    let moved = false;
    const mv = ev => {
      if (pinch || ev.pointerId !== e.pointerId) return;
      const [x, y] = toDesign(ev), dx = x - x0, dy = y - y0; moved = moved || Math.abs(dx) + Math.abs(dy) > 1;
      if (cropMode && how !== "move") return cropDrag(how, dx, dy, orig, L, c, sw, sh);
      if (L) {
        if (how === "move") { L.x = snapX(orig.x + dx, L.w); L.y = snapY(orig.y + dy, L.h); }
        else if (how === "rot") {
          const cx = orig.x + orig.w / 2, cy = orig.y + orig.h / 2; let a = Math.atan2(y - cy, x - cx) * 180 / Math.PI + 90;
          if (!ev.shiftKey) { const r = Math.round(a / 15) * 15; if (Math.abs(r - a) < 4) a = r; } L.rot = +a.toFixed(1);
        } else resizeLayer(L, orig, how, dx, dy, ev.shiftKey || L.kind === "text");
      } else if (c) {
        if (how === "move") { c.dx = clamp(orig.dx + dx / P.w, -1, 1); c.dy = clamp(orig.dy + dy / P.h, -1, 1);
          const cxp = P.w / 2 + c.dx * P.w; if (Math.abs(cxp - P.w / 2) < 8 / K) c.dx = 0; const cyp = P.h / 2 + c.dy * P.h; if (Math.abs(cyp - P.h / 2) < 8 / K) c.dy = 0; }
        else { const cx = box0.x + box0.w / 2, cy = box0.y + box0.h / 2;
          const d0 = Math.hypot(x0 - cx, y0 - cy), d1 = Math.hypot(x - cx, y - cy); c.zoom = clamp(orig.zoom * d1 / Math.max(1, d0), 0.3, 5); }
      }
      requestDraw(); placeSel();
    };
    const up = () => { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up); if (moved) commit(); };
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  }
  const scrPts = new Map(); let pinch = null;
  screen.addEventListener("pointerdown", e => {
    scrPts.set(e.pointerId, [e.clientX, e.clientY]);
    if (scrPts.size === 2 && C.canEdit && (curLayer() || curMain())) {
      const [a, b] = [...scrPts.values()];
      pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1, ang: Math.atan2(b[1] - a[1], b[0] - a[0]), o: JSON.parse(JSON.stringify(curLayer() || curMain())) };
    }
  }, true);
  window.addEventListener("pointermove", e => {
    if (!scrPts.has(e.pointerId)) return; scrPts.set(e.pointerId, [e.clientX, e.clientY]);
    if (!pinch || scrPts.size < 2) return;
    const [a, b] = [...scrPts.values()], k = (Math.hypot(a[0] - b[0], a[1] - b[1]) || 1) / pinch.d, o = pinch.o;
    const L = curLayer(), c = curMain();
    if (L) {
      const cx = o.x + o.w / 2, cy = o.y + o.h / 2; L.w = Math.max(20, o.w * k); L.h = Math.max(20, o.h * k); L.x = cx - L.w / 2; L.y = cy - L.h / 2;
      if (L.kind === "text") L.size = Math.max(10, Math.round(o.size * k));
      L.rot = +(o.rot + (Math.atan2(b[1] - a[1], b[0] - a[0]) - pinch.ang) * 180 / Math.PI).toFixed(1);
    } else if (c) c.zoom = clamp(o.zoom * k, 0.3, 5);
    requestDraw(); placeSel();
  });
  const scrUp = e => { if (!scrPts.delete(e.pointerId)) return; if (pinch && scrPts.size < 2) { pinch = null; commit(); } };
  window.addEventListener("pointerup", scrUp); window.addEventListener("pointercancel", scrUp);
  function tapEmpty(e) {
    const x0 = e.clientX, y0 = e.clientY;
    const up = ev => { window.removeEventListener("pointerup", up);
      if (Math.abs(ev.clientX - x0) + Math.abs(ev.clientY - y0) < 10 && sel) { sel = null; cropMode = false; drawTimeline(); props(); placeSel(); } };
    window.addEventListener("pointerup", up);
  }
  function snapX(x, w) { const c = x + w / 2; return Math.abs(c - P.w / 2) < 8 / K ? P.w / 2 - w / 2 : x; }
  function snapY(y, h) { const c = y + h / 2; return Math.abs(c - P.h / 2) < 8 / K ? P.h / 2 - h / 2 : y; }
  function resizeLayer(L, o, how, dx, dy, free) {
    const a = o.rot * Math.PI / 180, cos = Math.cos(a), sin = Math.sin(a);
    const lx = dx * cos + dy * sin, ly = -dx * sin + dy * cos;          // the drag, in the layer's own directions
    let w = o.w, h = o.h;
    if (how.includes("e")) w = o.w + lx; if (how.includes("w")) w = o.w - lx;
    if (how.includes("s")) h = o.h + ly; if (how.includes("n")) h = o.h - ly;
    if (!free && how.length === 2) { const k = Math.max(w / o.w, h / o.h); w = o.w * k; h = o.h * k; }
    w = Math.max(20, w); h = Math.max(20, h);
    if (L.kind === "text" && (how === "e" || how === "w")) { L.w = w; fitTextBox(L); h = L.h; }
    // keep the opposite edge where it was
    const ax = how.includes("w") ? 1 : how.includes("e") ? -1 : 0, ay = how.includes("n") ? 1 : how.includes("s") ? -1 : 0;
    const ocx = o.x + o.w / 2, ocy = o.y + o.h / 2;
    const ddx = (o.w - w) / 2 * ax, ddy = (o.h - h) / 2 * ay;
    const ncx = ocx + ddx * cos - ddy * sin, ncy = ocy + ddx * sin + ddy * cos;
    L.w = w; L.h = h; L.x = ncx - w / 2; L.y = ncy - h / 2;
    if (L.kind === "text" && how.length === 2) { L.size = Math.max(10, Math.round(o.size * w / o.w)); }
  }
  function cropDrag(how, dx, dy, o, L, c, sw, sh) {
    const target = L || c, cr = { ...o.crop };
    const box = L ? { w: o.w, h: o.h } : mainBox(o, sw, sh);
    const per = box.w / (sw * (1 - o.crop.l - o.crop.r)), perY = box.h / (sh * (1 - o.crop.t - o.crop.b));
    if (how.includes("w")) cr.l = clamp(o.crop.l + dx / per / sw, 0, 0.9 - cr.r);
    if (how.includes("e")) cr.r = clamp(o.crop.r - dx / per / sw, 0, 0.9 - cr.l);
    if (how.includes("n")) cr.t = clamp(o.crop.t + dy / perY / sh, 0, 0.9 - cr.b);
    if (how.includes("s")) cr.b = clamp(o.crop.b - dy / perY / sh, 0, 0.9 - cr.t);
    target.crop = cr;
    if (L) {                                   // the picture stays put; the frame shrinks around it
      L.x = o.x + (cr.l - o.crop.l) * sw * per; L.w = o.w - ((cr.l - o.crop.l) + (cr.r - o.crop.r)) * sw * per;
      L.y = o.y + (cr.t - o.crop.t) * sh * perY; L.h = o.h - ((cr.t - o.crop.t) + (cr.b - o.crop.b)) * sh * perY;
    }
    requestDraw(); placeSel();
  }
  const cropBtn = $("[data-crop-btn]");
  if (cropBtn) cropBtn.addEventListener("click", () => {
    const L = curLayer(), c = curMain();
    if (!(L && L.kind !== "text") && !c) return say("Select a clip or picture on the video to crop it.");
    cropMode = !cropMode; cropBtn.classList.toggle("on", cropMode); placeSel();
    say(cropMode ? "Crop: drag the edges, then click Crop again (or anywhere) to finish" : "");
  });
  // typing straight onto the video
  const typing = $("[data-typing]");
  function showTyping(L) {
    typing.hidden = false; typing.value = L.text;
    Object.assign(typing.style, { left: `${L.x * K}px`, top: `${L.y * K}px`, width: `${L.w * K}px`, height: `${L.h * K}px`,
      font: `${L.bold ? 700 : 400} ${L.size * K}px "${L.font}", sans-serif`, color: L.color, textAlign: L.align, transform: `rotate(${L.rot}deg)` });
    typing.focus(); typing.select();
    typing.oninput = () => { L.text = typing.value; fitTextBox(L); typing.style.height = `${L.h * K}px`; requestDraw(); placeSel(); };
    typing.onblur = () => { hideTyping(); commit(); };
  }
  function hideTyping() { typing.hidden = true; }

  // ================================================================ timeline
  const tl = $("[data-timeline]"), rowsEl = $("[data-rows]"), inner = $("[data-tl-inner]"), scrollEl = $("[data-tl-scroll]"), labelsEl = $("[data-tl-labels]");
  let PPS = 60, OFF = 0;                       // OFF: on phones the timeline starts half a screen in, under the centre playhead
  const X = sec => OFF + sec * PPS;
  const zoomIn = $("[data-tlzoom]");
  const setZoom = v => { PPS = clamp(v, 15, 220); zoomIn.value = PPS; drawTimeline(); followHead(true); };
  zoomIn.addEventListener("input", () => setZoom(+zoomIn.value));
  const zb = (q, f) => { const b = $(q); if (b) b.addEventListener("click", f); };
  zb("[data-zoom-in]", () => setZoom(PPS * 1.35)); zb("[data-zoom-out]", () => setZoom(PPS / 1.35));
  zb("[data-zoom-fit]", () => setZoom((scrollEl.clientWidth - 30 - OFF * 2) / Math.max(5, total() + 1)));
  // phones: the playhead stays in the middle and the timeline scrolls under it
  let scrollSet = -1;
  function followHead(force) {
    if (!M) return;
    const want = Math.round(t * PPS);
    if (force || Math.abs(scrollEl.scrollLeft - want) > 0.5) { scrollSet = want; scrollEl.scrollLeft = want; }
  }
  scrollEl.addEventListener("scroll", () => {
    if (!M) return;
    if (Math.abs(scrollEl.scrollLeft - scrollSet) <= 1) return;
    scrollSet = -1; if (playing) stop();
    seek(scrollEl.scrollLeft / PPS, true);
  }, { passive: true });
  // pinch the timeline to zoom (phones and trackpads)
  const tlPts = new Map(); let tlPinch = null;
  scrollEl.addEventListener("pointerdown", e => { if (e.pointerType === "touch") tlPts.set(e.pointerId, e.clientX); });
  scrollEl.addEventListener("pointermove", e => {
    if (!tlPts.has(e.pointerId)) return; tlPts.set(e.pointerId, e.clientX);
    if (tlPts.size === 2) { const [a, b] = [...tlPts.values()], d = Math.abs(a - b) || 1; if (!tlPinch) tlPinch = { d, pps: PPS }; else setZoom(tlPinch.pps * d / tlPinch.d); }
  });
  const tlUp = e => { tlPts.delete(e.pointerId); if (tlPts.size < 2) tlPinch = null; };
  scrollEl.addEventListener("pointerup", tlUp); scrollEl.addEventListener("pointercancel", tlUp);
  scrollEl.addEventListener("wheel", e => { if (e.ctrlKey) { e.preventDefault(); setZoom(PPS * (e.deltaY < 0 ? 1.12 : 1 / 1.12)); } }, { passive: false });
  function rowsSpec() {
    const maxT = Math.max(0, ...P.layers.map(L => L.track));
    const rows = [{ kind: "new", track: maxT + 1, label: "New layer", icon: "plus" }];
    for (let tr = maxT; tr >= 1; tr--) {
      const kinds = new Set(P.layers.filter(L => L.track === tr).map(L => L.kind));
      rows.push({ kind: "layer", track: tr, label: `Layer ${tr}`, icon: kinds.size === 1 && kinds.has("text") ? "text" : "stock" });
    }
    rows.push({ kind: "main", label: "Main", icon: mainMuted() ? "mute" : "volume" }, { kind: "captions", label: "Captions", icon: "captions" },
              { kind: "music", label: "Music", icon: "audio" });
    return rows;
  }
  const mainMuted = () => P.main.length > 0 && P.main.every(c => c.still || !c.volume);
  const savedVol = {};
  function toggleMainMute() {
    const mute = !mainMuted();
    P.main.forEach(c => { if (c.still) return; if (mute) { savedVol[c.id] = c.volume || 1; c.volume = 0; } else c.volume = savedVol[c.id] || 1; });
    commit(); say(mute ? "Clip sound off" : "Clip sound on");
  }
  function blockLabel(L) { return L.kind === "text" ? `T  ${L.text.split("\n")[0]}` : (items[L.item] || {}).title || L.kind; }
  function drawTimeline() {
    if (!P) return;
    OFF = M ? Math.round(scrollEl.clientWidth / 2) : 0;
    const lay = layout(), end = lay.end, width = M ? OFF * 2 + end * PPS : Math.max(scrollEl.clientWidth - 4, (end + 4) * PPS);
    inner.style.width = `${width}px`;
    const ruler = $("[data-ruler]"); ruler.innerHTML = "";
    const step = PPS > 90 ? 1 : PPS > 40 ? 2 : PPS > 20 ? 5 : 10;
    for (let s = 0; X(s) < width - (M ? OFF : 0) + 1; s += step) { const m = document.createElement("span"); m.style.left = `${X(s)}px`; m.textContent = fmt(s).replace(/\.0$/, ""); ruler.appendChild(m); }
    rowsEl.innerHTML = ""; labelsEl.innerHTML = `<div class="vd-lab-ruler"></div>`;
    rowsSpec().forEach(r => {
      const row = document.createElement("div"); row.className = `vd-row vd-row-${r.kind}`; row.dataset.kind = r.kind; if (r.track) row.dataset.track = r.track;
      const lab = document.createElement(r.kind === "main" && C.canEdit ? "button" : "div"); lab.className = `vd-lab vd-lab-${r.kind}`;
      lab.innerHTML = `${ico(r.icon, 16)}<span>${esc(r.label)}</span>`;
      if (r.kind === "main" && C.canEdit) { lab.type = "button"; lab.title = mainMuted() ? "Turn the clips' sound on" : "Turn the clips' sound off"; lab.addEventListener("click", toggleMainMute); }
      labelsEl.appendChild(lab);
      if (r.kind === "main") {
        lay.segs.forEach((s, i) => {
          const b = block(row, s.start, s.end, s.kind === "outro" ? "End card" : ((items[s.c.item] || {}).title || "Clip"),
            s.kind === "outro" ? "vd-b-outro" : (s.c.still ? "vd-b-still" : "vd-b-clip"), s.kind === "clip" && sel && sel.kind === "main" && sel.id === s.c.id);
          if (s.kind === "clip") { b.style.backgroundImage = `url(${(items[s.c.item] || {}).thumb || ""})`; b.dataset.main = s.c.id; addHandles(b); }
          else b.dataset.outro = "1";
          if (i > 0) {
            const tr = document.createElement("button"); tr.type = "button"; tr.className = `vd-tr${s.d ? " on" : ""}`;
            tr.style.left = `${X(s.start + s.d / 2)}px`; tr.title = s.d ? `${TRANS[s.trans]} (${s.d.toFixed(1)} s)` : "Add a transition";
            tr.innerHTML = s.d ? "⧓" : "+"; tr.dataset.trans = s.kind === "outro" ? "outro" : s.c.id;
            if (sel && sel.kind === "trans" && sel.id === tr.dataset.trans) tr.classList.add("sel");
            row.appendChild(tr);
          }
        });
      } else if (r.kind === "layer") {
        P.layers.filter(L => L.track === r.track).forEach(L => {
          const b = block(row, L.start, L.start + layerLen(L), blockLabel(L), `vd-b-${L.kind}`, sel && sel.kind === "layer" && sel.id === L.id);
          if (L.kind !== "text") b.style.backgroundImage = `url(${(items[L.item] || {}).thumb || ""})`;
          b.dataset.layer = L.id; addHandles(b);
        });
      } else if (r.kind === "captions") {
        P.captions.forEach((c, i) => { const b = block(row, c.start, c.end, c.text, "vd-b-cap", sel && sel.kind === "cap" && sel.i === i); b.dataset.cap = i; });
        P.lowers.forEach((c, i) => { const b = block(row, c.start, c.end, c.text, "vd-b-lower", sel && sel.kind === "lower" && sel.i === i); b.dataset.lower = i; });
      } else if (r.kind === "music" && P.music && items[P.music.item]) {
        const b = block(row, 0, end, `♪ ${items[P.music.item].title}`, "vd-b-music", sel && sel.kind === "music"); b.dataset.music = 1;
      } else if (r.kind === "new") row.innerHTML = `<span class="vd-new-hint" style="left:${OFF + 8}px">${M ? "" : "Drop here for a new layer"}</span>`;
      if ((r.kind === "music" && !(P.music && items[P.music.item])) || (r.kind === "captions" && !P.captions.length && !P.lowers.length)) {
        const add = document.createElement("button"); add.type = "button"; add.className = "vd-addrow"; add.style.left = `${X(0)}px`;
        add.dataset.open = r.kind === "music" ? "audio" : "captions";
        add.innerHTML = `${ico("plus", 14)} ${r.kind === "music" ? "Add music" : "Add captions"}`; row.appendChild(add);
      }
      rowsEl.appendChild(row);
    });
    const tot = end;
    const rt = (C.ratios || []).find(x => x[2] === P.w && x[3] === P.h);
    $("[data-size]").textContent = M ? (rt ? { story: "9:16", square: "1:1", portrait: "4:5", wide: "16:9" }[rt[0]] : "Ratio") : `${rt ? rt[1] : "Ratio"} · ${P.w}×${P.h}`;
    $("[data-limit]").textContent = tot > 90 ? `${Math.round(tot)} s: Reels longer than 90 s may be cut short` : tot > 60 && P.h > P.w ? `${Math.round(tot)} s (Stories show 60 s at a time)` : "";
    placeHead(); capList();
  }
  function block(row, a, b, label, cls, on) {
    const d = document.createElement("div");
    d.className = `vd-block ${cls}${on ? " on" : ""}`; d.style.left = `${X(a)}px`; d.style.width = `${Math.max(6, (b - a) * PPS - 2)}px`;
    d.innerHTML = `<span>${esc(label)}</span>`; row.appendChild(d); return d;
  }
  function addHandles(b) { if (!C.canEdit) return; ["l", "r"].forEach(s => { const h = document.createElement("span"); h.className = `vd-handle vd-h${s}`; h.dataset.edge = s; b.appendChild(h); }); }
  function placeHead() { const h = $("[data-head]"); if (h) h.style.left = `${X(t)}px`; }
  const timeAt = ev => { const r = inner.getBoundingClientRect(); return Math.max(0, (ev.clientX - r.left - OFF) / PPS); };
  function snapT(v, skip) {
    const lay = layout(), cand = [0, t, lay.end, ...lay.segs.flatMap(s => [s.start, s.end])];
    P.layers.forEach(L => { if (L.id !== skip) cand.push(L.start, L.start + layerLen(L)); });
    let best = v, bd = 8 / PPS;
    cand.forEach(c => { if (Math.abs(c - v) < bd) { bd = Math.abs(c - v); best = c; } });
    return best;
  }
  rowsEl.addEventListener("click", e => { const a = e.target.closest("[data-open]"); if (a) { e.stopPropagation(); openTab(a.dataset.open); } });
  rowsEl.addEventListener("pointerdown", e => {
    if (e.target.closest("[data-open]")) return;
    const tr = e.target.closest(".vd-tr");
    if (tr) { sel = { kind: "trans", id: tr.dataset.trans }; drawTimeline(); props(); openTab("transitions"); return; }
    const b = e.target.closest(".vd-block");
    const touch = e.pointerType === "touch";
    if (!b) {
      if (touch) { tapEmpty(e); return; }          // on phones a drag on empty timeline scrolls; a tap clears the selection
      stop(); seek(timeAt(e)); sel = null; drawTimeline(); props(); placeSel(); return;
    }
    // on phones, the first tap selects; once selected, a block can be dragged and its ends trimmed
    if (touch && !b.classList.contains("on") && (b.dataset.main || b.dataset.layer)) {
      const x0 = e.clientX, y0 = e.clientY;
      const up = ev => { window.removeEventListener("pointerup", up);
        if (Math.abs(ev.clientX - x0) + Math.abs(ev.clientY - y0) < 10) {
          sel = b.dataset.main ? { kind: "main", id: b.dataset.main } : { kind: "layer", id: b.dataset.layer };
          drawTimeline(); props(); placeSel();
        } };
      window.addEventListener("pointerup", up); return;
    }
    if (b.dataset.cap !== undefined) { sel = { kind: "cap", i: +b.dataset.cap }; seek(P.captions[sel.i].start); drawTimeline(); props(); return; }
    if (b.dataset.lower !== undefined) { sel = { kind: "lower", i: +b.dataset.lower }; seek(P.lowers[sel.i].start); drawTimeline(); props(); return; }
    if (b.dataset.music) { sel = { kind: "music" }; drawTimeline(); props(); return; }
    if (b.dataset.outro) { sel = { kind: "outro" }; drawTimeline(); props(); return; }
    if (!C.canEdit) return;
    const edge = e.target.dataset.edge;
    if (b.dataset.main) dragMain(e, b, b.dataset.main, edge);
    else if (b.dataset.layer) dragLayer(e, b.dataset.layer, edge);
  });
  function dragMain(e, b, id, edge) {
    e.preventDefault();
    const c = P.main.find(x => x.id === id), o = { ...c }, x0 = e.clientX, info = items[c.item] || {};
    sel = { kind: "main", id }; props();
    let moved = false, ghost = null, insertAt = null;
    const seg0 = layout().segs.find(s => s.c.id === id);
    const mv = ev => {
      const d = (ev.clientX - x0) / PPS; moved = moved || Math.abs(ev.clientX - x0) > 3;
      if (!moved) return;
      if (edge === "l") { if (c.still) c.out = clamp(o.out - d, 0.5, 30); else c.in = clamp(o.in + d * c.speed, 0, o.out - 0.3); }
      else if (edge === "r") { if (c.still) c.out = clamp(o.out + d, 0.5, 30); else c.out = clamp(o.out + d * c.speed, o.in + 0.3, info.duration || 9999); }
      else {                               // drag along the main track to change the order
        const at = timeAt(ev), segs = layout().segs.filter(s => s.kind === "clip");
        insertAt = segs.filter(s => s.c.id !== id && (s.start + s.end) / 2 < at).length;
        if (!ghost) { ghost = document.createElement("div"); ghost.className = "vd-ghost"; b.parentElement.appendChild(ghost); b.classList.add("vd-dragging"); }
        const others = segs.filter(s => s.c.id !== id); const gx = insertAt < others.length ? others[insertAt].start : (others.length ? others[others.length - 1].end : 0);
        ghost.style.left = `${X(gx) - 2}px`;
        b.style.transform = `translateX(${ev.clientX - x0}px)`;
        return;
      }
      drawTimeline(); requestDraw();
      seek(edge === "l" ? seg0.start : Math.min(seg0.start + clipLen(c) - 0.04, total()));
    };
    const up = () => {
      window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up);
      if (ghost && insertAt !== null) { const i = P.main.indexOf(c); P.main.splice(i, 1); P.main.splice(insertAt, 0, c); }
      if (moved) commit(); else { seek(seg0.start + 0.01); drawTimeline(); placeSel(); }
    };
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  }
  function dragLayer(e, id, edge) {
    e.preventDefault();
    const L = P.layers.find(x => x.id === id), o = { ...L }, x0 = e.clientX, info = items[L.item] || {};
    sel = { kind: "layer", id }; props();
    let moved = false;
    const mv = ev => {
      const d = (ev.clientX - x0) / PPS; moved = moved || Math.abs(ev.clientX - x0) + Math.abs(ev.clientY - e.clientY) > 3;
      if (!moved) return;
      if (edge === "l") {
        const ns = snapT(clamp(o.start + d, 0, o.start + layerLen(o) - 0.3), id), dd = ns - o.start;
        L.start = +ns.toFixed(3);
        if (L.kind === "video") L.in = clamp(o.in + dd * L.speed, 0, o.out - 0.3); else L.dur = Math.max(0.3, o.dur - dd);
      } else if (edge === "r") {
        const ne = snapT(o.start + layerLen(o) + d, id);
        if (L.kind === "video") L.out = clamp(o.in + (ne - o.start) * L.speed, o.in + 0.3, info.duration || 9999); else L.dur = Math.max(0.3, ne - o.start);
      } else {
        L.start = +snapT(Math.max(0, o.start + d), id).toFixed(3);
        const s2 = snapT(L.start + layerLen(L), id) - layerLen(L); if (Math.abs(s2 - L.start) < 8 / PPS) L.start = Math.max(0, s2);
        const row = document.elementFromPoint(ev.clientX, ev.clientY)?.closest(".vd-row");
        if (row && row.dataset.track) L.track = +row.dataset.track;
      }
      drawTimeline(); requestDraw();
    };
    const up = () => { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up);
      compactTracks(); if (moved) commit(); else { if (t < L.start || t >= L.start + layerLen(L)) seek(L.start + 0.01); drawTimeline(); placeSel(); } };
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  }
  function compactTracks() {
    const used = [...new Set(P.layers.map(L => L.track))].sort((a, b) => a - b);
    const map = Object.fromEntries(used.map((tr, i) => [tr, i + 1]));
    P.layers.forEach(L => { L.track = map[L.track]; });
  }
  // the playhead: drag it along the ruler
  $("[data-ruler]").addEventListener("pointerdown", e => {
    stop(); seek(timeAt(e));
    const mv = ev => seek(timeAt(ev)), up = () => { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  });
  // dropping library clips onto the timeline or the video
  tl.addEventListener("dragover", e => { if ([...e.dataTransfer.types].includes("text/x-mcdp-item")) { e.preventDefault(); const r = e.target.closest(".vd-row"); $$(".vd-row.drop").forEach(x => x.classList.remove("drop")); if (r) r.classList.add("drop"); } });
  tl.addEventListener("dragleave", e => { if (!tl.contains(e.relatedTarget)) $$(".vd-row.drop").forEach(x => x.classList.remove("drop")); });
  tl.addEventListener("drop", e => {
    const id = +e.dataTransfer.getData("text/x-mcdp-item"); if (!id) return;
    e.preventDefault(); $$(".vd-row.drop").forEach(x => x.classList.remove("drop"));
    const row = e.target.closest(".vd-row"), at = timeAt(e);
    if (!row || row.dataset.kind === "main" || row.dataset.kind === "captions" || row.dataset.kind === "music") {
      const segs = layout().segs.filter(s => s.kind === "clip"); addMain(id, segs.filter(s => (s.start + s.end) / 2 < at).length);
    } else addLayer(id, at, +row.dataset.track);
  });
  screen.addEventListener("dragover", e => { if ([...e.dataTransfer.types].includes("text/x-mcdp-item")) e.preventDefault(); });
  screen.addEventListener("drop", e => { const id = +e.dataTransfer.getData("text/x-mcdp-item"); if (!id) return; e.preventDefault(); P.main.length ? addLayer(id, t) : addMain(id); });

  // ================================================================ the properties panel
  const propsEl = $("[data-props]"), fxEl = $("[data-fx]"), trEl = $("[data-trp]"), fiEl = $("[data-fip]");
  const jump = (...b) => `<div class="vd-jump">${b.map(([k, ic, l]) => `<button type="button" data-open="${k}">${ico(ic, 16)} ${l}</button>`).join("")}</div>`;
  const opt = (pairs, v) => pairs.map(([k, l]) => `<option value="${k}"${String(k) === String(v) ? " selected" : ""}>${esc(l)}</option>`).join("");
  const rng = (name, label, v, min, max, step = 1) => `<label class="ed-range">${label}<input type="range" data-p="${name}" min="${min}" max="${max}" step="${step}" value="${v}"><span>${v}</span></label>`;
  function filterBlock(f, thumb) {
    return `<h3>Filter</h3><div class="vd-presets">${C.presets.map(([k, name]) =>
      `<button type="button" class="vd-preset${f.preset === k ? " on" : ""}" data-preset="${k}"><canvas width="64" height="64" data-pthumb="${k}"></canvas><span>${esc(name)}</span></button>`).join("")}</div>
      <h3>Adjust</h3>${rng("f.brightness", "Brightness", Math.round(f.brightness * 100), -100, 100)}${rng("f.contrast", "Contrast", Math.round(f.contrast * 100), -100, 100)}
      ${rng("f.saturation", "Saturation", Math.round(f.saturation * 100), -100, 100)}${rng("f.warmth", "Warmth", Math.round(f.warmth * 100), -100, 100)}
      ${rng("f.vignette", "Vignette", Math.round(f.vignette * 100), 0, 100)}`;
  }
  function presetThumbs(src, root = fiEl) {
    const im = new Image(); im.onload = () => $$("[data-pthumb]", root).forEach(c => {
      const x = c.getContext("2d", { willReadFrequently: true }), s = Math.max(64 / im.width, 64 / im.height);
      x.drawImage(im, (64 - im.width * s) / 2, (64 - im.height * s) / 2, im.width * s, im.height * s); applyGrade(c, grade({ preset: c.dataset.pthumb }));
    }); im.src = src;
  }
  function bgState(id) { const it = items[id] || {}; return it.mask ? "ready" : it.mask_state; }
  function props() {
    if (!P) return;
    const c = curMain(), L = curLayer();
    if (c) {
      const it = items[c.item] || {}, still = !!c.still, bs = bgState(c.item);
      propsEl.innerHTML = `<h2>${esc(it.title || "Clip")}</h2>
        ${still ? `<label>Shows for <span class="muted small">(seconds)</span><input type="number" step="0.5" min="0.5" max="30" data-p="out" value="${c.out}" class="ed-num"></label>` :
          `<div class="st-share-grid"><label>Starts at<input type="number" step="0.1" min="0" data-p="in" value="${c.in}" class="ed-num"></label><label>Ends at<input type="number" step="0.1" data-p="out" value="${c.out}" class="ed-num"></label></div>
          <p class="small muted">The clip is ${fmt(it.duration)} long. Drag its ends on the timeline to trim.</p>
          <label data-sec="speed">Speed<select data-p="speed">${opt(C.speeds.map(s => [s, s + "×"]), c.speed)}</select></label>
          <div data-sec="volume">${rng("volume", "Volume", Math.round(c.volume * 100), 0, 150)}</div>`}
        ${jump(["filters", "filters", "Filters"], ["effects", "effects", "Motion"], ...(P.main.indexOf(c) > 0 ? [["transitions", "transitions", "Transition in"]] : []))}
        <h3>Framing</h3>
        <div class="vd-seg"><button type="button" data-fit="fill" class="${c.fit === "fill" ? "on" : ""}">Fill the screen</button><button type="button" data-fit="fit" class="${c.fit === "fit" ? "on" : ""}">Show all of it</button></div>
        ${rng("zoom", "Zoom", Math.round(c.zoom * 100), 30, 300)}
        <p class="small muted">Drag the clip on the video to move it; drag a corner to zoom. <button type="button" class="linklike" data-reset-pos>Centre it</button> · <button type="button" class="linklike" data-crop-go>Crop</button>${c.crop.l + c.crop.t + c.crop.r + c.crop.b > 0 ? ` · <button type="button" class="linklike" data-uncrop>Undo the crop</button>` : ""}</p>
        <h3 data-sec="bg">Background</h3>
        ${!still ? `<label class="check vd-check"><input type="checkbox" data-cut ${c.bg.cut ? "checked" : ""} ${bs === "ready" ? "" : (C.bgOk ? "" : "disabled")}> Remove the background (keeps the person)</label>
          <p class="small muted" data-bgstate>${bs === "ready" ? "" : bs === "running" ? "Finding the person… this takes a few minutes; you can keep editing." : bs === "error" ? esc(it.mask_error || "That didn't work for this clip.") : C.bgOk ? "Works on the NAS for free; takes a few minutes the first time for each clip." : "Needs the hub update on the NAS."}</p>` : ""}
        <label>Behind it<select data-p="bg.mode">${opt(C.bgModes, c.bg.mode)}</select></label>
        ${c.bg.mode === "color" ? `<label class="ed-range">Colour<input type="color" data-p="bg.color" value="${c.bg.color}"></label>` : ""}
        ${c.bg.mode === "photo" ? `<label>Photo<select data-p="bg.item"><option value="">Pick a photo…</option>${(mediaList.pictures || []).map(m => `<option value="${m.id}"${m.id === c.bg.item ? " selected" : ""}>${esc(m.title)}</option>`).join("")}</select></label>` : ""}
        <p class="small muted">Shows where the clip doesn't cover the screen${!still ? ", or behind the person once the background is removed" : ""}.</p>
        <div class="btn-row"><button type="button" class="linklike danger" data-delsel>Remove the clip</button></div>`;
    } else if (L) {
      const it = items[L.item] || {}, text = L.kind === "text", bs = L.kind === "video" ? bgState(L.item) : null;
      propsEl.innerHTML = `<h2 data-sec="top">${text ? "Text" : esc(it.title || "Layer")}</h2>
        ${jump(...(text ? [] : [["filters", "filters", "Filters"]]), ["effects", "anim", "Animations"])}
        ${text ? `<label>Words<textarea rows="3" data-p="text">${esc(L.text)}</textarea></label>
          <label>Font<select data-p="font">${opt(C.fonts.map(f => [f, f]), L.font)}</select></label>
          <div class="st-share-grid"><label>Size<input type="number" min="10" max="400" data-p="size" value="${L.size}" class="ed-num"></label>
          <label>Bold<select data-p="bold">${opt([[1, "Bold"], [0, "Regular"]], L.bold ? 1 : 0)}</select></label></div>
          <div class="vd-seg">${["left", "center", "right"].map(a => `<button type="button" data-align="${a}" class="${L.align === a ? "on" : ""}">${a === "center" ? "Centre" : a[0].toUpperCase() + a.slice(1)}</button>`).join("")}</div>
          <label class="ed-range">Text colour<input type="color" data-p="color" value="${L.color}"></label>
          <label class="ed-range">Box<input type="color" data-p="fill" value="${L.fill || "#000000"}"><button type="button" class="linklike small" data-nofill>${L.fill ? "No box" : "(none)"}</button></label>
          <div class="vd-swatches">${[C.primary, C.accent, "#FFFFFF", "#1A1A1A"].map(c_ => `<button type="button" style="background:${c_}" data-swatch="${c_}" title="${c_}"></button>`).join("")}</div>` :
          (L.kind === "video" ? `<div class="st-share-grid"><label>Starts at<input type="number" step="0.1" min="0" data-p="in" value="${L.in}" class="ed-num"></label><label>Ends at<input type="number" step="0.1" data-p="out" value="${L.out}" class="ed-num"></label></div>
            <label>Speed<select data-p="speed">${opt(C.speeds.map(s => [s, s + "×"]), L.speed)}</select></label>${rng("volume", "Volume", Math.round(L.volume * 100), 0, 150)}
            <label class="check vd-check"><input type="checkbox" data-cut ${L.cut ? "checked" : ""} ${bs === "ready" || C.bgOk ? "" : "disabled"}> Cut out the person (remove the background)</label>
            <p class="small muted" data-bgstate>${bs === "running" ? "Finding the person… a few minutes." : bs === "error" ? "That didn't work for this clip." : ""}</p>` : "")}
        <div class="st-share-grid"><label>On screen at<input type="number" step="0.1" min="0" data-p="start" value="${L.start.toFixed(2)}" class="ed-num"></label>
          ${L.kind !== "video" ? `<label>For (seconds)<input type="number" step="0.1" min="0.3" data-p="dur" value="${L.dur}" class="ed-num"></label>` : "<span></span>"}</div>
        ${rng("opacity", "Opacity", Math.round(L.opacity * 100), 0, 100)}
        ${rng("rot", "Rotation", Math.round(L.rot), -180, 180)}
        <div class="btn-row"><button type="button" class="btn btn-small btn-quiet" data-layerup>Bring forward</button><button type="button" class="btn btn-small btn-quiet" data-layerdown>Send back</button>
          <button type="button" class="btn btn-small btn-quiet" data-center>Centre</button></div>
        <div class="btn-row"><button type="button" class="linklike danger" data-delsel>Remove the layer</button></div>`;
    } else if (sel && sel.kind === "trans") {
      if (!trTarget()) { sel = null; return props(); }
      propsEl.innerHTML = `<h2>Transition</h2>${trPicker()}`;
    } else if (sel && sel.kind === "cap") {
      const c = P.captions[sel.i]; if (!c) { sel = null; return props(); }
      propsEl.innerHTML = `<h2>Caption</h2><label>Words<textarea rows="2" maxlength="160" data-cp="text">${esc(c.text)}</textarea></label>
        <div class="st-share-grid"><label>From<input type="number" step="0.1" data-cp="start" value="${c.start}" class="ed-num"></label><label>To<input type="number" step="0.1" data-cp="end" value="${c.end}" class="ed-num"></label></div>
        <button type="button" class="linklike danger" data-capdel>Remove</button>`;
    } else if (sel && sel.kind === "lower") {
      const c = P.lowers[sel.i]; if (!c) { sel = null; return props(); }
      propsEl.innerHTML = `<h2>Name tag</h2><label>Name<input data-lp="text" value="${esc(c.text)}"></label><label>Role<input data-lp="sub" value="${esc(c.sub || "")}"></label>
        <div class="st-share-grid"><label>From<input type="number" step="0.1" data-lp="start" value="${c.start}" class="ed-num"></label><label>To<input type="number" step="0.1" data-lp="end" value="${c.end}" class="ed-num"></label></div>
        <button type="button" class="linklike danger" data-lowdel>Remove</button>`;
    } else if (sel && sel.kind === "music") {
      propsEl.innerHTML = `<h2>Music</h2><p><strong>${esc((items[P.music.item] || {}).title || "")}</strong></p>
        <label class="ed-range" data-sec="volume">Volume<input type="range" min="0" max="150" data-mvol value="${Math.round(P.music.volume * 100)}"><span>${Math.round(P.music.volume * 100)}%</span></label>
        ${jump(["audio", "audio", "Change the music"])}<button type="button" class="linklike danger" data-delsel>Remove the music</button>`;
    } else if (sel && sel.kind === "outro") propsEl.innerHTML = `<h2>End card</h2>${jump(["brand", "brand", "Change the end card"], ["transitions", "transitions", "How it comes in"])}`;
    else propsEl.innerHTML = `<h2>Edit</h2><ul class="small muted vd-tips"><li>Drag clips from the left onto the timeline: the <strong>Main</strong> track plays in order, layers go on top.</li>
      <li>Click something on the video to move, resize, rotate or crop it.</li><li>Drag the ends of anything on the timeline to trim it; drag a layer up or down to change what's on top.</li>
      <li>Click <strong>+</strong> between two clips for a transition.</li><li>Space plays; S splits at the playhead; Delete removes; Ctrl+Z undoes.</li></ul>`;
    toolPanels(); mtools();
    [propsEl, fxEl, trEl, fiEl].forEach(root => $$("input[type=range][data-p]", root).forEach(r => r.addEventListener("input", () => { r.nextElementSibling && (r.nextElementSibling.textContent = r.value); })));
  }
  // ---- the Effects, Transitions and Filters panels act on what's selected
  function trTarget() {
    if (sel && sel.kind === "trans") return sel.id === "outro" ? (P.outro.on ? P.outro : null) : P.main.find(x => x.id === sel.id) || null;
    if (sel && sel.kind === "outro") return P.outro;
    const c = curMain(); return c && P.main.indexOf(c) > 0 ? c : null;
  }
  function trPicker() {
    const c = trTarget(); c.trans = c.trans || { type: "none", dur: 0.5 };
    return `<div class="vd-trans">${C.transitions.map(([k, l]) =>
      `<button type="button" data-tr="${k}" class="${c.trans.type === k ? "on" : ""}"><span class="vd-tri vd-tri-${k}"></span>${esc(l)}</button>`).join("")}</div>
      ${rng("tdur", "Length (seconds)", c.trans.dur, 0.2, 2, 0.1)}
      <p class="small muted">The clips overlap by this much, so the video gets a little shorter.</p>
      ${P.main.length > 2 ? `<button type="button" class="btn btn-small btn-quiet" data-trall>Use on every cut</button>` : ""}`;
  }
  const none = m => `<p class="small muted vd-none">${m}</p>`;
  const tiles = (attr, list, cur) => `<div class="vd-fx">${list.map(([k, l]) => `<button type="button" ${attr}="${k}" class="${k === cur ? "on" : ""}"><span class="vd-fxi vd-fxi-${k}"></span>${esc(l)}</button>`).join("")}</div>`;
  function toolPanels() {
    const c = curMain(), L = curLayer(), fT = c || (L && L.kind !== "text" ? L : null);
    fiEl.innerHTML = fT ? `<p class="small muted">For: <strong>${esc((items[fT.item] || {}).title || "this clip")}</strong></p>${filterBlock(fT.filter)}`
                        : none("Select a clip or picture on the timeline, then pick a look.");
    if (fT) presetThumbs((items[fT.item] || {}).thumb, fiEl);
    fxEl.innerHTML = c ? `<h3>Motion</h3>${tiles("data-motion", C.motions, c.motion)}<p class="small muted">A slow push or pan across the clip, like a Ken Burns effect.</p>`
      : L ? `<h3>Comes in</h3>${tiles("data-ain", C.animIn, L.anim.in)}<h3>Goes out</h3>${tiles("data-aout", C.animOut, L.anim.out)}`
      : none("Select a clip (for motion) or a layer or text (for animations) on the timeline.");
    trEl.innerHTML = trTarget() ? `<p class="small muted">Into: <strong>${esc(sel && (sel.id === "outro" || sel.kind === "outro") ? "the end card" : (items[trTarget().item] || {}).title || "this clip")}</strong></p>${trPicker()}`
      : none(P.main.length > 1 || P.outro.on ? "Tap the + between two clips on the timeline, or select the second clip." : "Add a second clip first; transitions go between clips.");
  }
  function setPath(o, path, v) { const ks = path.split("."); let x = o; ks.slice(0, -1).forEach(k => { x = x[k]; }); x[ks[ks.length - 1]] = v; }
  const onInput = e => {
    const el = e.target, c = curMain(), L = curLayer(), target = c || L;
    if (el.hasAttribute("data-mvol") && P.music) { P.music.volume = el.value / 100; el.nextElementSibling.textContent = `${el.value}%`; syncMedia(); commit({ quiet: true }); musicNow(); return; }
    if (el.dataset.p && el.dataset.p !== "tdur" && target) {
      let v = el.type === "range" || el.type === "number" ? parseFloat(el.value) : el.value, p = el.dataset.p;
      if (p.startsWith("f.")) { target.filter[p.slice(2)] = v / 100; requestDraw(); return; }
      if (p === "volume" || p === "opacity" || p === "zoom") v = v / 100;
      if (p === "speed") v = parseFloat(v);
      if (p === "bold") v = v === "1";
      if (p === "bg.item") v = v ? +v : null;
      if (p === "in" && c && !c.still) v = clamp(v, 0, c.out - 0.3);
      if (p === "out" && target && !target.still && target.kind !== "text" && target.in !== undefined) v = Math.max(v, (target.in || 0) + 0.3);
      setPath(target, p, v);
      if (L && L.kind === "text" && ["text", "size", "font", "bold"].includes(p)) fitTextBox(L);
      if (p === "bg.item" && v) ensureItem(v).then(() => requestDraw());
      requestDraw(); placeSel();
      if (el.type !== "range" && el.tagName !== "TEXTAREA") commit(); else commit({ quiet: true });
      return;
    }
    if (el.dataset.p === "tdur" && trTarget()) { trTarget().trans.dur = parseFloat(el.value); commit({ quiet: true }); drawTimeline(); }
    if (el.dataset.cp !== undefined) { const cc = P.captions[sel.i]; cc[el.dataset.cp] = el.dataset.cp === "text" ? el.value : parseFloat(el.value); commit({ quiet: true }); drawTimeline(); }
    if (el.dataset.lp !== undefined) { const cc = P.lowers[sel.i]; cc[el.dataset.lp] = ["text", "sub"].includes(el.dataset.lp) ? el.value : parseFloat(el.value); commit({ quiet: true }); drawTimeline(); }
  };
  const onChange = e => { if (e.target.type === "range" || e.target.tagName === "TEXTAREA") commit(); };
  const onClick = async e => {
    const b = e.target.closest("button, input[data-cut]"); if (!b) return;
    const c = curMain(), L = curLayer(), target = c || L;
    if (b.dataset.open) { openTab(b.dataset.open); return; }
    if (b.dataset.motion && c) { c.motion = b.dataset.motion; commit(); const s_ = layout().segs.find(x => x.c === c); if (s_) preview(s_.start, s_.len); return; }
    if ((b.dataset.ain || b.dataset.aout) && L) { if (b.dataset.ain) L.anim.in = b.dataset.ain; else L.anim.out = b.dataset.aout; commit();
      preview(b.dataset.ain ? L.start : Math.max(L.start, L.start + layerLen(L) - 1), b.dataset.ain ? 1 : 1.1); return; }
    if (b.hasAttribute("data-trall")) { const src = trTarget(); if (!src) return; P.main.forEach((x, i) => { if (i > 0) x.trans = { ...src.trans }; }); commit(); say("Used on every cut"); return; }
    if (b.dataset.preset && target) { target.filter.preset = b.dataset.preset; commit(); return; }
    if (b.dataset.fit && c) { c.fit = b.dataset.fit; c.zoom = 1; c.dx = c.dy = 0; commit(); return; }
    if (b.dataset.align && L) { L.align = b.dataset.align; commit(); return; }
    if (b.dataset.swatch && L) { L.fill = L.fill ? b.dataset.swatch : L.fill; if (!L.fill) L.color = b.dataset.swatch; else L.fill = b.dataset.swatch; commit(); return; }
    if (b.hasAttribute("data-nofill") && L) { L.fill = ""; commit(); return; }
    if (b.hasAttribute("data-reset-pos") && c) { c.dx = c.dy = 0; c.zoom = 1; commit(); return; }
    if (b.hasAttribute("data-crop-go")) { cropBtn && cropBtn.click(); return; }
    if (b.hasAttribute("data-uncrop") && c) { c.crop = noCrop(); commit(); return; }
    if (b.hasAttribute("data-center") && L) { L.x = (P.w - L.w) / 2; L.y = (P.h - L.h) / 2; commit(); return; }
    if (b.hasAttribute("data-layerup") && L) { L.track += 1; compactTracks(); commit(); return; }
    if (b.hasAttribute("data-layerdown") && L) { if (L.track > 1) L.track -= 1; else P.layers.forEach(x => { if (x !== L) x.track += 1; }); compactTracks(); commit(); return; }
    if (b.hasAttribute("data-delsel")) { delSel(); return; }
    if (b.dataset.tr && trTarget()) { const c2 = trTarget(); c2.trans.type = b.dataset.tr; commit();
      const s = layout().segs.find(s_ => s_.c === c2 || (c2 === P.outro && s_.kind === "outro")); if (s) preview(Math.max(0, s.start - 0.5), s.d + 1); return; }
    if (b.hasAttribute("data-capdel")) { P.captions.splice(sel.i, 1); sel = null; commit(); return; }
    if (b.hasAttribute("data-lowdel")) { P.lowers.splice(sel.i, 1); sel = null; commit(); return; }
    if (b.matches("input[data-cut]") && target) {
      const id = target.item;
      if (b.checked && bgState(id) !== "ready") { startBg(id); }
      if (c) c.bg.cut = b.checked; else L.cut = b.checked;
      commit({ quiet: true }); requestDraw();
    }
  };
  [propsEl, fxEl, trEl, fiEl].forEach(r => { r.addEventListener("input", onInput); r.addEventListener("change", onChange); r.addEventListener("click", onClick); });
  // play a short stretch so the effect just picked can be seen
  let prevT;
  function preview(from, len) { clearTimeout(prevT); stop(); seek(from); play(); prevT = setTimeout(() => { if (playing) stop(); }, (len + 0.3) * 1000); }
  const bgPolls = {};
  async function startBg(id) {
    const r = await fetch(`/studio/bg/${id}`, { method: "POST", headers: { Accept: "application/json" } }); const j = await r.json();
    if (!r.ok) { say(j.error || "Background removal didn't start."); return; }
    items[id].mask_state = j.state; if (j.mask) items[id].mask = j.mask; props();
    if (j.state !== "ready") pollBg(id);
  }
  function pollBg(id) {
    clearTimeout(bgPolls[id]);
    bgPolls[id] = setTimeout(async () => {
      const j = await (await fetch(`/studio/bg/${id}`)).json();
      items[id].mask_state = j.state; items[id].mask_error = j.error;
      const st = $("[data-bgstate]", propsEl); if (st && j.state === "running") st.textContent = `Finding the person… ${j.pct || 0}%`;
      if (j.state === "ready") { items[id].mask = j.mask; props(); requestDraw(); say("Background removed"); }
      else if (j.state === "error") { props(); say(j.error || "Background removal didn't work."); }
      else pollBg(id);
    }, 3000);
  }
  function delSel() {
    if (!sel) return;
    if (sel.kind === "main") P.main = P.main.filter(c => c.id !== sel.id);
    else if (sel.kind === "layer") { P.layers = P.layers.filter(L => L.id !== sel.id); compactTracks(); }
    else if (sel.kind === "cap") P.captions.splice(sel.i, 1);
    else if (sel.kind === "lower") P.lowers.splice(sel.i, 1);
    else if (sel.kind === "music") P.music = null;
    else if (sel.kind === "trans" && trTarget()) { trTarget().trans = { type: "none", dur: 0.5 }; sel = null; commit(); return; }
    else return;
    sel = null; cropMode = false; commit(); seek(Math.min(t, total()));
  }
  function split() {
    const c = curMain(), L = curLayer();
    if (L) {
      const tl_ = t - L.start; if (tl_ < 0.3 || layerLen(L) - tl_ < 0.3) return;
      const b = JSON.parse(JSON.stringify(L)); b.id = uid(); b.start = +(L.start + tl_).toFixed(3);
      if (L.kind === "video") { const at = L.in + tl_ * L.speed; L.out = +at.toFixed(3); b.in = +at.toFixed(3); } else { b.dur = +(L.dur - tl_).toFixed(3); L.dur = +tl_.toFixed(3); }
      P.layers.push(b); sel = { kind: "layer", id: b.id }; commit(); return;
    }
    const s = layout().segs.find(s_ => s_.kind === "clip" && t > s_.start && t < s_.end && (!c || s_.c.id === c.id));
    if (!s) return;
    const cc = s.c, i = P.main.indexOf(cc), b = JSON.parse(JSON.stringify(cc)); b.id = uid(); b.trans = { type: "none", dur: 0.5 };
    if (cc.still) { const a = +(t - s.start).toFixed(2); if (a < 0.3 || cc.out - a < 0.3) return; b.out = +(cc.out - a).toFixed(2); cc.out = a; }
    else { const at = +(cc.in + (t - s.start) * cc.speed).toFixed(3); if (at - cc.in < 0.3 || cc.out - at < 0.3) return; b.in = at; cc.out = at; }
    P.main.splice(i + 1, 0, b); sel = { kind: "main", id: b.id }; commit();
  }
  function duplicate() {
    const L = curLayer(), c = curMain();
    if (c) { const b = JSON.parse(JSON.stringify(c)); b.id = uid(); b.trans = { type: "none", dur: 0.5 }; P.main.splice(P.main.indexOf(c) + 1, 0, b);
      sel = { kind: "main", id: b.id }; commit(); return; }
    if (!L) return;
    const b = JSON.parse(JSON.stringify(L)); b.id = uid(); b.x += 30; b.y += 30; b.track = nextTrack(b.start, layerLen(b));
    P.layers.push(b); sel = { kind: "layer", id: b.id }; commit();
  }
  const sb = $("[data-split]"), db_ = $("[data-dup]"), del = $("[data-del]");
  if (sb) sb.addEventListener("click", split); if (db_) db_.addEventListener("click", duplicate); if (del) del.addEventListener("click", delSel);
  document.addEventListener("keydown", e => {
    if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
    const mod = e.ctrlKey || e.metaKey;
    if (e.code === "Space") { e.preventDefault(); playing ? stop() : play(); }
    else if (!C.canEdit) return;
    else if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); undo(e.shiftKey ? 1 : -1); }
    else if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); undo(1); }
    else if (mod && e.key.toLowerCase() === "d") { e.preventDefault(); duplicate(); }
    else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); delSel(); }
    else if (e.key.toLowerCase() === "s" && !mod) { e.preventDefault(); split(); }
    else if (e.key === "Escape") { cropMode = false; cropBtn && cropBtn.classList.remove("on"); placeSel(); }
    else if (e.key.startsWith("Arrow") && curLayer()) {
      e.preventDefault(); const L = curLayer(), d = e.shiftKey ? 20 : 2;
      if (e.key === "ArrowLeft") L.x -= d; if (e.key === "ArrowRight") L.x += d; if (e.key === "ArrowUp") L.y -= d; if (e.key === "ArrowDown") L.y += d;
      commit({ quiet: true });
    } else if (e.key === "ArrowLeft") seek(t - 1 / 30); else if (e.key === "ArrowRight") seek(t + 1 / 30);
  });

  // ================================================================ side panels
  const TABS = [["media", "Media"], ["stock", "Stock"], ["audio", "Audio"], ["text", "Text"], ["captions", "Captions"], ["effects", "Effects"],
                ["transitions", "Transitions"], ["filters", "Filters"], ["brand", "Brand kit"]];
  const TITLE = Object.fromEntries(TABS);
  const vdRoot = $("#vd"), grid = $(".vd-grid"), panelEl = $("[data-panel]"), propsSheet = $(".vd-props");
  let tab = "media";
  $("[data-rail]").innerHTML = TABS.map(([k, l]) => `<button type="button" data-vtab="${k}" title="${l}">${ico(k, 22)}<span>${l}</span></button>`).join("");
  function openTab(k, fromRail) {
    if (fromRail && !M && k === tab && !grid.classList.contains("vd-collapsed")) { grid.classList.add("vd-collapsed"); $$("[data-vtab]").forEach(b => b.classList.remove("on")); return fit(); }
    tab = k; grid.classList.remove("vd-collapsed");
    $$("[data-vtab]").forEach(b => b.classList.toggle("on", b.dataset.vtab === k));
    $$("[data-vpanel]").forEach(p => { p.hidden = p.dataset.vpanel !== k; });
    $("[data-panel-title]").textContent = TITLE[k] || "";
    if (M) { propsSheet.classList.remove("open"); panelEl.classList.add("open"); vdRoot.classList.add("vd-sheeted"); panelEl.querySelector(".vd-panel-body").scrollTop = 0; }
    fit();
  }
  $$("[data-vtab]").forEach(b => b.addEventListener("click", () => openTab(b.dataset.vtab, true)));
  function openProps(sec) {
    if (M) { panelEl.classList.remove("open"); propsSheet.classList.add("open"); vdRoot.classList.add("vd-sheeted"); }
    const el = sec && $(`[data-sec="${sec}"]`, propsEl);
    if (el) setTimeout(() => el.scrollIntoView({ block: "start", behavior: "smooth" }), M ? 250 : 0);
  }
  function closeSheets() { panelEl.classList.remove("open"); propsSheet.classList.remove("open"); vdRoot.classList.remove("vd-sheeted"); }
  $$("[data-close-sheet]").forEach(b => b.addEventListener("click", closeSheets));
  // the phone toolbar along the bottom changes with what's selected (like CapCut's)
  const mtEl = $("[data-mtools]");
  function mtools() {
    if (!mtEl) return;
    const c = curMain(), L = curLayer(), T = (k, i, l) => [k, i, l];
    let list;
    if (!C.canEdit) list = [T("tab:captions", "captions", "Captions"), T("tab:brand", "brand", "Brand kit")];
    else if (c) list = [T("back", "back", ""), T("split", "split", "Split"), ...(c.still ? [] : [T("prop:volume", "volume", "Volume"), T("prop:speed", "speed", "Speed")]),
      T("tab:filters", "filters", "Filters"), T("tab:effects", "effects", "Motion"), ...(P.main.indexOf(c) > 0 ? [T("tab:transitions", "transitions", "Transition")] : []),
      T("crop", "crop", "Crop"), T("prop:bg", "bg", "Background"), T("prop:", "edit", "More"), T("dup", "dup", "Duplicate"), T("del", "del", "Delete")];
    else if (L) list = [T("back", "back", ""), ...(L.kind === "text" ? [T("type", "typing", "Edit text"), T("prop:top", "text", "Style")] : [T("prop:top", "edit", "Edit")]),
      T("split", "split", "Split"), T("tab:effects", "anim", "Animations"), ...(L.kind !== "text" ? [T("tab:filters", "filters", "Filters"), T("crop", "crop", "Crop")] : []),
      ...(L.kind === "video" ? [T("prop:volume", "volume", "Volume")] : []), T("fwd", "up", "Forward"), T("bwd", "down", "Back"), T("dup", "dup", "Duplicate"), T("del", "del", "Delete")];
    else if (sel && sel.kind === "trans") list = [T("back", "back", ""), T("tab:transitions", "transitions", "Change"), T("del", "del", "Remove")];
    else if (sel && sel.kind === "outro") list = [T("back", "back", ""), T("tab:brand", "brand", "End card"), T("tab:transitions", "transitions", "Transition")];
    else if (sel && sel.kind === "music") list = [T("back", "back", ""), T("prop:volume", "volume", "Volume"), T("tab:audio", "audio", "Replace"), T("del", "del", "Delete")];
    else if (sel) list = [T("back", "back", ""), T("prop:", "edit", "Edit"), T("del", "del", "Delete")];
    else list = [T("tab:media", "media", "Media"), T("tab:audio", "audio", "Audio"), T("tab:text", "text", "Text"), T("tab:captions", "captions", "Captions"),
      T("tab:effects", "effects", "Effects"), T("tab:transitions", "transitions", "Transitions"), T("tab:filters", "filters", "Filters"), T("tab:stock", "stock", "Stock"),
      T("ratio", "ratio", "Ratio"), T("tab:brand", "brand", "Brand kit")];
    mtEl.classList.toggle("vd-mt-sel", !!sel);
    mtEl.innerHTML = list.map(([k, i, l]) => `<button type="button" data-mt="${k}" class="${k === "back" ? "vd-mt-back" : ""}" ${k === "back" ? 'aria-label="Done"' : ""}>${ico(i, 22)}${l ? `<span>${l}</span>` : ""}</button>`).join("");
  }
  if (mtEl) mtEl.addEventListener("click", e => {
    const b = e.target.closest("[data-mt]"); if (!b) return;
    const k = b.dataset.mt, L = curLayer();
    if (k === "back") { sel = null; cropMode = false; cropBtn && cropBtn.classList.remove("on"); closeSheets(); drawTimeline(); props(); placeSel(); }
    else if (k.startsWith("tab:")) openTab(k.slice(4));
    else if (k.startsWith("prop:")) openProps(k.slice(5));
    else if (k === "split") split(); else if (k === "dup") duplicate(); else if (k === "del") delSel();
    else if (k === "crop") cropBtn && cropBtn.click();
    else if (k === "type" && L) showTyping(L);
    else if (k === "fwd" && L) { L.track += 1; compactTracks(); commit(); }
    else if (k === "bwd" && L) { if (L.track > 1) L.track -= 1; else P.layers.forEach(x => { if (x !== L) x.track += 1; }); compactTracks(); commit(); }
    else if (k === "ratio") { ratioMenu.hidden = false; }
  });
  // the shape of the video
  const ratioBtn = $("[data-ratio-btn]"), ratioMenu = $("[data-ratio-menu]");
  ratioBtn.addEventListener("click", e => { e.stopPropagation(); ratioMenu.hidden = !ratioMenu.hidden; });
  document.addEventListener("click", e => { if (!e.target.closest(".vd-ratio") && !e.target.closest("[data-mt=ratio]")) ratioMenu.hidden = true; });
  ratioMenu.addEventListener("click", async e => {
    const b = e.target.closest("[data-ratio]"); if (!b) return;
    ratioMenu.hidden = true;
    if (+b.dataset.w === P.w && +b.dataset.h === P.h) return;
    await save();
    try {
      const r = await fetch(`/studio/video/${C.iid}/ratio`, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ size: b.dataset.ratio }) });
      const j = await r.json(); if (!r.ok) throw new Error(j.error || "It didn't change.");
      P = j.project; hist.push(JSON.stringify(P)); hi = hist.length - 1;
      cv.width = 0; fit(); loadOutro(); drawTimeline(); props(); requestDraw(); say(`Now ${b.textContent.trim().replace(/\s+/g, " ")}`);
    } catch (err) { say(err.message); }
  });
  let mediaList = {}, mediaIndex = {};
  function tile(m, opts = {}) {
    const b = document.createElement("div"); b.className = "vd-m"; b.title = m.title; b.draggable = C.canEdit;
    b.innerHTML = `<img alt="" loading="lazy"><span></span>${m.duration ? `<em>${fmt(m.duration).replace(/\.\d$/, "")}</em>` : ""}
      ${C.canEdit ? `<div class="vd-m-acts"><button type="button" data-a="main">+ Main</button><button type="button" data-a="layer">+ Layer</button></div>` : ""}`;
    b.querySelector("img").src = m.thumb; b.querySelector("span").textContent = m.title;
    b.addEventListener("dragstart", e => { e.dataTransfer.setData("text/x-mcdp-item", String(m.id)); e.dataTransfer.effectAllowed = "copy"; });
    b.addEventListener("click", async e => {
      const a = e.target.dataset.a; if (!a) return;
      const id = opts.importer ? await opts.importer(m) : m.id; if (!id) return;
      a === "main" ? addMain(id) : (P.main.length ? addLayer(id, t) : addMain(id));
    });
    return b;
  }
  async function loadMedia() {
    const r = await fetch(`/studio/video-media?pid=${C.pid}`); mediaList = await r.json();
    [...mediaList.videos, ...mediaList.pictures].forEach(m => { mediaIndex[m.id] = m; if (!items[m.id]) items[m.id] = m; else Object.assign(items[m.id], { mask: m.mask, mask_state: m.mask_state }); });
    ["videos", "pictures"].forEach(k => {
      const box = $(`[data-media=${k}]`); box.innerHTML = "";
      mediaList[k].forEach(m => box.appendChild(tile(m)));
      if (!mediaList[k].length) box.innerHTML = `<p class="small muted">${k === "videos" ? "No videos for this brand yet. Add some above, or find stock video." : "No photos or designs yet."}</p>`;
    });
    musicLib(mediaList.sounds || []);
  }
  // stock video (Pixabay)
  let stockQ = { q: "", page: 1 };
  $("[data-stock-form]").addEventListener("submit", async e => {
    e.preventDefault(); const f = e.target, box = $("[data-stock-results]");
    stockQ = { q: f.q.value.trim(), page: 1 }; box.innerHTML = `<p class="small muted">Searching…</p>`;
    const r = await fetch(`/studio/stock/videos?${new URLSearchParams({ q: stockQ.q, o: f.o.value })}`); const j = await r.json();
    box.innerHTML = "";
    if (!r.ok) { box.innerHTML = `<p class="small st-err">${esc(j.error)}</p>`; return; }
    if (!j.hits.length) { box.innerHTML = `<p class="small muted">Nothing found; try other words.</p>`; return; }
    j.hits.forEach(h => {
      const tl_ = tile({ id: "px" + h.id, title: h.tags.split(",")[0] || "Stock video", thumb: h.thumb, duration: h.duration }, {
        importer: async () => { say("Bringing it in from Pixabay…");
          const rr = await fetch(`/studio/stock/videos/${h.id}`, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ q: stockQ.q, page: 1, pid: C.pid }) });
          const jj = await rr.json(); if (!rr.ok) { say(jj.error); return null; } items[jj.id] = jj; mediaIndex[jj.id] = jj; say(""); loadMedia(); return jj.id; } });
      tl_.draggable = false;
      if (h.preview) { const v = document.createElement("video"); v.muted = true; v.loop = true; v.playsInline = true; v.className = "vd-m-pv"; v.src = h.preview; tl_.insertBefore(v, tl_.firstChild);
        tl_.addEventListener("mouseenter", () => v.play().catch(() => {})); tl_.addEventListener("mouseleave", () => v.pause()); }
      box.appendChild(tl_);
    });
  });
  // music
  const audioPrev = new Audio();
  function trackRow(tk, actions) {
    const li = document.createElement("li");
    li.innerHTML = `<button type="button" class="vd-pp" aria-label="Play">▶</button><div><strong></strong><span class="small muted"></span></div>${actions}`;
    li.querySelector("strong").textContent = tk.title || tk.name; li.querySelector("span").textContent = `${fmt(tk.duration).replace(/\.\d$/, "")}${tk.user ? " · " + tk.user : ""}`;
    const pp = li.querySelector(".vd-pp");
    pp.addEventListener("click", () => {
      const src = tk.preview || tk.src;
      if (audioPrev.dataset.src === src && !audioPrev.paused) { audioPrev.pause(); pp.textContent = "▶"; return; }
      $$(".vd-pp").forEach(x => { x.textContent = "▶"; }); audioPrev.src = src; audioPrev.dataset.src = src; audioPrev.play(); pp.textContent = "❚❚";
    });
    return li;
  }
  function useMusic(tk) { items[tk.id] = { id: tk.id, title: tk.title, kind: "audio", src: tk.src || `/studio/media/${tk.id}`, duration: tk.duration };
    P.music = { item: tk.id, volume: P.music ? P.music.volume : 0.7 }; musicFresh = true; commit(); musicNow(); audioPrev.pause(); say(`Music: ${tk.title}`); }
  let musicAll = [];
  function musicLib(list) {
    musicAll = list || musicAll; const ul = $("[data-music-lib]"), q = ($("[data-music-filter]").value || "").toLowerCase(); ul.innerHTML = "";
    musicAll.filter(tk => !q || (tk.title + " " + tk.notes).toLowerCase().includes(q)).slice(0, 60).forEach(tk => {
      const li = trackRow(tk, C.canEdit ? `<button type="button" class="btn btn-small btn-quiet">Use</button>` : "");
      const b = li.querySelector(".btn"); if (b) b.addEventListener("click", () => useMusic(tk)); ul.appendChild(li);
    });
    if (!ul.children.length) ul.innerHTML = `<li class="small muted">${musicAll.length ? "Nothing matches." : "No shared music yet. Drop MP3s below or search Freesound."}</li>`;
  }
  $("[data-music-filter]").addEventListener("input", () => musicLib());
  function musicNow() {
    const now = $("[data-music-now]"), vol = $("[data-music-vol]");
    if (P.music && items[P.music.item]) {
      now.innerHTML = `<p><strong></strong> <button type="button" class="linklike danger small">Remove</button></p>`;
      now.querySelector("strong").textContent = `♪ ${items[P.music.item].title}`;
      now.querySelector("button").addEventListener("click", () => { P.music = null; commit(); musicNow(); });
      vol.value = Math.round(P.music.volume * 100); vol.disabled = false; $("[data-music-vol-v]").textContent = `${vol.value}%`;
    } else { now.innerHTML = `<p class="small muted">No music yet. Pick a track below.</p>`; vol.disabled = true; $("[data-music-vol-v]").textContent = ""; }
  }
  $("[data-music-vol]").addEventListener("input", e => { if (P.music) { P.music.volume = e.target.value / 100; $("[data-music-vol-v]").textContent = `${e.target.value}%`; syncMedia(); } });
  $("[data-music-vol]").addEventListener("change", () => commit({ quiet: true }));
  const fsForm = $("[data-fs-form]");
  fsForm.addEventListener("submit", async e => {
    e.preventDefault(); const ul = $("[data-fs-results]"), q = fsForm.q.value.trim(); ul.innerHTML = `<li class="small muted">Searching…</li>`;
    const r = await fetch(`/studio/stock/music?${new URLSearchParams({ q })}`); const j = await r.json(); ul.innerHTML = "";
    if (!r.ok) { ul.innerHTML = `<li class="small st-err">${esc(j.error)}</li>`; return; }
    j.hits.forEach(h => {
      const li = trackRow(h, C.canEdit ? `<button type="button" class="btn btn-small btn-quiet">Add</button>` : "");
      const b = li.querySelector(".btn");
      if (b) b.addEventListener("click", async () => {
        b.disabled = true; b.textContent = "Adding…";
        const rr = await fetch(`/studio/stock/music/${h.id}`, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ q, page: 1 }) });
        const jj = await rr.json(); if (!rr.ok) { say(jj.error); b.disabled = false; b.textContent = "Add"; return; }
        useMusic(jj); b.textContent = "Added"; const m = await (await fetch("/studio/music")).json(); musicLib(m.tracks);
      });
      ul.appendChild(li);
    });
    if (!j.hits.length) ul.innerHTML = `<li class="small muted">Nothing found; try other words.</li>`;
    ul.insertAdjacentHTML("beforeend", `<li class="small muted">From <a href="https://freesound.org" target="_blank" rel="noopener">Freesound</a>, Creative Commons 0.</li>`);
  });
  const pxBtn = $("[data-pixabay-music]");
  if (pxBtn) pxBtn.addEventListener("click", () => {
    const q = ($("[data-music-filter]").value || fsForm.q.value || "").trim();
    const w = Math.min(720, window.screen.availWidth * 0.45);
    window.open(`https://pixabay.com/music/search/${encodeURIComponent(q)}/`, "pixabay_music", `popup,width=${Math.round(w)},height=${Math.round(window.screen.availHeight * 0.85)},left=${Math.round(window.screen.availWidth - w)},top=40`);
  });

  // uploads (videos and photos into the brand; music into the shared Music folder)
  const post = (url, body, json) => fetch(url, { method: "POST", body, headers: json ? { "Content-Type": "application/json", Accept: "application/json" } : { Accept: "application/json" } })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`); return j; });
  async function upload(file, pid, folder, tray) {
    const li = document.createElement("li"); li.textContent = `${file.name}: starting`; tray && tray.appendChild(li);
    try {
      const { id } = await post("/studio/upload/start", JSON.stringify({ name: file.name, size: file.size, mime: file.type, pid, folder }), true);
      for (let o = 0; o < file.size; o += C.chunk) { await post(`/studio/upload/${id}/chunk?offset=${o}`, file.slice(o, o + C.chunk)); li.textContent = `${file.name}: ${Math.round(Math.min(file.size, o + C.chunk) / file.size * 100)}%`; }
      const done = await post(`/studio/upload/${id}/done`, new FormData()); li.textContent = `${file.name}: added`; return done.id;
    } catch (e) { li.textContent = `${file.name}: didn't go (${e.message})`; li.className = "bad"; return null; }
  }
  const upIn = $("[data-vupload]"), tray = $("[data-vtray]");
  const upMedia = async files => { for (const f of files) { const id = await upload(f, C.pid, C.uploadsFolder, tray); if (id) { await loadMedia(); P.main.length ? addLayer(id, t) : addMain(id); } } };
  if (upIn) upIn.addEventListener("change", () => { upMedia([...upIn.files]); upIn.value = ""; });
  const dz = $("[data-vupload-drop]");
  if (dz) { dz.addEventListener("dragover", e => e.preventDefault()); dz.addEventListener("drop", e => { e.preventDefault(); upMedia([...e.dataTransfer.files]); }); }
  const mIn = $("[data-music-upload]"), mdz = $("[data-music-drop]");
  const upMusic = async files => {
    const f0 = await (await fetch("/studio/music")).json();
    for (const f of files) { if (!f.type.startsWith("audio/")) { say(`${f.name} isn't a sound file`); continue; } const id = await upload(f, 0, f0.folder, null);
      if (id) { const m = await (await fetch("/studio/music")).json(); musicLib(m.tracks); const tk = m.tracks.find(x => x.id === id); if (tk) useMusic(tk); } }
  };
  if (mIn) mIn.addEventListener("change", () => { upMusic([...mIn.files]); mIn.value = ""; });
  if (mdz) { mdz.addEventListener("dragover", e => e.preventDefault()); mdz.addEventListener("drop", e => { e.preventDefault(); upMusic([...e.dataTransfer.files]); }); }

  // captions panel
  function capList() {
    const ol = $("[data-caplist]"); if (!ol) return; ol.innerHTML = "";
    P.captions.forEach((c, i) => { const li = document.createElement("li"); li.innerHTML = `<button type="button" class="linklike small"></button><span></span>`;
      li.querySelector("button").textContent = fmt(c.start); li.querySelector("span").textContent = c.text;
      li.querySelector("button").addEventListener("click", () => { sel = { kind: "cap", i }; seek(c.start); drawTimeline(); props(); }); ol.appendChild(li); });
    if (!P.captions.length) ol.innerHTML = `<li class="small muted">No captions yet.</li>`;
  }
  const addCap = $("[data-add-caption]");
  if (addCap) addCap.addEventListener("click", () => { P.captions.push({ start: +t.toFixed(2), end: +(t + 2).toFixed(2), text: "New caption" }); P.captions.sort((a, b) => a.start - b.start);
    sel = { kind: "cap", i: P.captions.findIndex(c => c.text === "New caption") }; commit(); });
  const auto = $("[data-auto-captions]");
  if (auto) auto.addEventListener("click", async () => {
    if (P.captions.length && !confirm("Replace the captions that are there now?")) return;
    await save(); auto.disabled = true; auto.textContent = "Listening… (about a minute)";
    try { const r = await fetch(`/studio/video/${C.iid}/captions`, { method: "POST", headers: { Accept: "application/json" } }); const j = await r.json();
      if (!r.ok) throw new Error(j.error || "It didn't work."); P.captions = j.captions; sel = null; commit();
      say(j.captions.length ? `Wrote ${j.captions.length} captions; check names and spellings` : "No speech found"); } catch (e) { say(e.message); }
    auto.disabled = false; auto.textContent = "Write captions from the speech";
  });
  $$("input[name=capstyle], input[name=cappos]").forEach(r => r.addEventListener("change", () => { P.style.captions = $("input[name=capstyle]:checked").value; P.style.position = $("input[name=cappos]:checked").value; commit({ quiet: true }); }));
  // brand panel
  const oOn = $("[data-outro-on]"), oText = $("[data-outro-text]"), oDur = $("[data-outro-dur]"), oImg = $("[data-outro-img]");
  let outroT;
  function brandPanel() {
    oOn.checked = P.outro.on; oText.value = P.outro.text; oDur.value = P.outro.dur; $("[data-outro-dur-v]").textContent = `${P.outro.dur} s`;
    $$("input[name=capstyle]").forEach(r => { r.checked = r.value === P.style.captions; }); $$("input[name=cappos]").forEach(r => { r.checked = r.value === P.style.position; });
    oImg.src = `/studio/video/${C.iid}/outro.png?text=${encodeURIComponent(P.outro.text)}`;
  }
  oOn.addEventListener("change", () => { P.outro.on = oOn.checked; commit(); });
  oText.addEventListener("input", () => { P.outro.text = oText.value; clearTimeout(outroT); outroT = setTimeout(() => { loadOutro(); oImg.src = outroImg.src; commit({ quiet: true }); }, 400); });
  oDur.addEventListener("input", () => { P.outro.dur = +oDur.value; $("[data-outro-dur-v]").textContent = `${P.outro.dur} s`; commit(); });

  // ================================================================ making the MP4
  const rbtn = $("[data-render]"), rs = $("[data-render-state]");
  let poll;
  function renderState(s) {
    if (!rs) return;
    rs.className = "small vd-render-state";
    if (s.state === "running") { rs.textContent = `Making the MP4: step ${Math.min(s.step + 1, s.steps)} of ${s.steps}…`; rbtn.disabled = true; }
    else if (s.state === "done") { rs.innerHTML = `Done (${Math.round(s.length || 0)} s). <a href="/studio/i/${C.iid}">See it</a>`; rbtn.disabled = false; }
    else if (s.state === "error") { rs.textContent = s.error; rs.className = "small vd-render-state st-err"; rbtn.disabled = false; }
    else rs.textContent = "";
  }
  async function pollRender() { clearTimeout(poll); const s = await (await fetch(`/studio/video/${C.iid}/render`)).json(); renderState(s); if (s.state === "running") poll = setTimeout(pollRender, 2000); }
  async function textPictures() {
    const fd = new FormData();
    await Promise.all([...new Set(P.layers.filter(L => L.kind === "text").map(L => `${L.bold ? 700 : 400} 40px "${L.font}"`))].map(f => document.fonts.load(f).catch(() => null)));
    for (const L of P.layers.filter(L => L.kind === "text")) {
      const c = document.createElement("canvas"); c.width = Math.max(2, Math.round(L.w / 2) * 2); c.height = Math.max(2, Math.round(L.h / 2) * 2);
      const x = c.getContext("2d"); x.scale(c.width / L.w, c.height / L.h); drawText(x, L);
      fd.append(`text_${L.id}`, await new Promise(r => c.toBlob(r, "image/png")), `${L.id}.png`);
    }
    return fd;
  }
  if (rbtn) rbtn.addEventListener("click", async () => {
    if (!P.main.length) return say("Add at least one clip to the main track first.");
    await save(); rbtn.disabled = true;
    const r = await fetch(`/studio/video/${C.iid}/render`, { method: "POST", body: await textPictures(), headers: { Accept: "application/json" } });
    const j = await r.json(); if (!r.ok) { renderState({ state: "error", error: j.error }); return; }
    renderState(j); setTimeout(pollRender, 1500);
  });

  // ================================================================ start
  (async () => {
    const r = await fetch(`/studio/video/${C.iid}/project`); const j = await r.json();
    P = j.project; items = Object.fromEntries(Object.entries(j.items).map(([k, v]) => [+k, v]));
    P.main.forEach(c => { c.id = c.id || uid(); });
    document.documentElement.style.setProperty("--brand", C.primary); document.documentElement.style.setProperty("--brand-2", C.accent);
    hist = [JSON.stringify(P)]; hi = 0;
    loadOutro(); fit(); brandPanel(); musicNow(); drawTimeline(); props(); seek(0);
    Object.values(items).forEach(it => { if (it.mask_state === "running") pollBg(it.id); });
    loadMedia();
    if (C.render && C.render.state === "running") pollRender(); else renderState(C.render || {});
    PPS = M ? 45 : Math.max(15, Math.min(220, (scrollEl.clientWidth - 40) / Math.max(10, total() + 2))); zoomIn.value = PPS; drawTimeline();
    openTab(M ? "media" : "media"); if (M) closeSheets(); followHead(true);
    say("");
  })();
})();
