"""Read-only look at the Facebook Pages and Instagram accounts connected in Postiz.

Postiz connects with a person's own Meta login, so it can reach every Page that person
manages, across business portfolios. Its stored Page tokens let the Ads page read organic
post stats for those Pages without assigning each one to the Meta system user.
Only reads the Integration table; never writes to Postiz.
"""
from __future__ import annotations

import os

EMPTY = {"facebook": {}, "instagram": {}}


def channels():
    """{'facebook': {page_id: {'name', 'token'}}, 'instagram': {ig_id: {'name', 'token'}}}"""
    url = os.environ.get("POSTIZ_DATABASE_URL", "")
    if not url:
        return {"facebook": {}, "instagram": {}}
    try:
        import psycopg
    except ImportError:
        return {"facebook": {}, "instagram": {}}
    try:
        with psycopg.connect(url, connect_timeout=5) as c:
            rows = c.execute(
                'SELECT "providerIdentifier", "internalId", COALESCE("customName", "name"), "token" '
                'FROM "Integration" WHERE "deletedAt" IS NULL AND "disabled" = false '
                "AND \"providerIdentifier\" IN ('facebook', 'instagram')").fetchall()
    except Exception:
        return {"facebook": {}, "instagram": {}}
    out = {"facebook": {}, "instagram": {}}
    for provider, internal_id, name, token in rows:
        # Instagram (via Facebook login) stores "pageToken___userToken"; the Page token is what reads insights.
        tok = (token or "").split("___")[0]
        if internal_id and tok:
            out[provider][str(internal_id)] = {"name": name or str(internal_id), "token": tok}
    return out
