/* Practice 360: reads the Practice Scorecard in MCDP Reports, one location at a time, and sends the numbers to the Hub.
   Runs on drapp.mcdentalpartners.com/reports with the scorecard open, signed in as the person running it.
   It uses the report's own export (the same data as each chart's "Export data"), changes only page filters while it runs,
   and puts them back. Progress is in window.__p360. Start: p360Collect(key, "https://ads.mcdp.com"). */
window.p360Collect = function (key, hub, opts) {
  opts = opts || {};   // {limit: n, dry: true} for a trial run that sends nothing
  const S = window.__p360 = {state: "running", done: 0, total: 0, step: "starting", error: "", result: null};
  const wait = ms => new Promise(z => setTimeout(z, ms));
  const csv = line => {               // one CSV line -> cells (handles quoted cells)
    const out = []; let cur = "", q = false;
    for (const ch of line) {
      if (ch === '"') q = !q; else if (ch === "," && !q) { out.push(cur); cur = ""; } else cur += ch;
    }
    out.push(cur); return out;
  };
  const rows = async v => (await v.exportData()).data.split("\r\n").filter(Boolean).map(csv);
  // The Summary tiles (Totals, Wages…) answer only to the Pods filter itself, so the summary step selects each location there.
  const pick = l => ({$schema: "http://powerbi.com/product/schema#hierarchy", filterType: 9,
                      target: [{table: "DIMLOCATION", column: "PODS"}, {table: "DIMLOCATION", column: "SAGELOCATIONNAME"}],
                      hierarchyData: [{operator: "Inherited", value: l.pod, children: [{operator: "Selected", value: l.name}]}]});
  const loc = name => ({$schema: "http://powerbi.com/product/schema#basic", target: {table: "DIMLOCATION", column: "SAGELOCATIONNAME"},
                        operator: "In", values: [name], filterType: 1});
  (async () => {
    const report = window.powerbi.get(document.querySelector("iframe").parentElement);
    const pages = await report.getPages();
    const start = pages.find(p => p.isActive);
    const sum = pages.find(p => p.displayName === "Summary"), det = pages.find(p => p.displayName === "Details");
    if (!sum || !det) throw new Error("Open MCDP Reports › Ortho › Practice Scorecard first.");
    await sum.setActive(); await wait(4000);
    let sv = await sum.getVisuals();
    const byHead = async (want) => { for (const v of sv) { try { const r = await rows(v); if (r[0] && r[0].join(",").includes(want)) return v; } catch (e) {} } return null; };
    // the summary tiles can take a while to render after the page opens: look again until all are found
    let podsV, labsV, wagesV, supV, totV, ytdV, dateV;
    for (let i = 0; i < 6; i++) {
      if (i) sv = await sum.getVisuals();
      [podsV, labsV, wagesV, supV, totV, ytdV, dateV] = [await byHead("PODS"), await byHead("Labs"), await byHead("Wages"),
        await byHead("Supplies"), await byHead("RPP Count"), await byHead("YTD Provider Discretionary"), await byHead("Date Range")];
      if (podsV && labsV && wagesV && supV && totV && ytdV && dateV) break;
      S.step = "waiting for the summary tiles"; await wait(5000);
    }
    const missing = Object.entries({Pods: podsV, Labs: labsV, Wages: wagesV, Supplies: supV, Totals: totV, YTD: ytdV, "Date range": dateV})
      .filter(([, v]) => !v).map(([k]) => k);
    if (missing.length) throw new Error("Couldn't find these Summary tiles: " + missing.join(", "));
    const list = (await rows(podsV)).slice(1).map(r => ({pod: r[0], name: r[1]})).filter(x => x.name).slice(0, opts.limit || 1000);
    const dateRange = (await rows(dateV))[1][0];
    S.total = list.length * 2;
    const slicerBefore = await podsV.getSlicerState();
    const got = {};
    try {
      for (const l of list) {
        S.step = "summary: " + l.name;
        await podsV.setSlicerState({filters: [pick(l)]}); await wait(2500);
        // tiles refresh at different speeds: read until two reads a second apart agree and Totals has moved on
        let snap = null, prev = null;
        for (let i = 0; i < 12; i++) {
          const t = (await rows(totV))[1] || [];
          snap = {labs: ((await rows(labsV))[1] || [])[0], wages: ((await rows(wagesV))[1] || [])[0], supplies: ((await rows(supV))[1] || [])[0],
                  exams: t[0], starts: t[1], net: t[2], rpp: t[3], ytd: ((await rows(ytdV))[1] || [])[0]};
          const sig = JSON.stringify(snap);
          if (sig === prev && t.join() !== S.lastTotals) break;
          prev = sig; await wait(1000);
        }
        S.lastTotals = [snap.exams, snap.starts, snap.net, snap.rpp].join();
        got[l.name] = {pod: l.pod, name: l.name, months: {}, summary: snap};
        S.done++;
      }
    } finally { await podsV.setSlicerState({filters: slicerBefore.filters || []}); }
    await det.setActive(); await wait(4000);
    const dv = await det.getVisuals();
    const line = async title => { for (const v of dv) { if (v.title === title && v.type === "lineChart") { try { const r = await rows(v); if (r[0][0] === "MONTHNAMEABBR") return v; } catch (e) {} } } return null; };
    const series = {production: await line("Production"), starts: await line("Starts"), exams: await line("New Patient Exams")};
    const dbase = await det.getFilters();
    try {
      for (const l of list) {
        S.step = "monthly: " + l.name;
        await det.setFilters([...dbase, loc(l.name)]); await wait(2500);
        let m = null, prev = null;
        for (let i = 0; i < 12; i++) {
          m = {};
          for (const [k, v] of Object.entries(series)) if (v) m[k] = (await rows(v)).slice(1).map(r => [r[0], r[1], r[2]]).filter(r => r[2] !== "");
          const sig = JSON.stringify(m);
          if (sig === prev && sig !== S.lastMonths) break;
          prev = sig; await wait(1000);
        }
        S.lastMonths = JSON.stringify(m);
        got[l.name].months = m;
        S.done++;
      }
    } finally { await det.setFilters(dbase); }
    await start.setActive();
    if (opts.dry) { S.result = {date_range: dateRange, locations: Object.values(got)}; S.state = "done"; return; }
    S.step = "sending to the Hub";
    const res = await fetch(hub.replace(/\/$/, "") + "/p360-in/import?k=" + encodeURIComponent(key),
                            {method: "POST", headers: {"Content-Type": "text/plain"}, body: JSON.stringify({date_range: dateRange, locations: Object.values(got)})});
    S.result = await res.json().catch(() => ({error: "The Hub answered " + res.status}));
    S.state = res.ok ? "done" : "failed";
    if (!res.ok) S.error = S.result.error || ("The Hub answered " + res.status);
  })().catch(e => { S.state = "failed"; S.error = String(e && (e.message || e.detailedMessage || e)); });
  return "started";
};
