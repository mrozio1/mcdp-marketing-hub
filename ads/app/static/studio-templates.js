/* MCDP Studio: the built-in template library for the picture designer.
   Every layout is built from the brand kit at the design's own size: the brand's two colours (and lighter and darker
   versions of them), its heading and body fonts, its logo and its name. Sizes are in U (1% of the short side), so a
   layout works on a square post, a story or a wide banner. Decorations are simple vector shapes drawn here.
   window.MCDPTemplates(kit) returns { CATS, T }: T[key] = { name, cat, months?, build() -> { bg, objs, logo? } }. */
window.MCDPTemplates = function (k) {
  "use strict";
  const { W, H, U, P, A, C, textDefaults, textbox, rect, frame, circleFrame, button, fabric } = k;
  const S = Math.min(W, H), wide = W / H > 1.3, tall = H / W > 1.3;

  // ---------------------------------------------------------------- colours from the brand kit
  const hex = c => { const m = /^#?([0-9a-f]{6})$/i.exec(c || ""); const n = parseInt(m ? m[1] : "0F7471", 16); return [n >> 16, (n >> 8) & 255, n & 255]; };
  const toHex = rgb => "#" + rgb.map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("").toUpperCase();
  const mix = (c, d, t) => { const a = hex(c), b = hex(d); return toHex(a.map((v, i) => v + (b[i] - v) * t)); };
  const tint = (c, t) => mix(c, "#FFFFFF", t), shade = (c, t) => mix(c, "#000000", t);
  const INK = "#1A1A1A", MUTED = "#5B5852", CREAM = "#FBF7F0", PAPER = "#FFFFFF";
  const PL = tint(P, 0.86), AL = tint(A, 0.8), PD = shade(P, 0.35);

  // ---------------------------------------------------------------- text and pieces
  const td = kind => textDefaults(kind);
  const H1 = (t, o = {}) => textbox(t, { ...td("heading"), fontSize: Math.round(U * 10), left: W * 0.08, top: H * 0.3, width: W * 0.84, fill: P, ...o });
  const SUB = (t, o = {}) => textbox(t, { ...td("subhead"), left: W * 0.08, top: H * 0.5, width: W * 0.84, fill: A, ...o });
  const BODY = (t, o = {}) => textbox(t, { ...td("body"), left: W * 0.08, top: H * 0.6, width: W * 0.74, fill: MUTED, ...o });
  const NAME = (o = {}) => textbox(C.brand, { ...td("subhead"), fontSize: Math.round(U * 3.6), left: W * 0.08, top: H * 0.9, width: W * 0.84, fill: P, name: "Practice name", ...o });
  const cta = (label, x, y, o = {}) => {
    const [bg, t, px, py] = button(label, o);
    const cx = x === "c" ? (W - bg.width) / 2 : x;
    bg.set({ left: cx, top: y }); t.set({ left: cx + px, top: y + py });
    return [bg, t];
  };
  const box = (o = {}) => rect({ fill: P, ...o });
  const path = (d, x, y, size, o = {}) => new fabric.Path(d, { left: x, top: y, scaleX: size / 100, scaleY: size / 100, fill: A, strokeWidth: 0, name: "Decoration", ...o });
  // a repeatable random: the same layout every time
  const rnd = seed => { let s = seed; return () => { s = (s * 9301 + 49297) % 233280; return s / 233280; }; };

  // ---------------------------------------------------------------- shapes (drawn in a 100 x 100 box)
  const SHAPE = {
    heart: "M50 92 C22 72 2 54 2 32 C2 14 16 4 30 4 C40 4 46 9 50 17 C54 9 60 4 70 4 C84 4 98 14 98 32 C98 54 78 72 50 92 Z",
    star: (() => { let d = ""; for (let i = 0; i < 10; i++) { const r = i % 2 ? 20 : 50, a = -Math.PI / 2 + i * Math.PI / 5; d += `${i ? "L" : "M"}${(50 + r * Math.cos(a)).toFixed(1)} ${(50 + r * Math.sin(a)).toFixed(1)} `; } return d + "Z"; })(),
    sparkle: "M50 0 C54 34 66 46 100 50 C66 54 54 66 50 100 C46 66 34 54 0 50 C34 46 46 34 50 0 Z",
    tooth: "M20 8 C6 8 0 26 7 46 C13 62 14 88 25 95 C35 100 38 70 50 70 C62 70 65 100 75 95 C86 88 87 62 93 46 C100 26 94 8 80 8 C68 8 62 16 50 16 C38 16 32 8 20 8 Z",
    leaf: "M8 92 C6 44 38 8 94 6 C94 60 58 94 8 92 Z",
    egg: "M50 2 C78 2 94 44 94 62 C94 86 74 98 50 98 C26 98 6 86 6 62 C6 44 22 2 50 2 Z",
    blob: "M62 4 C84 8 98 30 94 54 C90 80 68 98 42 96 C16 94 2 72 4 48 C6 22 36 0 62 4 Z",
    drop: "M50 2 C66 30 86 48 86 66 C86 86 70 98 50 98 C30 98 14 86 14 66 C14 48 34 30 50 2 Z",
    pin: "M50 2 C74 2 90 20 90 42 C90 66 62 84 50 98 C38 84 10 66 10 42 C10 20 26 2 50 2 Z M50 26 C41 26 34 33 34 42 C34 51 41 58 50 58 C59 58 66 51 66 42 C66 33 59 26 50 26 Z",
    check: "M8 52 L22 38 L42 58 L80 18 L94 32 L42 86 Z",
    cross: "M18 4 L50 36 L82 4 L96 18 L64 50 L96 82 L82 96 L50 64 L18 96 L4 82 L36 50 L4 18 Z",
    tag: "M4 18 C4 10 10 4 18 4 L56 4 L96 44 C100 48 100 54 96 58 L58 96 C54 100 48 100 44 96 L4 56 Z M26 18 C21 18 18 21 18 26 C18 31 21 34 26 34 C31 34 34 31 34 26 C34 21 31 18 26 18 Z",
    quote: "M8 60 C8 30 26 12 44 8 L46 18 C34 24 28 34 28 44 L44 44 L44 86 L8 86 Z M56 60 C56 30 74 12 92 8 L94 18 C82 24 76 34 76 44 L92 44 L92 86 L56 86 Z",
    sun: "M50 26 C63 26 74 37 74 50 C74 63 63 74 50 74 C37 74 26 63 26 50 C26 37 37 26 50 26 Z",
    wave: "M0 40 C20 20 40 20 60 40 C80 60 100 60 120 40 L120 100 L0 100 Z",
    shamrock: "M50 50 C40 36 22 34 20 20 C18 8 34 2 42 12 C46 4 54 4 58 12 C66 2 82 8 80 20 C78 34 60 36 50 50 Z M50 50 C64 40 66 22 80 20 C92 18 98 34 88 42 C96 46 96 54 88 58 C98 66 92 82 80 80 C66 78 64 60 50 50 Z M50 50 C36 60 34 78 20 80 C8 82 2 66 12 58 C4 54 4 46 12 42 C2 34 8 18 20 20 C34 22 36 40 50 50 Z M48 50 L54 50 L58 98 L50 98 Z",
    gift: "M8 36 L92 36 L92 52 L8 52 Z M14 52 L86 52 L86 96 L14 96 Z M44 36 L56 36 L56 96 L44 96 Z M50 34 C40 14 20 14 24 28 C26 34 40 36 50 34 Z M50 34 C60 14 80 14 76 28 C74 34 60 36 50 34 Z",
    balloon: "M50 4 C74 4 88 24 88 44 C88 66 68 82 54 84 L58 92 L42 92 L46 84 C32 82 12 66 12 44 C12 24 26 4 50 4 Z",
    pumpkin: "M50 26 C34 22 10 30 8 56 C6 82 28 96 50 92 C72 96 94 82 92 56 C90 30 66 22 50 26 Z M46 26 C46 16 50 8 58 4 L62 10 C56 14 54 20 54 26 Z",
    bracket: "M30 30 L70 30 L70 70 L30 70 Z M42 14 L58 14 L58 86 L42 86 Z",
    calendar: "M8 16 L92 16 L92 94 L8 94 Z",
  };
  const shape = (name, x, y, size, o) => path(SHAPE[name], x, y, size, o);
  // a burst of lines from a centre (fireworks, sunshine)
  const burst = (cx, cy, r, n, color, w = U * 0.8, inner = 0.35) => {
    const lines = [];
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      lines.push(new fabric.Line([cx + Math.cos(a) * r * inner, cy + Math.sin(a) * r * inner, cx + Math.cos(a) * r, cy + Math.sin(a) * r],
        { stroke: color, strokeWidth: w, strokeLineCap: "round", selectable: true }));
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
  // scattered decorations, kept out of a clear area {x, y, w, h} (fractions of the page)
  const LOGO_ZONES = [{ x: 0.7, y: 0.8, w: 0.3, h: 0.2 }, { x: 0, y: 0, w: 0.3, h: 0.16 }];
  const scatter = (seed, n, make, clear) => {
    const r = rnd(seed), out = [], zones = [...(clear ? [].concat(clear) : []), ...LOGO_ZONES];
    let tries = 0;
    while (out.length < n && tries++ < n * 20) {
      const x = r() * W, y = r() * H, s = (0.04 + r() * 0.07) * S;
      if (zones.some(c => x + s > c.x * W && x < (c.x + c.w) * W && y + s > c.y * H && y < (c.y + c.h) * H)) continue;
      out.push(make(x, y, s, r));
    }
    return out;
  };
  const confetti = (seed, n, clear) => scatter(seed, n, (x, y, s, r) => {
    const col = [P, A, tint(A, 0.4), tint(P, 0.5)][Math.floor(r() * 4)];
    return r() < 0.5 ? new fabric.Rect({ left: x, top: y, width: s * 0.28, height: s * 0.6, fill: col, angle: r() * 180, name: "Confetti" })
                     : new fabric.Circle({ left: x, top: y, radius: s * 0.18, fill: col, name: "Confetti" });
  }, clear);
  const logoAt = (pos, size = 0.16) => ({
    br: { x: 0.92, y: 0.94, size, anchor: "br" }, tl: { x: 0.08, y: 0.06, size, anchor: "tl" },
  })[pos];
  const photo = o => frame({ strokeWidth: 0, strokeDashArray: null, ...o });

  const CATS = [["favorites", "★ Favourites"], ["all", "All"], ["seasonal", "Holidays & seasonal"], ["offers", "Special offers"],
                ["facts", "Fun facts & tips"], ["news", "Practice news"], ["patients", "Patients & reviews"], ["basics", "Basics"]];
  const T = {};
  const add = (key, name, cat, build, extra = {}) => { T[key] = { name, cat, build, ...extra }; };

  // ================================================================ basics
  add("blank", "Blank", "basics", () => ({ bg: PAPER, objs: [] }));
  add("photo", "Photo and headline", "basics", () => ({ bg: PAPER, objs: [
    photo({ left: 0, top: 0, width: W, height: H * 0.58 }),
    H1("Grateful for every smile", { fontSize: Math.round(U * 9), top: H * 0.63 }),
    BODY("Happy Thanksgiving from all of us.", { top: H * 0.63 + U * 22, width: W * 0.7 }),
  ], logo: logoAt("br") }));
  add("bold", "Bold statement", "basics", () => ({ bg: P, objs: [
    H1("Your smile, our favourite thing", { fontSize: Math.round(U * 11), left: W * 0.1, width: W * 0.8, fill: PAPER, textAlign: "center" }),
    box({ left: W * 0.42, top: H * 0.3 - U * 6, width: W * 0.16, height: U * 1, fill: A, name: "Accent line" }),
    NAME({ left: W * 0.1, top: H * 0.84, width: W * 0.8, fill: PAPER, textAlign: "center" }),
  ] }));
  add("quote", "Big quote", "basics", () => ({ bg: CREAM, objs: [
    shape("quote", W * 0.08, H * 0.12, S * 0.2, { fill: A, name: "Quote mark" }),
    H1("A smile is the prettiest thing you can wear.", { fontSize: Math.round(U * 8), top: H * 0.34, fill: PD }),
    SUB("— Our team", { top: H * 0.34 + U * 30, fill: A }),
  ], logo: logoAt("br") }));
  add("split", "Photo split", "basics", () => wide ? { bg: PAPER, objs: [
    photo({ left: W * 0.5, top: 0, width: W * 0.5, height: H }),
    H1("Smiles made here", { fontSize: Math.round(U * 11), top: H * 0.28, width: W * 0.38 }),
    BODY("Gentle, modern care for the whole family.", { top: H * 0.28 + U * 28, width: W * 0.36 }),
  ], logo: logoAt("tl", 0.12) } : { bg: PAPER, objs: [
    photo({ left: 0, top: 0, width: W, height: H * 0.5 }),
    box({ left: 0, top: H * 0.5, width: W, height: H * 0.5, fill: P, name: "Colour block" }),
    H1("Smiles made here", { fontSize: Math.round(U * 10), top: H * 0.58, fill: PAPER }),
    BODY("Gentle, modern care for the whole family.", { top: H * 0.58 + U * 15, fill: tint(P, 0.75) }),
  ], logo: logoAt("br") });

  // ================================================================ holidays and seasonal (months: when they're offered first)
  add("newyear", "New Year", "seasonal", () => ({ bg: PD, objs: [
    burst(W * 0.2, H * 0.18, S * 0.16, 14, A), burst(W * 0.82, H * 0.14, S * 0.12, 12, tint(A, 0.4)), burst(W * 0.78, H * 0.8, S * 0.1, 12, tint(P, 0.5)),
    ...scatter(7, 10, (x, y, s) => shape("sparkle", x, y, s * 0.6, { fill: tint(A, 0.3) }), [{ x: 0.05, y: 0.32, w: 0.9, h: 0.42 }, { x: 0, y: 0.82, w: 1, h: 0.18 }]),
    H1("Happy New Year", { fontSize: Math.round(U * 13), top: H * 0.36, fill: PAPER, textAlign: "center" }),
    SUB("Here's to a year of healthy smiles", { top: H * 0.36 + U * 17, fill: tint(A, 0.2), textAlign: "center" }),
    NAME({ top: H * 0.88, fill: tint(P, 0.7), textAlign: "center" }),
  ] }), { months: [12, 1] });
  add("valentine", "Valentine's Day", "seasonal", () => ({ bg: tint(A, 0.88), objs: [
    ...scatter(11, 14, (x, y, s, r) => shape("heart", x, y, s, { fill: r() < 0.5 ? A : tint(P, 0.35), angle: (r() - 0.5) * 40, opacity: 0.9 }), { x: 0.06, y: 0.3, w: 0.88, h: 0.42 }),
    H1("We love your smile", { fontSize: Math.round(U * 12), top: H * 0.34, fill: PD, textAlign: "center" }),
    SUB("Happy Valentine's Day!", { top: H * 0.34 + U * 26, textAlign: "center", fill: A }),
  ], logo: logoAt("br") }), { months: [1, 2] });
  add("kidsmonth", "Children's Dental Health Month", "seasonal", () => ({ bg: PL, objs: [
    shape("tooth", W * 0.5 - S * 0.16, H * 0.08, S * 0.32, { fill: PAPER, stroke: P, strokeWidth: U * 1.2 / (S * 0.32 / 100), name: "Tooth" }),
    ...scatter(5, 8, (x, y, s) => shape("star", x, y, s * 0.7, { fill: A }), { x: 0.05, y: 0.06, w: 0.9, h: 0.88 }).slice(0, 6),
    SUB("FEBRUARY IS", { top: H * 0.46, textAlign: "center", charSpacing: 300, fill: A }),
    H1("National Children's Dental Health Month", { fontSize: Math.round(U * 8), top: H * 0.52, fill: PD, textAlign: "center" }),
    BODY("Healthy habits start young. Brush twice a day, floss daily and see us twice a year.", { left: W * 0.12, width: W * 0.76, top: H * 0.52 + U * 24, textAlign: "center" }),
  ], logo: logoAt("br", 0.13) }), { months: [1, 2] });
  add("stpatricks", "St. Patrick's Day", "seasonal", () => ({ bg: PAPER, objs: [
    ...scatter(17, 9, (x, y, s, r) => shape("shamrock", x, y, s * 1.2, { fill: r() < 0.6 ? P : tint(P, 0.45), angle: (r() - 0.5) * 50 }), { x: 0.06, y: 0.3, w: 0.88, h: 0.44 }),
    H1("Lucky to have you", { fontSize: Math.round(U * 12), top: H * 0.36, textAlign: "center" }),
    SUB("Happy St. Patrick's Day", { top: H * 0.36 + U * 26, textAlign: "center" }),
  ], logo: logoAt("br") }), { months: [3] });
  add("spring", "Spring", "seasonal", () => ({ bg: tint(P, 0.92), objs: [
    shape("leaf", -S * 0.06, H * 0.02, S * 0.4, { fill: tint(P, 0.4), angle: 10 }), shape("leaf", W - S * 0.2, H - S * 0.34, S * 0.32, { fill: tint(A, 0.3), flipX: true }),
    shape("blob", W * 0.66, H * 0.04, S * 0.3, { fill: AL }),
    H1("Spring into a brighter smile", { fontSize: Math.round(U * 10), top: H * 0.34 }),
    BODY("Book a spring check-up and start the season fresh.", { top: H * 0.34 + U * 26 }),
    ...cta("Book now", W * 0.08, H * 0.34 + U * 40),
  ], logo: logoAt("br") }), { months: [3, 4] });
  add("easter", "Easter", "seasonal", () => ({ bg: CREAM, objs: [
    ...[[0.1, 0.62, P], [0.26, 0.68, A], [0.42, 0.63, tint(P, 0.45)], [0.58, 0.69, tint(A, 0.35)]].map(([x, y, c], i) =>
      shape("egg", W * x, H * y, S * 0.2, { fill: c, angle: (i % 2 ? 8 : -8), name: "Egg" })),
    H1("Hoppy Easter!", { fontSize: Math.round(U * 12), top: H * 0.2, textAlign: "center" }),
    SUB("Wishing you a sweet (but not too sweet) holiday", { top: H * 0.2 + U * 15, textAlign: "center", fill: MUTED }),
  ], logo: logoAt("br") }), { months: [3, 4] });
  add("mothersday", "Mother's Day", "seasonal", () => ({ bg: tint(A, 0.86), objs: [
    circleFrame({ left: W * 0.5 - S * 0.22, top: H * 0.08, radius: S * 0.22, strokeWidth: 0, strokeDashArray: null }),
    shape("heart", W * 0.5 + S * 0.14, H * 0.08 + S * 0.32, S * 0.12, { fill: A }),
    H1("Happy Mother's Day", { fontSize: Math.round(U * 10), top: H * 0.08 + S * 0.5, textAlign: "center", fill: PD }),
    BODY("To the moms who remind us to brush: thank you.", { left: W * 0.14, width: W * 0.72, top: H * 0.08 + S * 0.5 + U * 14, textAlign: "center" }),
  ], logo: logoAt("br", 0.13) }), { months: [5] });
  add("smileday", "National Smile Day", "seasonal", () => ({ bg: AL, objs: [
    burst(W * 0.5, H * 0.25, S * 0.22, 18, A, U * 1.1, 0.68), new fabric.Circle({ left: W * 0.5 - S * 0.15, top: H * 0.25 - S * 0.15, radius: S * 0.15, fill: A, name: "Sun" }),
    new fabric.Path("M 26 56 Q 50 84 74 56", { left: W * 0.5 - S * 0.1, top: H * 0.25 - S * 0.01, scaleX: S * 0.2 / 48, scaleY: S * 0.2 / 48, fill: "", stroke: PAPER, strokeWidth: 7, strokeLineCap: "round", name: "Smile" }),
    new fabric.Circle({ left: W * 0.5 - S * 0.075, top: H * 0.25 - S * 0.07, radius: S * 0.022, fill: PAPER, name: "Eye" }),
    new fabric.Circle({ left: W * 0.5 + S * 0.031, top: H * 0.25 - S * 0.07, radius: S * 0.022, fill: PAPER, name: "Eye" }),
    SUB("MAY 31", { top: H * 0.52, textAlign: "center", charSpacing: 300, fill: PD }),
    H1("Happy National Smile Day", { fontSize: Math.round(U * 9), top: H * 0.58, textAlign: "center", fill: PD }),
    BODY("Show us yours! Tag us in your best smile today.", { left: W * 0.12, width: W * 0.76, top: H * 0.58 + U * 22, textAlign: "center" }),
  ] }), { months: [5] });
  add("fathersday", "Father's Day", "seasonal", () => ({ bg: PD, objs: [
    photo({ left: W * 0.08, top: H * 0.08, width: W * 0.84, height: H * 0.46, rx: U * 2, ry: U * 2 }),
    H1("Happy Father's Day", { fontSize: Math.round(U * 10), top: H * 0.6, fill: PAPER }),
    BODY("Here's to the dads who never miss a check-up.", { top: H * 0.6 + U * 14, fill: tint(P, 0.7) }),
  ], logo: logoAt("br", 0.13) }), { months: [6] });
  add("summer", "Summer smiles", "seasonal", () => ({ bg: tint(A, 0.85), objs: [
    burst(W * 0.84, H * 0.13, S * 0.14, 16, A, U * 0.8, 0.6), new fabric.Circle({ left: W * 0.84 - S * 0.07, top: H * 0.13 - S * 0.07, radius: S * 0.07, fill: A, name: "Sun" }),
    path(SHAPE.wave, 0, H * 0.76, W / 1.2 * 1.2, { fill: tint(P, 0.4), scaleY: (H * 0.3) / 100, name: "Wave" }),
    path(SHAPE.wave, -W * 0.2, H * 0.82, W / 1.2 * 1.4, { fill: P, scaleY: (H * 0.25) / 100, name: "Wave" }),
    H1("Summer smiles", { fontSize: Math.round(U * 13), top: H * 0.3 }),
    BODY("Sun's out, braces on. Enjoy the break and keep brushing!", { top: H * 0.3 + U * 17 }),
  ] }), { months: [6, 7, 8] });
  add("july4", "4th of July", "seasonal", () => ({ bg: PD, objs: [
    burst(W * 0.22, H * 0.2, S * 0.18, 16, A), burst(W * 0.78, H * 0.16, S * 0.14, 14, PAPER), burst(W * 0.16, H * 0.86, S * 0.08, 12, tint(A, 0.4)),
    ...scatter(23, 12, (x, y, s) => shape("star", x, y, s * 0.6, { fill: PAPER, opacity: 0.85 }), { x: 0.04, y: 0.34, w: 0.92, h: 0.42 }),
    H1("Happy 4th of July", { fontSize: Math.round(U * 12), top: H * 0.38, fill: PAPER, textAlign: "center" }),
    SUB("Our office is closed today. Have a safe, happy holiday!", { top: H * 0.38 + U * 26, fill: tint(A, 0.2), textAlign: "center" }),
  ] }), { months: [7] });
  add("backtoschool", "Back to school", "seasonal", () => ({ bg: PAPER, objs: [
    ...Array.from({ length: Math.floor(H / (U * 6)) }, (_, i) => new fabric.Line([0, U * 6 * (i + 1), W, U * 6 * (i + 1)], { stroke: tint(P, 0.82), strokeWidth: Math.max(1, U * 0.25), name: "Ruled line" })),
    new fabric.Line([W * 0.12, 0, W * 0.12, H], { stroke: tint(A, 0.35), strokeWidth: Math.max(2, U * 0.4), name: "Margin line" }),
    H1("Back to school, back to smiles", { fontSize: Math.round(U * 10), left: W * 0.18, width: W * 0.74, top: H * 0.24 }),
    BODY("Book an after-school appointment before the calendar fills up.", { left: W * 0.18, width: W * 0.68, top: H * 0.24 + U * 26, fill: INK }),
    ...cta("Book after school", W * 0.18, H * 0.24 + U * 42),
  ], logo: logoAt("br", 0.13) }), { months: [8, 9] });
  add("orthomonth", "Orthodontic Health Month", "seasonal", () => ({ bg: P, objs: [
    shape("bracket", W * 0.08, H * 0.08, S * 0.14, { fill: A, name: "Bracket" }), shape("bracket", W * 0.08 + S * 0.16, H * 0.08, S * 0.14, { fill: tint(A, 0.4), name: "Bracket" }),
    SUB("OCTOBER IS", { top: H * 0.34, charSpacing: 300, fill: tint(A, 0.2) }),
    H1("National Orthodontic Health Month", { fontSize: Math.round(U * 10), top: H * 0.4, fill: PAPER }),
    BODY("A great time to ask whether your child is ready for their first orthodontic check.", { top: H * 0.4 + U * 26, fill: tint(P, 0.75) }),
    ...cta("Book a free consult", W * 0.08, H * 0.4 + U * 44, { bg: { fill: A } }),
  ] }), { months: [10] });
  add("halloween", "Halloween", "seasonal", () => ({ bg: shade(P, 0.6), objs: [
    shape("pumpkin", W * 0.06, H * 0.7, S * 0.24, { fill: A }), shape("pumpkin", W * 0.74, H * 0.74, S * 0.18, { fill: tint(A, 0.25) }),
    ...scatter(31, 8, (x, y, s) => shape("star", x, y, s * 0.5, { fill: tint(A, 0.5), opacity: 0.8 }), { x: 0.05, y: 0.16, w: 0.9, h: 0.56 }),
    H1("Brace yourself, it's Halloween!", { fontSize: Math.round(U * 10), top: H * 0.2, fill: PAPER }),
    BODY("Skip the sticky, chewy and hard candy. Chocolate that melts is the braces-friendly treat.", { top: H * 0.2 + U * 26, fill: tint(P, 0.75) }),
  ], logo: logoAt("tl", 0.12) }), { months: [10] });
  add("thanksgiving", "Thanksgiving", "seasonal", () => ({ bg: CREAM, objs: [
    ...scatter(41, 10, (x, y, s, r) => shape("leaf", x, y, s * 1.1, { fill: [A, tint(A, 0.3), shade(A, 0.2), tint(P, 0.3)][Math.floor(r() * 4)], angle: r() * 360 }), { x: 0.06, y: 0.28, w: 0.88, h: 0.46 }),
    H1("Thankful for you", { fontSize: Math.round(U * 12), top: H * 0.34, textAlign: "center" }),
    SUB("Happy Thanksgiving from our family to yours", { top: H * 0.34 + U * 16, textAlign: "center" }),
  ], logo: logoAt("br") }), { months: [11] });
  add("holidays", "Happy holidays", "seasonal", () => ({ bg: P, objs: [
    ...scatter(53, 11, (x, y, s) => snowflake(x + s / 2, y + s / 2, s * 0.5, tint(P, 0.6)), [{ x: 0.04, y: 0.3, w: 0.92, h: 0.42 }, { x: 0, y: 0.82, w: 1, h: 0.18 }]),
    H1("Happy holidays", { fontSize: Math.round(U * 13), top: H * 0.36, fill: PAPER, textAlign: "center" }),
    SUB("From our family to yours", { top: H * 0.36 + U * 17, fill: tint(A, 0.2), textAlign: "center" }),
    NAME({ top: H * 0.88, fill: tint(P, 0.75), textAlign: "center" }),
  ] }), { months: [12] });
  add("winter", "Winter", "seasonal", () => ({ bg: tint(P, 0.9), objs: [
    snowflake(W * 0.18, H * 0.16, S * 0.12, tint(P, 0.4)), snowflake(W * 0.84, H * 0.26, S * 0.08, P), snowflake(W * 0.76, H * 0.82, S * 0.1, tint(P, 0.5)),
    H1("Cold outside, warm smiles inside", { fontSize: Math.round(U * 10), top: H * 0.34 }),
    BODY("Tip: cold air can make teeth sensitive. Breathe through your nose and ask us about sensitivity toothpaste.", { top: H * 0.34 + U * 26 }),
  ], logo: logoAt("br") }), { months: [1, 2] });
  add("holidayhours", "Holiday hours", "seasonal", () => ({ bg: PAPER, objs: [
    box({ left: 0, top: 0, width: W, height: H * 0.3, name: "Header band" }),
    snowflake(W * 0.86, H * 0.1, S * 0.06, tint(P, 0.5)), snowflake(W * 0.74, H * 0.2, S * 0.035, tint(P, 0.35)),
    H1("Holiday hours", { top: H * 0.1, width: W * 0.6, fill: PAPER }),
    textbox("Dec 24: Closed\nDec 25: Closed\nDec 31: Open 8 to 12\nJan 1: Closed", { ...td("subhead"), fontWeight: 400, lineHeight: 1.6, left: W * 0.08, top: H * 0.38, width: W * 0.84, fill: INK }),
    BODY("Thank you for being part of our family.", { top: H * 0.82, width: W * 0.6 }),
  ], logo: logoAt("br") }), { months: [11, 12] });

  // ================================================================ special offers
  add("offer", "Free consult", "offers", () => ({ bg: "#FBF6EE", objs: [
    new fabric.Circle({ left: W - S * 0.5, top: -S * 0.14, radius: S * 0.36, fill: A, name: "Circle", opacity: 0.9 }),
    H1("Free consults all November", { fontSize: Math.round(U * 10) }),
    BODY("Meet the doctor, get a plan and see your options. No pressure.", { top: H * 0.3 + U * 26, width: W * 0.7 }),
    ...cta("Book a free consult", W * 0.08, H * 0.74),
  ], logo: logoAt("tl", 0.18) }));
  add("percent", "Percent off", "offers", () => ({ bg: P, objs: [
    shape("tag", W * 0.5 - S * 0.3, H * 0.08, S * 0.6, { fill: A, name: "Price tag", angle: 0 }),
    textbox("20%\nOFF", { ...td("heading"), fontSize: Math.round(S * 0.12), lineHeight: 0.95, left: W * 0.5 - S * 0.24, top: H * 0.08 + S * 0.16, width: S * 0.5, textAlign: "center", fill: PAPER, name: "Offer" }),
    H1("Clear aligner treatment", { fontSize: Math.round(U * 8), top: H * 0.08 + S * 0.66, fill: PAPER, textAlign: "center" }),
    BODY("Limited time. Ask about our new patient special.", { left: W * 0.12, width: W * 0.76, top: H * 0.08 + S * 0.66 + U * 12, fill: tint(P, 0.75), textAlign: "center" }),
  ] }));
  add("monthly", "Monthly payments", "offers", () => ({ bg: PAPER, objs: [
    box({ left: W * 0.08, top: H * 0.1, width: W * 0.84, height: H * 0.42, rx: U * 3, ry: U * 3, fill: PL, name: "Panel" }),
    SUB("BRACES FROM", { top: H * 0.15, textAlign: "center", charSpacing: 250, fill: P }),
    textbox("$99/mo", { ...td("heading"), fontSize: Math.round(U * 18), left: W * 0.08, top: H * 0.2, width: W * 0.84, textAlign: "center", fill: PD, name: "Price" }),
    BODY("Flexible payment plans. 0% interest.", { left: W * 0.1, width: W * 0.8, top: H * 0.2 + U * 22, textAlign: "center", fill: P }),
    H1("A great smile fits your budget", { fontSize: Math.round(U * 7.5), top: H * 0.6 }),
    ...cta("Ask about payment plans", W * 0.08, H * 0.6 + U * 20),
  ], logo: logoAt("br", 0.13) }));
  add("referral", "Refer a friend", "offers", () => ({ bg: tint(A, 0.85), objs: [
    shape("gift", W * 0.5 - S * 0.16, H * 0.07, S * 0.32, { fill: P, name: "Gift" }),
    H1("Refer a friend, get a $50 gift card", { fontSize: Math.round(U * 9), top: H * 0.07 + S * 0.38, textAlign: "center", fill: PD }),
    BODY("When someone you send our way starts treatment, we'll say thanks.", { left: W * 0.12, width: W * 0.76, top: H * 0.07 + S * 0.38 + U * 22, textAlign: "center" }),
    ...cta("Ask at your next visit", "c", H * 0.07 + S * 0.38 + U * 36, { bg: { fill: P } }),
  ] }));
  add("limited", "Limited time", "offers", () => ({ bg: PD, objs: [
    box({ left: W * 0.62, top: H * 0.08, width: W * 0.3, height: U * 10, rx: U * 5, ry: U * 5, fill: A, name: "Badge" }),
    textbox("ENDS NOV 30", { ...td("cta"), left: W * 0.62, top: H * 0.08 + U * 3, width: W * 0.3, textAlign: "center", fill: PAPER, name: "Badge text" }),
    H1("Limited spots this month", { fontSize: Math.round(U * 11), top: H * 0.3, fill: PAPER }),
    BODY("Start treatment by the end of the month and your first adjustment is on us.", { top: H * 0.3 + U * 27, fill: tint(P, 0.75) }),
    ...cta("Claim your spot", W * 0.08, H * 0.3 + U * 42, { bg: { fill: A } }),
  ], logo: logoAt("br") }));
  add("giveaway", "Giveaway", "offers", () => ({ bg: PAPER, objs: [
    ...confetti(61, 26, { x: 0.05, y: 0.18, w: 0.9, h: 0.74 }),
    H1("GIVEAWAY!", { fontSize: Math.round(U * 14), top: H * 0.2, textAlign: "center" }),
    SUB("Win an electric toothbrush and a smile kit", { top: H * 0.2 + U * 17, textAlign: "center", fill: A }),
    textbox("1. Follow us\n2. Like this post\n3. Tag a friend", { ...td("subhead"), fontWeight: 600, lineHeight: 1.6, left: W * 0.25, top: H * 0.2 + U * 30, width: W * 0.5, fill: INK, name: "Steps" }),
    BODY("Winner picked Friday. Good luck!", { left: W * 0.1, width: W * 0.8, top: H * 0.82, textAlign: "center" }),
  ] }));
  add("newpatient", "New patient special", "offers", () => ({ bg: CREAM, objs: [
    SUB("NEW PATIENT SPECIAL", { top: H * 0.1, charSpacing: 200 }),
    H1("Your first visit includes", { fontSize: Math.round(U * 8), top: H * 0.16 }),
    ...["Exam with the doctor", "Digital x-rays and photos", "A personal treatment plan"].flatMap((tx, i) => [
      shape("check", W * 0.08, H * 0.36 + i * U * 11, U * 6, { fill: A, name: "Check" }),
      textbox(tx, { ...td("subhead"), fontWeight: 500, left: W * 0.08 + U * 9, top: H * 0.36 + i * U * 11 + U * 0.5, width: W * 0.7, fill: INK }),
    ]),
    textbox("Free", { ...td("heading"), fontSize: Math.round(U * 14), left: W * 0.08, top: H * 0.36 + U * 36, width: W * 0.5, fill: P, name: "Price" }),
    ...cta("Book your visit", W * 0.08, H * 0.36 + U * 54),
  ], logo: logoAt("br", 0.13) }));

  // ================================================================ fun facts and tips
  add("tip", "Tip of the week", "facts", () => ({ bg: PAPER, objs: [
    box({ left: U * 4, top: U * 4, width: W - U * 8, height: H - U * 8, fill: "transparent", stroke: P, strokeWidth: U * 1, name: "Border" }),
    SUB("TIP", { charSpacing: 400, left: W * 0.12, top: H * 0.16, width: W * 0.5, name: "Label" }),
    H1("Brush for two full minutes, twice a day", { fontSize: Math.round(U * 9), left: W * 0.12, top: H * 0.26, width: W * 0.76 }),
    BODY("Play a song you like. Most are about three minutes, so stop at the second chorus.", { left: W * 0.12, top: H * 0.62, width: W * 0.7 }),
  ], logo: { x: 0.88, y: 0.9, size: 0.14, anchor: "br" } }));
  add("didyouknow", "Did you know?", "facts", () => ({ bg: P, objs: [
    textbox("?", { ...td("heading"), fontSize: Math.round(S * 0.5), left: W * 0.56, top: -S * 0.06, width: S * 0.5, textAlign: "center", fill: tint(P, 0.18), name: "Question mark", keep: true }),
    SUB("DID YOU KNOW?", { top: H * 0.3, charSpacing: 300, fill: A }),
    H1("Tooth enamel is the hardest substance in the human body.", { fontSize: Math.round(U * 9), top: H * 0.37, fill: PAPER }),
    BODY("Even so, it can't grow back, so protect it.", { top: H * 0.37 + U * 34, fill: tint(P, 0.75) }),
  ], logo: logoAt("br") }));
  add("mythfact", "Myth vs fact", "facts", () => {
    const col = wide ? W * 0.42 : W * 0.84, x2 = wide ? W * 0.5 : W * 0.08, y2 = wide ? H * 0.22 : H * 0.55;
    return { bg: PAPER, objs: [
      box({ left: W * 0.08, top: H * 0.22, width: col, height: wide ? H * 0.66 : H * 0.28, rx: U * 2.5, ry: U * 2.5, fill: tint("#C0392B", 0.88), name: "Myth panel" }),
      box({ left: x2, top: y2, width: col, height: wide ? H * 0.66 : H * 0.28, rx: U * 2.5, ry: U * 2.5, fill: PL, name: "Fact panel" }),
      H1("Myth or fact?", { fontSize: Math.round(U * 8), top: H * 0.07 }),
      shape("cross", W * 0.08 + U * 4, H * 0.22 + U * 4, U * 6, { fill: "#C0392B", name: "Cross" }),
      textbox("MYTH", { ...td("subhead"), left: W * 0.08 + U * 12, top: H * 0.22 + U * 4.5, width: col * 0.6, fill: "#C0392B" }),
      textbox("You're too old for braces.", { ...td("subhead"), fontWeight: 500, left: W * 0.08 + U * 4, top: H * 0.22 + U * 14, width: col - U * 8, fill: INK }),
      shape("check", x2 + U * 4, y2 + U * 4, U * 6, { fill: P, name: "Check" }),
      textbox("FACT", { ...td("subhead"), left: x2 + U * 12, top: y2 + U * 4.5, width: col * 0.6, fill: P }),
      textbox("Healthy teeth can be moved at any age. Many of our patients are adults.", { ...td("subhead"), fontWeight: 500, left: x2 + U * 4, top: y2 + U * 14, width: col - U * 8, fill: INK }),
    ], logo: logoAt("br", 0.12) };
  });
  add("bignumber", "Big number", "facts", () => ({ bg: CREAM, objs: [
    textbox("32", { ...td("heading"), fontSize: Math.round(S * 0.36), left: W * 0.08, top: H * 0.1, width: W * 0.84, fill: P, name: "Number" }),
    H1("adult teeth, wisdom teeth included", { fontSize: Math.round(U * 7), top: H * 0.1 + S * 0.42, fill: PD }),
    BODY("Most people have 28 once wisdom teeth are out. How many do you have?", { top: H * 0.1 + S * 0.42 + U * 20 }),
  ], logo: logoAt("br") }));
  add("thisorthat", "This or that?", "facts", () => ({ bg: PAPER, objs: [
    box({ left: 0, top: 0, width: W / 2, height: H, fill: P, name: "Left half" }), box({ left: W / 2, top: 0, width: W / 2, height: H, fill: A, name: "Right half" }),
    H1("This or that?", { fontSize: Math.round(U * 8), top: H * 0.08, fill: PAPER, textAlign: "center" }),
    new fabric.Circle({ left: W / 2 - U * 9, top: H / 2 - U * 9, radius: U * 9, fill: PAPER, name: "VS circle" }),
    textbox("VS", { ...td("heading"), fontSize: Math.round(U * 7), left: W / 2 - U * 9, top: H / 2 - U * 4.5, width: U * 18, textAlign: "center", fill: INK }),
    textbox("Coloured bands", { ...td("heading"), fontSize: Math.round(U * 7), left: W * 0.04, top: H * 0.64, width: W * 0.42, textAlign: "center", fill: PAPER }),
    textbox("Clear bands", { ...td("heading"), fontSize: Math.round(U * 7), left: W * 0.54, top: H * 0.64, width: W * 0.42, textAlign: "center", fill: PAPER }),
    BODY("Tell us in the comments!", { left: W * 0.1, width: W * 0.8, top: H * 0.88, textAlign: "center", fill: PAPER }),
  ] }));
  add("quiz", "Quick quiz", "facts", () => ({ bg: tint(P, 0.9), objs: [
    SUB("QUICK QUIZ", { top: H * 0.08, charSpacing: 300 }),
    H1("How long should you brush?", { fontSize: Math.round(U * 9), top: H * 0.14 }),
    ...["A. 30 seconds", "B. 2 minutes", "C. 5 minutes"].flatMap((tx, i) => [
      box({ left: W * 0.08, top: H * 0.42 + i * U * 13, width: W * 0.84, height: U * 10, rx: U * 5, ry: U * 5, fill: PAPER, stroke: tint(P, 0.5), strokeWidth: Math.max(2, U * 0.3), name: "Answer" }),
      textbox(tx, { ...td("subhead"), fontWeight: 600, left: W * 0.12, top: H * 0.42 + i * U * 13 + U * 2.6, width: W * 0.7, fill: INK }),
    ]),
    BODY("Answer in tomorrow's story!", { top: H * 0.42 + U * 42, fill: P }),
  ], logo: logoAt("br", 0.12) }));
  add("steps", "3 steps", "facts", () => ({ bg: PAPER, objs: [
    H1("3 steps to a healthy smile", { fontSize: Math.round(U * 8.5), top: H * 0.08 }),
    ...[["Brush", "Twice a day, two minutes each time."], ["Floss", "Once a day, before bed."], ["Visit", "Every six months for a check-up."]].flatMap(([h, b], i) => {
      const y = H * 0.32 + i * U * 20;
      return [new fabric.Circle({ left: W * 0.08, top: y, radius: U * 6, fill: i === 1 ? A : P, name: "Number circle" }),
        textbox(String(i + 1), { ...td("heading"), fontSize: Math.round(U * 6.5), left: W * 0.08, top: y + U * 2, width: U * 12, textAlign: "center", fill: PAPER }),
        textbox(h, { ...td("subhead"), left: W * 0.08 + U * 16, top: y + U * 0.5, width: W * 0.6, fill: INK }),
        textbox(b, { ...td("body"), left: W * 0.08 + U * 16, top: y + U * 6.5, width: W * 0.64, fill: MUTED })];
    }),
  ], logo: logoAt("br", 0.12) }));
  add("snacks", "Braces-friendly snacks", "facts", () => {
    const col = W * 0.4;
    return { bg: CREAM, objs: [
      H1("Braces-friendly snacks", { fontSize: Math.round(U * 8.5), top: H * 0.08 }),
      shape("check", W * 0.08, H * 0.28, U * 6, { fill: P }), textbox("ENJOY", { ...td("subhead"), left: W * 0.08 + U * 8, top: H * 0.28 + U * 0.5, width: col, fill: P }),
      textbox("Yogurt\nBananas\nCheese\nSmoothies\nPasta", { ...td("body"), lineHeight: 1.7, left: W * 0.08, top: H * 0.28 + U * 9, width: col, fill: INK }),
      shape("cross", W * 0.54, H * 0.28, U * 6, { fill: "#C0392B" }), textbox("SKIP", { ...td("subhead"), left: W * 0.54 + U * 8, top: H * 0.28 + U * 0.5, width: col, fill: "#C0392B" }),
      textbox("Popcorn\nHard candy\nCaramel\nWhole apples\nIce", { ...td("body"), lineHeight: 1.7, left: W * 0.54, top: H * 0.28 + U * 9, width: col, fill: INK }),
    ], logo: logoAt("br", 0.12) };
  });

  // ================================================================ practice news
  add("notice", "Office hours", "news", () => ({ bg: PAPER, objs: [
    box({ left: 0, top: 0, width: W, height: H * 0.3, name: "Header band" }),
    H1("Our new hours", { fontSize: Math.round(U * 9), top: H * 0.1, fill: PAPER }),
    textbox("Monday to Thursday: 8 to 5\nFriday: 8 to 2\nSaturday and Sunday: Closed", { ...td("subhead"), fontWeight: 400, lineHeight: 1.6, left: W * 0.08, top: H * 0.38, width: W * 0.84, fill: INK }),
    BODY("Questions? Call or text us anytime.", { top: H * 0.78, width: W * 0.6 }),
  ], logo: logoAt("br") }));
  add("closed", "Office closed", "news", () => ({ bg: PD, objs: [
    snowflake(W * 0.84, H * 0.12, S * 0.08, tint(P, 0.5)),
    SUB("WEATHER UPDATE", { top: H * 0.3, charSpacing: 250, fill: A }),
    H1("Our office is closed today", { fontSize: Math.round(U * 11), top: H * 0.36, fill: PAPER }),
    BODY("We'll call to reschedule. Stay safe and warm!", { top: H * 0.36 + U * 27, fill: tint(P, 0.75) }),
  ], logo: logoAt("br") }));
  add("team", "Meet the team", "news", () => ({ bg: "#F4F1EA", objs: [
    circleFrame({ left: W * 0.5 - S * 0.22, top: H * 0.08, radius: S * 0.22 }),
    H1("Meet Dr. Jordan", { fontSize: Math.round(U * 8), top: H * 0.08 + S * 0.47, textAlign: "center" }),
    SUB("Orthodontist", { top: H * 0.08 + S * 0.47 + U * 11, textAlign: "center" }),
    BODY("Loves hiking with her two kids and has never met a smoothie she didn't like.", { left: W * 0.14, width: W * 0.72, top: H * 0.08 + S * 0.47 + U * 19, textAlign: "center" }),
  ] }));
  add("welcome", "Welcome aboard", "news", () => ({ bg: P, objs: [
    ...confetti(71, 16, { x: 0.1, y: 0.06, w: 0.8, h: 0.9 }),
    circleFrame({ left: W * 0.5 - S * 0.2, top: H * 0.1, radius: S * 0.2, strokeWidth: U * 1, stroke: PAPER, strokeDashArray: null }),
    SUB("PLEASE WELCOME", { top: H * 0.1 + S * 0.46, textAlign: "center", charSpacing: 250, fill: A }),
    H1("Dr. Morgan Lee", { fontSize: Math.round(U * 10), top: H * 0.1 + S * 0.52, textAlign: "center", fill: PAPER }),
    BODY("Our newest orthodontist joins us from Ann Arbor. Say hi at your next visit!", { left: W * 0.12, width: W * 0.76, top: H * 0.1 + S * 0.52 + U * 14, textAlign: "center", fill: tint(P, 0.75) }),
  ] }));
  add("event", "Event", "news", () => ({ bg: PAPER, objs: [
    box({ left: W * 0.08, top: H * 0.08, width: U * 22, height: U * 24, fill: A, rx: U * 2, ry: U * 2, name: "Date block" }),
    textbox("DEC", { ...td("subhead"), left: W * 0.08, top: H * 0.08 + U * 3, width: U * 22, textAlign: "center", fill: PAPER, name: "Month" }),
    textbox("12", { ...td("heading"), fontSize: Math.round(U * 10), left: W * 0.08, top: H * 0.08 + U * 9, width: U * 22, textAlign: "center", fill: PAPER, name: "Day" }),
    H1("Holiday open house", { fontSize: Math.round(U * 9), top: H * 0.42 }),
    BODY("Hot cocoa, photos with Santa and a raffle. 4 to 7 pm at the Traverse City office.", { top: H * 0.42 + U * 22, width: W * 0.76 }),
    ...cta("Save your spot", W * 0.08, H * 0.8),
  ] }));
  add("hiring", "We're hiring", "news", () => ({ bg: CREAM, objs: [
    box({ left: 0, top: 0, width: W, height: H * 0.42, fill: P, name: "Header band" }),
    SUB("JOIN OUR TEAM", { top: H * 0.12, charSpacing: 300, fill: A }),
    H1("We're hiring!", { fontSize: Math.round(U * 13), top: H * 0.18, fill: PAPER }),
    textbox("• Orthodontic assistant\n• Front desk coordinator\n• Treatment coordinator", { ...td("subhead"), fontWeight: 500, lineHeight: 1.6, left: W * 0.08, top: H * 0.5, width: W * 0.84, fill: INK, name: "Roles" }),
    ...cta("Apply today", W * 0.08, H * 0.5 + U * 26),
  ], logo: logoAt("br", 0.13) }));
  add("anniversary", "Anniversary", "news", () => ({ bg: PD, objs: [
    ...scatter(83, 10, (x, y, s) => shape("sparkle", x, y, s * 0.6, { fill: tint(A, 0.3) }), { x: 0.1, y: 0.1, w: 0.8, h: 0.8 }),
    SUB("CELEBRATING", { top: H * 0.16, textAlign: "center", charSpacing: 300, fill: A }),
    textbox("10", { ...td("heading"), fontSize: Math.round(S * 0.34), left: 0, top: H * 0.2, width: W, textAlign: "center", fill: PAPER, name: "Number" }),
    H1("years of smiles", { fontSize: Math.round(U * 9), top: H * 0.2 + S * 0.38, textAlign: "center", fill: PAPER }),
    BODY("Thank you for trusting us with your family's smiles.", { left: W * 0.12, width: W * 0.76, top: H * 0.2 + S * 0.38 + U * 12, textAlign: "center", fill: tint(P, 0.75) }),
  ] }));
  add("newoffice", "New location", "news", () => ({ bg: PAPER, objs: [
    shape("pin", W * 0.08, H * 0.1, S * 0.2, { fill: A, name: "Map pin" }),
    SUB("NOW OPEN", { top: H * 0.1 + S * 0.26, charSpacing: 300 }),
    H1("Our new Traverse City office", { fontSize: Math.round(U * 10), top: H * 0.1 + S * 0.32 }),
    BODY("123 Main Street. Same great team, more room for smiles.", { top: H * 0.1 + S * 0.32 + U * 25 }),
    ...cta("Get directions", W * 0.08, H * 0.1 + S * 0.32 + U * 38),
  ], logo: logoAt("br", 0.13) }));

  // ================================================================ patients and reviews
  add("review", "5-star review", "patients", () => ({ bg: CREAM, objs: [
    ...[0, 1, 2, 3, 4].map(i => shape("star", W * 0.08 + i * U * 9, H * 0.12, U * 8, { fill: A, name: "Star" })),
    shape("quote", W * 0.72, H * 0.06, S * 0.18, { fill: tint(P, 0.8), name: "Quote mark" }),
    H1("“The whole team made my son feel at home. His smile is amazing!”", { fontSize: Math.round(U * 7), top: H * 0.26, fill: PD, lineHeight: 1.2 }),
    SUB("— Sarah, mom of two", { top: H * 0.26 + U * 34 }),
  ], logo: logoAt("br") }));
  add("bracesoff", "Braces off day!", "patients", () => ({ bg: PAPER, objs: [
    photo({ left: W * 0.08, top: H * 0.08, width: W * 0.84, height: H * 0.52, rx: U * 3, ry: U * 3 }),
    ...confetti(97, 16, [{ x: 0.1, y: 0.1, w: 0.8, h: 0.48 }, { x: 0.05, y: 0.62, w: 0.9, h: 0.3 }]),
    H1("Braces off day!", { fontSize: Math.round(U * 11), top: H * 0.65, textAlign: "center" }),
    SUB("Congratulations, Emma!", { top: H * 0.65 + U * 14, textAlign: "center" }),
  ], logo: logoAt("br", 0.12) }));
  add("beforeafter", "Before and after", "patients", () => {
    const col = (wide ? W : W) * 0.4, top = H * 0.2, hh = wide ? H * 0.62 : H * 0.5;
    return { bg: PAPER, objs: [
      H1("What a difference!", { fontSize: Math.round(U * 7.5), top: H * 0.06, textAlign: "center" }),
      photo({ left: W * 0.08, top, width: col, height: hh, rx: U * 2, ry: U * 2, name: "Before photo" }),
      photo({ left: W * 0.52, top, width: col, height: hh, rx: U * 2, ry: U * 2, name: "After photo" }),
      box({ left: W * 0.08 + U * 2, top: top + U * 2, width: U * 18, height: U * 7, rx: U * 3.5, ry: U * 3.5, fill: INK, opacity: 0.75, name: "Label" }),
      textbox("BEFORE", { ...td("cta"), fontSize: Math.round(U * 3), left: W * 0.08 + U * 2, top: top + U * 3.8, width: U * 18, textAlign: "center", fill: PAPER }),
      box({ left: W * 0.52 + U * 2, top: top + U * 2, width: U * 18, height: U * 7, rx: U * 3.5, ry: U * 3.5, fill: A, name: "Label" }),
      textbox("AFTER", { ...td("cta"), fontSize: Math.round(U * 3), left: W * 0.52 + U * 2, top: top + U * 3.8, width: U * 18, textAlign: "center", fill: PAPER }),
      BODY("18 months with clear aligners.", { left: W * 0.08, width: W * 0.84, top: top + hh + U * 4, textAlign: "center" }),
    ], logo: logoAt("br", 0.12) };
  });
  add("patientmonth", "Patient of the month", "patients", () => ({ bg: tint(P, 0.9), objs: [
    new fabric.Circle({ left: W * 0.5 - S * 0.25, top: H * 0.06, radius: S * 0.25, fill: A, name: "Badge ring" }),
    circleFrame({ left: W * 0.5 - S * 0.22, top: H * 0.06 + S * 0.03, radius: S * 0.22, strokeWidth: 0, strokeDashArray: null }),
    SUB("PATIENT OF THE MONTH", { top: H * 0.06 + S * 0.56, textAlign: "center", charSpacing: 200 }),
    H1("Congrats, Liam!", { fontSize: Math.round(U * 10), top: H * 0.06 + S * 0.62, textAlign: "center" }),
    BODY("Perfect brushing checks all month long. Keep it up!", { left: W * 0.14, width: W * 0.72, top: H * 0.06 + S * 0.62 + U * 14, textAlign: "center" }),
  ] }));
  add("thankyou", "Thank you", "patients", () => ({ bg: A, objs: [
    ...scatter(101, 12, (x, y, s) => shape("heart", x, y, s * 0.7, { fill: tint(A, 0.35) }), { x: 0.05, y: 0.28, w: 0.9, h: 0.44 }),
    H1("Thank you!", { fontSize: Math.round(U * 14), top: H * 0.34, textAlign: "center", fill: PAPER }),
    SUB("For 500 five-star reviews. We love our patients.", { top: H * 0.34 + U * 18, textAlign: "center", fill: PD }),
    NAME({ top: H * 0.88, textAlign: "center", fill: PAPER }),
  ] }));
  add("smilegallery", "Smile gallery", "patients", () => {
    const g = wide ? [3, 2] : tall ? [2, 3] : [2, 2], gap = U * 2, cw = (W * 0.84 - gap * (g[0] - 1)) / g[0], ch = (H * 0.66 - gap * (g[1] - 1)) / g[1];
    const frames = [];
    for (let r = 0; r < g[1]; r++) for (let c = 0; c < g[0]; c++)
      frames.push(photo({ left: W * 0.08 + c * (cw + gap), top: H * 0.2 + r * (ch + gap), width: cw, height: ch, rx: U * 2, ry: U * 2 }));
    return { bg: CREAM, objs: [H1("Smiles we love", { fontSize: Math.round(U * 8), top: H * 0.06, textAlign: "center" }), ...frames,
      BODY("Want to see yours here? Tag us!", { left: W * 0.08, width: W * 0.84, top: H * 0.9, textAlign: "center" })] };
  });

  return { CATS, T, tint, shade };
};
