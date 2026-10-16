/* MCDP Hub touches for Social (social.mcdp.com), added to every page by the social-skin proxy.
   - says "Social" wherever the app shows its own product name (never inside posts, form fields or code)
   - keeps the app in its light mode (the hub's palette is set for both modes in skin.css)
   - the hub's tab icon, and a small "MCDP Hub · Open-source license" link (the app is AGPL-3.0,
     so people using it here are offered its source code). */
(() => {
  "use strict";
  const NAMES = /\b(Postiz|Gitroom)\b/g;
  const TEST = /\b(Postiz|Gitroom)\b/;
  const SKIP = "script, style, noscript, code, pre, kbd, samp, textarea, input, select, [contenteditable], [contenteditable] *, .mcdp-keep";
  const ATTRS = ["title", "placeholder", "alt", "aria-label"];
  const HUB = "https://ads.mcdp.com/";
  const SOURCE = "https://github.com/gitroomhq/postiz-app";

  const rename = (s) => s.replace(NAMES, "Social");

  function fixText(node) {
    const p = node.parentElement;
    if (!p || p.closest(SKIP)) return;
    if (TEST.test(node.nodeValue)) node.nodeValue = rename(node.nodeValue);
  }
  function fixAttrs(el) {
    if (el.closest && el.closest("[contenteditable]")) return;
    for (const a of ATTRS) {
      const v = el.getAttribute && el.getAttribute(a);
      if (v && TEST.test(v)) el.setAttribute(a, rename(v));
    }
  }
  function sweep(root) {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) { fixText(root); return; }
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
    if (root.nodeType === Node.ELEMENT_NODE) {
      if (root.matches(SKIP)) return;
      fixAttrs(root);
      root.querySelectorAll("[title], [placeholder], [alt], [aria-label]").forEach(fixAttrs);
    }
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) fixText(n);
  }
  function fixTitle() {
    if (TEST.test(document.title)) document.title = rename(document.title);
  }
  function fixIcon() {
    document.querySelectorAll('link[rel~="icon"]').forEach((l) => {
      if (!l.href.endsWith("/__mcdp/favicon.svg")) { l.href = "/__mcdp/favicon.svg"; l.type = "image/svg+xml"; }
    });
  }
  function lightMode() {
    if (!/(^|;\s*)mode=light/.test(document.cookie)) {
      document.cookie = "mode=light; path=/; max-age=31536000; SameSite=Lax";
    }
    const b = document.body;
    if (b && b.classList.contains("dark")) { b.classList.remove("dark"); b.classList.add("light"); }
  }
  function foot() {
    if (!document.body || document.querySelector(".mcdp-foot")) return;
    const f = document.createElement("nav");
    f.className = "mcdp-foot";
    f.setAttribute("aria-label", "MCDP Hub");
    f.innerHTML = '<a href="' + HUB + '">MCDP Hub</a><span aria-hidden="true">·</span>' +
                  '<a href="' + SOURCE + '" target="_blank" rel="noopener">Open-source license</a>';
    document.body.appendChild(f);
  }

  let queued = new Set(), scheduled = false;
  function flush() {
    scheduled = false;
    const items = [...queued];
    queued = new Set();
    items.forEach(sweep);
    fixTitle();
    fixIcon();
    lightMode();
  }
  const observer = new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === "characterData") queued.add(r.target);
      else if (r.type === "attributes") queued.add(r.target);
      else r.addedNodes.forEach((n) => queued.add(n));
    }
    if (!scheduled) { scheduled = true; requestAnimationFrame(flush); }
  });

  function start() {
    lightMode();
    sweep(document.body);
    fixTitle();
    fixIcon();
    observer.observe(document.documentElement, {
      subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS,
    });
    new MutationObserver(lightMode).observe(document.body, { attributes: true, attributeFilter: ["class"] });
    // after the app has taken over the page, so the link isn't in its way while it starts up
    if (document.readyState === "complete") foot(); else window.addEventListener("load", foot);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
