"""Practice results as an Outlook-friendly email.

Outlook for Windows draws email with Word's engine: no CSS grid/flex, no <style> blocks it
can rely on, no SVG. So everything here is plain tables with inline styles, 640px wide, in
fonts every Windows/Mac machine has.
"""
from __future__ import annotations

import html as _html
import re
from email.message import EmailMessage
from email.utils import formatdate

INK, SOFT, TEAL, ROSE, AMBER, GREEN, LINE, PAPER = (
    "#1D2D35", "#52646C", "#0F7471", "#B83A5E", "#A86A12", "#2E7D52", "#DCE4E5", "#F6F8F8")
FONT = "'Segoe UI', Arial, Helvetica, sans-serif"

SECTIONS = [  # (key, label) in email order
    ("summary", "Spend, results and pacing"),
    ("practices", "By practice (master record)"),
    ("organic", "Organic Facebook and Instagram posts"),
    ("attention", "What needs attention"),
    ("search", "Search terms"),
    ("campaigns", "Campaign table"),
    ("fixes", "Write-up: what to fix"),
    ("creative", "Write-up: ad ideas"),
    ("report", "Monthly report"),
    ("changes", "Changes made this month"),
]

# Inline styles for the HTML that Markdown produces from AI write-ups.
_TAG_STYLES = {
    "h1": f"font-family:{FONT};font-size:17px;color:{INK};margin:18px 0 6px;",
    "h2": f"font-family:{FONT};font-size:16px;color:{INK};margin:18px 0 6px;",
    "h3": f"font-family:{FONT};font-size:15px;color:{INK};margin:14px 0 4px;",
    "h4": f"font-family:{FONT};font-size:14px;color:{INK};margin:12px 0 4px;",
    "p": f"font-family:{FONT};font-size:14px;line-height:1.5;color:{INK};margin:0 0 10px;",
    "ul": "margin:0 0 10px 0;padding-left:22px;",
    "ol": "margin:0 0 10px 0;padding-left:22px;",
    "li": f"font-family:{FONT};font-size:14px;line-height:1.5;color:{INK};margin:0 0 4px;",
    "table": "border-collapse:collapse;width:100%;margin:6px 0 12px;",
    "th": f"font-family:{FONT};font-size:13px;color:{SOFT};text-align:left;border-bottom:1px solid {LINE};padding:5px 6px;",
    "td": f"font-family:{FONT};font-size:13px;color:{INK};border-bottom:1px solid {LINE};padding:5px 6px;vertical-align:top;",
    "code": "font-family:Consolas,monospace;font-size:13px;",
    "blockquote": f"margin:0 0 10px;padding-left:10px;border-left:3px solid {LINE};color:{SOFT};",
}
_OPEN_TAG = re.compile(r"<(h[1-4]|p|ul|ol|li|table|th|td|code|blockquote)(\s[^>]*)?>")


def inline_styles(fragment):
    """Give every Markdown-produced tag an inline style (merging any existing style)."""
    def sub(m):
        tag, attrs = m.group(1), m.group(2) or ""
        style = _TAG_STYLES[tag]
        sm = re.search(r'style="([^"]*)"', attrs)
        if sm:
            attrs = attrs.replace(sm.group(0), f'style="{style}{sm.group(1)}"')
            return f"<{tag}{attrs}>"
        return f'<{tag}{attrs} style="{style}">'
    if "<table" in fragment:
        fragment = fragment.replace("<table>", '<table cellpadding="0" cellspacing="0" role="presentation">')
    return _OPEN_TAG.sub(sub, fragment)


def bar_color(status):
    return {"over": ROSE, "under": AMBER}.get(status, TEAL)


def subject_for(practice, views):
    months = [v["pacing"]["month"] for v in views if v.get("pacing", {}).get("available")]
    return f"{practice['name']} — ad results{' for ' + months[0] if months else ''}"


def to_plain(html_doc):
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", html_doc)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|h[1-6]|tr|li|table)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "• ", text)
    text = re.sub(r"(?i)</t[dh]>", "  ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", "\n".join(line.strip() for line in text.splitlines())).strip()


def eml(subject, html_doc):
    """An unsent Outlook draft: 'X-Unsent: 1' makes Outlook for Windows open it ready to edit and send."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["To"] = ""
    msg["Date"] = formatdate(localtime=True)
    msg["X-Unsent"] = "1"
    msg.set_content(to_plain(html_doc))
    msg.add_alternative(html_doc, subtype="html")
    return msg.as_bytes()
