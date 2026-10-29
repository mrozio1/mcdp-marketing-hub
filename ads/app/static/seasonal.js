/* MCDP Hub seasonal look. window.HUB_SEASON = {theme, buddy, falling, corners, amount} is set by the page.
   Everything is pointer-events: none (clicks go straight through), hidden when printing, and still for anyone
   whose device asks for reduced motion. On touch screens the buddy rests in a corner instead of following. */
(() => {
  const cfg = Object.assign({buddy: true, falling: true, corners: true, amount: 'light'}, window.HUB_SEASON || {});
  if (!cfg.theme || window.__hubSeason) return;
  window.__hubSeason = true;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const fine = matchMedia('(pointer: fine)').matches;
  const css = document.createElement('style');
  css.textContent = `
  .hs-layer{position:fixed;inset:0;pointer-events:none;z-index:9000;overflow:hidden}
  .hs-buddy{position:fixed;left:0;top:0;width:60px;height:66px;pointer-events:none;z-index:9001;will-change:transform;transition:opacity .4s}
  .hs-buddy svg{width:100%;height:100%;display:block;filter:drop-shadow(0 4px 6px rgba(0,0,0,.12))}
  .hs-bob{animation:hs-bob 2.4s ease-in-out infinite;width:100%;height:100%}
  .hs-waddle{animation:hs-waddle .9s ease-in-out infinite;transform-origin:50% 90%;width:100%;height:100%}
  .hs-hop{animation:hs-hop 1.1s ease-in-out infinite;transform-origin:50% 100%;width:100%;height:100%}
  .hs-spin{animation:hs-spin 9s linear infinite;width:100%;height:100%}
  .hs-beat{animation:hs-beat 1.3s ease-in-out infinite;width:100%;height:100%}
  @keyframes hs-bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-6px)}}
  @keyframes hs-waddle{0%,100%{transform:rotate(-5deg)}50%{transform:rotate(5deg)}}
  @keyframes hs-hop{0%,100%{transform:translateY(0) scaleY(1)}40%{transform:translateY(-9px) scaleY(1.03)}80%{transform:translateY(0) scaleY(.95)}}
  @keyframes hs-spin{to{transform:rotate(360deg)}}
  @keyframes hs-beat{0%,60%,100%{transform:scale(1)}30%{transform:scale(1.12)}}
  .hs-corner{position:fixed;pointer-events:none;z-index:8999;opacity:.55}
  .hs-corner.tl{top:0;left:0}.hs-corner.tr{top:0;right:0;transform:scaleX(-1)}
  .hs-spider{position:fixed;top:0;right:70px;width:2px;pointer-events:none;z-index:8999;animation:hs-drop 7s ease-in-out infinite}
  .hs-spider::before{content:"";position:absolute;left:0;top:0;width:1px;height:100%;background:rgba(60,60,60,.35)}
  .hs-spider svg{position:absolute;bottom:-10px;left:-8px;width:18px;height:16px}
  @keyframes hs-drop{0%,100%{height:40px}50%{height:110px}}
  .hs-top{position:fixed;top:0;left:0;right:0;height:28px;pointer-events:none;z-index:8999}
  @media print{.hs-layer,.hs-buddy,.hs-corner,.hs-spider,.hs-top{display:none!important}}`;
  document.head.appendChild(css);

  const SVG = {
    ghost: `<svg viewBox="0 0 60 66"><path d="M30 3C15 3 7 14 7 28v31c0 2 2 3 4 2l6-4 6 5c1 1 3 1 4 0l3-3 3 3c1 1 3 1 4 0l6-5 6 4c2 1 4 0 4-2V28C53 14 45 3 30 3z" fill="#fff" stroke="#d9d4cb" stroke-width="2"/><ellipse cx="22" cy="27" rx="4" ry="5.5" fill="#2b2b2b"/><ellipse cx="38" cy="27" rx="4" ry="5.5" fill="#2b2b2b"/><circle cx="23.3" cy="25" r="1.3" fill="#fff"/><circle cx="39.3" cy="25" r="1.3" fill="#fff"/><ellipse cx="30" cy="38" rx="3.5" ry="4" fill="#2b2b2b"/><ellipse cx="15" cy="34" rx="3" ry="2" fill="#f4b6c2" opacity=".7"/><ellipse cx="45" cy="34" rx="3" ry="2" fill="#f4b6c2" opacity=".7"/></svg>`,
    turkey: `<svg viewBox="0 0 64 64"><g stroke="#7a4a1f" stroke-width="1"><ellipse cx="32" cy="26" rx="9" ry="22" fill="#c2410c" transform="rotate(-60 32 34)"/><ellipse cx="32" cy="26" rx="9" ry="22" fill="#d97706" transform="rotate(-30 32 34)"/><ellipse cx="32" cy="24" rx="9" ry="22" fill="#b45309"/><ellipse cx="32" cy="26" rx="9" ry="22" fill="#d97706" transform="rotate(30 32 34)"/><ellipse cx="32" cy="26" rx="9" ry="22" fill="#c2410c" transform="rotate(60 32 34)"/></g><ellipse cx="32" cy="42" rx="14" ry="15" fill="#7c4a24"/><circle cx="32" cy="27" r="9" fill="#8b5a2b"/><circle cx="28.5" cy="25" r="2" fill="#1f1f1f"/><circle cx="35.5" cy="25" r="2" fill="#1f1f1f"/><path d="M30 28l2 4 2-4z" fill="#f59e0b"/><path d="M33 30c2 1 2 5 0 6-1-2-1-4 0-6z" fill="#dc2626"/><path d="M26 57v5M38 57v5M24 62h5M36 62h5" stroke="#f59e0b" stroke-width="2" stroke-linecap="round"/></svg>`,
    snowman: `<svg viewBox="0 0 60 68"><circle cx="30" cy="50" r="16" fill="#fff" stroke="#d6dbe3" stroke-width="2"/><circle cx="30" cy="25" r="12" fill="#fff" stroke="#d6dbe3" stroke-width="2"/><rect x="19" y="13" width="22" height="3.5" rx="1" fill="#2b2b2b"/><rect x="23" y="2" width="14" height="12" rx="1" fill="#2b2b2b"/><rect x="23" y="10" width="14" height="2.5" fill="#b91c1c"/><circle cx="26" cy="23" r="1.6" fill="#2b2b2b"/><circle cx="34" cy="23" r="1.6" fill="#2b2b2b"/><path d="M30 26l8 2-8 1z" fill="#f97316"/><path d="M18 35q12 7 24 0l1 4q-13 6-26 0z" fill="#0f7471"/><path d="M38 37l3 9 4-1-4-8z" fill="#0f7471"/><circle cx="30" cy="45" r="1.6" fill="#2b2b2b"/><circle cx="30" cy="52" r="1.6" fill="#2b2b2b"/></svg>`,
    partystar: `<svg viewBox="0 0 64 66"><path d="M32 16l6 12 13 2-9.5 9 2.3 13L32 46l-11.8 6 2.3-13L13 30l13-2z" fill="#f6c343" stroke="#d29a12" stroke-width="2" stroke-linejoin="round"/><path d="M32 3l-7.5 17h15z" fill="#7A4E9C"/><circle cx="32" cy="3.5" r="2.6" fill="#e8559a"/><path d="M28.4 12h7.2M26.6 16.5h10.8" stroke="#f6c343" stroke-width="1.5"/><circle cx="28" cy="33" r="2" fill="#2b2b2b"/><circle cx="36" cy="33" r="2" fill="#2b2b2b"/><path d="M28.5 38q3.5 3 7 0" stroke="#2b2b2b" stroke-width="1.8" fill="none" stroke-linecap="round"/><ellipse cx="24" cy="37" rx="2.2" ry="1.4" fill="#f59e0b" opacity=".6"/><ellipse cx="40" cy="37" rx="2.2" ry="1.4" fill="#f59e0b" opacity=".6"/></svg>`,
    heart: `<svg viewBox="0 0 60 56"><path d="M30 52C12 40 4 30 4 19 4 10 11 4 19 4c5 0 9 3 11 7 2-4 6-7 11-7 8 0 15 6 15 15 0 11-8 21-26 33z" fill="#e5484d" stroke="#b42d34" stroke-width="2"/><ellipse cx="17" cy="15" rx="5" ry="3" fill="#fff" opacity=".35" transform="rotate(-30 17 15)"/><circle cx="23" cy="24" r="2.2" fill="#2b2b2b"/><circle cx="37" cy="24" r="2.2" fill="#2b2b2b"/><path d="M25 31q5 4 10 0" stroke="#2b2b2b" stroke-width="2" fill="none" stroke-linecap="round"/><ellipse cx="18" cy="30" rx="3" ry="1.8" fill="#fff" opacity=".35"/><ellipse cx="42" cy="30" rx="3" ry="1.8" fill="#fff" opacity=".35"/></svg>`,
    shamrock: `<svg viewBox="0 0 60 66"><g fill="#2e9e5b" stroke="#1d6e3d" stroke-width="1.6"><path d="M30 30c-6-6-14-6-14 2 0 7 8 8 14-2z"/><path d="M30 30c6-6 14-6 14 2 0 7-8 8-14-2z"/><path d="M30 30c-7-4-9-14-1-16 6-1 8 7 1 16z"/><path d="M30 30c7-4 9-14 1-16-6-1-8 7-1 16z"/><path d="M30 30c-6 4-12 10-6 14 5 3 9-3 6-14z"/><path d="M30 30c6 4 12 10 6 14-5 3-9-3-6-14z"/></g><path d="M30 38c0 10 2 18 8 24" stroke="#1d6e3d" stroke-width="3" fill="none" stroke-linecap="round"/><circle cx="26" cy="27" r="1.8" fill="#123"/><circle cx="34" cy="27" r="1.8" fill="#123"/><path d="M27 32q3 2.5 6 0" stroke="#123" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg>`,
    bunny: `<svg viewBox="0 0 60 66"><ellipse cx="22" cy="16" rx="6" ry="15" fill="#fff" stroke="#d8d1cc" stroke-width="2" transform="rotate(-10 22 16)"/><ellipse cx="38" cy="16" rx="6" ry="15" fill="#fff" stroke="#d8d1cc" stroke-width="2" transform="rotate(10 38 16)"/><ellipse cx="22" cy="17" rx="2.6" ry="10" fill="#f6c1cf" transform="rotate(-10 22 17)"/><ellipse cx="38" cy="17" rx="2.6" ry="10" fill="#f6c1cf" transform="rotate(10 38 17)"/><ellipse cx="30" cy="50" rx="17" ry="14" fill="#fff" stroke="#d8d1cc" stroke-width="2"/><circle cx="30" cy="35" r="13" fill="#fff" stroke="#d8d1cc" stroke-width="2"/><circle cx="25" cy="33" r="1.8" fill="#2b2b2b"/><circle cx="35" cy="33" r="1.8" fill="#2b2b2b"/><path d="M28.5 37.5l1.5 1.5 1.5-1.5z" fill="#e8799a"/><path d="M30 39v2M28 42q2 1.5 4 0" stroke="#2b2b2b" stroke-width="1.2" fill="none"/><ellipse cx="22" cy="39" rx="2.5" ry="1.5" fill="#f6c1cf" opacity=".8"/><ellipse cx="38" cy="39" rx="2.5" ry="1.5" fill="#f6c1cf" opacity=".8"/></svg>`,
    sun: `<svg viewBox="0 0 64 64"><g class="hs-rays" stroke="#f5a623" stroke-width="3.5" stroke-linecap="round"><path d="M32 3v8M32 53v8M3 32h8M53 32h8M11.5 11.5l5.7 5.7M46.8 46.8l5.7 5.7M11.5 52.5l5.7-5.7M46.8 17.2l5.7-5.7"/></g><circle cx="32" cy="32" r="17" fill="#fcd34d" stroke="#f5a623" stroke-width="2"/><path d="M19 28h11l-1 5c-1 3-8 3-9 0zM34 28h11l-1 5c-1 3-8 3-9 0z" fill="#1f2937"/><path d="M30 29h4" stroke="#1f2937" stroke-width="2"/><path d="M25 39q7 5 14 0" stroke="#7c2d12" stroke-width="2" fill="none" stroke-linecap="round"/></svg>`,
    usastar: `<svg viewBox="0 0 64 64"><path d="M32 4l7.6 16.6 18 2-13.4 12.3 3.8 17.8L32 43.6l-16 9.1 3.8-17.8L6.4 22.6l18-2z" fill="#fff" stroke="#1e3a8a" stroke-width="2.5" stroke-linejoin="round"/><path d="M32 4l7.6 16.6 18 2-13.4 12.3 3.8 17.8L32 43.6z" fill="#dc2626" opacity=".9"/><path d="M32 4L24.4 20.6l-18 2L19.8 34.9 16 52.7l16-9.1z" fill="#1e3a8a" opacity=".9"/><circle cx="27" cy="30" r="2" fill="#fff"/><circle cx="37" cy="30" r="2" fill="#fff"/><path d="M27.5 35q4.5 3.5 9 0" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round"/></svg>`,
    tooth: `<svg viewBox="0 0 60 66"><path d="M30 8c-5-4-17-5-20 4-3 8 0 16 3 22 2 5 2 11 3 18 1 6 4 10 7 9 3-1 3-9 4-14 1-3 2-5 3-5s2 2 3 5c1 5 1 13 4 14 3 1 6-3 7-9 1-7 1-13 3-18 3-6 6-14 3-22-3-9-15-8-20-4z" fill="#fff" stroke="#cfd6de" stroke-width="2"/><path d="M15 26h30" stroke="#94a3b8" stroke-width="1.6"/><g fill="#0F7471"><rect x="17.5" y="23.5" width="5" height="5" rx="1"/><rect x="27.5" y="23.5" width="5" height="5" rx="1"/><rect x="37.5" y="23.5" width="5" height="5" rx="1"/></g><circle cx="23" cy="17" r="2" fill="#2b2b2b"/><circle cx="37" cy="17" r="2" fill="#2b2b2b"/><path d="M25 33q5 4 10 0" stroke="#2b2b2b" stroke-width="1.8" fill="none" stroke-linecap="round"/><ellipse cx="18" cy="21" rx="2.5" ry="1.5" fill="#f4b6c2" opacity=".7"/><ellipse cx="42" cy="21" rx="2.5" ry="1.5" fill="#f4b6c2" opacity=".7"/></svg>`,
    web: `<svg width="150" height="150" viewBox="0 0 150 150" fill="none" stroke="#5b5852" stroke-width=".9"><path d="M0 0L150 0M0 0L140 60M0 0L105 105M0 0L60 140M0 0L0 150"/><path d="M38 0Q34 15 36 15Q31 26 27 27Q24 31 15 36Q14 34 0 38"/><path d="M75 0Q68 30 70 32Q60 52 53 53Q50 60 30 70Q28 68 0 75"/><path d="M112 0Q102 45 105 47Q90 77 80 80Q76 90 46 104Q42 102 0 112"/></svg>`,
    spider: `<svg viewBox="0 0 18 16"><ellipse cx="9" cy="9" rx="4.5" ry="5" fill="#2b2b2b"/><g stroke="#2b2b2b" stroke-width="1.1" fill="none"><path d="M5 7L1 4M5 9L0 9M5 11L1 14M13 7l4-3M13 9h5M13 11l4 3"/></g><circle cx="7.5" cy="8" r=".9" fill="#fff"/><circle cx="10.5" cy="8" r=".9" fill="#fff"/></svg>`,
  };
  const THEME = {
    newyear: {buddy: 'partystar', anim: 'hs-bob', fall: 'confetti'},
    valentines: {buddy: 'heart', anim: 'hs-beat', fall: 'hearts'},
    stpatricks: {buddy: 'shamrock', anim: 'hs-bob', fall: 'clovers'},
    spring: {buddy: 'bunny', anim: 'hs-hop', fall: 'petals'},
    summer: {buddy: 'sun', anim: 'hs-bob', fall: 'bubbles'},
    july4: {buddy: 'usastar', anim: 'hs-bob', fall: 'fireworks', top: 'bunting'},
    school: {buddy: 'tooth', anim: 'hs-waddle', fall: 'pencils'},
    halloween: {buddy: 'ghost', anim: 'hs-bob', corners: 'webs'},
    thanksgiving: {buddy: 'turkey', anim: 'hs-waddle', fall: 'leaves'},
    christmas: {buddy: 'snowman', anim: 'hs-bob', fall: 'snow', top: 'lights'},
  }[cfg.theme];
  if (!THEME) return;

  // ---- cursor buddy
  if (cfg.buddy) {
    const b = document.createElement('div');
    b.className = 'hs-buddy'; b.setAttribute('aria-hidden', 'true');
    b.innerHTML = `<div class="${reduce ? '' : THEME.anim}">${SVG[THEME.buddy]}</div>`;
    document.body.appendChild(b);
    let x = innerWidth - 90, y = innerHeight - 100, tx = x, ty = y;
    const place = () => { b.style.transform = `translate(${x}px, ${y}px) scaleX(${tx < x - 2 ? -1 : 1})`; };
    if (fine && !reduce) {
      place();
      addEventListener('mousemove', e => { tx = e.clientX + 18; ty = e.clientY + 14; b.style.opacity = 1; });
      document.addEventListener('mouseleave', () => { b.style.opacity = 0; });
      (function step() { x += (tx - x) * 0.08; y += (ty - y) * 0.08; place(); requestAnimationFrame(step); })();
      window.__hsBuddyTo = (px, py) => { tx = px + 18; ty = py + 14; x = tx - 30; y = ty - 18; place(); };
    } else {
      Object.assign(b.style, {left: 'auto', top: 'auto', right: '14px', bottom: '14px', transform: 'none', width: '46px', height: '50px', opacity: .9});
    }
  }

  // ---- corners and the top edge
  if (cfg.corners) {
    if (THEME.corners === 'webs') {
      for (const side of ['tl', 'tr']) { const w = document.createElement('div'); w.className = 'hs-corner ' + side; w.setAttribute('aria-hidden', 'true'); w.innerHTML = SVG.web; document.body.appendChild(w); }
      if (!reduce) { const s = document.createElement('div'); s.className = 'hs-spider'; s.setAttribute('aria-hidden', 'true'); s.innerHTML = SVG.spider; document.body.appendChild(s); }
    }
    if (THEME.top) {
      const g = document.createElement('div'); g.className = 'hs-top'; g.setAttribute('aria-hidden', 'true');
      let shapes = '';
      if (THEME.top === 'lights') {
        const cols = ['#b91c1c', '#0f7471', '#d97706', '#2e7d52'];
        for (let i = 0; i < 40; i++) shapes += `<circle cx="${i * 2.5 + 1.25}%" cy="${10 + (i % 2) * 5}" r="4" fill="${cols[i % 4]}">${reduce ? '' : `<animate attributeName="opacity" values="1;.35;1" dur="${2 + (i % 3)}s" begin="${(i % 5) * .3}s" repeatCount="indefinite"/>`}</circle>`;
      } else {      // bunting: little red, white and blue flags
        const cols = ['#dc2626', '#f8fafc', '#1e3a8a'];
        for (let i = 0; i < 34; i++) { const x = i * 3; shapes += `<svg x="${x}%" y="2" width="3%" height="22" viewBox="0 0 30 22" preserveAspectRatio="none" overflow="visible"><path d="M2 2h26L15 20z" fill="${cols[i % 3]}" stroke="#cbd5e1" stroke-width=".6"/></svg>`; }
      }
      g.innerHTML = `<svg width="100%" height="28"><path d="M0 4 H 10000" stroke="#94a3b8" stroke-width="1" opacity=".5"/>${shapes}</svg>`;
      document.body.appendChild(g);
    }
  }

  // ---- falling (or rising) things
  if (!cfg.falling || reduce || !THEME.fall) return;
  const c = document.createElement('canvas'); c.className = 'hs-layer'; c.setAttribute('aria-hidden', 'true'); document.body.appendChild(c);
  const ctx = c.getContext('2d'); let W, H; const dpr = Math.min(devicePixelRatio || 1, 2);
  const size = () => { W = innerWidth; H = innerHeight; c.width = W * dpr; c.height = H * dpr; c.style.width = W + 'px'; c.style.height = H + 'px'; ctx.setTransform(dpr, 0, 0, dpr, 0, 0); };
  size(); addEventListener('resize', size);
  const many = cfg.amount === 'normal';
  const R = (a, b) => a + Math.random() * (b - a);
  const pickOf = a => a[(Math.random() * a.length) | 0];
  const KIND = THEME.fall;
  // per kind: how many at once, how often a new one starts (ms; 0 = keep topped up), and up or down
  const SPEC = {
    snow: {n: many ? 90 : 45, every: 0}, confetti: {n: many ? 50 : 26, every: 0},
    leaves: {n: many ? 9 : 5, every: 3000}, clovers: {n: many ? 8 : 4, every: 3200}, petals: {n: many ? 14 : 8, every: 1600},
    hearts: {n: many ? 8 : 4, every: 2800, up: true}, bubbles: {n: many ? 10 : 5, every: 2600, up: true},
    pencils: {n: many ? 4 : 2, every: 5000}, fireworks: {n: 0, every: many ? 2600 : 4200},
  }[KIND];
  const items = [];
  const spawn = (anyY) => {
    const p = {x: R(0, W), y: SPEC.up ? (anyY ? R(H * .3, H) : H + 20) : (anyY ? R(0, H * .8) : -20), sway: R(0, 6.28), rot: R(0, 6.28), vr: R(-.03, .03)};
    if (KIND === 'snow') Object.assign(p, {r: R(1.6, 4), vy: R(.35, .95), a: R(.55, .95)});
    if (KIND === 'confetti') Object.assign(p, {w: R(4, 8), h: R(2, 4), vy: R(.8, 1.6), col: pickOf(['#e5484d', '#f6c343', '#0F7471', '#7A4E9C', '#3b82f6', '#22c55e']), vr: R(-.12, .12)});
    if (KIND === 'leaves') Object.assign(p, {s: R(12, 20), vy: R(.6, 1.1), col: pickOf(['#c2410c', '#d97706', '#b45309', '#a16207', '#9a3412']), maple: Math.random() < .6});
    if (KIND === 'clovers') Object.assign(p, {s: R(10, 16), vy: R(.5, 1), col: pickOf(['#2e9e5b', '#3fb36b', '#1d6e3d'])});
    if (KIND === 'petals') Object.assign(p, {s: R(15, 21), vy: R(.5, 1), col: pickOf(['#f19bb3', '#ea7f9f', '#f6b3c6'])});
    if (KIND === 'hearts') Object.assign(p, {s: R(10, 18), vy: -R(.5, .9), col: pickOf(['#e5484d', '#f472b6', '#fb7185'])});
    if (KIND === 'bubbles') Object.assign(p, {r: R(6, 14), vy: -R(.4, .8)});
    if (KIND === 'pencils') Object.assign(p, {s: R(18, 26), vy: R(.7, 1.1), vr: R(-.02, .02)});
    items.push(p);
  };
  const shape = {
    leaves(p) {
      ctx.fillStyle = p.col; ctx.beginPath();
      if (p.maple) { ctx.moveTo(0, 10); ctx.bezierCurveTo(-3, 6, -11, 6, -10, 1); ctx.bezierCurveTo(-13, -1, -8, -5, -6, -3); ctx.bezierCurveTo(-8, -9, -3, -12, 0, -12); ctx.bezierCurveTo(3, -12, 8, -9, 6, -3); ctx.bezierCurveTo(8, -5, 13, -1, 10, 1); ctx.bezierCurveTo(11, 6, 3, 6, 0, 10); }
      else { ctx.moveTo(0, -12); ctx.bezierCurveTo(8, -6, 8, 5, 0, 10); ctx.bezierCurveTo(-8, 5, -8, -6, 0, -12); }
      ctx.fill(); ctx.strokeStyle = 'rgba(70,35,10,.45)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(0, -9); ctx.lineTo(0, 13);
      if (p.maple) { ctx.moveTo(0, 2); ctx.lineTo(-6, -2); ctx.moveTo(0, 2); ctx.lineTo(6, -2); }
      ctx.stroke();
    },
    clovers(p) {
      ctx.fillStyle = p.col;
      for (let i = 0; i < 3; i++) { ctx.save(); ctx.rotate(i * 2.094); ctx.beginPath(); ctx.arc(-3, -6, 4.2, 0, 6.28); ctx.arc(3, -6, 4.2, 0, 6.28); ctx.moveTo(0, 0); ctx.lineTo(-7, -5); ctx.lineTo(7, -5); ctx.fill(); ctx.restore(); }
      ctx.strokeStyle = '#1d6e3d'; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(0, 0); ctx.quadraticCurveTo(2, 8, 6, 12); ctx.stroke();
    },
    petals(p) { ctx.fillStyle = p.col; ctx.beginPath(); ctx.moveTo(0, -10); ctx.bezierCurveTo(7, -6, 6, 6, 0, 9); ctx.bezierCurveTo(-6, 6, -7, -6, 0, -10); ctx.fill(); ctx.fillStyle = 'rgba(255,255,255,.5)'; ctx.beginPath(); ctx.ellipse(-1.5, -3, 1.5, 4, 0, 0, 6.28); ctx.fill(); },
    hearts(p) { ctx.fillStyle = p.col; ctx.beginPath(); ctx.moveTo(0, 9); ctx.bezierCurveTo(-12, 0, -9, -11, 0, -5); ctx.bezierCurveTo(9, -11, 12, 0, 0, 9); ctx.fill(); },
    pencils(p) {
      ctx.fillStyle = '#f6c343'; ctx.fillRect(-2.5, -12, 5, 18); ctx.fillStyle = '#e8799a'; ctx.fillRect(-2.5, -15, 5, 3); ctx.fillStyle = '#9ca3af'; ctx.fillRect(-2.5, -12, 5, 1.5);
      ctx.fillStyle = '#f3d9b1'; ctx.beginPath(); ctx.moveTo(-2.5, 6); ctx.lineTo(2.5, 6); ctx.lineTo(0, 12); ctx.fill(); ctx.fillStyle = '#374151'; ctx.beginPath(); ctx.moveTo(-.9, 9.8); ctx.lineTo(.9, 9.8); ctx.lineTo(0, 12); ctx.fill();
    },
  };
  const sparks = [];
  const burst = () => {
    const cx = R(W * .15, W * .85), cy = R(H * .12, H * .45), col = pickOf(['#dc2626', '#1e3a8a', '#3b82f6', '#f6c343', '#e5484d']);
    for (let i = 0; i < 34; i++) { const a = (i / 34) * 6.28, v = R(1.2, 2.6); sparks.push({x: cx, y: cy, vx: Math.cos(a) * v, vy: Math.sin(a) * v, life: 1, col}); }
  };
  for (let i = 0; i < (SPEC.every ? Math.min(2, SPEC.n) : SPEC.n); i++) spawn(true);
  let last = KIND === 'fireworks' ? -SPEC.every + 700 : 0;
  (function frame(t) {
    ctx.clearRect(0, 0, W, H);
    if (KIND === 'fireworks') {
      if (t - last > SPEC.every) { burst(); last = t; }
      for (let i = sparks.length - 1; i >= 0; i--) {
        const s = sparks[i]; s.x += s.vx; s.y += s.vy; s.vy += .03; s.vx *= .985; s.life -= .012;
        if (s.life <= 0) { sparks.splice(i, 1); continue; }
        ctx.globalAlpha = Math.min(1, s.life * 1.4); ctx.fillStyle = s.col; ctx.beginPath(); ctx.arc(s.x, s.y, 2.8, 0, 6.28); ctx.fill();
      }
      ctx.globalAlpha = 1; requestAnimationFrame(frame); return;
    }
    if (items.length < SPEC.n && (!SPEC.every || t - last > SPEC.every * R(.7, 1.3))) { spawn(false); last = t; }
    for (let i = items.length - 1; i >= 0; i--) {
      const p = items[i];
      p.sway += .015; p.y += p.vy; p.x += Math.sin(p.sway) * (KIND === 'snow' || KIND === 'bubbles' ? .35 : .8); p.rot += p.vr;
      if (KIND === 'snow') {
        ctx.globalAlpha = p.a; const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.r);
        g.addColorStop(0, '#fff'); g.addColorStop(.55, '#e4ecf5'); g.addColorStop(1, 'rgba(150,172,200,.55)');
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 6.28); ctx.fill(); ctx.globalAlpha = 1;
      } else if (KIND === 'bubbles') {
        ctx.globalAlpha = .55; ctx.strokeStyle = '#7dd3fc'; ctx.lineWidth = 1.2; ctx.fillStyle = 'rgba(186,230,253,.25)';
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 6.28); ctx.fill(); ctx.stroke();
        ctx.fillStyle = 'rgba(255,255,255,.8)'; ctx.beginPath(); ctx.arc(p.x - p.r * .35, p.y - p.r * .35, p.r * .22, 0, 6.28); ctx.fill(); ctx.globalAlpha = 1;
      } else if (KIND === 'confetti') {
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot); ctx.scale(1, Math.cos(p.sway * 3)); ctx.fillStyle = p.col; ctx.globalAlpha = .85;
        ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h); ctx.restore(); ctx.globalAlpha = 1;
      } else {
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot); ctx.scale(p.s / 20, p.s / 20); ctx.globalAlpha = KIND === 'hearts' ? .7 : .9;
        shape[KIND](p); ctx.restore(); ctx.globalAlpha = 1;
      }
      if (p.y > H + 30 || p.y < -40) { items.splice(i, 1); if (!SPEC.every) spawn(false); }
    }
    requestAnimationFrame(frame);
  })(0);
})();
