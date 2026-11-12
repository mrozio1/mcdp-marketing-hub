/* MCDP Studio: the built-in template library for the picture designer.
   Every layout is built from the brand kit at the design's own size: the brand's two colours (and lighter and darker
   versions of them), its heading and body fonts, its logo and its name. Two script fonts from the hub's font list are
   used for small accent words. Sizes are in U (1% of the short side), and text is stacked by a small layout engine
   (flow) so a layout works on a square post, a story or a wide banner. Decorations, textures and patterns are drawn
   here in code: there are no image files.
   window.MCDPTemplates(kit) returns { CATS, T, ACCENT_FONTS }: T[key] = { name, cat, months?, build() -> { bg, objs, logo? } }. */
window.MCDPTemplates = function (k) {
  "use strict";
  const { W, H, U, P, A, C, textDefaults, textbox, rect, frame, circleFrame, button, fabric } = k;
  const S = Math.min(W, H), wide = W / H > 1.3, tall = H / W > 1.3;
  const SCRIPT = "Dancing Script", HAND = "Caveat";
  const ACCENT_FONTS = [SCRIPT, HAND];

  // ---------------------------------------------------------------- colours from the brand kit
  const hex = c => { const m = /^#?([0-9a-f]{6})$/i.exec(c || ""); const n = parseInt(m ? m[1] : "0F7471", 16); return [n >> 16, (n >> 8) & 255, n & 255]; };
  const toHex = rgb => "#" + rgb.map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("").toUpperCase();
  const mix = (c, d, t) => { const a = hex(c), b = hex(d); return toHex(a.map((v, i) => v + (b[i] - v) * t)); };
  const tint = (c, t) => mix(c, "#FFFFFF", t), shade = (c, t) => mix(c, "#000000", t);
  const rgba = (c, a) => { const [r, g, b] = hex(c); return `rgba(${r},${g},${b},${a})`; };
  const INK = "#1A1A1A", MUTED = "#5B5852", CREAM = "#FBF7F0", PAPER = "#FFFFFF", RED = "#C0392B";
  const PL = tint(P, 0.86), PXL = tint(P, 0.93), AL = tint(A, 0.8), AXL = tint(A, 0.9), PD = shade(P, 0.35), PDD = shade(P, 0.6);

  // ---------------------------------------------------------------- fills: gradients, patterns, shadows
  const grad = (c1, c2, dir = "diag", stops) => new fabric.Gradient({
    type: "linear", gradientUnits: "percentage",
    coords: dir === "down" ? { x1: 0, y1: 0, x2: 0, y2: 1 } : dir === "across" ? { x1: 0, y1: 0, x2: 1, y2: 0 } : { x1: 0, y1: 0, x2: 1, y2: 1 },
    colorStops: stops || [{ offset: 0, color: c1 }, { offset: 1, color: c2 }],
  });
  const radial = (c1, c2) => new fabric.Gradient({ type: "radial", gradientUnits: "percentage",
    coords: { x1: 0.5, y1: 0.45, r1: 0, x2: 0.5, y2: 0.45, r2: 0.75 }, colorStops: [{ offset: 0, color: c1 }, { offset: 1, color: c2 }] });
  const tiles = {};
  // a repeating tile, drawn once per look (kinds: dots, grid, stripes, checker, grain, waves, plus, gingham)
  function pattern(kind, color, size = U * 4, alpha = 1) {
    const key = [kind, color, Math.round(size), alpha].join("|");
    if (!tiles[key]) {
      const n = Math.max(8, Math.round(kind === "grain" ? Math.min(160, size * 4) : size));
      const c = document.createElement("canvas"); c.width = c.height = n;
      const x = c.getContext("2d"); x.fillStyle = x.strokeStyle = rgba(color, alpha);
      if (kind === "dots") { x.beginPath(); x.arc(n / 2, n / 2, n * 0.14, 0, Math.PI * 2); x.fill(); }
      else if (kind === "grid") { x.lineWidth = Math.max(1, n * 0.04); x.strokeRect(0, 0, n, n); }
      else if (kind === "stripes") { x.lineWidth = n * 0.22; for (let i = -1; i <= 1; i++) { x.beginPath(); x.moveTo(i * n, n); x.lineTo(i * n + n, 0); x.stroke(); } }
      else if (kind === "checker") { x.fillRect(0, 0, n / 2, n / 2); x.fillRect(n / 2, n / 2, n / 2, n / 2); }
      else if (kind === "gingham") { x.globalAlpha = 0.5; x.fillRect(0, 0, n / 2, n); x.fillRect(0, 0, n, n / 2); }
      else if (kind === "plus") { x.lineWidth = Math.max(1, n * 0.07); x.beginPath(); x.moveTo(n * 0.5, n * 0.36); x.lineTo(n * 0.5, n * 0.64); x.moveTo(n * 0.36, n * 0.5); x.lineTo(n * 0.64, n * 0.5); x.stroke(); }
      else if (kind === "waves") { x.lineWidth = Math.max(1, n * 0.07); x.beginPath(); x.moveTo(0, n * 0.5); x.bezierCurveTo(n * 0.25, n * 0.2, n * 0.25, n * 0.2, n * 0.5, n * 0.5); x.bezierCurveTo(n * 0.75, n * 0.8, n * 0.75, n * 0.8, n, n * 0.5); x.stroke(); }
      else if (kind === "grain") { const r = rnd(7), d = x.createImageData(n, n), [cr, cg, cb] = hex(color); for (let i = 0; i < d.data.length; i += 4) { d.data[i] = cr; d.data[i + 1] = cg; d.data[i + 2] = cb; d.data[i + 3] = r() < 0.5 ? Math.round(r() * 255 * alpha) : 0; } x.putImageData(d, 0, 0); }
      tiles[key] = c;
    }
    return new fabric.Pattern({ source: tiles[key], repeat: "repeat" });
  }
  const shadow = (blur = 3, y = 1.2, a = 0.18) => new fabric.Shadow({ color: `rgba(0,0,0,${a})`, blur: U * blur, offsetX: 0, offsetY: U * y });
  const LOCK = { selectable: false, evented: false, lockMovementX: true, lockMovementY: true, lockScalingX: true, lockScalingY: true, lockRotation: true, hasControls: false };
  // full-page layers (a colour, gradient or texture), locked so they aren't dragged by accident (unlock from Layers)
  const layer = (fill, o = {}) => new fabric.Rect({ left: 0, top: 0, width: W, height: H, fill, name: "Background", ...LOCK, ...o });
  const texture = (kind, color, size, alpha, o = {}) => layer(pattern(kind, color, size, alpha), { name: "Background texture", ...o });
  const grain = (a = 0.06) => texture("grain", "#000000", U * 6, a, { name: "Paper grain" });

  // ---------------------------------------------------------------- shapes (drawn in a 100 x 100 box)
  const starD = (n, inner) => { let d = ""; for (let i = 0; i < n * 2; i++) { const r = i % 2 ? 50 * inner : 50, a = -Math.PI / 2 + i * Math.PI / n; d += `${i ? "L" : "M"}${(50 + r * Math.cos(a)).toFixed(2)} ${(50 + r * Math.sin(a)).toFixed(2)} `; } return d + "Z"; };
  const SHAPE = {
    heart: "M50 92 C22 72 2 54 2 32 C2 14 16 4 30 4 C40 4 46 9 50 17 C54 9 60 4 70 4 C84 4 98 14 98 32 C98 54 78 72 50 92 Z",
    star: starD(5, 0.4), burst: starD(16, 0.82), seal: starD(24, 0.9),
    sparkle: "M50 0 C54 34 66 46 100 50 C66 54 54 66 50 100 C46 66 34 54 0 50 C34 46 46 34 50 0 Z",
    tooth: "M20 8 C6 8 0 26 7 46 C13 62 14 88 25 95 C35 100 38 70 50 70 C62 70 65 100 75 95 C86 88 87 62 93 46 C100 26 94 8 80 8 C68 8 62 16 50 16 C38 16 32 8 20 8 Z",
    leaf: "M8 92 C6 44 38 8 94 6 C94 60 58 94 8 92 Z",
    maple: "M50 2 L58 22 L72 14 L68 36 L90 30 L80 48 L98 56 L74 64 L80 80 L58 72 L54 98 L46 98 L42 72 L20 80 L26 64 L2 56 L20 48 L10 30 L32 36 L28 14 L42 22 Z",
    egg: "M50 2 C78 2 94 44 94 62 C94 86 74 98 50 98 C26 98 6 86 6 62 C6 44 22 2 50 2 Z",
    blob: "M62 4 C84 8 98 30 94 54 C90 80 68 98 42 96 C16 94 2 72 4 48 C6 22 36 0 62 4 Z",
    blob2: "M48 2 C70 0 92 14 97 38 C102 62 86 88 62 96 C38 104 10 90 4 64 C-2 40 22 4 48 2 Z",
    arch: "M0 100 L0 50 C0 22 22 0 50 0 C78 0 100 22 100 50 L100 100 Z",
    archflip: "M0 0 L100 0 L100 50 C100 78 78 100 50 100 C22 100 0 78 0 50 Z",
    pill: "M30 0 L70 0 C86 0 100 22 100 50 C100 78 86 100 70 100 L30 100 C14 100 0 78 0 50 C0 22 14 0 30 0 Z",
    ticket: "M0 0 L100 0 L100 38 C92 38 88 44 88 50 C88 56 92 62 100 62 L100 100 L0 100 L0 62 C8 62 12 56 12 50 C12 44 8 38 0 38 Z",
    ribbon: "M0 0 L100 0 L92 50 L100 100 L0 100 L8 50 Z",
    pin: "M50 2 C74 2 90 20 90 42 C90 66 62 84 50 98 C38 84 10 66 10 42 C10 20 26 2 50 2 Z M50 26 C41 26 34 33 34 42 C34 51 41 58 50 58 C59 58 66 51 66 42 C66 33 59 26 50 26 Z",
    check: "M8 52 L22 38 L42 58 L80 18 L94 32 L42 86 Z",
    cross: "M18 4 L50 36 L82 4 L96 18 L64 50 L96 82 L82 96 L50 64 L18 96 L4 82 L36 50 L4 18 Z",
    tag: "M4 18 C4 10 10 4 18 4 L56 4 L96 44 C100 48 100 54 96 58 L58 96 C54 100 48 100 44 96 L4 56 Z M26 18 C21 18 18 21 18 26 C18 31 21 34 26 34 C31 34 34 31 34 26 C34 21 31 18 26 18 Z",
    quote: "M8 60 C8 30 26 12 44 8 L46 18 C34 24 28 34 28 44 L44 44 L44 86 L8 86 Z M56 60 C56 30 74 12 92 8 L94 18 C82 24 76 34 76 44 L92 44 L92 86 L56 86 Z",
    wave: "M0 40 C20 20 40 20 60 40 C80 60 100 60 120 40 L120 100 L0 100 Z",
    shamrock: "M50 50 C40 36 22 34 20 20 C18 8 34 2 42 12 C46 4 54 4 58 12 C66 2 82 8 80 20 C78 34 60 36 50 50 Z M50 50 C64 40 66 22 80 20 C92 18 98 34 88 42 C96 46 96 54 88 58 C98 66 92 82 80 80 C66 78 64 60 50 50 Z M50 50 C36 60 34 78 20 80 C8 82 2 66 12 58 C4 54 4 46 12 42 C2 34 8 18 20 20 C34 22 36 40 50 50 Z M48 50 L54 50 L58 98 L50 98 Z",
    gift: "M8 36 L92 36 L92 52 L8 52 Z M14 52 L86 52 L86 96 L14 96 Z M44 36 L56 36 L56 96 L44 96 Z M50 34 C40 14 20 14 24 28 C26 34 40 36 50 34 Z M50 34 C60 14 80 14 76 28 C74 34 60 36 50 34 Z",
    balloon: "M50 4 C74 4 88 24 88 44 C88 66 68 82 54 84 L58 92 L42 92 L46 84 C32 82 12 66 12 44 C12 24 26 4 50 4 Z",
    pumpkin: "M50 26 C34 22 10 30 8 56 C6 82 28 96 50 92 C72 96 94 82 92 56 C90 30 66 22 50 26 Z M46 26 C46 16 50 8 58 4 L62 10 C56 14 54 20 54 26 Z",
    bracket: "M30 30 L70 30 L70 70 L30 70 Z M42 14 L58 14 L58 86 L42 86 Z",
    flower: "M50 50 C40 30 40 6 50 6 C60 6 60 30 50 50 Z M50 50 C70 40 94 40 94 50 C94 60 70 60 50 50 Z M50 50 C60 70 60 94 50 94 C40 94 40 70 50 50 Z M50 50 C30 60 6 60 6 50 C6 40 30 40 50 50 Z M50 50 C36 36 22 18 30 12 C38 6 52 28 50 50 Z M50 50 C64 36 78 18 70 12 C62 6 48 28 50 50 Z",
    apple: "M50 26 C38 16 10 18 8 46 C6 74 28 98 42 94 C46 93 48 90 50 90 C52 90 54 93 58 94 C72 98 94 74 92 46 C90 18 62 16 50 26 Z M50 24 C50 14 54 6 62 2 L66 8 C58 12 56 18 56 24 Z",
    pencil: "M10 76 L70 16 L86 32 L26 92 L6 96 Z",
    cloud: "M26 80 C10 80 2 70 2 58 C2 46 12 38 24 40 C26 22 40 12 56 14 C70 16 80 28 80 40 C92 40 100 50 100 60 C100 72 90 80 78 80 Z",
    mug: "M10 20 L72 20 L66 92 L16 92 Z M72 34 C92 34 96 66 70 72 L70 62 C84 58 82 44 72 44 Z",
    calendar: "M6 14 L94 14 L94 96 L6 96 Z",
    crown: "M6 84 L10 26 L32 52 L50 14 L68 52 L90 26 L94 84 Z",
    bolt: "M58 2 L14 58 L46 58 L38 98 L86 38 L54 38 Z",
    smile: "M20 40 C20 76 80 76 80 40",
    moon: "M70 6 C40 10 20 34 20 60 C20 82 38 98 60 98 C78 98 92 88 98 74 C88 80 76 82 66 78 C44 70 38 42 50 22 C56 14 62 8 70 6 Z",
  };
  const path = (d, x, y, w, h, o = {}) => {
    const p = new fabric.Path(d, { left: x, top: y, fill: A, strokeWidth: 0, name: "Decoration", ...o });
    p.set({ scaleX: w / (p.width || 100), scaleY: (h == null ? w : h) / (p.height || 100) });
    return p;
  };
  const shape = (name, x, y, w, o, h) => path(SHAPE[name], x, y, w, h, o);
  const line = (d, x, y, w, h, color, sw = U * 0.7, o = {}) => {        // a stroked line drawing in a 100 x 100 box
    const p = new fabric.Path(d, { left: x, top: y, fill: "", stroke: color, strokeWidth: 100 * sw / w, strokeLineCap: "round", strokeLineJoin: "round", name: "Line art", ...o });
    p.set({ scaleX: w / 100, scaleY: (h == null ? w : h) / 100 });
    return p;
  };
  const SQUIGGLE = "M0 50 C8 20 17 20 25 50 C33 80 42 80 50 50 C58 20 67 20 75 50 C83 80 92 80 100 50";
  const SWASH = "M2 60 C30 30 70 30 98 48";
  const ARROW = "M8 90 C14 50 40 20 86 18 M70 6 L88 18 L74 34";
  const LOOP = "M4 70 C20 10 60 10 50 50 C40 90 80 90 96 30";
  const rnd = seed => { let s = seed; return () => { s = (s * 9301 + 49297) % 233280; return s / 233280; }; };
  const burst = (cx, cy, r, n, color, w = U * 0.8, inner = 0.35) => {
    const lines = [];
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      lines.push(new fabric.Line([cx + Math.cos(a) * r * inner, cy + Math.sin(a) * r * inner, cx + Math.cos(a) * r, cy + Math.sin(a) * r], { stroke: color, strokeWidth: w, strokeLineCap: "round" }));
    }
    return new fabric.Group(lines, { name: "Burst" });
  };
  const snowflake = (cx, cy, r, color) => {
    const parts = [];
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * Math.PI * 2, c = Math.cos(a), s = Math.sin(a);
      parts.push(new fabric.Line([cx, cy, cx + c * r, cy + s * r], { stroke: color, strokeWidth: Math.max(2, r * 0.09), strokeLineCap: "round" }));
      [0.55, 0.78].forEach(f => [-1, 1].forEach(sg => {
        const bx = cx + c * r * f, by = cy + s * r * f, b = a + sg * 0.7;
        parts.push(new fabric.Line([bx, by, bx + Math.cos(b) * r * 0.25, by + Math.sin(b) * r * 0.25], { stroke: color, strokeWidth: Math.max(2, r * 0.07), strokeLineCap: "round" }));
      }));
    }
    return new fabric.Group(parts, { name: "Snowflake" });
  };
  const ring = (cx, cy, r, color, w = U * 0.6, o = {}) => new fabric.Circle({ left: cx - r, top: cy - r, radius: r, fill: "", stroke: color, strokeWidth: w, name: "Ring", ...o });
  const dot = (cx, cy, r, color, o = {}) => new fabric.Circle({ left: cx - r, top: cy - r, radius: r, fill: color, name: "Circle", ...o });
  const box = (x, y, w, h, fill, o = {}) => rect({ left: x, top: y, width: w, height: h, fill, name: "Shape", ...o });
  const patch = (kind, x, y, w, h, color, size, alpha, o = {}) => box(x, y, w, h, pattern(kind, color, size, alpha), { name: "Pattern", ...o });
  const LOGO_ZONES = [{ x: 0.68, y: 0.8, w: 0.32, h: 0.2 }, { x: 0, y: 0, w: 0.3, h: 0.16 }];
  const scatter = (seed, n, make, clear) => {
    const r = rnd(seed), out = [], zones = [...(clear ? [].concat(clear) : []), ...LOGO_ZONES];
    let tries = 0;
    while (out.length < n && tries++ < n * 30) {
      const x = r() * W, y = r() * H, s = (0.04 + r() * 0.07) * S;
      if (zones.some(c => x + s > c.x * W && x < (c.x + c.w) * W && y + s > c.y * H && y < (c.y + c.h) * H)) continue;
      out.push(make(x, y, s, r));
    }
    return out;
  };
  const confetti = (seed, n, clear, cols) => scatter(seed, n, (x, y, s, r) => {
    const col = (cols || [P, A, tint(A, 0.4), tint(P, 0.5)])[Math.floor(r() * (cols || [1, 2, 3, 4]).length)];
    const kind = r();
    return kind < 0.4 ? new fabric.Rect({ left: x, top: y, width: s * 0.22, height: s * 0.5, fill: col, angle: r() * 180, name: "Confetti" })
      : kind < 0.7 ? new fabric.Circle({ left: x, top: y, radius: s * 0.14, fill: col, name: "Confetti" })
      : line(SQUIGGLE, x, y, s * 0.9, s * 0.4, col, s * 0.07, { angle: r() * 180, name: "Confetti" });
  }, clear);
  const logoAt = (pos, size = 0.16) => ({ br: { x: 0.93, y: 0.94, size, anchor: "br" }, tl: { x: 0.07, y: 0.06, size, anchor: "tl" } })[pos];

  // ---------------------------------------------------------------- photo spots (placeholders until a photo is dropped in)
  // o.q: what the photo should show (searched on Pixabay); o.own: team | office | patient (the practice's own photos come first)
  const spotFill = (c = P) => grad(tint(c, 0.62), tint(A, 0.55));
  const stk = o => { const { q, own, tone, i, ...rest } = o; return q ? { ...rest, stock: { q, kind: "photo", own: own || "", ...(i != null ? { i } : {}) } } : rest; };
  const photo = (x, y, w, h, o = {}) => frame({ left: x, top: y, width: w, height: h, fill: spotFill(o.tone), strokeWidth: 0, strokeDashArray: null, name: "Photo frame", ...stk(o) });
  const photoShape = (name, x, y, w, h, o = {}) => path(SHAPE[name], x, y, w, h, { fill: spotFill(o.tone), name: "Photo frame", role: "photo", ...stk(o) });
  const photoCircle = (cx, cy, r, o = {}) => circleFrame({ left: cx - r, top: cy - r, radius: r, fill: spotFill(o.tone), strokeWidth: 0, strokeDashArray: null, ...stk(o) });
  // a cut-out drawing (leaves, pumpkins, ornaments…) from Pixabay in place of a drawn shape; the shape stays if none is found
  const art = (obj, q, i = 0) => { obj.set({ stock: { q, kind: "art", i }, role: "art", name: "Drawing" }); return obj; };
  const artBox = (q, x, y, w, h, i = 0, o = {}) => art(box(x, y, w, h, "rgba(0,0,0,0)", { name: "Drawing", ...o }), q, i);
  // torn paper: a card with a hand-torn edge
  const tornD = seed => { const r = rnd(seed), j = () => r() * 2.6; let d = `M${j()} ${j()} `;
    for (let x = 4; x <= 100; x += 4) d += `L${x} ${j()} `; for (let y = 4; y <= 100; y += 4) d += `L${100 - j()} ${y} `;
    for (let x = 96; x >= 0; x -= 4) d += `L${x} ${100 - j()} `; for (let y = 96; y > 0; y -= 4) d += `L${j()} ${y} `; return d + "Z"; };
  const torn = (seed, x, y, w, h, fill = CREAM, o = {}) => path(tornD(seed), x, y, w, h, { fill, shadow: shadow(2.4, 0.9, 0.22), name: "Torn paper", ...o });
  const shadeUp = (a = 0.75) => layer(grad("rgba(0,0,0,0)", `rgba(0,0,0,${a})`, "down", [{ offset: 0.25, color: "rgba(0,0,0,0)" }, { offset: 1, color: `rgba(0,0,0,${a})` }]), { name: "Shade" });
  const dim = (a = 0.45) => layer(`rgba(0,0,0,${a})`, { name: "Shade" });
  const bokeh = (seed, n, color) => scatter(seed, n, (x, y, s, r) => dot(x, y, s * (0.3 + r() * 0.5), color, { opacity: 0.15 + r() * 0.25, name: "Bokeh" }));
  // drawings tucked into the four corners, half off the page (centred boxes, so they turn about their middle)
  const corners = (q, size = 0.42, i = 0) => [[0.06, 0.05, 20], [0.94, 0.06, 110], [0.07, 0.94, -70], [0.93, 0.95, 200]]
    .map(([x, y, a], n) => artBox(q, W * x, H * y, S * size, S * size, i + (n % 2), { angle: a, originX: "center", originY: "center" }));
  // a brand-colour wash laid over a photo (multiply): duotone-style photos that match the kit
  const wash = (x, y, w, h, color, op = 0.45, o = {}) => box(x, y, w, h, color, { opacity: op, globalCompositeOperation: "multiply", name: "Colour wash", ...o });
  const polaroid = (x, y, w, h, o = {}) => {
    const pad = Math.max(U * 1.6, w * 0.05);
    return [box(x, y, w, h + pad * 2.6, PAPER, { shadow: shadow(2.6, 1, 0.2), name: "Photo card" }),
      photo(x + pad, y + pad, w - pad * 2, h, o)];
  };

  // ---------------------------------------------------------------- text, stacked by a little layout engine
  const td = kind => textDefaults(kind);
  const est = (text, size, w, ratio, lh) => String(text).split("\n").reduce((n, ln) => n + Math.max(1, Math.ceil((ln.length * size * ratio) / Math.max(1, w))), 0) * size * lh;
  const MAKE = {
    h1: (it, w) => { const size = Math.round(U * (it.size || 10));
      const t = textbox(it.t, { ...td("heading"), fontSize: size, width: w, fill: it.c || P, lineHeight: it.lh || 1.04, charSpacing: it.sp || 0,
        ...(it.outline ? { fill: "transparent", stroke: it.c || P, strokeWidth: Math.max(1.5, size * 0.035), name: "Outline heading" } : {}), ...(it.o || {}) });
      return [[t], est(it.t, size, w, 0.56 + (it.sp || 0) / 1000, it.lh || 1.04)]; },
    script: (it, w) => { const size = Math.round(U * (it.size || 8));
      const t = textbox(it.t, { fontFamily: it.hand ? HAND : SCRIPT, fontWeight: 700, fontSize: size, lineHeight: 1.05, width: w, fill: it.c || A, name: "Accent text", role: "subhead", angle: it.angle || 0, ...(it.o || {}) });
      return [[t], est(it.t, size, w, 0.42, 1.05)]; },
    eyebrow: (it, w) => { const size = Math.round(U * (it.size || 3.2));
      const t = textbox(it.t.toUpperCase(), { ...td("subhead"), fontSize: size, charSpacing: 260, width: w, fill: it.c || A, name: "Label", ...(it.o || {}) });
      return [[t], est(it.t, size, w, 0.85, 1.25)]; },
    sub: (it, w) => { const size = Math.round(U * (it.size || 4.6));
      const t = textbox(it.t, { ...td("subhead"), fontSize: size, width: w, fill: it.c || A, ...(it.o || {}) });
      return [[t], est(it.t, size, w, 0.52, 1.25)]; },
    body: (it, w) => { const size = Math.round(U * (it.size || 3.4));
      const t = textbox(it.t, { ...td("body"), fontSize: size, width: w, fill: it.c || MUTED, ...(it.o || {}) });
      return [[t], est(it.t, size, w, 0.5, 1.4)]; },
    big: (it, w) => { const size = Math.round(U * (it.size || 22));
      const t = textbox(it.t, { ...td("heading"), fontSize: size, width: w, lineHeight: 0.92, fill: it.c || P, name: "Big number", ...(it.o || {}) });
      return [[t], est(it.t, size, w, 0.6, 0.92)]; },
    rule: (it) => [[box(0, 0, U * (it.w || 12), U * (it.h || 0.9), it.c || A, { name: "Accent line", rx: U * 0.45, ry: U * 0.45 })], U * (it.h || 0.9)],
    swash: (it) => [[line(SWASH, 0, 0, U * (it.w || 30), U * 5, it.c || A, U * 0.9, { name: "Underline" })], U * 3],
    cta: (it, w) => { const [bg, t, px, py] = button(it.t, { bg: { fill: it.fill || A, ...(it.bg || {}) }, text: { fill: it.c || PAPER } });
      const tw = it.fw ? w * it.fw - px * 2 : Math.min(w - px * 2, Math.max(t.fontSize * 4, it.t.length * t.fontSize * 0.62));   // sized to its words
      t.set({ width: tw, top: py, left: px }); bg.set({ width: tw + px * 2 }); return [[bg, t], bg.height]; },
    pill: (it) => { const size = Math.round(U * (it.size || 3)), w = it.t.length * size * 0.72 + size * 2.4, h = size * 2.1;
      return [[box(0, 0, w, h, it.fill || A, { rx: h / 2, ry: h / 2, name: "Label shape" }),
        textbox(it.t.toUpperCase(), { ...td("cta"), fontSize: size, charSpacing: 120, left: 0, top: (h - size * 1.1) / 2, width: w, textAlign: "center", fill: it.c || PAPER, name: "Label" })], h]; },
    stars: (it) => { const s = U * (it.size || 6); return [[0, 1, 2, 3, 4].map(i => shape("star", i * s * 1.15, 0, s, { fill: it.c || A, name: "Star" })), s]; },
    list: (it, w) => { const size = Math.round(U * (it.size || 4)), gap = size * (it.lg || 1.9), out = [];
      it.items.forEach((tx, i) => {
        const y = i * gap;
        if (it.mark === "num") { out.push(dot(size * 0.75, y + size * 0.62, size * 0.75, it.mc || A), textbox(String(i + 1), { ...td("cta"), fontSize: Math.round(size * 0.8), left: 0, top: y + size * 0.18, width: size * 1.5, textAlign: "center", fill: PAPER, name: "Number" })); }
        else if (it.mark === "dot") out.push(dot(size * 0.35, y + size * 0.62, size * 0.22, it.mc || A));
        else out.push(shape(it.mark || "check", 0, y + size * 0.1, size * 1.05, { fill: it.mc || A, name: "Tick" }));
        out.push(textbox(tx, { ...td("subhead"), fontWeight: it.weight || 500, fontSize: size, left: size * 2, top: y, width: w - size * 2, fill: it.c || INK }));
      });
      out.bw = Math.min(w, Math.max(...it.items.map(t => t.length)) * size * 0.52 + size * 2.4);   // so a list can sit centred
      return [out, (it.items.length - 1) * gap + size * 1.3]; },
  };
  // col: { x, y, w, h }; align: left | center | right; v: top | center | bottom. Each item: { k: kind, t: text, ..., after: space below in U }
  function flow(col, items, align = "left", v = "top") {
    const parts = []; let y = 0;
    items.filter(Boolean).forEach(it => {
      if (it.gap != null) { y += U * it.gap; return; }
      const w = col.w * (it.wf || 1), [objs, h] = MAKE[it.k](it, w);
      if (["h1", "script", "eyebrow", "sub", "body", "big"].includes(it.k)) objs.forEach(o => o.set({ left: 0, top: 0 }));
      parts.push({ objs, y, w, k: it.k, center: it.k !== "h1" && it.k !== "body" && it.k !== "sub" && it.k !== "eyebrow" && it.k !== "script" && it.k !== "big" && it.k !== "list" });
      y += h + U * (it.after != null ? it.after : 2.4);
    });
    const total = Math.max(0, y - U * 2.4);
    const off = v === "center" ? (col.h - total) / 2 : v === "bottom" ? col.h - total : 0;
    const out = [];
    parts.forEach(p => {
      // blocks with their own width (buttons, labels, stars, lines) are moved as a block; text boxes are aligned inside
      const bw = p.objs.bw ? p.objs.bw : p.center ? Math.max(...p.objs.map(o => (o.left || 0) + (o.width || 0) * (o.scaleX || 1))) : p.w;
      const bx = align === "center" ? col.x + (col.w - bw) / 2 : align === "right" ? col.x + col.w - bw : col.x;
      p.objs.forEach(o => {
        o.set({ left: (o.left || 0) + bx, top: (o.top || 0) + col.y + off + p.y });
        if (o.type === "textbox" && !p.center && p.k !== "list") o.set({ textAlign: align });
      });
      out.push(...p.objs);
    });
    return out;
  }
  // standard places for a layout's text and its picture, at any shape of page
  const M = 0.08;
  const full = (m = M) => ({ x: W * m, y: H * m, w: W * (1 - 2 * m), h: H * (1 - 2 * m) });
  const pic = (f = 0.5) => wide ? { vis: { x: W * (1 - f), y: 0, w: W * f, h: H }, txt: { x: W * 0.06, y: H * 0.1, w: W * (1 - f) - W * 0.11, h: H * 0.8 } }
    : { vis: { x: 0, y: 0, w: W, h: H * f }, txt: { x: W * M, y: H * f + H * 0.05, w: W * (1 - 2 * M), h: H * (1 - f) - H * 0.13 } };
  const NAME = (col, o = {}) => textbox(C.brand, { ...td("subhead"), fontSize: Math.round(U * 3.2), left: col.x, top: col.y, width: col.w, fill: P, name: "Practice name", ...o });

  const CATS = [["favorites", "★ Favourites"], ["all", "All"], ["seasonal", "Holidays & seasonal"], ["offers", "Special offers"],
                ["facts", "Fun facts & tips"], ["news", "Practice news"], ["patients", "Patients & reviews"], ["basics", "Basics"]];
  const T = {};
  const add = (key, name, cat, build, extra = {}) => { T[key] = { name, cat, build, ...extra }; };

  // ================================================================ basics
  add("blank", "Blank", "basics", () => ({ bg: PAPER, objs: [] }));
  add("photo", "Photo and headline", "basics", () => { const { vis, txt } = pic(0.58); return { bg: PAPER, objs: [
    photo(vis.x, vis.y, vis.w, vis.h, { q: "happy family smiling outdoors", own: "patient" }),
    ...flow(txt, [{ k: "eyebrow", t: "From our team", after: 1.4 }, { k: "h1", t: "Grateful for every smile", size: 8.5 }, { k: "body", t: "Thank you for trusting us with your family's smiles.", wf: 0.8 }]),
  ], logo: logoAt("br", 0.14) }; });
  add("bold", "Bold statement", "basics", () => ({ bg: P, objs: [
    layer(radial(tint(P, 0.12), shade(P, 0.25))), grain(0.05),
    ring(W * 0.5, H * 0.5, S * 0.44, tint(P, 0.25), U * 0.4), ring(W * 0.5, H * 0.5, S * 0.47, tint(P, 0.18), U * 0.25),
    ...flow(full(0.14), [{ k: "rule", w: 14, after: 4 }, { k: "h1", t: "Your smile, our favourite thing", size: 11, c: PAPER }, { k: "script", t: "always", size: 9, after: 0 }], "center", "center"),
    NAME({ x: 0, y: H * 0.88, w: W }, { textAlign: "center", fill: tint(P, 0.7) }),
  ] }));
  add("quote", "Big quote", "basics", () => ({ bg: CREAM, objs: [
    grain(0.05), shape("quote", W * 0.08, H * 0.1, S * 0.18, { fill: A, name: "Quote mark" }),
    ...flow({ x: W * 0.08, y: H * 0.3, w: W * 0.84, h: H * 0.5 }, [{ k: "h1", t: "A smile is the prettiest thing you can wear.", size: 8.5, c: PD }, { k: "swash", w: 26, after: 2 }, { k: "sub", t: "Our team" }]),
  ], logo: logoAt("br", 0.14) }));
  add("split", "Photo split", "basics", () => { const { vis, txt } = pic(0.5); return { bg: P, objs: [
    photo(vis.x, vis.y, vis.w, vis.h, { q: "modern dental clinic", own: "office" }), wash(vis.x, vis.y, vis.w, vis.h, P, 0.25),
    ...flow(txt, [{ k: "eyebrow", t: "Welcome", c: AL, after: 1.4 }, { k: "h1", t: "Smiles made here", size: 11, c: PAPER }, { k: "body", t: "Gentle, modern care for the whole family.", c: tint(P, 0.75) }, { k: "cta", t: "Book a visit" }], "left", "center"),
  ], logo: logoAt("tl", 0.13) }; });
  add("arch", "Arch photo", "basics", () => ({ bg: AXL, objs: [
    grain(0.05), dot(W * 0.82, H * 0.16, S * 0.12, AL), dot(W * 0.14, H * 0.6, S * 0.06, PL),
    photoShape("arch", W * 0.5 - S * 0.3, H * 0.07, S * 0.6, H * 0.5, { q: "happy child smiling", own: "patient" }),
    ...flow({ x: W * 0.1, y: H * 0.62, w: W * 0.8, h: H * 0.3 }, [{ k: "script", t: "hello", size: 9, after: 0 }, { k: "h1", t: "Smiles start here", size: 9 }, { k: "body", t: "We can't wait to meet you." }], "center"),
  ] }));
  add("collage", "Photo collage", "basics", () => {
    const g = U * 1.6, top = H * (wide ? 0.08 : 0.06), hh = H * (wide ? 0.84 : 0.58), w1 = W * (wide ? 0.32 : 0.5);
    return { bg: PAPER, objs: [
      photo(W * 0.06, top, w1 - g, hh, { rx: U * 2, ry: U * 2, q: "dental office", own: "team" }),
      photo(W * 0.06 + w1, top, W * 0.88 - w1, (hh - g) / 2, { rx: U * 2, ry: U * 2, tone: A, q: "coffee cups friends", own: "team", i: 1 }),
      photo(W * 0.06 + w1, top + (hh + g) / 2, W * 0.88 - w1, (hh - g) / 2, { rx: U * 2, ry: U * 2, q: "high five", own: "office", i: 2 }),
      ...(wide ? [] : flow({ x: W * 0.06, y: top + hh + H * 0.05, w: W * 0.88, h: H * 0.25 }, [{ k: "h1", t: "A week at the office", size: 8 }, { k: "body", t: "Smiles, high fives and a lot of laughs." }])),
    ], logo: logoAt("br", 0.13) };
  });
  add("minimal", "Minimal centred", "basics", () => ({ bg: PAPER, objs: [
    box(U * 3, U * 3, W - U * 6, H - U * 6, "transparent", { stroke: tint(P, 0.6), strokeWidth: U * 0.3, name: "Border" }),
    ...flow(full(0.16), [{ k: "eyebrow", t: C.brand, c: P, after: 3 }, { k: "h1", t: "Healthy smiles, happy lives", size: 9, c: INK }, { k: "rule", w: 10, after: 3 }, { k: "body", t: "Gentle care for kids, teens and adults." }], "center", "center"),
  ] }));
  add("poster", "Poster type", "basics", () => ({ bg: A, objs: [
    grain(0.06), patch("dots", 0, H * 0.78, W, H * 0.22, PAPER, U * 3, 0.35),
    ...flow({ x: W * 0.07, y: H * 0.08, w: W * 0.86, h: H * 0.7 }, [
      { k: "h1", t: "SMILE", size: 22, c: PAPER, after: 0, lh: 0.9 }, { k: "h1", t: "MORE", size: 22, c: P, outline: true, after: 0, lh: 0.9 }, { k: "h1", t: "WORRY", size: 22, c: PAPER, after: 0, lh: 0.9 }, { k: "h1", t: "LESS", size: 22, c: P, outline: true, lh: 0.9 },
    ]),
  ], logo: logoAt("br", 0.14) }));
  add("announce", "Big announcement", "basics", () => ({ bg: PD, objs: [
    photo(0, 0, W, H, { q: "confetti celebration" }), wash(0, 0, W, H, P, 0.6), layer(grad("rgba(0,0,0,0)", "rgba(0,0,0,0.55)", "down"), { name: "Shade" }),
    ...flow({ x: W * 0.08, y: H * 0.45, w: W * 0.84, h: H * 0.45 }, [{ k: "pill", t: "Big news", after: 2.4 }, { k: "h1", t: "Something exciting is coming", size: 10, c: PAPER }, { k: "body", t: "Stay tuned. You'll hear it here first.", c: tint(P, 0.85) }], "left", "bottom"),
  ], logo: logoAt("tl", 0.14) }));

  // ================================================================ holidays and seasonal (months: when they're offered first)
  add("newyear", "New Year", "seasonal", () => ({ bg: PDD, objs: [
    layer(radial(shade(P, 0.2), PDD)),
    burst(W * 0.2, H * 0.17, S * 0.15, 14, A), burst(W * 0.84, H * 0.13, S * 0.11, 12, tint(A, 0.4)), burst(W * 0.14, H * 0.8, S * 0.07, 12, tint(P, 0.5)),
    ...scatter(7, 14, (x, y, s) => shape("sparkle", x, y, s * 0.5, { fill: tint(A, 0.3) }), [{ x: 0.05, y: 0.28, w: 0.9, h: 0.5 }, { x: 0, y: 0.84, w: 1, h: 0.16 }]),
    ...flow(full(0.12), [{ k: "script", t: "Cheers to", size: 9, after: 0 }, { k: "h1", t: "a new year", size: 13, c: PAPER }, { k: "rule", w: 12, after: 2.6 }, { k: "sub", t: "of healthy, happy smiles", c: tint(A, 0.25) }], "center", "center"),
    NAME({ x: 0, y: H * 0.89, w: W }, { textAlign: "center", fill: tint(P, 0.65) }),
  ] }), { months: [12, 1] });
  add("resolutions", "New Year's resolutions", "seasonal", () => ({ bg: CREAM, objs: [
    grain(0.05), patch("grid", W * 0.06, H * 0.06, W * 0.88, H * 0.88, P, U * 5, 0.1),
    ...flow({ x: W * 0.12, y: H * 0.1, w: W * 0.76, h: H * 0.8 }, [{ k: "script", t: "Smile resolutions", size: 9, after: 2 },
      { k: "list", items: ["Brush for two full minutes", "Floss every night", "Wear elastics as directed", "Keep every check-up"], size: 4.4, mc: P },
      { gap: 2 }, { k: "body", t: "Which one are you starting with?" }], "left", "center"),
    shape("sparkle", W * 0.84, H * 0.1, S * 0.08, { fill: A }),
  ], logo: logoAt("br", 0.13) }), { months: [12, 1] });
  add("valentine", "Valentine's Day", "seasonal", () => ({ bg: AXL, objs: [
    texture("dots", A, U * 4, 0.18),
    ...scatter(11, 10, (x, y, s, r) => shape("heart", x, y, s, { fill: r() < 0.5 ? A : tint(P, 0.35), angle: (r() - 0.5) * 40 }), [{ x: 0.06, y: 0.26, w: 0.88, h: 0.5 }]),
    artBox("watercolor heart", W * 0.66, H * 0.04, S * 0.3, S * 0.3, 0, { angle: 12 }),
    box(W * 0.1, H * 0.28, W * 0.8, H * 0.44, PAPER, { rx: U * 3, ry: U * 3, shadow: shadow(), name: "Card" }),
    ...flow({ x: W * 0.14, y: H * 0.28, w: W * 0.72, h: H * 0.44 }, [{ k: "script", t: "We love", size: 8, after: 0 }, { k: "h1", t: "your smile", size: 11, c: PD }, { k: "sub", t: "Happy Valentine's Day!", size: 4 }], "center", "center"),
  ], logo: logoAt("br", 0.13) }), { months: [1, 2] });
  add("valentine2", "Valentine photo heart", "seasonal", () => ({ bg: tint(A, 0.86), objs: [
    grain(0.05), photoShape("heart", W * 0.5 - S * 0.3, H * 0.08, S * 0.6, S * 0.56, { q: "pink roses" }),
    shape("heart", W * 0.5 + S * 0.2, H * 0.06, S * 0.1, { fill: A }), shape("sparkle", W * 0.5 - S * 0.34, H * 0.1 + S * 0.42, S * 0.07, { fill: P }),
    ...flow({ x: W * 0.08, y: H * 0.1 + S * 0.62, w: W * 0.84, h: H * 0.3 }, [{ k: "h1", t: "You make us smile", size: 9, c: PD }, { k: "body", t: "Happy Valentine's Day from all of us." }], "center"),
  ] }), { months: [1, 2] });
  add("kidsmonth", "Children's Dental Health Month", "seasonal", () => ({ bg: PL, objs: [
    texture("plus", P, U * 6, 0.15),
    shape("tooth", W * 0.5 - S * 0.14, H * 0.07, S * 0.28, { fill: PAPER, stroke: P, strokeWidth: 3, shadow: shadow(2, 0.6, 0.12), name: "Tooth" }),
    line(SHAPE.smile, W * 0.5 - S * 0.06, H * 0.07 + S * 0.1, S * 0.12, S * 0.06, P, U * 0.6, { name: "Smile" }),
    ...[[0.18, 0.12], [0.8, 0.2], [0.2, 0.32]].map(([x, y]) => shape("star", W * x, H * y, S * 0.06, { fill: A })),
    ...flow({ x: W * 0.1, y: H * 0.08 + S * 0.34, w: W * 0.8, h: H * 0.5 }, [{ k: "pill", t: "February is", after: 2.4 }, { k: "h1", t: "National Children's Dental Health Month", size: 7.5, c: PD }, { k: "body", t: "Healthy habits start young. Brush twice a day, floss daily and see us twice a year." }], "center"),
  ], logo: logoAt("br", 0.12) }), { months: [1, 2] });
  add("stpatricks", "St. Patrick's Day", "seasonal", () => ({ bg: PAPER, objs: [
    texture("gingham", P, U * 8, 0.12),
    ...scatter(17, 9, (x, y, s, r) => shape("shamrock", x, y, s * 1.2, { fill: r() < 0.6 ? P : tint(P, 0.45), angle: (r() - 0.5) * 50 }), [{ x: 0.08, y: 0.26, w: 0.84, h: 0.52 }]),
    artBox("shamrock", W * 0.74, H * 0.74, S * 0.24, S * 0.24, 0, { angle: 14 }),
    dot(W * 0.5, H * 0.52, S * 0.3, PAPER, { shadow: shadow(), name: "Badge" }),
    ...flow({ x: W * 0.5 - S * 0.26, y: H * 0.52 - S * 0.26, w: S * 0.52, h: S * 0.52 }, [{ k: "script", t: "lucky", size: 9, after: 0 }, { k: "h1", t: "to have you", size: 7.5 }, { k: "eyebrow", t: "Happy St. Patrick's Day", size: 2.4 }], "center", "center"),
  ], logo: logoAt("br", 0.12) }), { months: [3] });
  add("spring", "Spring", "seasonal", () => { const { vis, txt } = pic(0.5); return { bg: PXL, objs: [
    photoShape(wide ? "blob" : "archflip", vis.x + vis.w * 0.08, vis.y + (wide ? vis.h * 0.1 : 0), vis.w * 0.84, vis.h * (wide ? 0.8 : 0.95), { q: "spring blossoms" }),
    ...[[0.06, 0.06], [0.82, 0.38], [0.14, 0.42]].map(([x, y], i) => art(shape("flower", W * x, H * y, S * (0.1 + i * 0.02), { fill: i % 2 ? A : tint(A, 0.4), name: "Flower" }), "watercolor flower", i % 2)),
    ...flow(txt, [{ k: "script", t: "Spring into", size: 8, after: 0 }, { k: "h1", t: "a brighter smile", size: 9.5 }, { k: "body", t: "Book a spring check-up and start the season fresh." }, { k: "cta", t: "Book now" }], wide ? "left" : "center", "center"),
  ], logo: logoAt("br", 0.12) }; }, { months: [3, 4] });
  add("easter", "Easter", "seasonal", () => ({ bg: CREAM, objs: [
    texture("dots", P, U * 5, 0.12),
    ...[[0.1, P], [0.29, A], [0.48, tint(P, 0.45)], [0.67, tint(A, 0.35)]].flatMap(([x, c], i) => [
      shape("egg", W * x, H * (0.62 + (i % 2) * 0.05), S * 0.2, { fill: c, angle: i % 2 ? 8 : -8, name: "Egg" }),
      line(SQUIGGLE, W * x + S * 0.03, H * (0.62 + (i % 2) * 0.05) + S * 0.1, S * 0.14, S * 0.04, PAPER, U * 0.5, { angle: i % 2 ? 8 : -8 })]),
    ...flow({ x: W * 0.08, y: H * 0.1, w: W * 0.84, h: H * 0.44 }, [{ k: "script", t: "Hoppy", size: 10, after: 0 }, { k: "h1", t: "Easter!", size: 13 }, { k: "body", t: "Wishing you a sweet (but not too sweet) holiday." }], "center", "center"),
  ], logo: logoAt("br", 0.12) }), { months: [3, 4] });
  add("eggcellent", "Easter egg hunt", "seasonal", () => ({ bg: tint(P, 0.9), objs: [
    patch("stripes", 0, H * 0.82, W, H * 0.18, P, U * 4, 0.2),
    ...flow(full(0.1), [{ k: "eyebrow", t: "This Saturday", after: 1.6 }, { k: "h1", t: "Egg-cellent egg hunt", size: 10, c: PD }, { k: "body", t: "Find the golden egg hidden in our office and win a prize. All week long!" }, { k: "cta", t: "Bring the kids" }], "left", "center"),
    shape("egg", W * 0.74, H * 0.08, S * 0.18, { fill: A, angle: 12 }), shape("egg", W * 0.84, H * 0.2, S * 0.12, { fill: tint(P, 0.4), angle: -14 }),
  ], logo: logoAt("br", 0.12) }), { months: [3, 4] });
  add("mothersday", "Mother's Day", "seasonal", () => ({ bg: tint(A, 0.86), objs: [
    grain(0.05), photoCircle(W * 0.5, H * 0.08 + S * 0.24, S * 0.24, { q: "mother and child" }),
    ring(W * 0.5, H * 0.08 + S * 0.24, S * 0.27, A, U * 0.4),
    shape("flower", W * 0.5 + S * 0.15, H * 0.08 + S * 0.36, S * 0.13, { fill: A, name: "Flower" }),
    ...flow({ x: W * 0.1, y: H * 0.08 + S * 0.56, w: W * 0.8, h: H * 0.35 }, [{ k: "script", t: "Happy", size: 8, after: 0 }, { k: "h1", t: "Mother's Day", size: 10, c: PD }, { k: "body", t: "To the moms who remind us to brush: thank you." }], "center"),
  ], logo: logoAt("br", 0.12) }), { months: [5] });
  add("mothersday2", "Mother's Day collage", "seasonal", () => ({ bg: PAPER, objs: [
    texture("dots", A, U * 4, 0.2),
    box(W * 0.2, H * 0.1, W * 0.34, H * 0.34, tint(P, 0.7), { angle: -8, name: "Card" }),
    ...polaroid(W * 0.44, H * 0.08, W * 0.4, H * 0.32, { q: "mother daughter hug" }), ...polaroid(W * 0.12, H * 0.18, W * 0.34, H * 0.26, { tone: A, q: "flowers bouquet" }),
    ...flow({ x: W * 0.08, y: H * 0.6, w: W * 0.84, h: H * 0.3 }, [{ k: "h1", t: "Moms make the best smiles", size: 8, c: PD }, { k: "body", t: "Tag a mom who deserves a day off today." }], "center"),
  ], logo: logoAt("br", 0.12) }), { months: [5] });
  add("smileday", "National Smile Day", "seasonal", () => ({ bg: AXL, objs: [
    texture("dots", A, U * 4, 0.2),
    burst(W * 0.5, H * 0.25, S * 0.22, 18, A, U * 1.1, 0.68), dot(W * 0.5, H * 0.25, S * 0.15, A, { name: "Sun" }),
    line(SHAPE.smile, W * 0.5 - S * 0.07, H * 0.25 - S * 0.01, S * 0.14, S * 0.08, PAPER, U * 0.9, { name: "Smile" }),
    dot(W * 0.5 - S * 0.05, H * 0.25 - S * 0.045, S * 0.02, PAPER, { name: "Eye" }), dot(W * 0.5 + S * 0.05, H * 0.25 - S * 0.045, S * 0.02, PAPER, { name: "Eye" }),
    ...flow({ x: W * 0.1, y: H * 0.25 + S * 0.26, w: W * 0.8, h: H * 0.4 }, [{ k: "pill", t: "May 31", fill: P, after: 2.4 }, { k: "h1", t: "Happy National Smile Day", size: 9, c: PD }, { k: "body", t: "Show us yours! Tag us in your best smile today." }], "center"),
  ] }), { months: [5] });
  add("graduation", "Congrats, grads!", "seasonal", () => ({ bg: PD, objs: [
    ...confetti(19, 20, [{ x: 0.06, y: 0.24, w: 0.88, h: 0.6 }], [A, tint(A, 0.4), PAPER, tint(P, 0.4)]),
    ...flow(full(0.12), [{ k: "eyebrow", t: "Class of this year", c: AL, after: 1.6 }, { k: "h1", t: "Congrats, grads!", size: 12, c: PAPER }, { k: "script", t: "your smile looks great in a cap and gown", size: 5.5, c: AL }], "center", "center"),
  ], logo: logoAt("br", 0.12) }), { months: [5, 6] });
  add("memorialday", "Memorial Day", "seasonal", () => ({ bg: PAPER, objs: [
    patch("stripes", 0, 0, W, H * 0.06, P, U * 3, 0.5), patch("stripes", 0, H * 0.94, W, H * 0.06, P, U * 3, 0.5),
    ...[0.3, 0.5, 0.7].map(x => shape("star", W * x - S * 0.035, H * 0.2, S * 0.07, { fill: A })),
    ...flow(full(0.14), [{ k: "h1", t: "Remembering and honouring", size: 8.5, c: PD }, { k: "rule", w: 10, after: 3 }, { k: "body", t: "Our office is closed Monday for Memorial Day. We'll see you Tuesday." }], "center", "center"),
  ], logo: logoAt("br", 0.12) }), { months: [5] });
  add("fathersday", "Father's Day", "seasonal", () => { const { vis, txt } = pic(0.55); return { bg: PDD, objs: [
    photo(vis.x, vis.y, vis.w, vis.h, { q: "father and son outdoors" }), wash(vis.x, vis.y, vis.w, vis.h, P, 0.35), patch("stripes", txt.x, txt.y - U * 2, U * 18, U * 2.2, A, U * 2, 0.7),
    ...flow({ ...txt, y: txt.y + U * 3 }, [{ k: "h1", t: "Happy Father's Day", size: 10, c: PAPER }, { k: "body", t: "Here's to the dads who never miss a check-up.", c: tint(P, 0.75) }], "left", "center"),
  ], logo: logoAt("br", 0.12) }; }, { months: [6] });
  add("summer", "Summer smiles", "seasonal", () => ({ bg: tint(A, 0.85), objs: [
    burst(W * 0.84, H * 0.13, S * 0.14, 16, A, U * 0.8, 0.6), dot(W * 0.84, H * 0.13, S * 0.07, A, { name: "Sun" }),
    path(SHAPE.wave, 0, H * 0.74, W * 1.2, H * 0.3, { fill: tint(P, 0.45), name: "Wave" }), path(SHAPE.wave, -W * 0.2, H * 0.8, W * 1.4, H * 0.24, { fill: P, name: "Wave" }),
    patch("waves", 0, H * 0.86, W, H * 0.14, PAPER, U * 5, 0.3),
    ...flow({ x: W * 0.08, y: H * 0.22, w: W * 0.84, h: H * 0.45 }, [{ k: "script", t: "Sun's out", size: 8, after: 0 }, { k: "h1", t: "smiles out", size: 13 }, { k: "body", t: "Enjoy the break and keep brushing!", c: INK }], "left", "center"),
  ] }), { months: [6, 7, 8] });
  add("summerhours", "Summer hours", "seasonal", () => ({ bg: PAPER, objs: [
    texture("stripes", A, U * 10, 0.1), box(W * 0.08, H * 0.08, W * 0.84, H * 0.84, PAPER, { rx: U * 3, ry: U * 3, shadow: shadow(), name: "Card" }),
    ...flow({ x: W * 0.14, y: H * 0.12, w: W * 0.72, h: H * 0.76 }, [{ k: "script", t: "Summer", size: 9, after: 0 }, { k: "h1", t: "office hours", size: 9 }, { k: "rule", after: 3 },
      { k: "list", items: ["Monday to Thursday: 7 to 4", "Friday: Closed", "Back to regular hours in September"], mark: "dot", size: 3.8 }], "left", "center"),
  ], logo: logoAt("br", 0.12) }), { months: [5, 6] });
  add("july4", "4th of July", "seasonal", () => ({ bg: PDD, objs: [
    layer(radial(shade(P, 0.25), PDD)),
    burst(W * 0.22, H * 0.2, S * 0.17, 16, A), burst(W * 0.8, H * 0.15, S * 0.13, 14, PAPER), burst(W * 0.16, H * 0.86, S * 0.08, 12, tint(A, 0.4)),
    ...scatter(23, 12, (x, y, s) => shape("star", x, y, s * 0.55, { fill: PAPER, opacity: 0.85 }), [{ x: 0.04, y: 0.32, w: 0.92, h: 0.46 }]),
    ...flow({ x: W * 0.08, y: H * 0.34, w: W * 0.84, h: H * 0.42 }, [{ k: "script", t: "Happy", size: 8, after: 0 }, { k: "h1", t: "4th of July", size: 13, c: PAPER }, { k: "sub", t: "Our office is closed today. Have a safe, happy holiday!", c: tint(A, 0.25), size: 4 }], "center", "center"),
  ] }), { months: [7] });
  add("backtoschool", "Back to school", "seasonal", () => ({ bg: PAPER, objs: [
    texture("grid", P, U * 6, 0.12), box(0, 0, W * 0.04, H, A, { name: "Margin band" }),
    art(shape("apple", W * 0.74, H * 0.06, S * 0.18, { fill: RED, name: "Apple" }), "red apple"), art(shape("pencil", W * 0.78, H * 0.72, S * 0.16, { fill: A, name: "Pencil" }), "pencil"),
    ...flow({ x: W * 0.12, y: H * 0.14, w: W * 0.72, h: H * 0.68 }, [{ k: "script", t: "Back to school,", size: 7, hand: true, after: 0 }, { k: "h1", t: "back to smiles", size: 11 }, { k: "body", t: "Book an after-school appointment before the calendar fills up.", c: INK }, { k: "cta", t: "Book after school" }], "left", "center"),
  ], logo: logoAt("br", 0.12) }), { months: [8, 9] });
  add("schoollist", "Back-to-school checklist", "seasonal", () => ({ bg: PL, objs: [
    texture("dots", P, U * 4, 0.12),
    box(W * 0.1, H * 0.08, W * 0.8, H * 0.84, PAPER, { rx: U * 2, ry: U * 2, shadow: shadow(), name: "Clipboard" }),
    box(W * 0.38, H * 0.06, W * 0.24, U * 5, P, { rx: U * 1.2, ry: U * 1.2, name: "Clip" }),
    ...flow({ x: W * 0.16, y: H * 0.14, w: W * 0.68, h: H * 0.72 }, [{ k: "h1", t: "Braces back-to-school kit", size: 7, c: PD },
      { k: "list", items: ["Travel toothbrush", "Floss picks or threaders", "Orthodontic wax", "Spare elastics", "Lip balm"], size: 3.8, mc: A }], "left", "center"),
  ], logo: logoAt("br", 0.11) }), { months: [8, 9] });
  add("fall", "Fall", "seasonal", () => ({ bg: CREAM, objs: [
    grain(0.06),
    ...scatter(43, 8, (x, y, s, r) => shape(r() < 0.5 ? "maple" : "leaf", x, y, s * 0.8, { fill: [A, shade(A, 0.2), tint(A, 0.3), tint(P, 0.3)][Math.floor(r() * 4)], angle: r() * 360 }), [{ x: 0.06, y: 0.24, w: 0.88, h: 0.54 }]),
    artBox("autumn leaf", W * 0.72, -S * 0.06, S * 0.36, S * 0.36, 0, { angle: 0 }), artBox("autumn leaf", -S * 0.08, H - S * 0.3, S * 0.36, S * 0.36, 1),
    ...flow({ x: W * 0.1, y: H * 0.26, w: W * 0.8, h: H * 0.5 }, [{ k: "script", t: "Fall in love", size: 8, after: 0 }, { k: "h1", t: "with your smile", size: 11 }, { k: "body", t: "Autumn appointments are filling fast. Book yours today." }], "center", "center"),
  ], logo: logoAt("br", 0.12) }), { months: [9, 10] });
  add("hygienemonth", "Dental Hygiene Month", "seasonal", () => ({ bg: PAPER, objs: [
    texture("waves", P, U * 6, 0.1),
    ...flow({ x: W * 0.08, y: H * 0.08, w: W * 0.84, h: H * 0.84 }, [{ k: "pill", t: "October is", fill: P, after: 2.6 }, { k: "h1", t: "National Dental Hygiene Month", size: 8.5, c: PD },
      { k: "list", items: ["Brush 2 minutes, twice a day", "Floss once a day", "Rinse after snacks", "Chew sugar-free gum"], mark: "num", size: 4, mc: A }], "left", "center"),
  ], logo: logoAt("br", 0.12) }), { months: [10] });
  add("orthomonth", "Orthodontic Health Month", "seasonal", () => ({ bg: P, objs: [
    layer(grad(tint(P, 0.08), shade(P, 0.3))), texture("plus", PAPER, U * 7, 0.12),
    ...flow(full(0.1), [{ k: "eyebrow", t: "October is", c: AL, after: 1.8 }, { k: "h1", t: "National Orthodontic Health Month", size: 9.5, c: PAPER }, { k: "body", t: "A great time to ask whether your child is ready for their first orthodontic check. Experts recommend one by age 7.", c: tint(P, 0.78) }, { k: "cta", t: "Book a free consult" }], "left", "center"),
  ], logo: logoAt("br", 0.12) }), { months: [10] });
  add("halloween", "Halloween", "seasonal", () => ({ bg: PDD, objs: [
    layer(radial(shade(P, 0.4), shade(P, 0.75))),
    shape("moon", W * 0.74, H * 0.06, S * 0.18, { fill: AXL, name: "Moon" }),
    art(shape("pumpkin", W * 0.04, H * 0.7, S * 0.26, { fill: A }), "pumpkin", 0), art(shape("pumpkin", W * 0.72, H * 0.74, S * 0.2, { fill: tint(A, 0.25) }), "pumpkin", 1),
    ...scatter(31, 8, (x, y, s) => shape("star", x, y, s * 0.45, { fill: tint(A, 0.5), opacity: 0.8 }), [{ x: 0.05, y: 0.2, w: 0.9, h: 0.5 }]),
    ...flow({ x: W * 0.08, y: H * 0.2, w: W * 0.84, h: H * 0.5 }, [{ k: "script", t: "Brace yourself,", size: 7, c: AL, after: 0 }, { k: "h1", t: "it's Halloween!", size: 11, c: PAPER }, { k: "body", t: "Skip the sticky, chewy and hard candy. Chocolate that melts is the braces-friendly treat.", c: tint(P, 0.78) }], "left", "center"),
  ], logo: logoAt("tl", 0.12) }), { months: [10] });
  add("candybuyback", "Candy buy-back", "seasonal", () => ({ bg: A, objs: [
    texture("stripes", PAPER, U * 8, 0.12),
    shape("seal", W * 0.62, H * 0.06, S * 0.3, { fill: P, angle: 10, shadow: shadow(2, 0.8, 0.2), name: "Badge" }),
    textbox("Nov\n1–6", { ...td("heading"), fontSize: Math.round(S * 0.055), lineHeight: 1, left: W * 0.62, top: H * 0.06 + S * 0.1, width: S * 0.3, textAlign: "center", fill: PAPER, angle: 10, name: "Badge text" }),
    ...flow({ x: W * 0.08, y: H * 0.36, w: W * 0.84, h: H * 0.52 }, [{ k: "h1", t: "Halloween candy buy-back", size: 9.5, c: PAPER }, { k: "body", t: "Bring your extra candy to our office. We'll trade it for prizes and send it to the troops.", c: PDD }, { k: "cta", t: "Learn more", fill: P }]),
  ] }), { months: [10, 11] });
  add("thanksgiving", "Thanksgiving", "seasonal", () => ({ bg: CREAM, objs: [
    grain(0.06),
    ...corners("autumn leaves", 0.4),
    ...flow({ x: W * 0.1, y: H * 0.28, w: W * 0.8, h: H * 0.46 }, [{ k: "script", t: "Thankful", size: 11, after: 0 }, { k: "h1", t: "for you", size: 11 }, { k: "sub", t: "Happy Thanksgiving from our family to yours", size: 4 }], "center", "center"),
  ], logo: logoAt("br", 0.12) }), { months: [11] });
  add("grateful", "Grateful photo", "seasonal", () => { const { vis, txt } = pic(0.55); return { bg: PAPER, objs: [
    photo(vis.x, vis.y, vis.w, vis.h, { q: "autumn table setting" }), wash(vis.x, vis.y, vis.w, vis.h, A, 0.25),
    shape("maple", txt.x + txt.w - S * 0.12, txt.y, S * 0.12, { fill: A, angle: 18 }),
    ...flow(txt, [{ k: "script", t: "grateful", size: 10, after: 0 }, { k: "h1", t: "for every smile", size: 8.5 }, { k: "body", t: "Thank you for letting us be part of your family's story." }], "left", "center"),
  ], logo: logoAt("br", 0.12) }; }, { months: [11] });
  add("smilefriday", "Black Friday special", "seasonal", () => ({ bg: INK, objs: [
    texture("grain", "#FFFFFF", U * 6, 0.05),
    ...flow(full(0.1), [{ k: "pill", t: "This weekend only", after: 3 }, { k: "h1", t: "BLACK", size: 17, c: PAPER, after: 0, lh: 0.9 }, { k: "h1", t: "FRIDAY", size: 17, c: A, outline: true, lh: 0.9 }, { k: "sub", t: "$500 off full treatment when you start by Monday", c: PAPER, size: 4 }, { k: "cta", t: "Book your consult" }], "left", "center"),
  ], logo: logoAt("br", 0.12) }), { months: [11] });
  add("fsa", "Use your FSA/HSA", "seasonal", () => ({ bg: PL, objs: [
    texture("dots", P, U * 4, 0.12), shape("calendar", W * 0.7, H * 0.08, S * 0.2, { fill: PAPER, stroke: P, strokeWidth: 4, name: "Calendar" }),
    box(W * 0.7, H * 0.08, S * 0.2, S * 0.05, P, { name: "Calendar top" }),
    textbox("31", { ...td("heading"), fontSize: Math.round(S * 0.08), left: W * 0.7, top: H * 0.08 + S * 0.07, width: S * 0.2, textAlign: "center", fill: P, name: "Day" }),
    ...flow({ x: W * 0.08, y: H * 0.3, w: W * 0.84, h: H * 0.58 }, [{ k: "eyebrow", t: "Before December 31", after: 1.6 }, { k: "h1", t: "Use it or lose it!", size: 10, c: PD }, { k: "body", t: "FSA and HSA dollars can often go toward orthodontic treatment. Check your plan and book before the year ends." }, { k: "cta", t: "Ask our team", fill: P }]),
  ], logo: logoAt("br", 0.12) }), { months: [11, 12] });
  add("holidays", "Happy holidays", "seasonal", () => ({ bg: P, objs: [
    layer(radial(tint(P, 0.1), shade(P, 0.35))),
    ...scatter(53, 8, (x, y, s) => snowflake(x + s / 2, y + s / 2, s * 0.5, tint(P, 0.55)), [{ x: 0.04, y: 0.28, w: 0.92, h: 0.46 }, { x: 0, y: 0.82, w: 1, h: 0.18 }]),
    artBox("christmas ornament", W * 0.1, -S * 0.02, S * 0.22, S * 0.3, 0), artBox("christmas ornament", W * 0.72, -S * 0.04, S * 0.2, S * 0.28, 1),
    ...flow({ x: W * 0.08, y: H * 0.3, w: W * 0.84, h: H * 0.42 }, [{ k: "script", t: "Happy", size: 9, c: AL, after: 0 }, { k: "h1", t: "holidays", size: 14, c: PAPER }, { k: "sub", t: "From our family to yours", c: tint(A, 0.25), size: 4 }], "center", "center"),
    NAME({ x: 0, y: H * 0.88, w: W }, { textAlign: "center", fill: tint(P, 0.7) }),
  ] }), { months: [12] });
  add("seasons", "Season's greetings card", "seasonal", () => ({ bg: tint(P, 0.9), objs: [
    texture("gingham", P, U * 7, 0.14),
    ...polaroid(W * 0.5 - S * 0.3, H * 0.07, S * 0.6, S * 0.46, { q: "christmas tree lights" }),
    shape("gift", W * 0.5 + S * 0.2, H * 0.07 + S * 0.42, S * 0.16, { fill: A, angle: 8, name: "Gift" }),
    ...flow({ x: W * 0.08, y: H * 0.1 + S * 0.66, w: W * 0.84, h: H * 0.25 }, [{ k: "script", t: "Season's greetings", size: 8, c: PD, after: 1 }, { k: "body", t: "Warm wishes from all of us." }], "center"),
  ] }), { months: [12] });
  add("sweaterday", "Ugly sweater day", "seasonal", () => ({ bg: shade(A, 0.15), objs: [
    patch("checker", 0, 0, W, H * 0.08, PAPER, U * 4, 0.6), patch("checker", 0, H * 0.92, W, H * 0.08, PAPER, U * 4, 0.6),
    ...scatter(59, 10, (x, y, s) => snowflake(x + s / 2, y + s / 2, s * 0.4, PAPER), [{ x: 0.04, y: 0.25, w: 0.92, h: 0.52 }, { x: 0, y: 0, w: 1, h: 0.1 }, { x: 0, y: 0.9, w: 1, h: 0.1 }]),
    ...flow({ x: W * 0.08, y: H * 0.26, w: W * 0.84, h: H * 0.5 }, [{ k: "h1", t: "Ugly sweater day", size: 11, c: PAPER }, { k: "script", t: "come see our team in their finest", size: 6, c: PDD }], "center", "center"),
  ], logo: logoAt("br", 0.12) }), { months: [12] });
  add("holidayhours", "Holiday hours", "seasonal", () => ({ bg: PAPER, objs: [
    box(0, 0, W, H * 0.32, P, { name: "Header band" }), patch("plus", 0, 0, W, H * 0.32, PAPER, U * 6, 0.12),
    snowflake(W * 0.86, H * 0.1, S * 0.06, tint(P, 0.5)), snowflake(W * 0.74, H * 0.22, S * 0.035, tint(P, 0.4)),
    ...flow({ x: W * 0.08, y: H * 0.04, w: W * 0.6, h: H * 0.26 }, [{ k: "script", t: "Holiday", size: 7, c: AL, after: 0 }, { k: "h1", t: "office hours", size: 8.5, c: PAPER }], "left", "center"),
    ...flow({ x: W * 0.08, y: H * 0.38, w: W * 0.84, h: H * 0.44 }, [{ k: "list", items: ["Dec 24: Closed", "Dec 25: Closed", "Dec 31: Open 8 to 12", "Jan 1: Closed"], mark: "dot", size: 4.4, lg: 1.8 }, { gap: 2 }, { k: "body", t: "Thank you for being part of our family." }]),
  ], logo: logoAt("br", 0.13) }), { months: [11, 12] });
  add("winter", "Winter", "seasonal", () => ({ bg: tint(P, 0.9), objs: [
    texture("dots", PAPER, U * 5, 0.8),
    snowflake(W * 0.18, H * 0.15, S * 0.11, tint(P, 0.4)), snowflake(W * 0.85, H * 0.25, S * 0.07, P), snowflake(W * 0.78, H * 0.84, S * 0.09, tint(P, 0.5)),
    ...flow({ x: W * 0.08, y: H * 0.3, w: W * 0.84, h: H * 0.5 }, [{ k: "script", t: "Cold outside,", size: 7, after: 0 }, { k: "h1", t: "warm smiles inside", size: 10 }, { k: "body", t: "Tip: cold air can make teeth sensitive. Breathe through your nose and ask us about sensitivity toothpaste." }], "left", "center"),
  ], logo: logoAt("br", 0.12) }), { months: [1, 2] });

  // ================================================================ special offers
  add("offer", "Free consult", "offers", () => ({ bg: CREAM, objs: [
    grain(0.05), dot(W - S * 0.1, S * 0.12, S * 0.28, A, { opacity: 0.9, name: "Circle" }), ring(W - S * 0.1, S * 0.12, S * 0.35, A, U * 0.4),
    ...flow({ x: W * 0.08, y: H * 0.3, w: W * (wide ? 0.55 : 0.78), h: H * 0.62 }, [{ k: "pill", t: "Limited time", fill: P, after: 2.4 }, { k: "h1", t: "Free consults all month", size: 10 }, { k: "body", t: "Meet the doctor, get a plan and see your options. No pressure.", wf: 0.9 }, { k: "cta", t: "Book a free consult" }], "left", "center"),
  ], logo: logoAt("tl", 0.16) }));
  add("percent", "Percent off", "offers", () => ({ bg: P, objs: [
    layer(grad(tint(P, 0.08), shade(P, 0.3))), texture("dots", PAPER, U * 4, 0.1),
    shape("burst", W * 0.5 - S * 0.25, H * 0.08, S * 0.5, { fill: A, shadow: shadow(3, 1.2, 0.25), name: "Starburst" }),
    textbox("20%\nOFF", { ...td("heading"), fontSize: Math.round(S * 0.1), lineHeight: 0.95, left: W * 0.5 - S * 0.25, top: H * 0.08 + S * 0.15, width: S * 0.5, textAlign: "center", fill: PAPER, name: "Offer" }),
    ...flow({ x: W * 0.1, y: H * 0.1 + S * 0.54, w: W * 0.8, h: H * 0.3 }, [{ k: "h1", t: "Clear aligner treatment", size: 7.5, c: PAPER }, { k: "body", t: "Limited time. Ask about our new patient special.", c: tint(P, 0.78) }], "center"),
  ] }));
  add("monthly", "Monthly payments", "offers", () => ({ bg: PAPER, objs: [
    texture("grid", P, U * 6, 0.08),
    box(W * 0.08, H * 0.08, W * 0.84, H * 0.42, grad(P, shade(P, 0.3)), { rx: U * 3, ry: U * 3, shadow: shadow(), name: "Panel" }),
    ...flow({ x: W * 0.1, y: H * 0.08, w: W * 0.8, h: H * 0.42 }, [{ k: "eyebrow", t: "Braces from", c: AL, after: 1 }, { k: "big", t: "$99/mo", size: 16, c: PAPER, after: 1.4 }, { k: "body", t: "Flexible plans. 0% interest.", c: tint(P, 0.8) }], "center", "center"),
    ...flow({ x: W * 0.08, y: H * 0.56, w: W * 0.84, h: H * 0.34 }, [{ k: "h1", t: "A great smile fits your budget", size: 7 }, { k: "cta", t: "Ask about payment plans" }]),
  ], logo: logoAt("br", 0.12) }));
  add("referral", "Refer a friend", "offers", () => ({ bg: tint(A, 0.85), objs: [
    texture("dots", A, U * 4, 0.2), ...confetti(29, 12, [{ x: 0.06, y: 0.3, w: 0.88, h: 0.62 }]),
    art(shape("gift", W * 0.5 - S * 0.15, H * 0.06, S * 0.3, { fill: P, shadow: shadow(2, 0.8), name: "Gift" }), "gift box"),
    ...flow({ x: W * 0.1, y: H * 0.08 + S * 0.34, w: W * 0.8, h: H * 0.5 }, [{ k: "script", t: "Share the smile", size: 7, after: 0 }, { k: "h1", t: "Refer a friend, get a $50 gift card", size: 8, c: PD }, { k: "body", t: "When someone you send our way starts treatment, we'll say thanks." }, { k: "cta", t: "Ask at your next visit", fill: P }], "center"),
  ] }));
  add("limited", "Limited time", "offers", () => ({ bg: PDD, objs: [
    layer(grad(shade(P, 0.3), shade(P, 0.7))), patch("stripes", W * 0.6, 0, W * 0.4, H, A, U * 6, 0.12),
    shape("ticket", W * 0.58, H * 0.08, W * 0.34, { fill: A, name: "Ticket" }, U * 12),
    textbox("ENDS NOV 30", { ...td("cta"), fontSize: Math.round(U * 3.4), left: W * 0.58, top: H * 0.08 + U * 4, width: W * 0.34, textAlign: "center", fill: PAPER, name: "Badge text" }),
    ...flow({ x: W * 0.08, y: H * 0.3, w: W * 0.84, h: H * 0.6 }, [{ k: "h1", t: "Limited spots this month", size: 11, c: PAPER }, { k: "body", t: "Start treatment by the end of the month and your first adjustment is on us.", c: tint(P, 0.75) }, { k: "cta", t: "Claim your spot" }], "left", "center"),
  ], logo: logoAt("br", 0.12) }));
  add("giveaway", "Giveaway", "offers", () => ({ bg: PAPER, objs: [
    texture("dots", P, U * 4, 0.1), ...confetti(61, 26, [{ x: 0.05, y: 0.14, w: 0.9, h: 0.78 }]),
    ...flow({ x: W * 0.1, y: H * 0.12, w: W * 0.8, h: H * 0.78 }, [{ k: "pill", t: "Giveaway", after: 2 }, { k: "h1", t: "Win an electric toothbrush!", size: 9, c: PD },
      { k: "list", items: ["Follow us", "Like this post", "Tag a friend"], mark: "num", size: 4, mc: P }, { k: "body", t: "Winner picked Friday. Good luck!" }], "center", "center"),
  ] }));
  add("newpatient", "New patient special", "offers", () => ({ bg: CREAM, objs: [
    grain(0.05), shape("seal", W * 0.7, H * 0.08, S * 0.22, { fill: A, angle: -8, name: "Badge" }),
    textbox("FREE", { ...td("heading"), fontSize: Math.round(S * 0.055), left: W * 0.7, top: H * 0.08 + S * 0.08, width: S * 0.22, textAlign: "center", fill: PAPER, angle: -8, name: "Badge text" }),
    ...flow({ x: W * 0.08, y: H * 0.1, w: W * 0.84, h: H * 0.8 }, [{ k: "eyebrow", t: "New patient special", after: 1.4 }, { k: "h1", t: "Your first visit includes", size: 8, wf: 0.7 },
      { k: "list", items: ["Exam with the doctor", "Digital x-rays and photos", "A personal treatment plan"], size: 4 }, { k: "cta", t: "Book your visit", fill: P }], "left", "center"),
  ], logo: logoAt("br", 0.12) }));
  add("siblings", "Family discount", "offers", () => ({ bg: PXL, objs: [
    ...[0.28, 0.5, 0.72].map((x, i) => photoCircle(W * x, H * 0.2, S * (i === 1 ? 0.13 : 0.1), { tone: i === 1 ? P : A, q: ["brother sister smiling", "happy family portrait", "kids smiling"][i], own: "patient", i })),
    ...flow({ x: W * 0.08, y: H * 0.4, w: W * 0.84, h: H * 0.5 }, [{ k: "script", t: "Smiles run in the family", size: 7, after: 0.4 }, { k: "h1", t: "Save 10% on each sibling", size: 9, c: PD }, { k: "body", t: "Brothers, sisters, parents: when more than one of you starts treatment, everyone saves." }, { k: "cta", t: "Ask about family savings", fill: P }], "center"),
  ], logo: logoAt("br", 0.12) }));
  add("aligners", "Clear aligners", "offers", () => { const { vis, txt } = pic(0.48); return { bg: PAPER, objs: [
    box(vis.x, vis.y, vis.w, vis.h, grad(PL, AXL), { name: "Colour block" }),
    photoShape("blob2", vis.x + vis.w * 0.15, vis.y + vis.h * 0.1, vis.w * 0.7, vis.h * 0.8, { q: "woman smiling portrait" }),
    ...flow(txt, [{ k: "pill", t: "New", fill: P, after: 2 }, { k: "h1", t: "Straighten your smile, invisibly", size: 8.5 }, { k: "body", t: "Clear aligners fit your life: removable, comfortable and nearly invisible." }, { k: "cta", t: "See if you're a fit" }], "left", "center"),
  ], logo: logoAt("br", 0.12) }; });
  add("whitening", "Free whitening", "offers", () => ({ bg: P, objs: [
    layer(radial(tint(P, 0.15), shade(P, 0.3))), ...scatter(67, 10, (x, y, s) => shape("sparkle", x, y, s * 0.6, { fill: PAPER, opacity: 0.8 }), [{ x: 0.06, y: 0.2, w: 0.88, h: 0.66 }]),
    ...flow(full(0.12), [{ k: "eyebrow", t: "When you finish treatment", c: AL, after: 2 }, { k: "h1", t: "Free whitening", size: 13, c: PAPER }, { k: "script", t: "for your brightest smile", size: 6.5, c: AL }], "center", "center"),
  ], logo: logoAt("br", 0.12) }));
  add("giftcard", "Gift cards", "offers", () => ({ bg: CREAM, objs: [
    grain(0.05),
    box(W * 0.5 - S * 0.34, H * 0.1, S * 0.68, S * 0.42, grad(P, shade(P, 0.35)), { rx: U * 3, ry: U * 3, angle: -6, shadow: shadow(3, 1.6, 0.25), name: "Gift card" }),
    textbox(C.brand, { ...td("subhead"), fontSize: Math.round(U * 3.6), left: W * 0.5 - S * 0.3, top: H * 0.1 + S * 0.06, width: S * 0.5, fill: PAPER, angle: -6, name: "Card name" }),
    textbox("$100", { ...td("heading"), fontSize: Math.round(S * 0.09), left: W * 0.5 - S * 0.28, top: H * 0.1 + S * 0.22, width: S * 0.5, fill: A, angle: -6, name: "Card value" }),
    ...flow({ x: W * 0.08, y: H * 0.1 + S * 0.56, w: W * 0.84, h: H * 0.3 }, [{ k: "h1", t: "Give the gift of a smile", size: 8, c: PD }, { k: "body", t: "Gift cards toward treatment, whitening or retainers. Ask at the front desk." }], "center"),
  ] }));
  add("contest", "Photo contest", "offers", () => ({ bg: AXL, objs: [
    texture("dots", A, U * 4, 0.2),
    ...polaroid(W * 0.5 - S * 0.25, H * 0.06, S * 0.5, S * 0.38, { q: "big smile laughing", own: "patient" }),
    shape("crown", W * 0.5 + S * 0.16, H * 0.02, S * 0.13, { fill: A, angle: 16, name: "Crown" }),
    ...flow({ x: W * 0.08, y: H * 0.1 + S * 0.5, w: W * 0.84, h: H * 0.32 }, [{ k: "h1", t: "Best smile contest", size: 9, c: PD }, { k: "body", t: "Post your brightest smile, tag us and use our hashtag. Winner gets a $50 gift card!" }], "center"),
  ] }));

  // ================================================================ fun facts and tips
  add("tip", "Tip of the week", "facts", () => ({ bg: PAPER, objs: [
    box(U * 4, U * 4, W - U * 8, H - U * 8, "transparent", { stroke: P, strokeWidth: U * 0.8, rx: U * 2, ry: U * 2, name: "Border" }),
    patch("dots", W * 0.62, U * 4, W * 0.38 - U * 4, H * 0.22, A, U * 3.5, 0.3),
    ...flow({ x: W * 0.12, y: H * 0.12, w: W * 0.76, h: H * 0.7 }, [{ k: "pill", t: "Tip of the week", after: 2.6 }, { k: "h1", t: "Brush for two full minutes, twice a day", size: 8.5 }, { k: "body", t: "Play a song you like. Most are about three minutes, so stop at the second chorus." }], "left", "center"),
  ], logo: logoAt("br", 0.12) }));
  add("didyouknow", "Did you know?", "facts", () => ({ bg: P, objs: [
    layer(grad(tint(P, 0.06), shade(P, 0.3))),
    textbox("?", { ...td("heading"), fontSize: Math.round(S * 0.55), left: W - S * 0.42, top: -S * 0.08, width: S * 0.45, textAlign: "center", fill: tint(P, 0.16), name: "Question mark", keep: true }),
    ...flow({ x: W * 0.08, y: H * 0.18, w: W * 0.84, h: H * 0.66 }, [{ k: "script", t: "Did you know?", size: 8, c: AL, after: 1.6 }, { k: "h1", t: "Tooth enamel is the hardest substance in the human body.", size: 8.5, c: PAPER }, { k: "body", t: "Even so, it can't grow back, so protect it.", c: tint(P, 0.78) }], "left", "center"),
  ], logo: logoAt("br", 0.12) }));
  add("mythfact", "Myth vs fact", "facts", () => {
    const cw = wide ? W * 0.42 : W * 0.84, ch = wide ? H * 0.6 : H * 0.3, x2 = wide ? W * 0.5 : W * 0.08, y1 = H * 0.24, y2 = wide ? y1 : y1 + ch + U * 3;
    return { bg: PAPER, objs: [
      texture("grid", P, U * 6, 0.07),
      ...flow({ x: W * 0.08, y: H * 0.06, w: W * 0.84, h: H * 0.14 }, [{ k: "h1", t: "Myth or fact?", size: 8 }], "left", "center"),
      box(W * 0.08, y1, cw, ch, tint(RED, 0.9), { rx: U * 2.5, ry: U * 2.5, name: "Myth panel" }),
      box(x2, y2, cw, ch, PL, { rx: U * 2.5, ry: U * 2.5, name: "Fact panel" }),
      ...flow({ x: W * 0.08 + U * 4, y: y1 + U * 3, w: cw - U * 8, h: ch - U * 6 }, [{ k: "pill", t: "✕ Myth", fill: RED, after: 1.8 }, { k: "sub", t: "You're too old for braces.", c: INK, size: 4.2 }], "left", "center"),
      ...flow({ x: x2 + U * 4, y: y2 + U * 3, w: cw - U * 8, h: ch - U * 6 }, [{ k: "pill", t: "✓ Fact", fill: P, after: 1.8 }, { k: "sub", t: "Healthy teeth can be moved at any age. Many of our patients are adults.", c: INK, size: 4.2 }], "left", "center"),
    ], logo: logoAt("br", 0.11) };
  });
  add("bignumber", "Big number", "facts", () => ({ bg: CREAM, objs: [
    grain(0.05), dot(W * 0.2, H * 0.24, S * 0.2, AL, { name: "Circle" }),
    ...flow({ x: W * 0.08, y: H * 0.08, w: W * 0.84, h: H * 0.84 }, [{ k: "big", t: "32", size: 34, after: 1 }, { k: "h1", t: "adult teeth, wisdom teeth included", size: 7, c: PD }, { k: "body", t: "Most people have 28 once wisdom teeth are out. How many do you have?" }], "left", "center"),
  ], logo: logoAt("br", 0.12) }));
  add("babyteeth", "20 baby teeth", "facts", () => ({ bg: AXL, objs: [
    texture("dots", A, U * 4, 0.18),
    ...[0, 1, 2, 3, 4].map(i => shape("tooth", W * 0.12 + i * W * 0.16, H * 0.1 + (i % 2) * U * 4, S * 0.11, { fill: PAPER, stroke: P, strokeWidth: 4, name: "Tooth" })),
    ...flow({ x: W * 0.08, y: H * 0.32, w: W * 0.84, h: H * 0.6 }, [{ k: "eyebrow", t: "Fun fact", after: 1 }, { k: "big", t: "20", size: 24, after: 0.6 }, { k: "h1", t: "baby teeth, usually all in by age 3", size: 6.5, c: PD }, { k: "body", t: "They hold space for adult teeth, so caring for them matters." }], "center", "center"),
  ], logo: logoAt("br", 0.12) }));
  add("age7", "First check by age 7", "facts", () => ({ bg: PAPER, objs: [
    box(0, 0, W, H, grad(PXL, AXL, "down"), { ...LOCK, name: "Background" }),
    dot(W * 0.5, H * 0.26, S * 0.18, P, { shadow: shadow(), name: "Badge" }),
    textbox("7", { ...td("heading"), fontSize: Math.round(S * 0.22), left: W * 0.5 - S * 0.18, top: H * 0.26 - S * 0.13, width: S * 0.36, textAlign: "center", fill: PAPER, name: "Number" }),
    ring(W * 0.5, H * 0.26, S * 0.22, P, U * 0.4),
    ...flow({ x: W * 0.1, y: H * 0.26 + S * 0.28, w: W * 0.8, h: H * 0.4 }, [{ k: "h1", t: "The magic number for a first orthodontic check", size: 7, c: PD }, { k: "body", t: "Experts recommend kids see an orthodontist by age 7. Most won't need treatment yet, but we can spot things early." }], "center"),
  ], logo: logoAt("br", 0.11) }));
  add("thisorthat", "This or that?", "facts", () => ({ bg: PAPER, objs: [
    box(0, 0, W / 2, H, P, { name: "Left half" }), box(W / 2, 0, W / 2, H, A, { name: "Right half" }),
    patch("dots", 0, 0, W / 2, H, PAPER, U * 4, 0.1), patch("stripes", W / 2, 0, W / 2, H, PAPER, U * 6, 0.1),
    ...flow({ x: W * 0.08, y: H * 0.06, w: W * 0.84, h: H * 0.16 }, [{ k: "h1", t: "This or that?", size: 8, c: PAPER }], "center", "center"),
    dot(W / 2, H / 2, U * 9, PAPER, { shadow: shadow(), name: "VS circle" }),
    textbox("VS", { ...td("heading"), fontSize: Math.round(U * 7), left: W / 2 - U * 9, top: H / 2 - U * 4, width: U * 18, textAlign: "center", fill: INK }),
    ...flow({ x: W * 0.04, y: H * 0.6, w: W * 0.42, h: H * 0.2 }, [{ k: "h1", t: "Coloured bands", size: 6.5, c: PAPER }], "center", "center"),
    ...flow({ x: W * 0.54, y: H * 0.6, w: W * 0.42, h: H * 0.2 }, [{ k: "h1", t: "Clear bands", size: 6.5, c: PAPER }], "center", "center"),
    textbox("Tell us in the comments!", { ...td("body"), left: W * 0.1, width: W * 0.8, top: H * 0.88, textAlign: "center", fill: PAPER }),
  ] }));
  add("quiz", "Quick quiz", "facts", () => ({ bg: tint(P, 0.9), objs: [
    texture("plus", P, U * 6, 0.12),
    ...flow({ x: W * 0.08, y: H * 0.08, w: W * 0.84, h: H * 0.84 }, [{ k: "pill", t: "Quick quiz", fill: P, after: 2 }, { k: "h1", t: "How long should you brush?", size: 8.5, after: 3 },
      ...["A. 30 seconds", "B. 2 minutes", "C. 5 minutes"].map(t => ({ k: "cta", t, fw: 0.8, fill: PAPER, c: INK, bg: { stroke: tint(P, 0.5), strokeWidth: Math.max(2, U * 0.3), shadow: shadow(1.4, 0.5, 0.1) }, after: 1.8 })),
      { gap: 1 }, { k: "body", t: "Answer in tomorrow's story!", c: P }], "left", "center"),
  ], logo: logoAt("br", 0.11) }));
  add("steps", "3 steps", "facts", () => ({ bg: PAPER, objs: [
    box(0, 0, W * 0.035, H, A, { name: "Side band" }), patch("dots", W * 0.7, H * 0.06, W * 0.26, H * 0.2, P, U * 3.5, 0.18),
    ...flow({ x: W * 0.1, y: H * 0.08, w: W * 0.8, h: H * 0.84 }, [{ k: "script", t: "3 steps to", size: 7, after: 0 }, { k: "h1", t: "a healthy smile", size: 9, after: 3 },
      { k: "list", items: ["Brush twice a day, two minutes each time", "Floss once a day, before bed", "Visit every six months for a check-up"], mark: "num", size: 4, lg: 2.4, mc: P }], "left", "center"),
  ], logo: logoAt("br", 0.11) }));
  add("snacks", "Braces-friendly snacks", "facts", () => {
    const col = W * 0.4;
    return { bg: CREAM, objs: [
      grain(0.05),
      ...flow({ x: W * 0.08, y: H * 0.06, w: W * 0.84, h: H * 0.18 }, [{ k: "h1", t: "Braces-friendly snacks", size: 8 }], "left", "center"),
      box(W * 0.06, H * 0.27, W * 0.42, H * 0.6, PAPER, { rx: U * 2, ry: U * 2, shadow: shadow(1.6, 0.6, 0.12), name: "Card" }),
      box(W * 0.52, H * 0.27, W * 0.42, H * 0.6, PAPER, { rx: U * 2, ry: U * 2, shadow: shadow(1.6, 0.6, 0.12), name: "Card" }),
      ...flow({ x: W * 0.09, y: H * 0.3, w: col - U * 2, h: H * 0.54 }, [{ k: "pill", t: "Enjoy", fill: P, after: 2 }, { k: "list", items: ["Yogurt", "Bananas", "Cheese", "Smoothies", "Pasta"], size: 3.6, mc: P }]),
      ...flow({ x: W * 0.55, y: H * 0.3, w: col - U * 2, h: H * 0.54 }, [{ k: "pill", t: "Skip", fill: RED, after: 2 }, { k: "list", items: ["Popcorn", "Hard candy", "Caramel", "Whole apples", "Ice"], mark: "cross", size: 3.6, mc: RED }]),
    ], logo: logoAt("br", 0.11) };
  });
  add("toothbrush", "Swap your toothbrush", "facts", () => ({ bg: PL, objs: [
    texture("stripes", P, U * 8, 0.08),
    shape("calendar", W * 0.5 - S * 0.15, H * 0.07, S * 0.3, { fill: PAPER, shadow: shadow(), name: "Calendar" }), box(W * 0.5 - S * 0.15, H * 0.07, S * 0.3, S * 0.07, P, { name: "Calendar top" }),
    textbox("3–4\nmonths", { ...td("heading"), fontSize: Math.round(S * 0.05), lineHeight: 1, left: W * 0.5 - S * 0.15, top: H * 0.07 + S * 0.11, width: S * 0.3, textAlign: "center", fill: P, name: "Months" }),
    ...flow({ x: W * 0.1, y: H * 0.08 + S * 0.36, w: W * 0.8, h: H * 0.42 }, [{ k: "h1", t: "Time for a new toothbrush?", size: 8, c: PD }, { k: "body", t: "Swap it every 3 to 4 months, or sooner if the bristles are frayed, and after you've been sick." }], "center"),
  ], logo: logoAt("br", 0.11) }));
  add("wax", "Poking wire? Wax it", "facts", () => ({ bg: PAPER, objs: [
    box(0, 0, W, H * 0.3, A, { name: "Header band" }), patch("dots", 0, 0, W, H * 0.3, PAPER, U * 4, 0.18),
    ...flow({ x: W * 0.08, y: H * 0.04, w: W * 0.84, h: H * 0.24 }, [{ k: "eyebrow", t: "Braces 101", c: PAPER, after: 1 }, { k: "h1", t: "Poking wire?", size: 9.5, c: PAPER }], "left", "center"),
    ...flow({ x: W * 0.08, y: H * 0.36, w: W * 0.84, h: H * 0.5 }, [{ k: "list", items: ["Dry the spot with a tissue", "Roll a pea-size ball of orthodontic wax", "Press it over the wire or bracket", "Call us if it still bothers you"], mark: "num", size: 4, lg: 2.2, mc: P }]),
  ], logo: logoAt("br", 0.11) }));
  add("retainer", "Retainer care", "facts", () => { const { vis, txt } = pic(0.42); return { bg: CREAM, objs: [
    box(vis.x, vis.y, vis.w, vis.h, P, { name: "Colour block" }), patch("waves", vis.x, vis.y, vis.w, vis.h, PAPER, U * 6, 0.14),
    shape("blob", vis.x + vis.w * 0.3, vis.y + vis.h * 0.15, Math.min(vis.w, vis.h) * 0.7, { fill: tint(P, 0.25), name: "Blob" }),
    ...flow({ x: vis.x + vis.w * 0.08, y: vis.y, w: vis.w * 0.84, h: vis.h }, [{ k: "script", t: "Retainer", size: 9, c: AL, after: 0 }, { k: "h1", t: "care 101", size: 10, c: PAPER }], "left", "center"),
    ...flow(txt, [{ k: "list", items: ["Rinse it every time you take it out", "Brush it gently with cool water", "Keep it in its case, never a napkin", "Keep it away from hot water and pets"], size: 3.8, lg: 2.1 }], "left", "center"),
  ], logo: logoAt("br", 0.11) }; });
  add("glossary", "Word of the week", "facts", () => ({ bg: PAPER, objs: [
    grain(0.04), box(W * 0.08, H * 0.1, U * 0.9, H * 0.6, A, { name: "Accent line" }),
    ...flow({ x: W * 0.13, y: H * 0.1, w: W * 0.76, h: H * 0.75 }, [{ k: "eyebrow", t: "Word of the week", after: 2 }, { k: "h1", t: "Malocclusion", size: 10 }, { k: "script", t: "mal·o·clu·sion", size: 5.5, hand: true, c: MUTED, after: 2 }, { k: "body", t: "When the top and bottom teeth don't line up when you bite. It's one of the most common reasons people see an orthodontist." }], "left", "center"),
  ], logo: logoAt("br", 0.11) }));
  add("faq", "You asked, we answered", "facts", () => ({ bg: PXL, objs: [
    texture("dots", P, U * 4, 0.1),
    box(W * 0.08, H * 0.1, W * 0.84, H * 0.3, P, { rx: U * 3, ry: U * 3, name: "Question bubble" }),
    box(W * 0.08, H * 0.46, W * 0.84, H * 0.4, PAPER, { rx: U * 3, ry: U * 3, shadow: shadow(1.6, 0.6, 0.12), name: "Answer bubble" }),
    ...flow({ x: W * 0.13, y: H * 0.1, w: W * 0.74, h: H * 0.3 }, [{ k: "eyebrow", t: "You asked", c: AL, after: 1.4 }, { k: "h1", t: "Do braces hurt?", size: 8, c: PAPER }], "left", "center"),
    ...flow({ x: W * 0.13, y: H * 0.46, w: W * 0.74, h: H * 0.4 }, [{ k: "eyebrow", t: "We answered", after: 1.4 }, { k: "body", t: "Getting braces on doesn't hurt. Your teeth may feel sore for a few days after, and soft foods help.", size: 3.6 }], "left", "center"),
  ], logo: logoAt("br", 0.11) }));

  // ================================================================ practice news
  add("notice", "Office hours", "news", () => ({ bg: PAPER, objs: [
    box(0, 0, W, H * 0.3, P, { name: "Header band" }), patch("stripes", 0, H * 0.3, W, U * 1.4, A, U * 2, 0.8),
    ...flow({ x: W * 0.08, y: H * 0.04, w: W * 0.84, h: H * 0.24 }, [{ k: "h1", t: "Our new hours", size: 9, c: PAPER }], "left", "center"),
    ...flow({ x: W * 0.08, y: H * 0.38, w: W * 0.84, h: H * 0.44 }, [{ k: "list", items: ["Monday to Thursday: 8 to 5", "Friday: 8 to 2", "Saturday and Sunday: Closed"], mark: "dot", size: 4.4 }, { gap: 2 }, { k: "body", t: "Questions? Call or text us anytime." }]),
  ], logo: logoAt("br", 0.13) }));
  add("closed", "Office closed", "news", () => ({ bg: PDD, objs: [
    layer(grad(shade(P, 0.4), shade(P, 0.7), "down")), shape("cloud", W * 0.6, H * 0.08, S * 0.3, { fill: tint(P, 0.25), name: "Cloud" }),
    snowflake(W * 0.84, H * 0.3, S * 0.05, PAPER), snowflake(W * 0.7, H * 0.33, S * 0.03, PAPER),
    ...flow({ x: W * 0.08, y: H * 0.34, w: W * 0.84, h: H * 0.5 }, [{ k: "pill", t: "Weather update", after: 2.4 }, { k: "h1", t: "Our office is closed today", size: 10, c: PAPER }, { k: "body", t: "We'll call to reschedule. Stay safe and warm!", c: tint(P, 0.75) }]),
  ], logo: logoAt("br", 0.12) }));
  add("team", "Meet the team", "news", () => ({ bg: CREAM, objs: [
    grain(0.05), dot(W * 0.5 + S * 0.2, H * 0.08 + S * 0.08, S * 0.08, AL), dot(W * 0.5 - S * 0.25, H * 0.08 + S * 0.38, S * 0.05, PL),
    photoShape("arch", W * 0.5 - S * 0.2, H * 0.06, S * 0.4, S * 0.48, { q: "doctor portrait smiling", own: "team" }),
    ...flow({ x: W * 0.1, y: H * 0.06 + S * 0.52, w: W * 0.8, h: H * 0.38 }, [{ k: "script", t: "Meet", size: 6.5, after: 0 }, { k: "h1", t: "Dr. Jordan", size: 9 }, { k: "eyebrow", t: "Orthodontist", c: MUTED, after: 1.8 }, { k: "body", t: "Loves hiking with her two kids and has never met a smoothie she didn't like." }], "center"),
  ] }));
  add("teamqa", "Team spotlight Q&A", "news", () => { const { vis, txt } = pic(0.44); return { bg: PAPER, objs: [
    box(vis.x, vis.y, vis.w, vis.h, AXL, { name: "Colour block" }), photoCircle(vis.x + vis.w / 2, vis.y + vis.h / 2, Math.min(vis.w, vis.h) * 0.36, { q: "receptionist smiling", own: "team" }),
    ...flow(txt, [{ k: "eyebrow", t: "Team spotlight", after: 1 }, { k: "h1", t: "Sam, treatment coordinator", size: 6.5 },
      { k: "sub", t: "Favourite snack?", size: 3.6, c: P, after: 0.6 }, { k: "body", t: "Anything with peanut butter." }, { k: "sub", t: "Best part of the job?", size: 3.6, c: P, after: 0.6 }, { k: "body", t: "Braces-off day, every time." }], "left", "center"),
  ], logo: logoAt("br", 0.11) }; });
  add("welcome", "Welcome aboard", "news", () => ({ bg: P, objs: [
    layer(radial(tint(P, 0.12), shade(P, 0.3))), ...confetti(71, 16, [{ x: 0.1, y: 0.04, w: 0.8, h: 0.92 }], [A, tint(A, 0.4), PAPER, tint(P, 0.5)]),
    photoCircle(W * 0.5, H * 0.1 + S * 0.2, S * 0.2, { stroke: PAPER, strokeWidth: U * 1, strokeDashArray: null, q: "doctor portrait", own: "team" }),
    ...flow({ x: W * 0.1, y: H * 0.1 + S * 0.46, w: W * 0.8, h: H * 0.4 }, [{ k: "script", t: "Please welcome", size: 6.5, c: AL, after: 0 }, { k: "h1", t: "Dr. Morgan Lee", size: 10, c: PAPER }, { k: "body", t: "Our newest orthodontist. Say hi at your next visit!", c: tint(P, 0.8) }], "center"),
  ] }));
  add("event", "Event", "news", () => ({ bg: PAPER, objs: [
    texture("dots", P, U * 4, 0.08),
    box(W * 0.08, H * 0.08, U * 22, U * 25, A, { rx: U * 2, ry: U * 2, shadow: shadow(1.6, 0.6, 0.15), name: "Date block" }),
    box(W * 0.08, H * 0.08, U * 22, U * 7, shade(A, 0.2), { rx: U * 2, ry: U * 2, name: "Date top" }),
    textbox("DEC", { ...td("cta"), fontSize: Math.round(U * 3.6), left: W * 0.08, top: H * 0.08 + U * 1.6, width: U * 22, textAlign: "center", fill: PAPER, name: "Month" }),
    textbox("12", { ...td("heading"), fontSize: Math.round(U * 11), left: W * 0.08, top: H * 0.08 + U * 9, width: U * 22, textAlign: "center", fill: PAPER, name: "Day" }),
    ...flow({ x: W * 0.08, y: H * 0.08 + U * 30, w: W * 0.84, h: H * 0.9 - U * 34 }, [{ k: "script", t: "You're invited", size: 6.5, after: 0 }, { k: "h1", t: "Holiday open house", size: 9 }, { k: "body", t: "Hot cocoa, photos with Santa and a raffle. 4 to 7 pm at our office." }, { k: "cta", t: "Save your spot" }], "left", "center"),
  ], logo: logoAt("br", 0.11) }));
  add("hiring", "We're hiring", "news", () => ({ bg: CREAM, objs: [
    box(0, 0, W, H * 0.4, P, { name: "Header band" }), patch("plus", 0, 0, W, H * 0.4, PAPER, U * 6, 0.12),
    ...flow({ x: W * 0.08, y: H * 0.05, w: W * 0.84, h: H * 0.32 }, [{ k: "script", t: "Join our team", size: 6.5, c: AL, after: 0 }, { k: "h1", t: "We're hiring!", size: 12, c: PAPER }], "left", "center"),
    ...flow({ x: W * 0.08, y: H * 0.46, w: W * 0.84, h: H * 0.44 }, [{ k: "list", items: ["Orthodontic assistant", "Front desk coordinator", "Treatment coordinator"], size: 4.2 }, { k: "cta", t: "Apply today" }]),
  ], logo: logoAt("br", 0.12) }));
  add("anniversary", "Anniversary", "news", () => ({ bg: PDD, objs: [
    layer(radial(shade(P, 0.25), PDD)), ...scatter(83, 12, (x, y, s) => shape("sparkle", x, y, s * 0.55, { fill: tint(A, 0.3) }), [{ x: 0.12, y: 0.12, w: 0.76, h: 0.76 }]),
    ring(W * 0.5, H * 0.5, S * 0.38, A, U * 0.5), ring(W * 0.5, H * 0.5, S * 0.41, tint(A, 0.4), U * 0.25),
    ...flow({ x: W * 0.5 - S * 0.32, y: H * 0.5 - S * 0.32, w: S * 0.64, h: S * 0.64 }, [{ k: "eyebrow", t: "Celebrating", c: AL, after: 0 }, { k: "big", t: "10", size: 28, c: PAPER, after: 0 }, { k: "script", t: "years of smiles", size: 7, c: AL }], "center", "center"),
  ] }));
  add("newoffice", "New location", "news", () => { const { vis, txt } = pic(0.48); return { bg: PAPER, objs: [
    photo(vis.x, vis.y, vis.w, vis.h, { q: "modern office building", own: "office" }), wash(vis.x, vis.y, vis.w, vis.h, P, 0.2),
    shape("pin", vis.x + vis.w * 0.5 - S * 0.07, vis.y + vis.h * 0.5 - S * 0.1, S * 0.14, { fill: A, shadow: shadow(2, 1, 0.3), name: "Map pin" }),
    ...flow(txt, [{ k: "pill", t: "Now open", fill: P, after: 2 }, { k: "h1", t: "Our new office", size: 9.5 }, { k: "body", t: "123 Main Street. Same great team, more room for smiles." }, { k: "cta", t: "Get directions" }], "left", "center"),
  ], logo: logoAt("br", 0.12) }; });
  add("technology", "New technology", "news", () => ({ bg: PD, objs: [
    layer(grad(shade(P, 0.2), shade(P, 0.6))), texture("grid", PAPER, U * 6, 0.06),
    shape("bolt", W * 0.76, H * 0.08, S * 0.14, { fill: A, name: "Bolt" }),
    ...flow({ x: W * 0.08, y: H * 0.1, w: W * 0.84, h: H * 0.8 }, [{ k: "pill", t: "New in our office", after: 2.4 }, { k: "h1", t: "Goodbye goopy impressions", size: 10, c: PAPER }, { k: "body", t: "Our 3D scanner takes a picture of your teeth in minutes. No mess, no gagging.", c: tint(P, 0.78) }], "left", "center"),
  ], logo: logoAt("br", 0.12) }));
  add("community", "Community event", "news", () => ({ bg: AXL, objs: [
    texture("stripes", A, U * 8, 0.1),
    ...polaroid(W * 0.08, H * 0.08, W * 0.4, H * 0.3, { q: "fun run race", own: "team" }), ...polaroid(W * 0.52, H * 0.14, W * 0.4, H * 0.3, { tone: A, q: "running shoes", i: 1 }),
    ...flow({ x: W * 0.08, y: H * 0.58, w: W * 0.84, h: H * 0.32 }, [{ k: "script", t: "Proud to sponsor", size: 6.5, after: 0 }, { k: "h1", t: "the spring fun run", size: 8.5, c: PD }, { k: "body", t: "Thanks to everyone who came out to cheer!" }]),
  ], logo: logoAt("br", 0.11) }));
  add("reviewask", "Leave us a review", "news", () => ({ bg: PAPER, objs: [
    texture("dots", A, U * 4, 0.16),
    box(W * 0.1, H * 0.12, W * 0.8, H * 0.7, PAPER, { rx: U * 3, ry: U * 3, shadow: shadow(), name: "Card" }),
    ...flow({ x: W * 0.14, y: H * 0.12, w: W * 0.72, h: H * 0.7 }, [{ k: "stars", size: 7, after: 3 }, { k: "h1", t: "Love your smile?", size: 9, c: PD }, { k: "body", t: "A quick Google review helps other families find us. It only takes a minute." }, { k: "cta", t: "Leave a review", fill: P }], "center", "center"),
  ], logo: logoAt("br", 0.11) }));
  add("birthday", "Team birthday", "news", () => ({ bg: tint(P, 0.88), objs: [
    texture("dots", P, U * 4, 0.12),
    ...[[0.1, A], [0.22, P], [0.78, tint(A, 0.3)]].map(([x, c], i) => art(shape("balloon", W * x, H * (0.06 + i * 0.04), S * 0.16, { fill: c, name: "Balloon" }), "balloons", i % 2)),
    photoCircle(W * 0.5, H * 0.08 + S * 0.22, S * 0.2, { q: "birthday cake", own: "team" }),
    ...flow({ x: W * 0.1, y: H * 0.1 + S * 0.46, w: W * 0.8, h: H * 0.36 }, [{ k: "script", t: "Happy birthday,", size: 7, after: 0 }, { k: "h1", t: "Taylor!", size: 11, c: PD }, { k: "body", t: "Wish our favourite assistant a great day in the comments." }], "center"),
  ] }));

  // ================================================================ patients and reviews (real words from real patients go in the placeholders)
  add("review", "5-star review", "patients", () => ({ bg: CREAM, objs: [
    grain(0.05), shape("quote", W * 0.72, H * 0.06, S * 0.18, { fill: tint(P, 0.8), name: "Quote mark" }),
    ...flow({ x: W * 0.08, y: H * 0.1, w: W * 0.84, h: H * 0.76 }, [{ k: "stars", size: 6, after: 3 }, { k: "h1", t: "“Paste a real patient review here. Their own words work best.”", size: 6.5, c: PD, lh: 1.2 }, { k: "swash", w: 22, after: 1.4 }, { k: "sub", t: "Patient's first name" }], "left", "center"),
  ], logo: logoAt("br", 0.12) }));
  add("review2", "Review card", "patients", () => ({ bg: P, objs: [
    layer(grad(tint(P, 0.1), shade(P, 0.3))), texture("dots", PAPER, U * 4, 0.08),
    box(W * 0.08, H * 0.14, W * 0.84, H * 0.64, PAPER, { rx: U * 3, ry: U * 3, shadow: shadow(3, 1.4, 0.25), name: "Card" }),
    photoCircle(W * 0.5, H * 0.14, S * 0.08, { stroke: PAPER, strokeWidth: U * 0.8, strokeDashArray: null, q: "smiling woman portrait", own: "patient" }),
    ...flow({ x: W * 0.14, y: H * 0.14 + S * 0.1, w: W * 0.72, h: H * 0.64 - S * 0.12 }, [{ k: "stars", size: 5, after: 2 }, { k: "body", t: "“Paste a real patient review here.”", size: 4, c: INK }, { k: "sub", t: "Patient's first name", size: 3.6 }], "center", "center"),
    textbox("What our patients say", { ...td("subhead"), fontSize: Math.round(U * 4), left: W * 0.08, top: H * 0.84, width: W * 0.84, textAlign: "center", fill: PAPER }),
  ] }));
  add("bracesoff", "Braces off day!", "patients", () => ({ bg: PAPER, objs: [
    texture("dots", A, U * 4, 0.14), ...polaroid(W * 0.5 - S * 0.3, H * 0.06, S * 0.6, S * 0.4, { q: "teenager smiling", own: "patient" }),
    ...confetti(97, 16, [{ x: 0.12, y: 0.06, w: 0.76, h: 0.56 }, { x: 0.05, y: 0.66, w: 0.9, h: 0.3 }]),
    ...flow({ x: W * 0.08, y: H * 0.06 + S * 0.56, w: W * 0.84, h: H * 0.9 - S * 0.56 }, [{ k: "h1", t: "Braces off day!", size: 10 }, { k: "script", t: "Congratulations, Emma!", size: 6 }], "center"),
  ], logo: logoAt("br", 0.11) }));
  add("beforeafter", "Before and after", "patients", () => {
    const top = H * 0.2, hh = H * (wide ? 0.6 : 0.5), cw = W * 0.4;
    return { bg: PAPER, objs: [
      texture("grid", P, U * 6, 0.06),
      ...flow({ x: W * 0.08, y: H * 0.04, w: W * 0.84, h: H * 0.14 }, [{ k: "h1", t: "What a difference!", size: 7.5 }], "center", "center"),
      photo(W * 0.08, top, cw, hh, { rx: U * 2, ry: U * 2, name: "Before photo" }), photo(W * 0.52, top, cw, hh, { rx: U * 2, ry: U * 2, name: "After photo", tone: A }),
      shape("ribbon", W * 0.08 - U, top + U * 3, U * 20, { fill: INK, name: "Label" }, U * 7),
      textbox("BEFORE", { ...td("cta"), fontSize: Math.round(U * 3), left: W * 0.08 - U, top: top + U * 4.7, width: U * 20, textAlign: "center", fill: PAPER }),
      shape("ribbon", W * 0.52 - U, top + U * 3, U * 20, { fill: A, name: "Label" }, U * 7),
      textbox("AFTER", { ...td("cta"), fontSize: Math.round(U * 3), left: W * 0.52 - U, top: top + U * 4.7, width: U * 20, textAlign: "center", fill: PAPER }),
      textbox("18 months with clear aligners", { ...td("body"), left: W * 0.08, width: W * 0.84, top: top + hh + U * 4, textAlign: "center", fill: MUTED }),
    ], logo: logoAt("br", 0.11) };
  });
  add("patientmonth", "Patient of the month", "patients", () => ({ bg: tint(P, 0.9), objs: [
    texture("plus", P, U * 6, 0.1),
    shape("seal", W * 0.5 - S * 0.28, H * 0.05, S * 0.56, { fill: A, name: "Badge" }),
    photoCircle(W * 0.5, H * 0.05 + S * 0.28, S * 0.22, { stroke: PAPER, strokeWidth: U * 0.8, strokeDashArray: null, q: "boy smiling", own: "patient" }),
    ...flow({ x: W * 0.1, y: H * 0.07 + S * 0.58, w: W * 0.8, h: H * 0.34 }, [{ k: "eyebrow", t: "Patient of the month", c: P, after: 1 }, { k: "h1", t: "Congrats, Liam!", size: 9.5 }, { k: "body", t: "Perfect brushing checks all month long. Keep it up!" }], "center"),
  ] }));
  add("thankyou", "Thank you", "patients", () => ({ bg: A, objs: [
    texture("dots", PAPER, U * 4, 0.14), ...scatter(101, 12, (x, y, s) => shape("heart", x, y, s * 0.7, { fill: tint(A, 0.35) }), [{ x: 0.05, y: 0.26, w: 0.9, h: 0.48 }]),
    ...flow({ x: W * 0.08, y: H * 0.28, w: W * 0.84, h: H * 0.44 }, [{ k: "script", t: "thank you", size: 13, c: PAPER, after: 1 }, { k: "sub", t: "for 500 five-star reviews. We love our patients.", c: PDD }], "center", "center"),
    NAME({ x: 0, y: H * 0.88, w: W }, { textAlign: "center", fill: PAPER }),
  ] }));
  add("smilegallery", "Smile gallery", "patients", () => {
    const g = wide ? [3, 2] : tall ? [2, 3] : [2, 2], gap = U * 2, cw = (W * 0.84 - gap * (g[0] - 1)) / g[0], ch = (H * 0.64 - gap * (g[1] - 1)) / g[1];
    const frames = [];
    for (let r = 0; r < g[1]; r++) for (let c = 0; c < g[0]; c++)
      frames.push(photo(W * 0.08 + c * (cw + gap), H * 0.2 + r * (ch + gap), cw, ch, { rx: U * 2, ry: U * 2, tone: (r + c) % 2 ? A : P, q: "smiling people", own: "patient", i: frames.length }));
    return { bg: CREAM, objs: [grain(0.05), ...flow({ x: W * 0.08, y: H * 0.04, w: W * 0.84, h: H * 0.14 }, [{ k: "script", t: "Smiles we love", size: 8, after: 0 }], "center", "center"), ...frames,
      textbox("Want to see yours here? Tag us!", { ...td("body"), left: W * 0.08, width: W * 0.84, top: H * 0.88, textAlign: "center", fill: MUTED })] };
  });
  add("spotlight", "Patient spotlight", "patients", () => { const { vis, txt } = pic(0.5); return { bg: PAPER, objs: [
    box(vis.x, vis.y, vis.w, vis.h, P, { name: "Colour block" }), patch("dots", vis.x, vis.y, vis.w, vis.h, PAPER, U * 4, 0.12),
    photoShape("blob", vis.x + vis.w * 0.14, vis.y + vis.h * 0.1, vis.w * 0.72, vis.h * 0.8, { q: "girl smiling portrait", own: "patient" }),
    ...flow(txt, [{ k: "eyebrow", t: "Patient spotlight", after: 1 }, { k: "h1", t: "Meet Ava", size: 9 }, { k: "body", t: "Soccer goalie, artist and proud owner of a brand-new smile." }, { k: "sub", t: "Favourite band colour: purple", size: 3.6, c: P }], "left", "center"),
  ], logo: logoAt("br", 0.11) }; });
  add("selfie", "Show us your smile", "patients", () => ({ bg: AXL, objs: [
    texture("stripes", A, U * 8, 0.1),
    box(W * 0.18, H * 0.06, W * 0.64, H * 0.66, INK, { rx: U * 5, ry: U * 5, shadow: shadow(), name: "Phone" }),
    photo(W * 0.21, H * 0.06 + U * 4, W * 0.58, H * 0.66 - U * 8, { rx: U * 3, ry: U * 3, q: "selfie smiling friends", own: "patient" }),
    shape("heart", W * 0.7, H * 0.56, S * 0.1, { fill: A, angle: 12 }), shape("heart", W * 0.76, H * 0.5, S * 0.06, { fill: P, angle: -10 }),
    ...flow({ x: W * 0.08, y: H * 0.76, w: W * 0.84, h: H * 0.2 }, [{ k: "h1", t: "Show us your smile!", size: 7.5, c: PD }, { k: "body", t: "Post a selfie and tag us for a chance to be featured." }], "center", "center"),
  ] }));
  add("milestone", "Smile milestone", "patients", () => ({ bg: P, objs: [
    layer(radial(tint(P, 0.15), shade(P, 0.35))), ...confetti(113, 22, [{ x: 0.06, y: 0.2, w: 0.88, h: 0.62 }], [A, tint(A, 0.4), PAPER]),
    ...flow(full(0.1), [{ k: "big", t: "1,000", size: 22, c: PAPER, after: 0.6 }, { k: "script", t: "smiles and counting", size: 8, c: AL, after: 2 }, { k: "body", t: "Thank you for every high five along the way.", c: tint(P, 0.8) }], "center", "center"),
  ], logo: logoAt("br", 0.12) }));

  // ================================================================ photo-led layouts (real photos and drawings fill in when the template is picked)
  // a full-bleed photo under a dark fade, with the words at the bottom (or the middle)
  const hero = (q, items, o = {}) => () => ({ bg: PDD, objs: [
    photo(0, 0, W, H, { q, own: o.own, tone: o.tone }), ...(o.wash ? [wash(0, 0, W, H, o.wash, 0.35)] : []), o.mid ? dim(o.dim || 0.42) : shadeUp(o.dim || 0.78),
    ...(o.extra ? o.extra() : []),
    ...flow(o.mid ? full(0.12) : { x: W * 0.08, y: H * 0.42, w: W * 0.84, h: H * 0.48 }, items, o.align || (o.mid ? "center" : "left"), o.mid ? "center" : "bottom"),
  ], logo: logoAt(o.logo || "tl", 0.13) });
  // a photo behind a torn paper card holding the words
  const tornCard = (q, items, o = {}) => () => {
    const cw = wide ? W * 0.5 : W * 0.78, ch = wide ? H * 0.74 : H * (tall ? 0.42 : 0.56), cx = (W - cw) / 2, cy = wide ? (H - ch) / 2 : H * (tall ? 0.46 : 0.36);
    return { bg: PD, objs: [photo(0, 0, W, H, { q, own: o.own }), dim(0.18), torn(o.seed || 5, cx, cy, cw, ch, o.paper || CREAM),
      ...(o.extra ? o.extra(cx, cy, cw, ch) : []),
      ...flow({ x: cx + cw * 0.1, y: cy + ch * 0.1, w: cw * 0.8, h: ch * 0.8 }, items, "center", "center")], logo: logoAt("br", 0.12) };
  };
  // drawings around a plain card: leaves, flowers, ornaments…
  const framed = (artQ, items, o = {}) => () => ({ bg: o.bg || CREAM, objs: [
    ...(o.texture ? [o.texture()] : [grain(0.05)]), ...corners(artQ, o.size || 0.46),
    ...flow(full(0.2), items, "center", "center"),
  ], ...(o.logo ? { logo: logoAt(o.logo, 0.11) } : {}) });
  // one big drawing with words across it
  const feature = (artQ, items, o = {}) => () => ({ bg: o.bg || CREAM, objs: [
    ...(o.texture ? [o.texture()] : [grain(0.05)]),
    artBox(artQ, W * 0.5, H * (o.ay || 0.52), S * (o.size || 0.78), S * (o.size || 0.78), 0, { originX: "center", originY: "center" }),
    ...flow(o.col || full(0.1), items, "center", o.v || "center"),
  ], logo: logoAt("br", 0.11) });
  const caps = (t, size = 13, c = PAPER) => ({ k: "h1", t: t.toUpperCase(), size, c, lh: 0.95, sp: 20 });

  // ---- holidays and seasonal
  add("tgphoto", "Thanksgiving table", "seasonal", hero("thanksgiving dinner table", [{ k: "script", t: "Happy", size: 9, c: AL, after: 0 }, caps("Thanksgiving", 12), { k: "rule", w: 12, after: 2.4 }, { k: "body", t: "May your heart be as full as your plate.", c: PAPER, size: 3.8 }, { k: "pill", t: "From all of us", fill: A }]), { months: [11] });
  add("tgleaves", "Thanksgiving leaf frame", "seasonal", framed("autumn leaves", [{ k: "script", t: "Happy", size: 10, after: 0 }, caps("Thanksgiving", 11, PD), { k: "body", t: "Wishing you good food, warm hearts and grateful moments." }, { k: "pill", t: "From " + C.brand, fill: P, size: 2.6 }]), { months: [11] });
  add("tgpumpkin", "Painted pumpkin", "seasonal", feature("pumpkin", [{ k: "script", t: "grateful", size: 12, c: PAPER, after: 0, o: { shadow: shadow(1.4, 0.4, 0.35) } }, { k: "script", t: "& thankful", size: 9, c: PAPER, o: { shadow: shadow(1.4, 0.4, 0.35) } }], { col: { x: W * 0.1, y: H * 0.4, w: W * 0.8, h: H * 0.26 } }), { months: [11] });
  add("tgtorn", "Thankful torn paper", "seasonal", tornCard("autumn leaves", [{ k: "script", t: "thankful", size: 11, after: 0 }, caps("for you", 9, PD), { k: "rule", w: 10, after: 2 }, { k: "body", t: "Happy Thanksgiving from our family to yours." }], { seed: 11 }), { months: [11] });
  add("tgdark", "Thanksgiving dark leaves", "seasonal", () => ({ bg: INK, objs: [
    texture("grain", "#FFFFFF", U * 6, 0.04),
    ...[0.1, 0.38, 0.66, 0.94].flatMap((x, i) => [artBox("autumn leaves", W * x, H * 0.04, S * 0.26, S * 0.26, i % 2, { originX: "center", originY: "center", angle: i * 40 }),
      artBox("autumn leaves", W * x, H * 0.96, S * 0.26, S * 0.26, (i + 1) % 2, { originX: "center", originY: "center", angle: 180 + i * 30 })]),
    box(W * 0.18, H * 0.3, W * 0.64, H * 0.4, "transparent", { stroke: tint(A, 0.4), strokeWidth: U * 0.3, name: "Frame line" }),
    ...flow({ x: W * 0.2, y: H * 0.32, w: W * 0.6, h: H * 0.36 }, [{ k: "eyebrow", t: "Happy", c: PAPER, after: 1 }, caps("Thanks", 12, A), caps("giving", 12, A)], "center", "center"),
  ] }), { months: [11] });
  add("hwphoto", "Halloween photo", "seasonal", hero("halloween pumpkins", [{ k: "script", t: "Happy", size: 8, c: AL, after: 0 }, caps("Halloween", 12), { k: "body", t: "Braces tip: skip the sticky, chewy and hard candy. Melty chocolate is the friendliest treat.", c: PAPER, size: 3.5 }]), { months: [10] });
  add("hwframe", "Halloween frame", "seasonal", framed("halloween pumpkin", [{ k: "eyebrow", t: "Have a spooktacular", c: PAPER, after: 1 }, caps("Halloween", 11, A), { k: "body", t: "Costumes welcome at every appointment this week!", c: tint(P, 0.8) }], { bg: PDD, texture: () => texture("dots", PAPER, U * 5, 0.05), size: 0.42 }), { months: [10] });
  add("fallphoto", "Autumn photo", "seasonal", hero("autumn forest", [{ k: "script", t: "Hello", size: 10, c: AL, after: 0 }, caps("Fall", 16), { k: "body", t: "Cozy sweaters, crisp air and healthy smiles.", c: PAPER, size: 3.8 }], { mid: true }), { months: [9, 10] });
  add("xmasphoto", "Merry & bright", "seasonal", hero("christmas lights bokeh", [{ k: "script", t: "Merry", size: 11, c: PAPER, after: 0 }, caps("& bright", 11, AL), { k: "body", t: "Wishing you a joyful holiday season.", c: PAPER, size: 3.8 }], { mid: true, wash: P, extra: () => bokeh(131, 10, AL) }), { months: [12] });
  add("xmasframe", "Ornament frame", "seasonal", () => ({ bg: CREAM, objs: [
    texture("dots", P, U * 5, 0.08),
    ...[0.16, 0.38, 0.62, 0.84].map((x, i) => artBox("christmas ornament", W * x - S * 0.09, -S * 0.02 + (i % 2) * S * 0.06, S * 0.18, S * 0.24, i % 2)),
    ...flow({ x: W * 0.1, y: H * 0.32, w: W * 0.8, h: H * 0.5 }, [{ k: "script", t: "Season's", size: 10, after: 0 }, caps("Greetings", 11, PD), { k: "rule", w: 10, after: 2 }, { k: "body", t: "Thank you for a wonderful year of smiles." }], "center", "center"),
    artBox("pine branch", W * 0.5, H * 0.94, S * 0.7, S * 0.3, 0, { originX: "center", originY: "center" }),
  ], logo: logoAt("br", 0.11) }), { months: [12] });
  add("xmastorn", "Holiday torn paper", "seasonal", tornCard("christmas gifts", [{ k: "script", t: "Happy", size: 9, after: 0 }, caps("holidays", 10, PD), { k: "body", t: "Our office is closed Dec 24 to 26." }], { seed: 17, extra: (x, y, w) => [artBox("holly", x + w - S * 0.12, y - S * 0.08, S * 0.22, S * 0.22, 0)] }), { months: [12] });
  add("winterphoto", "Snowy day", "seasonal", hero("snowy pine forest", [{ k: "script", t: "Let it snow", size: 9, c: PAPER, after: 0 }, caps("Winter smiles", 9), { k: "body", t: "Stay warm out there. We're open regular hours all season.", c: PAPER, size: 3.6 }]), { months: [1, 2] });
  add("nyphoto", "New Year fireworks", "seasonal", hero("fireworks night sky", [{ k: "script", t: "Cheers to", size: 9, c: AL, after: 0 }, caps("a new year", 11), { k: "body", t: "Here's to a year of healthy, happy smiles.", c: PAPER, size: 3.8 }], { mid: true }), { months: [12, 1] });
  add("valphoto", "Valentine torn paper", "seasonal", tornCard("red roses", [{ k: "script", t: "We love", size: 10, after: 0 }, caps("your smile", 10, PD), { k: "body", t: "Happy Valentine's Day!" }], { seed: 23, paper: PAPER, extra: (x, y, w, h) => [artBox("watercolor heart", x - S * 0.06, y + h - S * 0.14, S * 0.2, S * 0.2, 0, { angle: -14 })] }), { months: [2] });
  add("springframe", "Spring flowers frame", "seasonal", framed("watercolor flowers", [{ k: "script", t: "Hello", size: 11, after: 0 }, caps("Spring", 13, PD), { k: "body", t: "Fresh season, fresh smile. Book your spring check-up." }, { k: "cta", t: "Book now", fill: P }], { bg: PXL, size: 0.44 }), { months: [3, 4] });
  add("easterphoto", "Easter photo", "seasonal", tornCard("easter eggs pastel", [{ k: "script", t: "Hoppy", size: 10, after: 0 }, caps("Easter", 11, PD), { k: "body", t: "Wishing you a sweet (but not too sweet) holiday." }], { seed: 29, paper: PAPER }), { months: [3, 4] });
  add("momphoto", "Mother's Day flowers", "seasonal", framed("watercolor flowers", [{ k: "script", t: "Happy", size: 9, after: 0 }, caps("Mother's Day", 9, PD), { k: "body", t: "To the moms who remind us to brush: thank you." }], { bg: tint(A, 0.9), size: 0.4 }), { months: [5] });
  add("summerphoto", "Summer beach", "seasonal", hero("beach summer", [{ k: "script", t: "Summer", size: 11, c: PAPER, after: 0 }, caps("smiles", 13), { k: "body", t: "Enjoy the break and keep brushing!", c: PAPER, size: 3.8 }], { mid: true, dim: 0.28 }), { months: [6, 7, 8] });
  add("july4photo", "4th of July photo", "seasonal", hero("american flag", [{ k: "script", t: "Happy", size: 9, c: PAPER, after: 0 }, caps("4th of July", 11), { k: "body", t: "Our office is closed today. Have a safe, happy holiday!", c: PAPER, size: 3.6 }]), { months: [7] });
  add("schoolphoto", "Back to school flat lay", "seasonal", tornCard("school supplies flat lay", [{ k: "script", t: "Back to school,", size: 7, hand: true, after: 0 }, caps("back to smiles", 8, PD), { k: "body", t: "After-school appointments fill fast. Book yours today." }, { k: "cta", t: "Book now", fill: P }], { seed: 31, paper: PAPER }), { months: [8, 9] });

  // ---- special offers
  add("consultphoto", "Free consult photo", "offers", () => { const { vis, txt } = pic(0.52); return { bg: CREAM, objs: [
    photo(vis.x, vis.y, vis.w, vis.h, { q: "smiling young woman", own: "patient" }),
    shape("seal", vis.x + vis.w - S * 0.26, vis.y + vis.h - S * 0.2, S * 0.22, { fill: A, angle: -10, shadow: shadow(), name: "Badge" }),
    textbox("FREE", { ...td("heading"), fontSize: Math.round(S * 0.055), left: vis.x + vis.w - S * 0.26, top: vis.y + vis.h - S * 0.2 + S * 0.08, width: S * 0.22, textAlign: "center", fill: PAPER, angle: -10, name: "Badge text" }),
    ...flow(txt, [{ k: "eyebrow", t: "This month only", after: 1.2 }, { k: "h1", t: "Your first consult is on us", size: 8.5 }, { k: "body", t: "Meet the doctor, see your options and get a plan." }, { k: "cta", t: "Book a free consult", fill: P }], "left", "center"),
  ], logo: logoAt("tl", 0.13) }; });
  add("giftphoto", "Gift card photo", "offers", tornCard("gift box ribbon", [{ k: "eyebrow", t: "The gift of a smile", after: 1.2 }, { k: "big", t: "$50", size: 18, c: P, after: 1 }, { k: "body", t: "Gift cards toward treatment, whitening or retainers. Ask at the front desk." }], { seed: 37, paper: PAPER }));
  add("giveawayphoto", "Giveaway photo", "offers", hero("electric toothbrush", [{ k: "pill", t: "Giveaway", after: 2 }, { k: "h1", t: "Win an electric toothbrush!", size: 9, c: PAPER }, { k: "list", items: ["Follow us", "Like this post", "Tag a friend"], mark: "num", size: 3.8, c: PAPER, mc: A }]));
  add("alignerphoto", "Clear aligners photo", "offers", hero("clear aligner", [{ k: "eyebrow", t: "Nearly invisible", c: AL, after: 1 }, { k: "h1", t: "Straighten your smile on your schedule", size: 8.5, c: PAPER }, { k: "cta", t: "See if you're a fit" }], { wash: P }));
  add("summerspecial", "Summer special photo", "offers", tornCard("summer sunglasses beach", [{ k: "script", t: "Summer special", size: 8, after: 0 }, caps("$300 off", 12, PD), { k: "body", t: "Start treatment by August 31." }, { k: "cta", t: "Book a free consult", fill: P }], { seed: 41, paper: PAPER }), { months: [6, 7, 8] });

  // ---- fun facts and tips
  add("factphoto", "Fact over photo", "facts", () => ({ bg: PAPER, objs: [
    photo(0, 0, W, H, { q: "toothbrush toothpaste" }), wash(0, 0, W, H, P, 0.3),
    box(W * 0.08, H * (wide ? 0.12 : 0.4), wide ? W * 0.5 : W * 0.84, H * (wide ? 0.76 : 0.52), PAPER, { rx: U * 3, ry: U * 3, shadow: shadow(3, 1.2, 0.25), name: "Card" }),
    ...flow({ x: W * 0.13, y: H * (wide ? 0.12 : 0.4), w: wide ? W * 0.4 : W * 0.74, h: H * (wide ? 0.76 : 0.52) }, [{ k: "script", t: "Did you know?", size: 7, after: 0.6 }, { k: "h1", t: "Enamel is the hardest substance in your body.", size: 6.5, c: PD }, { k: "body", t: "But it can't grow back, so protect it with fluoride toothpaste." }], "left", "center"),
  ], logo: logoAt("br", 0.11) }));
  add("snackphoto", "Snack swap photo", "facts", () => { const { vis, txt } = pic(0.46); return { bg: CREAM, objs: [
    photo(vis.x, vis.y, vis.w, vis.h, { q: "healthy snacks fruit yogurt" }),
    ...flow(txt, [{ k: "eyebrow", t: "Braces-friendly snack swaps", after: 1.4 }, { k: "list", items: ["Popcorn → cheese cubes", "Caramel → yogurt", "Whole apple → apple slices", "Hard candy → smoothie"], size: 3.8 }], "left", "center"),
  ], logo: logoAt("br", 0.11) }; });
  add("brushart", "Brushing tip drawing", "facts", feature("toothbrush", [{ k: "pill", t: "Tip of the week", fill: P, after: 2 }, { k: "h1", t: "Angle your brush at 45° to the gums", size: 8, c: PD }, { k: "body", t: "Small circles, two minutes, twice a day." }], { size: 0.5, ay: 0.26, col: { x: W * 0.1, y: H * 0.5, w: W * 0.8, h: H * 0.4 }, v: "top", bg: PXL, texture: () => texture("dots", P, U * 4, 0.1) }));
  add("waterphoto", "Water tip photo", "facts", hero("glass of water", [{ k: "eyebrow", t: "Quick tip", c: AL, after: 1 }, { k: "h1", t: "Sip water after snacks", size: 9, c: PAPER }, { k: "body", t: "It rinses away food and sugar until you can brush.", c: PAPER, size: 3.6 }], { wash: P }));

  // ---- practice news
  add("officephoto", "Our office", "news", hero("modern dental clinic", [{ k: "pill", t: "Come see us", after: 2 }, { k: "h1", t: "Bright, comfy and made for smiles", size: 9, c: PAPER }, { k: "body", t: "Free parking, Wi-Fi and the best coffee in town.", c: PAPER, size: 3.6 }], { own: "office" }));
  add("teamphoto", "Team photo", "news", () => { const { vis, txt } = pic(0.6); return { bg: P, objs: [
    photo(vis.x, vis.y, vis.w, vis.h, { q: "dental team", own: "team" }), patch("stripes", txt.x, txt.y - U * 1.5, U * 16, U * 2, A, U * 2, 0.8),
    ...flow({ ...txt, y: txt.y + U * 2 }, [{ k: "script", t: "Meet", size: 7, c: AL, after: 0 }, { k: "h1", t: "the team behind your smile", size: 8, c: PAPER }], "left", "center"),
  ], logo: logoAt("br", 0.12) }; });
  add("hiringphoto", "We're hiring photo", "news", tornCard("dental office", [{ k: "script", t: "Join our team", size: 7, after: 0 }, caps("We're hiring", 10, PD), { k: "list", items: ["Orthodontic assistant", "Front desk coordinator"], size: 3.6 }, { k: "cta", t: "Apply today", fill: P }], { seed: 43, paper: PAPER, own: "office" }));
  add("partyphoto", "Celebration photo", "news", hero("party balloons confetti", [{ k: "script", t: "You're invited", size: 7, c: AL, after: 0 }, { k: "h1", t: "Patient appreciation party", size: 9, c: PAPER }, { k: "pill", t: "Sat, June 14 · 11 to 2", fill: A }], { mid: true }));
  add("closedphoto", "Snow day photo", "news", hero("snow storm", [{ k: "pill", t: "Weather update", after: 2 }, { k: "h1", t: "Our office is closed today", size: 9.5, c: PAPER }, { k: "body", t: "We'll call to reschedule. Stay safe and warm!", c: PAPER, size: 3.6 }]));

  // ---- patients and reviews
  add("patientphoto", "Patient spotlight photo", "patients", hero("teenager smiling", [{ k: "eyebrow", t: "Patient spotlight", c: AL, after: 1 }, { k: "h1", t: "Meet Jordan", size: 10, c: PAPER }, { k: "body", t: "Soccer star, honor roll and a brand-new smile.", c: PAPER, size: 3.6 }], { own: "patient" }));
  add("reviewphoto", "Review over photo", "patients", () => ({ bg: PD, objs: [
    photo(0, 0, W, H, { q: "happy woman smiling", own: "patient" }), dim(0.25),
    box(W * 0.08, H * (wide ? 0.14 : 0.5), wide ? W * 0.52 : W * 0.84, H * (wide ? 0.72 : 0.42), PAPER, { rx: U * 3, ry: U * 3, shadow: shadow(3, 1.2, 0.3), name: "Card" }),
    ...flow({ x: W * 0.13, y: H * (wide ? 0.14 : 0.5), w: wide ? W * 0.42 : W * 0.74, h: H * (wide ? 0.72 : 0.42) }, [{ k: "stars", size: 5, after: 2 }, { k: "body", t: "“Paste a real patient review here. Their own words work best.”", size: 4, c: INK }, { k: "sub", t: "Patient's first name", size: 3.6 }], "left", "center"),
  ], logo: logoAt("tl", 0.12) }));
  add("bracesoffphoto", "Braces off celebration", "patients", tornCard("teenager smiling", [{ k: "script", t: "Braces off!", size: 10, after: 0 }, caps("Congrats, Emma", 8, PD), { k: "body", t: "18 months of hard work. Look at that smile!" }], { seed: 47, paper: PAPER, own: "patient",
    extra: (x, y, w, h) => confetti(149, 10, [{ x: x / W, y: y / H, w: w / W, h: h / H }]) }));
  add("thanksphoto", "Thank you photo", "patients", hero("thank you card flowers", [{ k: "script", t: "thank you", size: 13, c: PAPER, after: 1 }, { k: "body", t: "For trusting us with your family's smiles.", c: PAPER, size: 3.8 }], { mid: true }));

  return { CATS, T, ACCENT_FONTS, tint, shade };
};
