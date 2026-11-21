// LeadSigma collector for the MCDP Hub (used until LeadSigma's API is connected).
// Run on https://onlinebooking.leadsigma.com/admin/marketing_attribution while signed in:
//   lsCollect(key, "https://ads.mcdp.com", {days: 35})   or   {from: "2025-11-01", to: "2026-11-20"}
// It reads the Marketing Attribution CSV once per day (in the page, nothing is saved to disk) and sends the files to the
// Hub with a one-use key. The Hub keeps only counts. Progress: window.__ls
window.lsCollect = function (key, hub, opts) {
  opts = opts || {};
  const S = window.__ls = {state: "running", step: "starting", done: 0, total: 0, error: "", result: null};
  const iso = d => d.toISOString().slice(0, 10);
  const day = s => new Date(s + "T12:00:00Z");
  (async () => {
    const to = opts.to ? day(opts.to) : new Date(Date.now() - 864e5);          // yesterday: today isn't finished
    const from = opts.from ? day(opts.from) : new Date(to.getTime() - ((opts.days || 35) - 1) * 864e5);
    const list = [];
    for (let d = new Date(from); d <= to; d = new Date(d.getTime() + 864e5)) list.push(iso(d));
    S.total = list.length;
    const days = {};
    for (const d of list) {
      S.step = d;
      const r = await fetch("/admin/marketing_attribution.csv?start_date=" + d + "&end_date=" + d, {credentials: "same-origin"});
      if (!r.ok) throw new Error("LeadSigma answered " + r.status + " for " + d + " (signed out?)");
      const t = await r.text();
      if (!/^\s*Report,Label/i.test(t)) throw new Error("LeadSigma didn't send its CSV for " + d + " (signed out?)");
      days[d] = t;
      S.done++;
    }
    S.step = "sending to the Hub";
    const res = await fetch(hub + "/ls-in/import?k=" + encodeURIComponent(key),
                            {method: "POST", headers: {"Content-Type": "text/plain"}, body: JSON.stringify({days})});
    const j = await res.json().catch(() => ({error: "The Hub answered " + res.status}));
    if (!res.ok || j.error) throw new Error(j.error || ("The Hub answered " + res.status));
    S.result = j; S.state = "done"; S.step = "done";
  })().catch(e => { S.state = "failed"; S.error = String(e && e.message || e); });
  return "started";
};
