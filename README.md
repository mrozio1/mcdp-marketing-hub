# MCDP Marketing Hub

Self-hosted, free replacement for Mixpost, built for the MCDP marketing team.

| Layer | Tool | Cost |
|---|---|---|
| Social scheduling, calendar, analytics, team approvals | [Postiz](https://github.com/gitroomhq/postiz-app) (AGPL-3.0, no gated features) | $0 |
| Public HTTPS URL, no open server ports | Cloudflare Tunnel (MCDP Cloudflare account) | $0 |
| SEO research, rank tracking, audits | [OpenSEO](https://github.com/every-app/open-seo) on Cloudflare free plan | $0 + optional DataForSEO usage |
| Paid-media audits and plans | [claude-ads](https://github.com/AgriciDaniel/claude-ads) in Claude Code | $0 |
| Control from Claude | Postiz MCP + OpenSEO MCP | $0 |

Internal use only, so AGPL obligations (publishing modifications) do not apply
unless the service is offered to people outside MCDP.

## Server requirements

Any Linux machine with Docker and **~4 GB RAM** (Postiz runs Temporal and
Elasticsearch). Shared web hosting (cPanel/StartCP-style) cannot run it.

## Install

```bash
git clone https://github.com/mrozio1/mcdp-marketing-hub.git
cd mcdp-marketing-hub
PUBLIC_URL=https://social.<mcdp-domain> CLOUDFLARE_TUNNEL_TOKEN=<token> ./install.sh
```

`install.sh` generates all secrets, writes `.env` (mode 600), pulls images and
starts everything. Re-running it is safe.

In Cloudflare Zero Trust → Tunnels, point the tunnel's public hostname
`social.<mcdp-domain>` at `http://postiz:5000`.

After creating the first admin account: `./lock-signups.sh`.

## Connecting networks

Each network needs its own developer app. The callback URL pattern is
`https://social.<mcdp-domain>/integrations/social/<network>`. Put the keys in
`.env`, then `docker compose --profile tunnel up -d postiz`.

Fast approvals: Bluesky (no app needed), Mastodon, LinkedIn, Facebook/Instagram
(development mode). Slow: Meta app review, YouTube, TikTok — start these early.

## Built-in AI (any provider, not just OpenAI)

Postiz's AI features (post-editor assistant, AI images, Agent) are hard-wired to
OpenAI. A small LiteLLM container (`postiz-ai`) sits in between: Postiz talks
"OpenAI" to it, and it forwards to whichever provider you choose. No Postiz code
is modified.

```sh
sh enable-ai.sh YOUR_GOOGLE_AI_STUDIO_KEY                    # Gemini (free tier)
sh enable-ai.sh YOUR_ANTHROPIC_KEY anthropic/claude-sonnet-4-5
sh enable-ai.sh YOUR_GROQ_KEY groq/llama-3.3-70b-versatile
sh enable-ai.sh off
```

The script tests the connection and reports a bad key or model name; if a
Gemini model has been retired it picks the current one automatically.
Model mapping lives in `litellm.yaml`. Check anytime with `sh test-ai.sh`.

Busy periods: if the main model is overloaded (Gemini's "high demand" 503s), the
translator retries twice, then answers from a backup model (`AI_TEXT_FALLBACK_MODEL`;
for Gemini, the scripts pick the newest "flash-lite" model, which runs on separate
capacity). The Ads page also retries briefly before showing a plain "busy" message.

### Free AI images via Cloudflare Workers AI

Gemini's free tier allows no image generation. `cf-image.py` is a tiny adapter
that serves Postiz's image requests (including the Polotno design editor's AI
image tab) from Cloudflare Workers AI's free daily allowance (Flux schnell,
roughly 150+ images/day), while text stays on Gemini.

```sh
sh enable-cf-images.sh CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN   # token: "Workers AI" template
sh enable-cf-images.sh off
```

## Ads page (Google Ads + Meta Ads, per practice)

A separate page (container `postiz-ads`) for paid ads. Add each practice with its
monthly Google and Meta budgets, upload the CSV exports, and it shows:

- **What needs attention**: campaigns spending without conversions, campaigns far
  above the practice's own cost per conversion, low click-through, and missing
  conversion tracking (checked first).
- **Search terms** costing money with no conversions, and the ones that convert.
- **Budget pacing** against each practice's monthly budget.
- **AI write-ups** through the same translator as Postiz: what to fix (with
  negative-keyword candidates taken only from the uploaded search terms), ad
  ideas, and a printable monthly report per practice.

All numbers are calculated exactly; the AI only explains them, following rules
condensed from [claude-ads](https://github.com/AgriciDaniel/claude-ads) (see
`ads/NOTICE.md`).

### Organic posts (Facebook + Instagram) and the combined view

Link each practice's **Facebook Page** and **Instagram account** under *Linked accounts*
(the practice's own Page is pre-selected when the name matches). Each night the page pulls
posts published since the start of last month with their views, reach, likes, comments,
shares, saves and clicks. On a practice page, switch between **All** (paid + organic,
with combined "times seen" and "clicks and interactions"), **Paid ads** and **Organic
posts** (per-network totals, engagement rate, top posts). The master record rolls up every
linked Page and Instagram account. Organic numbers also feed the AI write-ups and the
Outlook email. Meta renamed post metrics in Nov 2025 ("impressions" → "views"); the sync
asks for current names first and skips any Meta rejects.

Needs on the Meta system user: the practices' Pages and Instagram accounts assigned, and a
token with `pages_show_list`, `pages_read_engagement`, `read_insights`, `instagram_basic`,
`instagram_manage_insights` (plus `ads_read`/`ads_management`).

**Post performance report** (practice page → Organic posts → *Post performance report*):
every organic post for the practice ranked by engagement (likes + comments + shares + saves),
views, engagement rate or date, with title (first line of the post), link, views, likes,
comments, shares, saves, engagement rate, "vs. average post", top/bottom 25% tags and
computed takeaways. Periods: this month, last month, last 90 days. Print/PDF, spreadsheet
(CSV) download, or **Email to leadership** (Outlook-safe). Post history is kept for 90 days.

### Emailing results (Outlook)

**Email this page** on a practice page (or **Email this report** on the report) opens a
preview of an Outlook-safe email (tables and inline styles only, so Outlook for Windows
draws it correctly). Tick which sections to include, then either **Copy for Outlook**
and paste into a new message, or **Download Outlook draft (.eml)**, which Outlook for
Windows opens as an unsent draft ready to address. Write-ups can be removed with the ×.

### Making the fixes (approve each change)

On a practice page, **Prepare fixes** turns the findings into exact changes, checked
against the live Google Ads and Meta accounts:

- **Block wasted searches** — exact-match negative keywords for search terms that cost
  money and never converted anywhere (Search campaigns only; up to 20 at a time).
- **Pause campaigns** spending with no conversions.
- **Bring budgets in line** — every active campaign's daily budget moves by the same
  percentage so the month lands on the practice's budget; at most halved or +50% per step.
  Shared Google budgets and Meta lifetime budgets are left alone.
- **New ads** — the AI writes a Google responsive search ad or a Meta ad (copying an
  existing ad's image, landing page and button); always created **paused**.

Nothing changes until someone ticks changes and clicks **Apply**. Before applying (and
undoing), the current value is read again; if it was changed in Google or Meta meanwhile,
nothing is changed and the item is marked out of date. Every applied change has **Undo**,
and the practice page keeps the history.

Access needed for fixes: the Google robot account at **Standard** access (not Read only),
and a Meta system user token with `ads_management` (ad accounts assigned with *Manage
campaigns*; Pages with *Create ads* for new ads). Google's Explorer access tier allows
these changes (up to 2,880 operations a day).

```sh
sh enable-ads.sh                  # first time: prints the team password
sh enable-ads.sh NEW_PASSWORD     # change it
sh enable-ads.sh off
```

Public address: add a public hostname to the same Cloudflare tunnel, e.g.
`ads.<mcdp-domain>` → `http://ads:8000`.

### Automatic nightly pulls (phase 2)

Read-only connections; nothing is changed in either platform. Each night
(10:00 UTC ≈ 5 a.m. Central, or **Sync now**) the page pulls month-to-date daily
campaign data, plus Google search terms, for every practice with linked accounts.
In the first three days of a month it pulls the whole previous month instead.
Synced data replaces the previous sync (newest 3 kept) and goes through the same
analysis as uploads.

Set up on the Ads page under **Connections** (no terminal needed):

- **Meta:** paste a Business Settings *system user* token (`ads_read`, assets =
  the practices' ad accounts, no expiry) for the MCDP Social app. The system user
  must first be assigned to the app (Accounts → Apps → MCDP Social → Assign people),
  or Generate token shows "No permissions available".
- **Google:** Google retired developer tokens on 2026-09-09; access now comes from a
  Google Cloud project. Create a project, enable the Google Ads API, apply for
  Explorer access, create a *service account* with a JSON key, and add its email as a
  **Read only** user in the Google Ads manager account (or each practice account).
  Upload the key on the Connections page with the manager account ID.

Then link each practice's accounts under **Linked ad accounts**.

**Practices sharing one ad account.** If several practices run in one Google or Meta
account, link the same account to each and give each practice a *Campaigns to include*
filter (words in the campaign names, comma-separated; `-word` excludes). A practice with no
filter is the **master record**: it shows every campaign, a spend breakdown by practice,
campaigns no practice claims yet, and one-click **Create practice** suggestions grouped from
campaign names. Campaigns whose names don't fit the pattern (e.g. a dated promotion) can be
added to a practice by exact name: tick them on the master record and pick the practice, or
use **Campaigns added by name** on the practice page (× to remove). A campaign added by name
counts only for that practice. Filters apply when the page reads the data, so changing one takes effect
immediately; each shared account is pulled once per sync. Keys entered on the
page are stored in the Ads database (included in backups); `connect-ads.sh` can
store them in `.env` instead, and also supports personal Google sign-in
(`google-oauth CLIENT_ID CLIENT_SECRET [MANAGER_ID]`).
Meta "results" count `lead`,
`schedule_total`, `contact_total` and `submit_application_total` actions
(override with `META_RESULT_ACTIONS`). API versions: Meta v25.0, Google Ads v25
(override with `META_API_VERSION` / `GOOGLE_ADS_API_VERSION`).

Tests: `cd ads && ADS_DISABLE_SCHEDULER=1 python -m pytest tests` (includes a mock of the Meta and Google APIs).

## Maintenance

- Backups: automatic once a day (database, media and the Ads page data) via the `postiz-backup`
  container into `./backups`, newest 14 kept. No cron needed (NAS crontabs often
  aren't available to non-root users). Run one now with `sh backup.sh`;
  check with `docker logs postiz-backup`.
- Update: `docker compose --profile tunnel pull && docker compose --profile tunnel up -d`.
- Logs: `docker compose logs postiz --tail 100`.
