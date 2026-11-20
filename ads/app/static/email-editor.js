/* Studio › Email campaigns › Design: a drag-and-drop email editor.
   The canvas is the server's own rendering of the email (the same HTML that is sent), redrawn as you edit.
   Nothing here can send to a list: sending happens only on the review page (checks, typed practice name, 10-minute wait). */
(() => {
  const D = JSON.parse(document.getElementById("eb-data").textContent);
  const form = document.getElementById("eb-form"), hidden = document.getElementById("eb-blocks"), revEl = document.getElementById("eb-rev");
  const frame = document.getElementById("eb-frame"), savedEl = document.getElementById("eb-saved");
  const LABEL = Object.fromEntries(D.types), ACTION = form.getAttribute("action");
  const ICON = {
    heading: "M6 4v16M18 4v16M6 12h12", text: "M4 6h16M4 12h16M4 18h10", image: "M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4",
    button: "M4 8h16v8H4zM9 12h6", columns: "M4 5h7v14H4zM13 5h7v14h-7z", divider: "M4 12h16",
    offer: "M4 7h16v10H4zM12 7v10M4 12h16", reviews: "M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z",
    social: "M6 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4M18 4a2 2 0 1 0 0 4 2 2 0 0 0 0-4M18 16a2 2 0 1 0 0 4 2 2 0 0 0 0-4M8 11l8-4M8 13l8 4",
    video: "M4 6h12v12H4zM16 10l4-2v8l-4-2", spacer: "M12 4v16M8 8l4-4 4 4M8 16l4 4 4-4"};
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const svg = d => `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"></path></svg>`;

  let blocks = D.blocks, sel = null, rev = D.rev;
  let undo = [], redo = [], lastKey = "", lastAt = 0;

  // ------------------------------------------------------------- new blocks
  function fresh(type, extra) {
    const b = {type};
    (D.fields[type] || []).forEach(f => { b[f] = f === "ids" ? [] : f === "item" ? null : (D.opts[f] ? D.opts[f][0] : ""); });
    if (type === "social") Object.assign(b, D.socialDefaults, {align: "center"});
    if (type === "reviews") Object.assign(b, {title: "What families are saying", ids: D.reviews.slice(0, 2).map(r => r.id)});
    if (type === "button") b.label = "Book a visit";
    if (type === "video") b.label = "Watch the video";
    return Object.assign(b, extra || {});
  }

  // ------------------------------------------------------------- history
  function snap(key) {
    const now = Date.now();
    if (key && key === lastKey && now - lastAt < 1200) { lastAt = now; return; }   // one undo step per burst of typing
    lastKey = key || ""; lastAt = now;
    undo.push(JSON.stringify({blocks, sel})); if (undo.length > 100) undo.shift();
    redo = []; tools();
  }
  function travel(from, to) {
    if (!from.length) return;
    to.push(JSON.stringify({blocks, sel}));
    const s = JSON.parse(from.pop()); blocks = s.blocks; sel = s.sel != null && s.sel < blocks.length ? s.sel : null;
    lastKey = ""; changed(true); inspect(); tools();
  }
  function tools() { document.getElementById("eb-undo").disabled = !undo.length; document.getElementById("eb-redo").disabled = !redo.length; }
  document.getElementById("eb-undo").addEventListener("click", () => travel(undo, redo));
  document.getElementById("eb-redo").addEventListener("click", () => travel(redo, undo));

  // ------------------------------------------------------------- saving (one save at a time; never overwrites someone else's)
  let saveTimer = null, inFlight = null, again = false, dirty = false, stopped = false;
  function body() {
    hidden.value = JSON.stringify(blocks); revEl.value = rev;
    return new FormData(form);
  }
  function scheduleSave(now) { dirty = true; savedEl.textContent = "Saving…"; clearTimeout(saveTimer); saveTimer = setTimeout(save, now ? 0 : 800); }
  async function save() {
    if (stopped) return;
    if (inFlight) { again = true; return inFlight; }
    inFlight = (async () => {
      try {
        const r = await fetch(ACTION, {method: "POST", body: body(), headers: {"x-requested-with": "fetch"}});
        const j = await r.json().catch(() => ({}));
        if (r.status === 409 || j.conflict) {
          stopped = true;
          document.getElementById("eb-conflict-msg").textContent = j.error || "Someone else changed this email.";
          document.getElementById("eb-conflict").hidden = false; savedEl.textContent = "Not saved";
        } else if (!r.ok) {
          savedEl.textContent = j.error || "Couldn't save"; savedEl.classList.add("bad");
        } else {
          rev = j.rev; dirty = again; savedEl.textContent = again ? "Saving…" : "Saved"; savedEl.classList.remove("bad");
          showChecks(j.checks);
        }
      } catch (e) { savedEl.textContent = "Offline: not saved yet"; savedEl.classList.add("bad"); }
    })();
    await inFlight; inFlight = null;
    if (again && !stopped) { again = false; return save(); }
  }
  async function flush() { clearTimeout(saveTimer); if (dirty || inFlight) { await save(); } return !stopped && !savedEl.classList.contains("bad"); }
  window.addEventListener("beforeunload", e => { if (dirty || inFlight) { e.preventDefault(); e.returnValue = ""; } });

  // ------------------------------------------------------------- checks (the same ones that gate sending)
  function showChecks(list) {
    if (!list) return;
    const bad = list.filter(c => !c.ok);
    const n = document.getElementById("eb-checks-n");
    n.textContent = bad.length ? `${bad.length} to fix` : "All clear"; n.className = "em-pill " + (bad.length ? "em-cancelled" : "em-sent");
    document.getElementById("eb-checks").innerHTML = list.map(c => `<li class="${c.ok ? "ok" : "bad"}"><span class="em-mark" aria-hidden="true">${c.ok ? "✓" : "!"}</span>
      <div><span>${esc(c.text)}</span>${c.ok ? "" : `<span class="em-why">${esc(c.detail)}</span>`}</div></li>`).join("");
  }
  showChecks(D.checks);

  // ------------------------------------------------------------- the canvas
  let drawTimer = null, drawSeq = 0;
  function changed(now) { drawSoon(now); scheduleSave(); }
  function drawSoon(now) { clearTimeout(drawTimer); drawTimer = setTimeout(draw, now ? 0 : 250); }
  async function draw() {
    const seq = ++drawSeq;
    const r = await fetch(ACTION + "/draw", {method: "POST", body: body()});
    if (!r.ok || seq !== drawSeq) return;
    const html = await r.text(), doc = frame.contentDocument, y = doc && doc.scrollingElement ? doc.scrollingElement.scrollTop : 0;
    doc.open(); doc.write(html); doc.close();
    wire(); if (doc.scrollingElement) doc.scrollingElement.scrollTop = y;
  }
  frame.addEventListener("load", () => { wire(); });

  const CANVAS_CSS = `
    [data-b] { cursor: pointer; position: relative; }
    [data-b]:hover > td { outline: 2px dashed #9cc3e2; outline-offset: -2px; }
    [data-b].eb-on > td { outline: 2px solid #2995d2; outline-offset: -2px; }
    [data-b].eb-drag { opacity: .35; }
    [data-fixed] { cursor: not-allowed; }
    .eb-line { position: absolute; left: 0; right: 0; height: 4px; background: #2995d2; border-radius: 2px; pointer-events: none; z-index: 9; box-shadow: 0 0 0 3px rgba(41,149,210,.25); }
    .eb-bar { position: absolute; z-index: 10; display: flex; gap: 2px; background: #151f2a; border-radius: 8px; padding: 3px; font: 600 12px Arial, sans-serif; box-shadow: 0 4px 12px rgba(0,0,0,.2); }
    .eb-bar button { all: unset; color: #fff; padding: 5px 8px; border-radius: 5px; cursor: pointer; }
    .eb-bar button:hover, .eb-bar button:focus-visible { background: #2c6396; }
    .eb-bar .eb-tag { color: #9fb2c0; padding: 5px 8px; }
    a { cursor: pointer; }`;

  function rowsOf(doc) { return [...doc.querySelectorAll("[data-b]")].filter(r => r.dataset.b !== "end"); }

  function wire() {
    const doc = frame.contentDocument;
    if (!doc || !doc.body || doc.body.dataset.wired) return;
    doc.body.dataset.wired = "1";
    const st = doc.createElement("style"); st.textContent = CANVAS_CSS; doc.head.appendChild(st);
    doc.querySelectorAll("[data-b]").forEach(r => { if (r.dataset.b !== "end") r.draggable = true; });
    paintSel();
    doc.addEventListener("click", e => {
      const a = e.target.closest("a"); if (a) e.preventDefault();                 // links never open from the editor
      const bar = e.target.closest(".eb-bar button");
      if (bar) { act(bar.dataset.act); return; }
      const row = e.target.closest("[data-b]");
      if (row && row.dataset.b !== "end") { select(+row.dataset.b); }
      else if (e.target.closest("[data-fixed]")) { select(null); flashMsg("The header and footer come from the practice's brand and are always there."); }
      else select(null);
    });
    // drag to reorder, and drops from the palette or Studio pictures
    let line = null, dragFrom = null;
    const where = y => {
      const rows = rowsOf(doc);
      for (const r of rows) { const b = r.getBoundingClientRect(); if (y < b.top + b.height / 2) return {i: +r.dataset.b, top: b.top}; }
      const last = rows[rows.length - 1];
      return {i: blocks.length, top: last ? last.getBoundingClientRect().bottom : (doc.querySelector("[data-b='end']") || doc.body).getBoundingClientRect().top};
    };
    doc.addEventListener("dragstart", e => {
      const row = e.target.closest && e.target.closest("[data-b]"); if (!row) return;
      dragFrom = +row.dataset.b; row.classList.add("eb-drag");
      e.dataTransfer.setData("text/eb-move", String(dragFrom)); e.dataTransfer.effectAllowed = "move";
    });
    doc.addEventListener("dragend", () => { dragFrom = null; doc.querySelectorAll(".eb-drag").forEach(r => r.classList.remove("eb-drag")); line && line.remove(); line = null; });
    doc.addEventListener("dragover", e => {
      const ok = [...e.dataTransfer.types].some(t => t.startsWith("text/eb-")); if (!ok) return;
      e.preventDefault();
      const w = where(e.clientY);
      if (!line) { line = doc.createElement("div"); line.className = "eb-line"; doc.body.appendChild(line); }
      const tbl = doc.querySelector("table table") || doc.body, bb = tbl.getBoundingClientRect(), sy = doc.scrollingElement.scrollTop;
      Object.assign(line.style, {top: (w.top + sy - 2) + "px", left: bb.left + "px", width: bb.width + "px", right: "auto"});
    });
    doc.addEventListener("dragleave", e => { if (e.target === doc.documentElement && line) { line.remove(); line = null; } });
    doc.addEventListener("drop", e => {
      e.preventDefault(); line && line.remove(); line = null;
      const i = where(e.clientY).i, dt = e.dataTransfer;
      const mv = dt.getData("text/eb-move"), ty = dt.getData("text/eb-type"), it = dt.getData("text/eb-item");
      if (mv !== "") {
        const from = +mv; if (from === i || from + 1 === i) return;
        snap(); const [b] = blocks.splice(from, 1); const to = from < i ? i - 1 : i; blocks.splice(to, 0, b); sel = to;
        changed(true); inspect();
      } else if (ty) add(ty, i);
      else if (it) add("image", i, {item: +it, alt: (D.media.find(m => m.id === +it) || {}).title || ""});
    });
  }

  function paintSel() {
    const doc = frame.contentDocument; if (!doc || !doc.body) return;
    doc.querySelectorAll(".eb-on").forEach(r => r.classList.remove("eb-on"));
    doc.querySelectorAll(".eb-bar").forEach(x => x.remove());
    if (sel == null) return;
    const row = doc.querySelector(`[data-b="${sel}"]`); if (!row) return;
    row.classList.add("eb-on");
    const bar = doc.createElement("div"); bar.className = "eb-bar"; bar.setAttribute("role", "toolbar"); bar.setAttribute("aria-label", "Block");
    bar.innerHTML = `<span class="eb-tag">${esc(LABEL[blocks[sel].type])}</span>` +
      [["up", "↑", "Move up", sel > 0], ["down", "↓", "Move down", sel < blocks.length - 1], ["copy", "Duplicate", "Duplicate"], ["del", "Delete", "Delete"]]
        .filter(x => x[3] !== false).map(([a, t, l]) => `<button type="button" data-act="${a}" aria-label="${l}">${t}</button>`).join("");
    doc.body.appendChild(bar);
    const r = row.getBoundingClientRect(), sy = doc.scrollingElement.scrollTop;
    bar.style.top = Math.max(0, r.top + sy - 34) + "px"; bar.style.left = (r.right - bar.offsetWidth) + "px";
  }

  function act(a) {
    if (sel == null) return;
    snap();
    if (a === "up" && sel > 0) { [blocks[sel - 1], blocks[sel]] = [blocks[sel], blocks[sel - 1]]; sel--; }
    else if (a === "down" && sel < blocks.length - 1) { [blocks[sel + 1], blocks[sel]] = [blocks[sel], blocks[sel + 1]]; sel++; }
    else if (a === "copy") { blocks.splice(sel + 1, 0, JSON.parse(JSON.stringify(blocks[sel]))); sel++; }
    else if (a === "del") { blocks.splice(sel, 1); sel = null; flashMsg("Block deleted. Undo brings it back."); }
    changed(true); inspect();
  }

  function add(type, at, extra) {
    snap();
    const i = at ?? (sel != null ? sel + 1 : blocks.length);
    blocks.splice(i, 0, fresh(type, extra)); sel = i;
    changed(true); inspect();
    const first = document.querySelector("#eb-fields input, #eb-fields textarea"); if (first) first.focus();
  }

  function select(i) { sel = i; paintSel(); inspect(); }
  document.getElementById("eb-deselect").addEventListener("click", () => select(null));

  // ------------------------------------------------------------- palette and Studio pictures
  const pal = document.getElementById("eb-palette");
  pal.innerHTML = D.types.map(([t, l]) => `<button type="button" class="eb-tile" draggable="true" data-type="${t}">${svg(ICON[t] || ICON.text)}<span>${esc(l)}</span></button>`).join("");
  pal.addEventListener("click", e => { const t = e.target.closest("[data-type]"); if (t) add(t.dataset.type); });
  pal.addEventListener("dragstart", e => { const t = e.target.closest("[data-type]"); if (t) { e.dataTransfer.setData("text/eb-type", t.dataset.type); e.dataTransfer.effectAllowed = "copy"; } });
  const med = document.getElementById("eb-media");
  med.innerHTML = D.media.length ? D.media.map(m => `<button type="button" class="eb-pic" draggable="true" data-item="${m.id}" title="${esc(m.title)}"><img src="/studio/t/${m.id}" alt="${esc(m.title)}" loading="lazy"></button>`).join("")
    : `<p class="small eb-muted">No pictures for ${esc(D.practice)} yet. Add them in Studio first.</p>`;
  med.addEventListener("click", e => {
    const t = e.target.closest("[data-item]"); if (!t) return;
    const id = +t.dataset.item, title = (D.media.find(m => m.id === id) || {}).title || "";
    if (sel != null && ["image", "video"].includes(blocks[sel].type)) { snap(); blocks[sel].item = id; if (!blocks[sel].alt) blocks[sel].alt = title; changed(true); inspect(); }
    else add("image", null, {item: id, alt: title});
  });
  med.addEventListener("dragstart", e => { const t = e.target.closest("[data-item]"); if (t) { e.dataTransfer.setData("text/eb-item", t.dataset.item); e.dataTransfer.effectAllowed = "copy"; } });

  // ------------------------------------------------------------- the inspector
  const box = document.getElementById("eb-fields");
  const F = (f, label, kind, hint) => ({f, label, kind, hint});
  const SPEC = {
    heading: [F("text", "Heading", "line"), F("size", "Size", "seg", {l: "Large", m: "Medium"}), F("align", "Align", "seg", {left: "Left", center: "Center"})],
    text: [F("text", "Text", "rich"), F("align", "Align", "seg", {left: "Left", center: "Center"})],
    image: [F("item", "Picture", "pics"), F("alt", "Describe the picture (for screen readers and blocked images)", "input"),
            F("link", "Link when clicked (optional)", "url"), F("width", "Width", "seg", {full: "Full width", padded: "With margins"})],
    button: [F("label", "Button text", "input"), F("url", "Link", "url"), F("color", "Color", "colors"), F("align", "Align", "seg", {left: "Left", center: "Center"})],
    columns: [F("left", "Left column", "rich"), F("right", "Right column", "rich")],
    offer: [F("title", "Offer (exactly as in the brand kit's approved offers)", "input"), F("text", "Details", "rich"),
            F("label", "Button text", "input"), F("url", "Link", "url")],
    divider: [], spacer: [F("height", "Height", "seg", {s: "Small", m: "Medium", l: "Large"})],
    reviews: [F("title", "Heading (optional)", "input"), F("summary", "Star rating line", "seg", {on: "Show", off: "Hide"}), F("ids", "Reviews", "reviews")],
    social: [...D.social.map(([k, l]) => F(k, l + " page", "url")), F("align", "Align", "seg", {left: "Left", center: "Center"})],
    video: [F("url", "YouTube or Vimeo link", "url"), F("label", "Button text", "input"), F("item", "Cover picture (YouTube links get one automatically)", "pics"),
            F("alt", "Describe the cover picture", "input")],
  };
  const NOTE = {
    divider: "A thin line between sections. No settings.",
    text: "Use the buttons for bold, links and the person's first name. Brand kit \"never say\" words and prices are checked as you type.",
    reviews: `Pick up to 3 of ${D.practice}'s Google reviews (4 and 5 stars). Names show as first name and last initial.`,
    social: "Links can only go to the big social sites or the practice's own website.",
    video: "Email can't play video, so this shows a picture and a button that opens the video.",
    offer: "Prices must match the brand kit's approved offers or sending is blocked.",
  };

  function inspect() {
    const blockBox = document.getElementById("eb-block"), emailBox = document.getElementById("eb-email");
    paintSel();
    if (sel == null || !blocks[sel]) { blockBox.hidden = true; emailBox.hidden = false; return; }
    blockBox.hidden = false; emailBox.hidden = true;
    const b = blocks[sel];
    document.getElementById("eb-block-title").textContent = LABEL[b.type] || b.type;
    box.innerHTML = (NOTE[b.type] ? `<p class="small eb-muted">${esc(NOTE[b.type])}</p>` : "") + (SPEC[b.type] || []).map(s => field(b, s)).join("");
  }

  function field(b, {f, label, kind, hint}) {
    const id = `ebf-${f}`, v = b[f] ?? "";
    if (kind === "seg") return `<fieldset class="eb-seg"><legend>${label}</legend>${Object.entries(hint).map(([k, l]) =>
      `<label><input type="radio" name="${id}" data-f="${f}" value="${k}" ${v === k ? "checked" : ""}><span>${l}</span></label>`).join("")}</fieldset>`;
    if (kind === "colors") return `<fieldset class="eb-seg"><legend>${label}</legend>${[["primary", "Main", D.colors.primary], ["accent", "Accent", D.colors.accent], ["dark", "Dark", "#13232f"]].map(([k, l, c]) =>
      `<label><input type="radio" name="${id}" data-f="${f}" value="${k}" ${v === k ? "checked" : ""}><span><i style="background:${c}"></i>${l}</span></label>`).join("")}</fieldset>`;
    if (kind === "rich") return `<div class="eb-rich"><label for="${id}">${label}</label>
      <span class="eb-fmt" role="group" aria-label="Format"><button type="button" data-fmt="bold" data-for="${id}"><b>B</b></button><button type="button" data-fmt="link" data-for="${id}">Link</button><button type="button" data-fmt="name" data-for="${id}">First name</button></span>
      <textarea id="${id}" data-f="${f}" rows="${f === "text" ? 6 : 5}">${esc(v)}</textarea></div>`;
    if (kind === "pics") {
      const pics = D.media.map(m => `<button type="button" class="eb-pic ${v === m.id ? "on" : ""}" data-pick="${m.id}" aria-pressed="${v === m.id}" title="${esc(m.title)}"><img src="/studio/t/${m.id}" alt="${esc(m.title)}" loading="lazy"></button>`).join("");
      return `<div class="eb-pickbox"><span class="eb-flabel">${label}</span>${pics ? `<div class="eb-media eb-media-sm">${pics}</div>` : `<p class="small eb-muted">No pictures in ${esc(D.practice)}'s Studio yet.</p>`}
        ${v ? `<button type="button" class="linklike" data-pick="">Remove the picture</button>` : ""}</div>`;
    }
    if (kind === "reviews") {
      if (!D.reviews.length) return `<p class="small eb-muted">No Google reviews with text are linked to ${esc(D.practice)} yet (Reviews tab › link its Google location).</p>`;
      const ids = b.ids || [];
      return `<fieldset class="eb-revs"><legend>${label} <span class="eb-count">${ids.length} of 3</span></legend>${D.reviews.map(r =>
        `<label class="eb-rev"><input type="checkbox" data-rev="${esc(r.id)}" ${ids.includes(r.id) ? "checked" : ""} ${!ids.includes(r.id) && ids.length >= 3 ? "disabled" : ""}>
          <span><span class="eb-stars" aria-label="${r.stars} stars">${"★".repeat(r.stars || 5)}</span> <strong>${esc(r.who)}</strong> <span class="eb-muted small">${esc(r.date)}</span><br>${esc(r.text.length > 180 ? r.text.slice(0, 180) + "…" : r.text)}</span></label>`).join("")}</fieldset>`;
    }
    if (kind === "line") return `<label for="${id}">${label}</label><textarea id="${id}" data-f="${f}" rows="2">${esc(v)}</textarea>`;
    const ph = kind === "url" ? (D.site ? `https://${D.site}/…` : "https://") : "";
    return `<label for="${id}">${label}</label><input id="${id}" data-f="${f}" value="${esc(v)}" ${kind === "url" ? `type="url" inputmode="url" placeholder="${ph}"` : ""}>`;
  }

  box.addEventListener("input", e => {
    const t = e.target; if (sel == null || !t.dataset.f || t.type === "radio") return;
    snap(`${sel}:${t.dataset.f}`); blocks[sel][t.dataset.f] = t.value; changed();
  });
  box.addEventListener("change", e => {
    const t = e.target; if (sel == null) return;
    if (t.type === "radio" && t.dataset.f) { snap(); blocks[sel][t.dataset.f] = t.value; changed(true); }
    if (t.dataset.rev != null) {
      snap(); const ids = (blocks[sel].ids || []).filter(x => x !== t.dataset.rev);
      if (t.checked && ids.length < 3) ids.push(t.dataset.rev);
      blocks[sel].ids = ids; changed(true); inspect();
    }
  });
  box.addEventListener("click", e => {
    const p = e.target.closest("[data-pick]");
    if (p && sel != null) {
      snap(); const id = p.dataset.pick ? +p.dataset.pick : null; blocks[sel].item = id;
      if (id && !blocks[sel].alt) blocks[sel].alt = (D.media.find(m => m.id === id) || {}).title || "";
      changed(true); inspect(); return;
    }
    const fb = e.target.closest("[data-fmt]"); if (!fb) return;
    const ta = document.getElementById(fb.dataset.for), [a, z] = [ta.selectionStart, ta.selectionEnd], picked = ta.value.slice(a, z);
    let ins;
    if (fb.dataset.fmt === "bold") ins = `**${picked || "bold text"}**`;
    else if (fb.dataset.fmt === "name") ins = "{first_name}";
    else {
      const url = prompt("Link to (https://…)", D.site ? `https://${D.site}/` : "https://");
      if (!url) return; ins = `[${picked || "link text"}](${url.trim()})`;
    }
    ta.setRangeText(ins, a, z, "end"); ta.focus(); ta.dispatchEvent(new Event("input", {bubbles: true}));
  });

  // ------------------------------------------------------------- email settings
  const toEl = document.getElementById("eb-to");
  function lists() {
    const on = [...form.querySelectorAll('input[name="list_id"]:checked')].map(i => i.dataset.name);
    toEl.textContent = on.length ? " to " + on.join(" + ") : " · no list picked yet";
  }
  function counts() {
    form.querySelectorAll("[data-count]").forEach(c => {
      const n = form.elements[c.dataset.count].value.length, max = c.dataset.count === "subject" ? 60 : 90;
      c.textContent = `${n}/${max}`; c.classList.toggle("over", n > max);
    });
  }
  form.querySelectorAll('#eb-email input, .eb-name input').forEach(i => i.addEventListener(i.type === "checkbox" ? "change" : "input", () => {
    lists(); counts(); drawSoon(); scheduleSave(i.type === "checkbox");
  }));
  lists(); counts();
  form.addEventListener("submit", e => e.preventDefault());       // Enter in a field never leaves the page

  // ------------------------------------------------------------- preview size
  document.querySelectorAll("[data-w]").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll("[data-w]").forEach(x => x.classList.toggle("on", x === b));
    document.getElementById("eb-stage").classList.toggle("phone", b.dataset.w === "phone");
    setTimeout(paintSel, 50);
  }));

  // ------------------------------------------------------------- test and next
  document.getElementById("eb-test-send").addEventListener("click", async () => {
    const msg = document.getElementById("eb-test-msg"), to = document.getElementById("eb-test-to").value.trim();
    if (!to) { msg.textContent = "Which address?"; return; }
    msg.textContent = "Saving and sending…";
    if (!(await flush())) { msg.textContent = "Save the email first (see the message at the top)."; return; }
    const fd = new FormData(); fd.append("to", to);
    const r = await fetch(ACTION + "/test", {method: "POST", body: fd, headers: {"x-requested-with": "fetch"}});
    const j = await r.json().catch(() => ({}));
    msg.textContent = j.message || (r.ok ? "Sent." : "Couldn't send the test.");
  });
  document.getElementById("eb-next").addEventListener("click", async e => {
    e.preventDefault();
    const go = e.currentTarget.href;
    savedEl.textContent = "Saving…";
    if (await flush()) location.href = go;
  });

  // ------------------------------------------------------------- keys
  document.addEventListener("keydown", e => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !typing) { e.preventDefault(); travel(e.shiftKey ? redo : undo, e.shiftKey ? undo : redo); }
    else if (e.key === "Escape" && sel != null) select(null);
  });

  let toast = null;
  function flashMsg(t) {
    if (!toast) { toast = document.createElement("p"); toast.className = "eb-toast"; toast.setAttribute("role", "status"); document.body.appendChild(toast); }
    toast.textContent = t; toast.classList.add("on"); clearTimeout(toast._t); toast._t = setTimeout(() => toast.classList.remove("on"), 3500);
  }

  inspect(); tools();
  if (frame.contentDocument && frame.contentDocument.readyState === "complete") wire();
})();
