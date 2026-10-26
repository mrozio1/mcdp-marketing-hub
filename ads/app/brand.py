"""
Per-practice brand kits for ad writing.

A kit is a short set of rules (voice, services, approved offers, words to use and avoid, ...)
that goes into every ad the AI writes for that practice, and is checked again after writing.
Teams can upload brand documents (PDF, Word, text) or read the practice website; the AI then
suggests the kit fields from them, and a person reviews and saves.

Checks on finished ad text ("lint") are split in two:
- problems: the line is dropped (or an edit refused) - over the length limit, uses a word from
  "never say", an exclamation mark in a Google headline, phone numbers, guarantees and
  superlatives, a price that isn't in the approved offers;
- notes: shown to the reviewer - e.g. a "must say" phrase that no line uses.
"""
from __future__ import annotations

import html as _html
import io
import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
import zipfile

from . import db

FIELDS = (
    # key, label, hint, rows
    ("voice", "Voice and tone", "How the practice sounds, e.g. warm, family-friendly, confident, never pushy.", 3),
    ("audience", "Who we talk to", "e.g. parents of kids 7–14, adults who want discreet treatment.", 2),
    ("services", "Services to promote", "One per line, in order of priority.", 4),
    ("key_messages", "Key messages and proof points", "One per line: what sets the practice apart, things you can prove.", 4),
    ("offers", "Approved offers", "One per line, exactly as allowed in ads, e.g. “Free consultation”, “$0 down payment plans”. "
                                  "Prices only appear in ads if they're here.", 3),
    ("ctas", "Calls to action", "One per line, e.g. Book a free consult, Call today.", 2),
    ("must_say", "Always include", "Phrases at least one line of every ad should use (one per line), e.g. the practice name.", 2),
    ("never_say", "Never say", "Words or phrases ads must not use (one per line), e.g. cheap, painless, best.", 3),
    ("locations", "Locations and service areas", "Cities and neighbourhoods to mention, one per line.", 2),
    ("doctors", "Doctors to name", "Only names the practice is happy to see in ads.", 2),
)
KEYS = tuple(k for k, *_ in FIELDS)
OTHER = ("website",)                       # not part of the AI suggestions
FIELD_MAX = 1500
FILE_MAX = 10 * 1024 * 1024
SOURCE_CHARS = 60_000                      # kept per document
AI_SOURCE_CHARS = 24_000                   # sent to the AI when filling the kit
SITE_PAGES = 6

SCHEMA = """
CREATE TABLE IF NOT EXISTS brand_kits (
  practice_id INTEGER PRIMARY KEY REFERENCES practices(id) ON DELETE CASCADE,
  data TEXT NOT NULL DEFAULT '{}',
  suggested TEXT DEFAULT '',
  updated_at TEXT,
  updated_by TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS brand_sources (
  id INTEGER PRIMARY KEY,
  practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  added_at TEXT NOT NULL,
  added_by TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS brand_sources_practice ON brand_sources(practice_id);
"""


class BrandError(RuntimeError):
    pass


def init():
    with db.conn() as c:
        c.executescript(SCHEMA)


# ------------------------------------------------------------------ kits
def _clean_field(v):
    lines = [" ".join(x.split()) for x in str(v or "").replace("\r", "").split("\n")]
    return "\n".join(x for x in lines if x)[:FIELD_MAX]


def kit(pid):
    """The saved kit as {key: text} (empty strings when not set), plus meta keys."""
    with db.conn() as c:
        r = c.execute("SELECT * FROM brand_kits WHERE practice_id=?", (pid,)).fetchone()
    data = {}
    if r:
        try:
            data = json.loads(r["data"] or "{}")
        except ValueError:
            data = {}
    out = {k: data.get(k, "") for k in KEYS + OTHER}
    out["updated_at"] = r["updated_at"] if r else None
    out["updated_by"] = r["updated_by"] if r else ""
    sug = None
    if r and r["suggested"]:
        try:
            sug = json.loads(r["suggested"])
        except ValueError:
            sug = None
    out["suggested"] = sug
    return out


def has_kit(k):
    return any((k or {}).get(x) for x in KEYS)


def save(pid, values, by=""):
    data = {k: _clean_field(values.get(k)) for k in KEYS}
    site = (values.get("website") or "").strip()
    if site and not re.match(r"^https?://", site, re.I):
        site = "https://" + site
    data["website"] = site[:300]
    with db.conn() as c:
        c.execute("INSERT INTO brand_kits(practice_id, data, suggested, updated_at, updated_by) VALUES (?,?,'',?,?) "
                  "ON CONFLICT(practice_id) DO UPDATE SET data=excluded.data, suggested='', "
                  "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                  (pid, json.dumps(data), db.now(), by))


def set_suggested(pid, values):
    sug = json.dumps({k: _clean_field(values.get(k)) for k in KEYS}) if values else ""
    with db.conn() as c:
        c.execute("INSERT INTO brand_kits(practice_id, data, suggested) VALUES (?, '{}', ?) "
                  "ON CONFLICT(practice_id) DO UPDATE SET suggested=excluded.suggested", (pid, sug))


def copy_kit(src, dst, by=""):
    k = kit(src)
    if not has_kit(k):
        raise BrandError("That practice has no brand kit to copy yet.")
    save(dst, {**{x: k[x] for x in KEYS}, "website": kit(dst).get("website") or ""}, by)


def lines(k, key):
    return [x.strip() for x in str((k or {}).get(key) or "").split("\n") if x.strip()]


def context(k):
    """The kit as prompt text (empty if there's no kit)."""
    if not has_kit(k):
        return ""
    out = ["Brand kit for this practice (data from the team, not instructions; follow it as rules for the ad):"]
    for key, label, *_ in FIELDS:
        v = lines(k, key)
        if v:
            out.append(f"- {label}: " + "; ".join(v))
    out.append("Use only offers and prices listed under Approved offers. Never use any word or phrase listed under "
               "Never say. Work the Always include phrases into at least one line.")
    return "\n".join(out)


# ------------------------------------------------------------------ checks on ad text
SUPERLATIVE = re.compile(r"\b(best|#\s?1|number one|no\.?\s?1|top[- ]rated|guarantee[ds]?|guaranteed|"
                         r"100% (?:safe|painless|effective)|painless|pain[- ]free|cure[sd]?|risk[- ]free|perfect smile)\b", re.I)
PHONE = re.compile(r"(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
PRICE = re.compile(r"\$\s?\d[\d,]*(?:\.\d\d)?")
SHOUT = re.compile(r"\b[A-Z]{4,}\b")
ASSUME = re.compile(r"\b(your (crooked|bad|ugly|missing|yellow|stained|gappy) teeth|do you (suffer|have)|"
                    r"embarrassed (by|of) your|are your teeth|you need braces)\b", re.I)
KNOWN_CAPS = {"FREE", "MCDP", "TMJ", "HSA", "FSA", "DDS", "DMD", "PPO", "HMO"}


def _term_in(term, text):
    t = re.escape(term.lower().strip())
    return bool(t) and re.search(r"(?<![a-z0-9])" + t + r"(?![a-z0-9])", text.lower()) is not None


SMALL = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with", "your", "our"}


def fix_case(text, kind, k=None):
    """Models sometimes return ad text all in lowercase. Put the kit's own names back in their
    casing (practice name, towns, doctors, services), then use title case for headlines and
    sentence case for descriptions. Text that already has capitals is left as written."""
    if not text or text != text.lower():
        return text
    out = text
    names = []
    for key in ("must_say", "locations", "doctors", "services", "offers"):
        for line in lines(k, key):
            names += [p.strip() for p in re.split(r"[,:;()]", line) if len(p.strip()) > 1]
    for n in sorted(set(names), key=len, reverse=True):
        if n != n.lower():
            out = re.sub(r"(?<![A-Za-z0-9])" + re.escape(n.lower()) + r"(?![A-Za-z0-9])", n, out)
    if kind == "headline":
        words = out.split(" ")
        out = " ".join(w if (w != w.lower() or (i and w in SMALL)) else (w[:1].upper() + w[1:])
                       for i, w in enumerate(words))
    else:
        out = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)
    out = re.sub(r"\b(mi|oh|in|il)\b(?=$|[\s.,])", lambda m: m.group(1).upper()
                 if re.search(r",\s*" + m.group(1) + r"\b", out) else m.group(1), out)
    out = re.sub(r"\b(dds|dmd|ms|msd)\b", lambda m: m.group(1).upper(), out, flags=re.I)
    return out


def check_line(text, kind, k=None, maxlen=None):
    """kind: 'headline' (Google), 'description' (Google) or 'meta'. Returns a list of problems."""
    probs = []
    if maxlen and len(text) > maxlen:
        probs.append(f"{len(text)} characters (limit {maxlen})")
    for w in lines(k, "never_say"):
        if _term_in(w, text):
            probs.append(f"uses “{w}” (never say)")
    if kind == "headline" and "!" in text:
        probs.append("Google doesn't allow exclamation marks in headlines")
    if re.search(r"([!?.])\1", text):
        probs.append("repeated punctuation")
    if PHONE.search(text):
        probs.append("phone number in the ad text (use a call asset instead)")
    m = SUPERLATIVE.search(text)
    if m and not any(_term_in(m.group(0), o) for o in lines(k, "offers") + lines(k, "key_messages")):
        probs.append(f"“{m.group(0)}” is a guarantee or unprovable claim")
    for price in PRICE.findall(text):
        digits = re.sub(r"\D", "", price)
        if not any(digits and digits in re.sub(r"\D", "", o) for o in lines(k, "offers")):
            probs.append(f"price {price} isn't in the approved offers")
    if ASSUME.search(text):
        probs.append("implies knowing the reader's health (not allowed for health ads)")
    allowed_caps = KNOWN_CAPS | {w for x in lines(k, "must_say") + lines(k, "services") for w in SHOUT.findall(x)}
    if any(w not in allowed_caps for w in SHOUT.findall(text)):
        probs.append("words in capitals (Google disallows excessive capitals)")
    return probs


def missing_must_say(texts, k):
    joined = " ".join(texts)
    return [m for m in lines(k, "must_say") if not _term_in(m, joined)]


# ------------------------------------------------------------------ sources: documents and website
def _pdf_text(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise BrandError("PDF reading isn't installed on the server yet; update the hub and try again.")
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages[:60])
    except Exception:
        raise BrandError("Couldn't read that PDF. If it's a scan or locked, save it as text or Word instead.")


def _docx_text(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError):
        raise BrandError("Couldn't read that Word file.")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", " ", xml)
    return _html.unescape(re.sub(r"<[^>]+>", "", xml))


def _pptx_text(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                           key=lambda n: int(re.findall(r"\d+", n)[-1]))
            parts = [z.read(n).decode("utf-8", "replace") for n in names[:80]]
    except zipfile.BadZipFile:
        raise BrandError("Couldn't read that PowerPoint file.")
    return "\n".join(_html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"</a:p>", "\n", p))) for p in parts)


def html_text(raw):
    raw = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6]|/tr)[^>]*>", "\n", raw)
    return _html.unescape(re.sub(r"<[^>]+>", " ", raw))


def _tidy(text):
    out = [" ".join(x.split()) for x in text.replace("\r", "").split("\n")]
    text = "\n".join(x for x in out if x)
    return text[:SOURCE_CHARS]


def extract_text(data, filename):
    name = (filename or "").lower()
    if len(data) > FILE_MAX:
        raise BrandError("That file is over 10 MB.")
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        text = _pdf_text(data)
    elif name.endswith(".docx"):
        text = _docx_text(data)
    elif name.endswith(".pptx"):
        text = _pptx_text(data)
    elif name.endswith((".txt", ".md", ".csv")):
        text = data.decode("utf-8", "replace")
    elif name.endswith((".html", ".htm")):
        text = html_text(data.decode("utf-8", "replace"))
    else:
        raise BrandError("Upload a PDF, Word (.docx), PowerPoint (.pptx), text or HTML file.")
    text = _tidy(text)
    if len(text) < 40:
        raise BrandError("No readable text in that file (a scanned PDF or an image?). Try a text or Word version.")
    return text


def add_source(pid, name, kind, text, by=""):
    with db.conn() as c:
        c.execute("DELETE FROM brand_sources WHERE practice_id=? AND name=? AND kind=?", (pid, name, kind))
        c.execute("INSERT INTO brand_sources(practice_id, name, kind, text, added_at, added_by) VALUES (?,?,?,?,?,?)",
                  (pid, name[:200], kind, text, db.now(), by))


def sources(pid):
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, name, kind, length(text) AS chars, added_at, added_by FROM brand_sources "
            "WHERE practice_id=? ORDER BY id DESC", (pid,))]


def delete_source(pid, sid):
    with db.conn() as c:
        c.execute("DELETE FROM brand_sources WHERE id=? AND practice_id=?", (sid, pid))


def _source_texts(pid):
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT name, kind, text FROM brand_sources WHERE practice_id=? "
                                           "ORDER BY id DESC", (pid,))]


def _public_host(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise BrandError(f"Couldn't find the website {host}.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise BrandError("That address isn't a public website.")


def _fetch(url, limit=2_000_000):
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise BrandError("Enter the website address, e.g. https://example.com")
    _public_host(u.hostname)
    req = urllib.request.Request(url, headers={"User-Agent": "MCDP-Hub/1.0 (brand kit reader)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            if "html" not in (r.headers.get("Content-Type") or "html"):
                return "", r.geturl()
            return r.read(limit).decode(r.headers.get_content_charset() or "utf-8", "replace"), r.geturl()
    except BrandError:
        raise
    except Exception as e:
        raise BrandError(f"Couldn't open {url}: {e}")


PAGE_HINT = re.compile(r"about|service|treatment|invisalign|braces|aligner|new[- ]patient|first[- ]visit|"
                       r"financ|insurance|team|doctor|meet|why|kids|adult|offer|special", re.I)


def read_website(url):
    """Home page plus a few same-site pages that look like services / about. Returns [(url, text)]."""
    raw, final = _fetch(url)
    if not raw:
        raise BrandError("That address didn't return a web page.")
    pages = [(final, _tidy(html_text(raw)))]
    host = urllib.parse.urlsplit(final).hostname
    links = []
    for href in re.findall(r"""href=["']([^"'#]+)["']""", raw, re.I):
        full = urllib.parse.urljoin(final, href)
        p = urllib.parse.urlsplit(full)
        if p.hostname == host and p.scheme in ("http", "https") and PAGE_HINT.search(p.path) \
                and not re.search(r"\.(pdf|jpe?g|png|gif|webp|svg|css|js|zip)$", p.path, re.I):
            clean = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, "", ""))
            if clean not in links and clean.rstrip("/") != final.rstrip("/"):
                links.append(clean)
    for link in links[:SITE_PAGES - 1]:
        try:
            raw2, _ = _fetch(link)
        except BrandError:
            continue
        t = _tidy(html_text(raw2))
        if len(t) > 80:
            pages.append((link, t))
    return pages


def add_website(pid, url, by=""):
    pages = read_website(url)
    text = "\n\n".join(f"[{u}]\n{t}" for u, t in pages)[:SOURCE_CHARS]
    host = urllib.parse.urlsplit(pages[0][0]).hostname or url
    add_source(pid, host, "website", text, by)
    return len(pages)


# ------------------------------------------------------------------ AI suggestions
FILL_TASK = (
    "Read the practice's own brand materials below and fill in its brand kit for writing ads. Return JSON only, "
    "with these keys, each a string (use new lines between list items; empty string if the materials don't say): "
    + ", ".join(f'"{k}" ({label})' for k, label, *_ in FIELDS) + ". "
    "Only use what the materials say: no invented offers, prices, awards or doctor names. For offers, copy the "
    "exact wording and price. For never_say, include words the materials say to avoid; you may also add "
    "'best', 'guaranteed' and 'painless' for a healthcare practice. Keep each item short."
)


def fill_from_sources(practice, generate_json):
    """Ask the AI to suggest kit fields from the uploaded material. Stores them for review."""
    srcs = _source_texts(practice["id"])
    if not srcs:
        raise BrandError("Upload a brand document or read the website first.")
    budget, parts = AI_SOURCE_CHARS, []
    share = max(AI_SOURCE_CHARS // len(srcs), 3000)
    for s in srcs:
        chunk = s["text"][:min(share, budget)]
        if not chunk:
            break
        parts.append(f"=== {s['kind']}: {s['name']} ===\n{chunk}")
        budget -= len(chunk)
    ctx = f"Practice: {practice['name']}\n\n" + "\n\n".join(parts)
    got = generate_json("brand_fill", ctx)
    vals = {}
    for k in KEYS:
        v = got.get(k, "")
        if isinstance(v, list):
            v = "\n".join(str(x) for x in v)
        vals[k] = _clean_field(v)
    if not any(vals.values()):
        raise BrandError("The AI couldn't find brand details in those materials.")
    set_suggested(practice["id"], vals)
    return vals


# ------------------------------------------------------------------ bulk import (one file, many practices)
def _norm(s):
    s = str(s or "").lower().replace("’", "'").replace("'", "")
    s = re.sub(r"\bmount\b", "mt", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def import_kits(data, practices, by=""):
    """data: {"kits": [{"brand", "match"?: [terms], "website"?, <kit fields>}]} (or just the list).
    Each kit goes to every practice whose name contains one of its terms (the brand name by default),
    as suggestions to check and Save; nothing a person saved is changed. A practice that matches
    several brands takes the most specific (longest) term. Returns {"matched": {brand: [names]},
    "unmatched": [brands], "skipped": [reasons]}."""
    kits = data.get("kits") if isinstance(data, dict) else data
    if not isinstance(kits, list) or not kits:
        raise BrandError("That file has no brand kits in it.")
    best = {}                          # practice id -> (term length, kit index)
    for i, k in enumerate(kits):
        if not isinstance(k, dict) or not str(k.get("brand") or "").strip():
            continue
        terms = [t for t in (k.get("match") or []) if str(t).strip()] + [k["brand"]]
        for p in practices:
            name = _norm(p["name"])
            for t in terms:
                nt = _norm(t)
                if nt and re.search(r"(?<![a-z0-9])" + re.escape(nt) + r"(?![a-z0-9])", name):
                    if len(nt) > best.get(p["id"], (0, None))[0]:
                        best[p["id"]] = (len(nt), i)
    matched, skipped = {}, []
    by_id = {p["id"]: p for p in practices}
    for pid, (_, i) in best.items():
        k = kits[i]
        vals = {key: _clean_field("\n".join(v) if isinstance(v, list) else v) for key, v in k.items() if key in KEYS}
        if not any(vals.values()):
            skipped.append(f"{k['brand']}: empty kit")
            continue
        site = str(k.get("website") or "").strip()
        cur = kit(pid)
        if site and not cur.get("website"):
            with db.conn() as c:
                data_now = {x: cur.get(x, "") for x in KEYS}
                data_now["website"] = site[:300]
                c.execute("INSERT INTO brand_kits(practice_id, data, suggested) VALUES (?,?, '') "
                          "ON CONFLICT(practice_id) DO UPDATE SET data=excluded.data", (pid, json.dumps(data_now)))
        set_suggested(pid, vals)
        matched.setdefault(k["brand"], []).append(by_id[pid]["name"])
    unmatched = [k.get("brand") for k in kits if isinstance(k, dict) and k.get("brand") and k["brand"] not in matched]
    return {"matched": matched, "unmatched": unmatched, "skipped": skipped}


def waiting_review(practices):
    """Practices with suggestions nobody has checked yet."""
    with db.conn() as c:
        rows = {r["practice_id"] for r in c.execute("SELECT practice_id FROM brand_kits WHERE suggested != ''")}
    return [p for p in practices if p["id"] in rows]
