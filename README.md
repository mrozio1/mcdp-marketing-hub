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

## Welcome page and layout

`ads.mcdp.com` opens on a welcome page: a greeting that follows the viewer's time of day
(“Good morning / afternoon / evening”, from their computer's clock; “Welcome.” if scripts are
off), four ways in (Practices & ads, Social posts, Market map, Export map lists), this month's ad
spend, leads and practices mapped, and up to three things worth a look (fixes waiting, a map or
list that needs attention, the last ad sync). The practices list lives at `/practices`.

The whole app uses the same quiet look (ivory background, serif headings, hairlines). Anything
that isn't a daily task — uploads, linked accounts, budgets, keys and tokens, the office list,
map setup — sits in a closed drop-down on its page. Drop-downs open by themselves when something
there still needs setting up, and any link to one (e.g. `#settings`) opens it.

### Social (social.mcdp.com) in the same look

The `social-skin` container is a small nginx that sits in front of the social app. It passes
everything through unchanged, but adds `social-skin/www/skin.css` and `skin.js` to each page:
the ivory/teal colours and serif headings, light mode only, an “M” mark in place of the vendor
logo, and the vendor's name replaced with “Social” in what you read on screen (never inside
posts you are writing, code or form fields). A small footer pill links back to the hub and to the
open-source licence and source code, which the AGPL licence asks us to keep offered.

To switch it on, point the tunnel's public hostname `social.mcdp.com` at
`http://social-skin:8080` instead of `http://postiz:5000` (Cloudflare Zero Trust → Networks →
Tunnels → your tunnel → Public Hostname). To switch it off, point it back. Nothing inside the
social app is changed, so updates to it keep working; if a new screen shows stray purple, add a
rule to `skin.css` and bump `?v=` in `social-skin/default.conf`.

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

### Boosted posts and reels

A boost (from a Page, Instagram or the Meta Business Suite "Ads" tab) is a Meta campaign with an
automatic name like *Post: "…"*, so name filters miss it. Each sync asks Meta which Facebook Page or
Instagram account every ad promotes, in every ad account the hub can see, and a boost goes to the
practice that Page or account is linked to (Practice setup → Facebook Pages / Instagram), even when it
was charged to a different ad account. Other campaigns that promote a practice's Page fill in only when
no practice's filter words match them. Boosts carry a **Boosted** tag in the campaign list. If one
lands on the wrong practice, use **Move to…** on that row; it then belongs only to that practice
(**Back to automatic** undoes it).

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

### People and access (Team page)

Everyone signs in with their own email and password, and has one of three levels:

| Access | Can |
|---|---|
| **Viewer** | See every page, open reports and the email version, use the map, export map lists to Excel. Changes nothing. |
| **Editor** | Also upload exports, prepare / apply / undo fixes, write AI write-ups, sync, refresh the map, add and rename offices, build lists and star / note / leave out practices. |
| **Admin** | Also keys and connections (Connections page), map setup, deleting practices, and the **Team** page. |

Two on/off switches per person on the Team page work with any access level:

- **Social** shows the Social link and welcome tile (on by default). Social itself
  (social.mcdp.com) has its own sign-ins, so this hides the way in rather than locking it;
  remove someone's Social account inside Social to stop them using it.
- **Field visits** lets them log office visits, receipts and routes.

The checks are made on the server for every click, so hiding a button is only a convenience;
a role change or switching someone off applies on their next click. The fixes history shows
who applied or undid each change.

Adding someone: **Team → Add a person** makes a one-time setup link (valid 7 days). Send it to
them however you like; they pick their own password, so nobody else knows it. **Reset password**
makes a new link and clears the old password. There's no email service, so links are copied by hand.

First time: sign in with the shared team password (leave the email blank), add yourself as an
**Admin** on the Team page and open your link. From then on the shared password stops working.
If every admin is ever locked out, run on the server:

```sh
docker exec postiz-ads python -m app.users reset you@example.com
```

which prints a fresh setup link.

Public address: add a public hostname to the same Cloudflare tunnel, e.g.
`ads.<mcdp-domain>` → `http://ads:8000`.

### Field visits (agents in the field)

Switch **Field visits** on for field marketers on the Team page (it works with any access level;
a viewer with it on can log visits but still can't change anything else). Admins set the
field budgets and office-to-practice links; admins and editors can remove a visit.

- **Log a visit** from a dental pin on the map, from a practice on the Export Map List, or for a
  new office on the spot. They record who they met (one or more people, each with name, email and
role; *+ Add another person* adds a row), the relationship (Cold, Warm,
  Hot or Referring), what they dropped off and its cost, a follow-up date and notes.
- **Receipt photo**: the photo is shrunk on the phone and saved with the visit, and the hub's
  free AI reads the total into the cost box for the agent to check.
- **On site**: when a visit is saved, the phone's location is recorded once (if allowed) and the
  visit is marked *on site* when it's within 0.2 mi of the office. Nothing is tracked in between.
- **Field page**: each person sees only their own visits, spend, follow-ups and export;
  admins see everyone's and can filter by agent. Visits by day, week or month, filtered by practice, with a map of the
  day's stops in order, follow-ups due, an Excel export, and spend against monthly budgets per
  practice and per agent (same pacing bar as the ads). A visit counts against the practice of
  our nearest office; matches are suggested from the names and can be changed under
  *Offices and practices*.
- **Map**: visited pins get a ring in the relationship's colour; follow-ups due pulse. Filters
  show only visited offices or only follow-ups due.
- **Plan a route**: add pins (or the follow-ups due, or list rows) to the route, choose the
  start (where you are, your home address from *Your account*, or one of our offices), and the
  hub puts the stops in a short driving order and opens them in the Google Maps app (10 stops per
  link; longer routes are split).

- **Salesforce** (Connections → Salesforce): each visit is sent when it's saved, as a completed
  Task on the office's Account (owner: the agent's Salesforce user with the same email, else the
  connected user), with the first person met as its contact; everyone met becomes a Contact
  (matched by email first). Offices are matched to Accounts by name (and ZIP); new ones are
  created with Type *Referral office*. A follow-up date becomes an open Task. Existing Accounts
  and Contacts are never edited. Failures show on the visit and are retried every 15 minutes.
  Setup: an External Client App in Salesforce with callback `https://<hub>/salesforce/callback`
  and the `id`, `api` and `refresh_token` scopes; paste its consumer key and secret, then sign in.

Google's terms: our records hold the office name from the NPI Registry or as the agent types it;
Google's own name and address are only shown live. Receipts are stored next to the database in
`/data/receipts`.

### Market map (our offices, referrers, competitors)

The **Map** page shows our offices, the closest general dental practices to each office
(default 100 per office, within a maximum radius of 15 miles; potential referrers) and every
orthodontist within the radius that isn't ours (competitors),
on a Google map with layer switches and a per-office filter. Dentists that also come up for
"orthodontist" searches are ringed (they likely offer braces or aligners). Each refresh flags
practices that are **new** since the last one and ones **no longer listed** on Google. Click a
pin for its name, address, phone, website and rating, loaded live from Google.

**Our offices** can be uploaded in one go: a CSV or Excel list (name + address, or
latitude/longitude) or the KML/KMZ export of a Google My Maps map. The page shows what it read,
grouped by map layer (layers that look like ours are pre-ticked), and matches each ticked office
on Google, flagging any match that's far from the pin in the file. Single offices can also be
added with a Google search box. Orthodontists whose names contain one of our brand names show on
our layer instead of as competitors.

It uses the official Google Places API (New) and Maps JavaScript API, never scraping, and
follows Google's storage rules: only place IDs (kept) and coordinates (deleted after 30 days)
come from Google; our labels, dates and office list are ours. To keep only the closest N dental
practices, the search starts 2 miles around each office and widens until it has N (set N to 0 for
every practice in the radius); "no longer listed" is only decided where the map actually looked.
Each refresh, cheapest first:
an ID-only search sweep (free, unlimited), one lookup for each place never seen before, and a
location renewal for known places (skipped if renewed in the last 20 days). Places that aren't
dental practices are remembered and never looked up again. The map refreshes itself about once
a month; a lookup limit per refresh (default 3,000) keeps it inside Google's monthly free
allowance (5,000 new-place lookups, 10,000 location renewals). Names and addresses from Google
are never stored or exported.

**Export Map List.** Google's data can't be exported, so the exportable lists come from the
**NPI Registry** (CMS's public registry of licensed providers), placed with the Census Bureau
geocoder (ZIP centroid when an address can't be placed). The **Export Map List** button on the
Map page opens its own page: pick one of our practices (or All offices) and **Referral** (general
dental practices: potential referrers) or **Competition** (orthodontic-only practices that aren't
ours), all within the map radius, nearest first. Dentists registered at the same address are
grouped into one practice. Untick practices to leave them out, star them, add notes, then
**Export to Excel** (starred only optional). Rows that line up with a pin on our map get a Google
Maps link to that listing. The lists update themselves monthly.

Setup (Google Cloud project `mcdp-ads`): link a billing account, enable **Places API (New)**
and **Maps JavaScript API**, create a **browser key** (restricted to `https://ads.mcdp.com/*`,
APIs: Maps JavaScript API + Places API (New)) and a **server key** (Places API (New) only),
paste both under **Map → Map setup**, add our brand names, upload or add our offices, then
**Build the map**. Optional: a Map ID (Map management) for production styling; a billing budget
alert is recommended.

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
