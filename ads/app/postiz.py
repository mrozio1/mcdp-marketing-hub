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
                "AND \"providerIdentifier\" IN ('facebook', 'instagram', 'instagram-standalone')").fetchall()
    except Exception:
        return {"facebook": {}, "instagram": {}}
    return from_rows(rows)


def integrations_by_id(ids):
    """Postiz channels by their Postiz id: [{'id', 'provider', 'internal_id', 'name', 'customer'}]."""
    url = os.environ.get("POSTIZ_DATABASE_URL", "")
    if not url or not ids:
        return []
    try:
        import psycopg
    except ImportError:
        return []
    queries = (
        'SELECT i."id", i."providerIdentifier", i."internalId", COALESCE(i."customName", i."name"), c."name" '
        'FROM "Integration" i LEFT JOIN "Customer" c ON c."id" = i."customerId" WHERE i."id" = ANY(%s)',
        'SELECT "id", "providerIdentifier", "internalId", COALESCE("customName", "name"), NULL '
        'FROM "Integration" WHERE "id" = ANY(%s)',
    )
    for q in queries:                          # older Postiz versions have no customers (groups)
        try:
            with psycopg.connect(url, connect_timeout=5) as c:
                rows = c.execute(q, (list(ids),)).fetchall()
            return [{"id": str(r[0]), "provider": r[1] or "", "internal_id": str(r[2] or ""),
                     "name": r[3] or "", "customer": r[4] or ""} for r in rows]
        except Exception:
            continue
    return []


def from_rows(rows):
    """Integration rows -> channels. Instagram comes in two kinds:
    'instagram' (connected through a Facebook Page; token "pageToken___userToken", the Page token reads it)
    and 'instagram-standalone' (connected with an Instagram login; an Instagram token for graph.instagram.com)."""
    out = {"facebook": {}, "instagram": {}}
    for provider, internal_id, name, token in rows:
        tok = (token or "").split("___")[0]
        kind = "instagram" if provider.startswith("instagram") else provider
        if internal_id and tok and kind in out:
            prev = out[kind].get(str(internal_id))
            if prev and provider == "instagram-standalone":
                continue                  # the same account connected both ways: the Facebook-Page one reads more
            out[kind][str(internal_id)] = {"name": name or str(internal_id), "token": tok}
    return out
