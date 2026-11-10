/* MCDP Studio video editor. The edit (clips with trims and speed, captions, name tags, end card, music) is a
   small JSON project saved to the hub; the preview plays it in the browser, and "Make the MP4" has the server
   build the finished video with ffmpeg. */
(() => {
  "use strict";
  const C = JSON.parse(document.getElementById("vd-config").textContent);
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const fmt = t => { t = Math.max(0, t || 0); const m = Math.floor(t / 60); return `${m}:${(t - m * 60).toFixed(1).padStart(4, "0")}`; };
  const esc = s => String(s).replace(/[<&"]/g, c => ({ "<": "&lt;", "&": "&amp;", '"': "&quot;" })[c]);

  let P = null, items = {}, sel = null, t = 0, playing = false;
  const pv = $("[data-pv]"), pi = $("[data-pi]"), po = $("[data-po]"), cap = $("[data-pcap]"), low = $("[data-plower]");
  const music = new Audio(); music.preload = "auto";

  // ---------------------------------------------------------------- the edit's timing
  const clipLen = c => c.still ? c.out : (c.out - c.in) / (c.speed || 1);
  const segs = () => {
    let s = 0; const out = [];
    P.clips.forEach((c, i) => { const l = clipLen(c); out.push({ kind: "clip", i, c, start: s, end: s + l }); s += l; });
    if (P.outro.on) out.push({ kind: "outro", start: s, end: s + P.outro.dur });
    return out;
  };
  const total = () => { const s = segs(); return s.length ? s[s.length - 1].end : 0; };
  const segAt = tt => segs().find(s => tt >= s.start && tt < s.end) || null;

  // ---------------------------------------------------------------- saving
  let saveT, saving = Promise.resolve();
  const say = m => { $("[data-saved]").textContent = m; };
  function changed(redraw = true) {
    if (redraw) draw();
    if (!C.canEdit) return;
    say("Unsaved changes");
    clearTimeout(saveT); saveT = setTimeout(save, 1200);
  }
  function save() {
    saving = saving.then(async () => {
      say("Saving…");
      try {
        const r = await fetch(`/studio/video/${C.iid}/project`, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ project: P, title: $("[data-title]").value.trim() }) });
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
        say(`Saved ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`);
      } catch (e) { say(`Not saved: ${e.message}`); }
    });
    return saving;
  }
  $("[data-title]").addEventListener("change", () => changed(false));

  // ---------------------------------------------------------------- preview
  const screen = $("[data-screen]"), wrap = $("[data-screen-wrap]");
  function fitScreen() {
    const r = wrap.getBoundingClientRect(), k = Math.min((r.width - 24) / P.w, (r.height - 24) / P.h);
    screen.style.width = `${Math.round(P.w * k)}px`; screen.style.height = `${Math.round(P.h * k)}px`;
    screen.style.setProperty("--short", `${Math.min(P.w, P.h) * k}px`);
  }
  window.addEventListener("resize", () => P && fitScreen());
  let curSrc = null;
  function show(tt, startPlaying) {
    t = Math.max(0, Math.min(tt, total()));
    const s = segAt(t);
    $("[data-pempty]").hidden = !!P.clips.length;
    pv.hidden = pi.hidden = po.hidden = true;
    if (!s) { pv.pause(); return overlays(); }
    if (s.kind === "outro") {
      po.hidden = false; pv.pause();
    } else if (s.c.still) {
      pi.src = `/studio/f/${s.c.item}`; pi.hidden = false; pv.pause();
      pi.classList.toggle("vd-fit", (s.c.fit || "fit") === "fit"); screen.style.setProperty("--bgimg", `url(/studio/f/${s.c.item})`);
    } else {
      const src = `/studio/f/${s.c.item}`;
      if (curSrc !== src) { pv.src = src; curSrc = src; }
      pv.hidden = false; pv.classList.toggle("vd-fit", s.c.fit === "fit");
      const want = s.c.in + (t - s.start) * (s.c.speed || 1);
      if (Math.abs(pv.currentTime - want) > 0.15) { try { pv.currentTime = want; } catch (e) {} }
      pv.playbackRate = s.c.speed || 1; pv.volume = Math.min(1, s.c.volume ?? 1); pv.muted = !(s.c.volume > 0);
      if (startPlaying) pv.play().catch(() => {});
    }
    overlays();
  }
  function overlays() {
    const c = P.captions.find(x => t >= x.start && t < x.end);
    cap.hidden = !c; if (c) cap.querySelector("span").textContent = c.text;
    cap.className = `vd-cap vd-cap-${P.style.captions} vd-pos-${P.style.position}`;
    const l = P.lowers.find(x => t >= x.start && t < x.end);
    low.hidden = !l; if (l) { low.querySelector("strong").textContent = l.text; low.querySelector("span").textContent = l.sub || ""; low.querySelector("span").hidden = !l.sub; }
    $("[data-time]").textContent = `${fmt(t)} / ${fmt(total())}`;
    $("[data-head]").style.left = `calc(var(--lab) + ${t * pps()}px)`;
  }
  let last = 0;
  function tick(now) {
    if (!playing) return;
    const dt = Math.min(0.25, (now - last) / 1000); last = now;
    const s = segAt(t);
    if (s && s.kind === "clip" && !s.c.still && !pv.paused && pv.readyState >= 2) {
      t = s.start + (pv.currentTime - s.c.in) / (s.c.speed || 1);
      if (pv.currentTime >= s.c.out - 0.03) t = s.end;
    } else t += dt;
    if (t >= total()) { stop(); show(total() - 0.001); return; }
    const s2 = segAt(t);
    if (s2 !== null && (s === null || s2.start !== s.start)) show(t, true); else overlays();
    requestAnimationFrame(tick);
  }
  function play() {
    if (!P.clips.length) return;
    if (t >= total() - 0.05) t = 0;
    playing = true; last = performance.now();
    $("[data-play]").innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="#fff"><path d="M6 4h4v16H6zM14 4h4v16h-4z"/></svg>';
    show(t, true);
    if (P.music && items[P.music.item]) {
      music.src = `/studio/f/${P.music.item}`; music.volume = Math.min(1, P.music.volume);
      music.currentTime = t; music.play().catch(() => {});
    }
    requestAnimationFrame(tick);
  }
  function stop() {
    playing = false; pv.pause(); music.pause();
    $("[data-play]").innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="#fff"><path d="M7 4v16l13-8z"/></svg>';
  }
  $("[data-play]").addEventListener("click", () => playing ? stop() : play());
  document.addEventListener("keydown", e => {
    if (e.code === "Space" && !/INPUT|TEXTAREA|SELECT|BUTTON/.test(document.activeElement.tagName)) { e.preventDefault(); playing ? stop() : play(); }
  });

  // ---------------------------------------------------------------- timeline
  const tl = $("[data-timeline]");
  const pps = () => { const w = tl.getBoundingClientRect().width - 96; return Math.max(8, w / Math.max(total() + 2, 15)); };
  function block(lane, a, b, label, cls, onSel, isSel) {
    const d = document.createElement("button");
    d.type = "button"; d.className = `vd-block ${cls}${isSel ? " on" : ""}`;
    d.style.left = `${a * pps()}px`; d.style.width = `${Math.max(6, (b - a) * pps() - 2)}px`;
    d.innerHTML = `<span>${esc(label)}</span>`;
    d.addEventListener("click", e => { e.stopPropagation(); onSel(); });
    lane.appendChild(d);
    return d;
  }
  function draw() {
    if (!P) return;
    const ss = segs(), k = pps();
    const ruler = $("[data-ruler]"); ruler.innerHTML = "";
    const step = k > 40 ? 1 : k > 18 ? 2 : k > 8 ? 5 : 10;
    for (let s = 0; s <= total() + 2; s += step) {
      const m = document.createElement("span"); m.style.left = `${s * k}px`; m.textContent = fmt(s).replace(/\.0$/, ""); ruler.appendChild(m);
    }
    const lanes = Object.fromEntries($$("[data-lane]").map(l => [l.dataset.lane, l]));
    Object.values(lanes).forEach(l => { l.innerHTML = ""; });
    ss.forEach(s => {
      if (s.kind === "outro") {
        block(lanes.clips, s.start, s.end, "End card", "vd-b-outro", () => { openTab("brand"); select(null); }, false);
        return;
      }
      const it = items[s.c.item] || {};
      const d = block(lanes.clips, s.start, s.end, (it.title || "Clip") + (s.c.speed !== 1 && !s.c.still ? ` · ${s.c.speed}×` : ""),
        s.c.still ? "vd-b-still" : "vd-b-clip", () => { select({ type: "clip", i: s.i }); show(s.start); }, sel && sel.type === "clip" && sel.i === s.i);
      d.style.backgroundImage = `url(/studio/t/${s.c.item})`;
      if (C.canEdit) ["l", "r"].forEach(side => {
        const h = document.createElement("span"); h.className = `vd-handle vd-h${side}`; d.appendChild(h);
        h.addEventListener("pointerdown", e => trimDrag(e, s.i, side));
      });
    });
    P.captions.forEach((c, i) => block(lanes.captions, c.start, c.end, c.text, "vd-b-cap", () => { select({ type: "cap", i }); show(c.start); },
      sel && sel.type === "cap" && sel.i === i));
    P.lowers.forEach((c, i) => block(lanes.graphics, c.start, c.end, c.text, "vd-b-lower", () => { select({ type: "lower", i }); show(c.start); },
      sel && sel.type === "lower" && sel.i === i));
    if (P.outro.on && ss.length) { const o = ss[ss.length - 1]; block(lanes.graphics, o.start, o.end, "End card", "vd-b-outro", () => openTab("brand"), false); }
    if (P.music && items[P.music.item]) block(lanes.music, 0, total(), `♪ ${items[P.music.item].title}`, "vd-b-music", () => openTab("music"), false);
    const tot = total();
    $("[data-size]").textContent = `${P.w} × ${P.h}`;
    $("[data-limit]").textContent = tot > 90 ? `${Math.round(tot)} s: Reels and Stories longer than 90 s may be cut short` :
      tot > 60 && P.h > P.w ? `${Math.round(tot)} s (Stories show 60 s at a time)` : "";
    overlays(); props(); capList();
  }
  tl.addEventListener("click", e => {
    if (e.target.closest(".vd-block")) return;
    const r = $("[data-lane=clips]").getBoundingClientRect();
    const x = e.clientX - r.left; if (x < 0) return;
    stop(); show(x / pps());
  });
  function trimDrag(e, i, side) {
    e.preventDefault(); e.stopPropagation();
    const c = P.clips[i], x0 = e.clientX, k = pps(), orig = { ...c };
    const info = items[c.item] || {};
    const move = ev => {
      const d = (ev.clientX - x0) / k;
      if (c.still) { c.out = Math.max(0.5, Math.min(30, +(orig.out + (side === "r" ? d : -d)).toFixed(2))); }
      else if (side === "l") c.in = Math.max(0, Math.min(orig.out - 0.3, +(orig.in + d * c.speed).toFixed(2)));
      else c.out = Math.max(orig.in + 0.3, Math.min(info.duration || 9999, +(orig.out + d * c.speed).toFixed(2)));
      draw();
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); select({ type: "clip", i }); changed(); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  }

  // ---------------------------------------------------------------- selection and properties
  function select(s) { sel = s; draw(); }
  function props() {
    $("[data-sel-none]").hidden = !!sel;
    $("[data-sel-clip]").hidden = !(sel && sel.type === "clip");
    $("[data-sel-cap]").hidden = !(sel && sel.type === "cap");
    $("[data-sel-lower]").hidden = !(sel && sel.type === "lower");
    if (!sel) return;
    if (sel.type === "clip") {
      const c = P.clips[sel.i]; if (!c) return select(null);
      const it = items[c.item] || {};
      $("[data-sel-title]").textContent = it.title || "Clip";
      $("[data-clip-video]").hidden = !!c.still; $("[data-clip-still]").hidden = !c.still;
      $$("input[name=cfit]").forEach(r => { r.checked = r.value === (c.fit || (c.still ? "fit" : "fill")); });
      if (c.still) $("[data-cdur]").value = c.out;
      else {
        $("[data-cin]").value = c.in; $("[data-cout]").value = c.out; $("[data-cspeed]").value = String(c.speed);
        $("[data-cvol]").value = Math.round(c.volume * 100); $("[data-cvol-v]").textContent = `${Math.round(c.volume * 100)}%`;
        $("[data-clip-src]").textContent = it.duration ? `The clip is ${fmt(it.duration)} long; this part plays for ${fmt(clipLen(c))}.` : "";
      }
    } else if (sel.type === "cap") {
      const c = P.captions[sel.i]; if (!c) return select(null);
      if (document.activeElement !== $("[data-captext]")) $("[data-captext]").value = c.text;
      $("[data-capa]").value = c.start; $("[data-capb]").value = c.end;
    } else {
      const c = P.lowers[sel.i]; if (!c) return select(null);
      if (document.activeElement !== $("[data-ltext]")) $("[data-ltext]").value = c.text;
      if (document.activeElement !== $("[data-lsub]")) $("[data-lsub]").value = c.sub || "";
      $("[data-la]").value = c.start; $("[data-lb]").value = c.end;
    }
  }
  const num = el => parseFloat(el.value);
  const onClip = (sel_, fn) => $(sel_).addEventListener("change", e => { const c = P.clips[sel.i]; fn(c, e.target); changed(); });
  onClip("[data-cin]", (c, el) => { c.in = Math.max(0, Math.min(num(el), c.out - 0.3)); });
  onClip("[data-cout]", (c, el) => { const d = (items[c.item] || {}).duration || 9999; c.out = Math.min(d, Math.max(num(el), c.in + 0.3)); });
  onClip("[data-cspeed]", (c, el) => { c.speed = parseFloat(el.value); });
  onClip("[data-cdur]", (c, el) => { c.out = Math.max(0.5, Math.min(30, num(el))); });
  $$("input[name=cfit]").forEach(r => r.addEventListener("change", () => { P.clips[sel.i].fit = r.value; changed(); show(t); }));
  $("[data-cvol]").addEventListener("input", e => { P.clips[sel.i].volume = e.target.value / 100; $("[data-cvol-v]").textContent = `${e.target.value}%`; });
  $("[data-cvol]").addEventListener("change", () => changed());
  $$("[data-cmove]").forEach(b => b.addEventListener("click", () => {
    const i = sel.i, j = i + +b.dataset.cmove; if (j < 0 || j >= P.clips.length) return;
    [P.clips[i], P.clips[j]] = [P.clips[j], P.clips[i]]; sel.i = j; changed();
  }));
  $("[data-cdel]").addEventListener("click", () => { P.clips.splice(sel.i, 1); sel = null; changed(); show(Math.min(t, total())); });
  $("[data-captext]").addEventListener("input", e => { P.captions[sel.i].text = e.target.value; changed(); });
  $("[data-capa]").addEventListener("change", e => { const c = P.captions[sel.i]; c.start = Math.max(0, num(e.target)); c.end = Math.max(c.end, c.start + 0.3); sortCaps(); changed(); });
  $("[data-capb]").addEventListener("change", e => { const c = P.captions[sel.i]; c.end = Math.max(c.start + 0.3, num(e.target)); changed(); });
  $("[data-capdel]").addEventListener("click", () => { P.captions.splice(sel.i, 1); sel = null; changed(); });
  $("[data-ltext]").addEventListener("input", e => { P.lowers[sel.i].text = e.target.value; changed(); });
  $("[data-lsub]").addEventListener("input", e => { P.lowers[sel.i].sub = e.target.value; changed(); });
  $("[data-la]").addEventListener("change", e => { const c = P.lowers[sel.i]; c.start = Math.max(0, num(e.target)); c.end = Math.max(c.end, c.start + 0.5); changed(); });
  $("[data-lb]").addEventListener("change", e => { const c = P.lowers[sel.i]; c.end = Math.max(c.start + 0.5, num(e.target)); changed(); });
  $("[data-ldel]").addEventListener("click", () => { P.lowers.splice(sel.i, 1); sel = null; changed(); });
  function sortCaps() {
    const cur = sel && sel.type === "cap" ? P.captions[sel.i] : null;
    P.captions.sort((a, b) => a.start - b.start);
    if (cur) sel.i = P.captions.indexOf(cur);
  }

  const split = $("[data-split]");
  if (split) split.addEventListener("click", () => {
    const s = segAt(t);
    if (!s || s.kind !== "clip") return;
    const c = s.c, i = s.i;
    if (c.still) {
      const a = +(t - s.start).toFixed(2); if (a < 0.3 || c.out - a < 0.3) return;
      P.clips.splice(i, 1, { ...c, out: a }, { ...c, out: +(c.out - a).toFixed(2) });
    } else {
      const at = +(c.in + (t - s.start) * c.speed).toFixed(2); if (at - c.in < 0.3 || c.out - at < 0.3) return;
      P.clips.splice(i, 1, { ...c, out: at }, { ...c, in: at });
    }
    select({ type: "clip", i: i + 1 }); changed();
  });

  // ---------------------------------------------------------------- side panels
  function openTab(k) {
    $$("[data-vtab]").forEach(b => b.classList.toggle("on", b.dataset.vtab === k));
    $$("[data-vpanel]").forEach(p => { p.hidden = p.dataset.vpanel !== k; });
  }
  $$("[data-vtab]").forEach(b => b.addEventListener("click", () => openTab(b.dataset.vtab)));

  async function addClip(id) {
    try {
      const r = await fetch(`/studio/video-info/${id}`); const info = await r.json();
      if (!r.ok) throw new Error(info.error || "That file can't be used.");
      items[id] = { title: info.title, kind: info.kind, duration: info.duration, w: info.w, h: info.h };
      P.clips.push(info.kind === "video" ? { item: id, in: 0, out: +Math.min(info.duration || 5, 60).toFixed(2), speed: 1, volume: 1, fit: "fill" }
                                        : { item: id, in: 0, out: 3, speed: 1, volume: 0, still: true, fit: "fit" });
      select({ type: "clip", i: P.clips.length - 1 }); changed(); show(segs()[P.clips.length - 1].start);
    } catch (e) { say(e.message); }
  }
  let media = null;
  async function loadMedia() {
    const r = await fetch(`/studio/video-media?pid=${C.pid}`); media = await r.json();
    const fill = (key, onPick, empty) => {
      const box = $(`[data-media=${key}]`); box.innerHTML = "";
      media[key].forEach(m => {
        const b = document.createElement("button"); b.type = "button"; b.className = "vd-m"; b.title = m.title;
        b.innerHTML = `<img alt="" loading="lazy"><span></span>${m.duration ? `<em>${fmt(m.duration).replace(/\.\d$/, "")}</em>` : ""}`;
        b.querySelector("img").src = m.thumb; b.querySelector("span").textContent = m.title;
        if (C.canEdit) b.addEventListener("click", () => onPick(m));
        box.appendChild(b);
      });
      if (!media[key].length) box.innerHTML = `<p class="small muted">${empty}</p>`;
    };
    fill("videos", m => addClip(m.id), "No videos for this brand yet. Add some above.");
    fill("pictures", m => addClip(m.id), "No photos or designs yet.");
    fill("sounds", m => { items[m.id] = { title: m.title, kind: "audio" }; P.music = { item: m.id, volume: P.music ? P.music.volume : 0.25 }; changed(); musicPanel(); },
      "No sounds in this brand's library yet.");
  }

  // brand panel
  const oOn = $("[data-outro-on]"), oText = $("[data-outro-text]"), oDur = $("[data-outro-dur]"), oImg = $("[data-outro-img]");
  let outroT;
  const outroImg = () => { clearTimeout(outroT); outroT = setTimeout(() => { const u = `/studio/video/${C.iid}/outro.png?text=${encodeURIComponent(P.outro.text)}`; oImg.src = u; po.src = u; }, 300); };
  function brandPanel() {
    oOn.checked = P.outro.on; oText.value = P.outro.text; oDur.value = P.outro.dur; $("[data-outro-dur-v]").textContent = `${P.outro.dur} s`;
    $$("input[name=capstyle]").forEach(r => { r.checked = r.value === P.style.captions; });
    $$("input[name=cappos]").forEach(r => { r.checked = r.value === P.style.position; });
    outroImg();
  }
  oOn.addEventListener("change", () => { P.outro.on = oOn.checked; changed(); });
  oText.addEventListener("input", () => { P.outro.text = oText.value; outroImg(); changed(false); });
  oDur.addEventListener("input", () => { P.outro.dur = +oDur.value; $("[data-outro-dur-v]").textContent = `${P.outro.dur} s`; changed(); });
  $$("input[name=capstyle], input[name=cappos]").forEach(r => r.addEventListener("change", () => {
    P.style.captions = $("input[name=capstyle]:checked").value; P.style.position = $("input[name=cappos]:checked").value; changed();
  }));
  const addLower = $("[data-add-lower]");
  if (addLower) addLower.addEventListener("click", () => {
    P.lowers.push({ start: +t.toFixed(2), end: +Math.min(t + 4, Math.max(total(), t + 1)).toFixed(2), text: "Dr. Name", sub: "Orthodontist" });
    select({ type: "lower", i: P.lowers.length - 1 }); changed(); $("[data-ltext]").select();
  });

  // captions panel
  function capList() {
    const ol = $("[data-caplist]"); ol.innerHTML = "";
    P.captions.forEach((c, i) => {
      const li = document.createElement("li");
      li.innerHTML = `<button type="button" class="linklike small"></button><span></span>`;
      li.querySelector("button").textContent = fmt(c.start); li.querySelector("span").textContent = c.text;
      li.querySelector("button").addEventListener("click", () => { select({ type: "cap", i }); show(c.start); });
      if (sel && sel.type === "cap" && sel.i === i) li.className = "on";
      ol.appendChild(li);
    });
    if (!P.captions.length) ol.innerHTML = `<li class="small muted">No captions yet.</li>`;
  }
  const addCap = $("[data-add-caption]");
  if (addCap) addCap.addEventListener("click", () => {
    P.captions.push({ start: +t.toFixed(2), end: +(t + 2).toFixed(2), text: "New caption" });
    sortCaps(); select({ type: "cap", i: P.captions.findIndex(c => c.text === "New caption" && Math.abs(c.start - t) < 0.01) });
    changed(); $("[data-captext]").select();
  });
  const auto = $("[data-auto-captions]");
  if (auto) auto.addEventListener("click", async () => {
    if (P.captions.length && !confirm("Replace the captions that are there now?")) return;
    await save();
    auto.disabled = true; auto.textContent = "Listening… (about a minute)";
    try {
      const r = await fetch(`/studio/video/${C.iid}/captions`, { method: "POST", headers: { Accept: "application/json" } });
      const j = await r.json(); if (!r.ok) throw new Error(j.error || "It didn't work.");
      P.captions = j.captions; sel = null; changed();
      say(j.captions.length ? `Wrote ${j.captions.length} captions; check names and spellings` : "No speech found in the clips");
    } catch (e) { say(e.message); }
    auto.disabled = false; auto.textContent = "Write captions from the speech";
  });

  // music panel
  function musicPanel() {
    const now = $("[data-music-now]");
    if (P.music && items[P.music.item]) {
      now.innerHTML = `<p><strong></strong> <button type="button" class="linklike danger small">Remove</button></p>`;
      now.querySelector("strong").textContent = `♪ ${items[P.music.item].title}`;
      now.querySelector("button").addEventListener("click", () => { P.music = null; changed(); musicPanel(); });
      $("[data-music-vol]").value = Math.round(P.music.volume * 100); $("[data-music-vol]").disabled = false;
    } else { now.innerHTML = `<p class="small muted">No music.</p>`; $("[data-music-vol]").disabled = true; }
  }
  $("[data-music-vol]").addEventListener("input", e => { if (P.music) { P.music.volume = e.target.value / 100; music.volume = Math.min(1, P.music.volume); } });
  $("[data-music-vol]").addEventListener("change", () => changed(false));

  // uploads: same pieces-at-a-time upload as the library, into "Video uploads"
  const upIn = $("[data-vupload]"), tray = $("[data-vtray]");
  async function upload(file) {
    const li = document.createElement("li"); li.textContent = `${file.name}: starting`; tray.appendChild(li);
    const post = (url, body, json) => fetch(url, { method: "POST", body, headers: json ? { "Content-Type": "application/json", Accept: "application/json" } : { Accept: "application/json" } })
      .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`); return j; });
    try {
      const { id } = await post("/studio/upload/start", JSON.stringify({ name: file.name, size: file.size, mime: file.type, pid: C.pid, folder: C.uploadsFolder }), true);
      for (let off = 0; off < file.size; off += C.chunk) {
        await post(`/studio/upload/${id}/chunk?offset=${off}`, file.slice(off, off + C.chunk));
        li.textContent = `${file.name}: ${Math.round(Math.min(file.size, off + C.chunk) / file.size * 100)}%`;
      }
      const fd = new FormData();
      const done = await post(`/studio/upload/${id}/done`, fd);
      li.textContent = `${file.name}: added`;
      await loadMedia(); await addClip(done.id);
    } catch (e) { li.textContent = `${file.name}: didn't go (${e.message})`; li.className = "bad"; }
  }
  if (upIn) upIn.addEventListener("change", async () => { for (const f of [...upIn.files]) await upload(f); upIn.value = ""; });
  const dz = $("[data-vupload-drop]");
  if (dz) { dz.addEventListener("dragover", e => e.preventDefault()); dz.addEventListener("drop", async e => { e.preventDefault(); for (const f of [...e.dataTransfer.files]) await upload(f); }); }

  // render
  const rb = $("[data-render]"), rs = $("[data-render-state]");
  let poll;
  function renderState(s) {
    if (!rs) return;
    if (s.state === "running") { rs.textContent = `Making the MP4: part ${Math.min(s.step + 1, s.steps)} of ${s.steps}…`; rb.disabled = true; }
    else if (s.state === "done") { rs.innerHTML = `Done (${Math.round(s.length || 0)} s). <a href="/studio/i/${C.iid}">See it in the library</a>`; rb.disabled = false; }
    else if (s.state === "error") { rs.textContent = s.error; rs.className = "small vd-render-state st-err"; rb.disabled = false; }
    else rs.textContent = "";
  }
  async function pollRender() {
    clearTimeout(poll);
    const r = await fetch(`/studio/video/${C.iid}/render`); const s = await r.json(); renderState(s);
    if (s.state === "running") poll = setTimeout(pollRender, 2000);
  }
  if (rb) rb.addEventListener("click", async () => {
    if (!P.clips.length) return say("Add at least one clip first.");
    await save();
    rb.disabled = true; rs.className = "small vd-render-state";
    const r = await fetch(`/studio/video/${C.iid}/render`, { method: "POST", headers: { Accept: "application/json" } });
    const j = await r.json(); if (!r.ok) { renderState({ state: "error", error: j.error }); return; }
    renderState(j); setTimeout(pollRender, 1500);
  });

  // ---------------------------------------------------------------- start
  (async () => {
    const r = await fetch(`/studio/video/${C.iid}/project`); const j = await r.json();
    P = j.project; items = Object.fromEntries(Object.entries(j.items).map(([k, v]) => [+k, v]));
    document.documentElement.style.setProperty("--brand", C.primary); document.documentElement.style.setProperty("--brand-2", C.accent);
    fitScreen(); brandPanel(); musicPanel(); draw(); show(0);
    loadMedia();
    if (C.render && C.render.state === "running") pollRender(); else renderState(C.render || {});
    say("");
  })();
})();
