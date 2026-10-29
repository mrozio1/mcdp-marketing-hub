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

### Adding a practice that shares the master's ad accounts

Give it its **campaign filter words** and leave its ad accounts unlinked: a practice with filter words
and no ad account of its own automatically uses the master record's Meta and Google accounts (shown on
Connections as "master's, by filter words"). Link its own account only if it really has one. The
practices list explains anything that keeps a practice from showing numbers: nothing linked, filter
words that match none of the account's campaigns, or the last sync's error for that account. A practice
only counts as the master when it has an ad account linked and no filter words.

If a practice is linked to an ad account Google or Meta reports as **inactive** (for example an
"[MCDP] …" Google Ads account that was never set up with billing), the hub uses the master's account for
that practice instead, for syncing, fixes and new campaigns, until the account is activated; then its own
account is used again automatically. Connections and the practice's setup say when this is happening, and
the account lists show active accounts first.

### Reporting false Google reviews

Google's API can't report reviews, so on any Google review in the inbox **Report to Google** has you pick
Google's reason (Spam, Conflict of interest, Off topic, ...), opens Google's own Reviews Management Tool
in a new tab with step-by-step pointers (location, reviewer, date), and marks the review **Reported**. It
leaves Needs reply and waits in the **Reported** tab. While a report is open the hub reads every page of
that location's reviews on each check; when Google stops returning the review (in a complete pull) it's
marked **Removed by Google** and stops counting on the scoreboard. A report still up after 7 days is
flagged with a link to check its status or appeal. **Undo report** puts it back if it was never sent.
The scoreboard shows reported and removed counts per practice.

### Master Reports (monthly and weekly)

**Master Reports** at the bottom of the welcome page opens a report across every practice: **Monthly**
(defaults to the last full month; any of the last 12, including this month to date) or a **Weekly**
snapshot (last full Monday–Sunday week). The cover has the period's headline numbers with the change
against the period before, a daily ad spend chart, a short summary and up to five suggestions for the
next period; then a master overview of paid ads, organic posts, Google reviews and field visits with
practices ranked and the highs and lows called out; then half a page per practice with its locations,
its numbers (previous period in brackets), daily spend, top campaigns and notes. **Save as PDF** prints
it on Letter pages: cover, overview, two practices per page.

Ad numbers come from a daily archive the sync fills in (synced exports only keep the latest month), so
reports work for any past period from the first sync on; the first sync after installing also pulls
the previous month once so comparisons work straight away. Shared accounts are counted once in the
totals, and the master record isn't listed as a practice (its spend outside any practice shows as
"not assigned to a practice"). Words in the summary and suggestions are built from the numbers, not guessed.

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
  existing ad's image, landing page and button). **Apply selected** creates them paused;
  **Apply and turn on new ads** creates and switches them on. Created ads and campaigns show
  **On / Paused** in the history with a **Turn on / Pause** button, so nobody needs to open
  Google Ads or Ads Manager to start or stop them (Google and Meta still review new ads first). Pick the
  campaign, an optional focus ("Invisalign for adults") and 1–3 versions. Each draft can be
  edited before applying (live character counts), and edits are checked again.

### New Google campaigns

**New Google campaign** on a practice page drafts a complete Search campaign from the brand
kit: say what it promotes, the daily budget, an optional max cost per click and a radius;
office addresses come from the map offices linked to the practice (or type them). The AI
writes the keywords (phrase match), an ad group and a responsive search ad; everything is
checked against the kit and editable. **Create in Google Ads** sends it in one all-or-nothing
request, **paused**: Search network only, Maximize clicks, English, people in or regularly in
the radius, a few standard negative keywords (jobs, careers, school…). The name always
starts with the practice's campaign word, but any name works: a campaign created here is added
to the practice's campaigns automatically, so its spend counts in that practice's Google Ads
numbers and budget pacing (and a sync starts). Until it has impressions it's listed as
"Created here, no numbers yet". **Undo** removes the campaign. Needs the Google robot at Standard access.

### Tools (adapted from OpenAdKit, MIT)

Each practice page has **Score this ad** on every drafted ad and campaign (hook, specificity,
brand fit, call to action and healthcare care rules, a tier and the top 3 named fixes) with
**Apply these fixes** (swaps each fix into the draft, skipping any that break a rule). New ads
and campaigns are scored, fixed and re-scored automatically as soon as they're written; the
draft shows what changed and the before → after score. There is also a
**Tools** section, run in the background on the brand kit and live numbers:
- **Optimize:** fix low click rates (live Google ads with last-30-day numbers vs the practice's
  own average), Quality Score check, keyword ideas (each becomes an **Add keywords** proposal
  under Make the fixes: phrase match, Apply/Undo; health-term flags get an automatic review
  request), ad fatigue (week-by-week CTR and CPM from the synced daily rows), landing-page check.
- **Research:** competitor ad teardown with three better versions; links to the Meta Ad Library
  and Google Ads Transparency Center. The Export Map List competition rows link there too.
- **Social & leads:** content calendar, Reel ideas, Meta lead form, email subject lines.

### Ad images

Under **Brand kit → Ad images** each practice keeps a logo, a library of its own photos, and
AI images made with the free Cloudflare image service (`sh enable-cf-images.sh ACCOUNT_ID
TOKEN` must be on). AI prompts always exclude text, logos, before/after, mouths and
procedures. Each drafted **new Google campaign** can take up to 4 pictures, added after the
campaign is created as square (1200×1200) and wide (1200×628) image assets; if Google
refuses a picture, the campaign is still created and the reason is shown. Each **Meta new ad**
can use one picture instead of copying the existing ad's: it's uploaded as 1080×1080 with the
logo small in the bottom-right corner. Files live in the Ads data volume under `/data/brand`.

### Brand kits (per practice)

The practice page's **Brand kit** drop-down holds the rules new ads are written with:
voice, audience, services, proof points, approved offers, calls to action, phrases to
always include, words never to use, locations and doctors. Fill it by hand, upload the
practice's brand guide (PDF, Word, PowerPoint or text), or **Read the website** (home page
plus a few service / about pages), then **Fill the kit from these**: the AI suggests each
field and nothing is used until someone checks it and clicks Save. A kit can be copied
from another practice as a starting point. Several kits can be loaded at once from a .json
file under **Import brand kits** on the Practices page: each brand's kit goes to every
practice whose name includes the brand name (or one of its `match` words), again only as
suggestions to check and Save.

After writing, every line is checked: lines over the length limit, using a "never say"
word, with an exclamation mark in a Google headline, a phone number, a guarantee or
unprovable claim ("best", "painless"), a price that isn't an approved offer, capitals, or
wording that implies knowing the reader's health are left out (listed under "Lines left
out"). A missing "always include" phrase is flagged on the draft.

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

### Which practice a visit is for

Log a visit starts with a **Visiting for** dropdown. It is filled in from the last visit to that office, then from the practice of our nearest office, then from the agent's last pick, and it must be set before saving. The chosen practice decides the visit's budget, the Field page and report figures, and goes into the Salesforce Task subject ("Field visit for Heights Orthodontics: Maple Street Family Dental") and description. Visits logged before this existed keep using the nearest office's practice.

## Calendar (Outlook invites)

The **Calendar** tab shows events by month, week or list, with field follow-up dates next to them. **New event** builds the subject from three picks (Practice → Category → Type, e.g. "Heights Orthodontics · Referral Visit: Cookie drop-off — Maple Street Family Dental"), plus where, when, who's invited (required or optional), reminder, Teams link, category details (what you're bringing and estimated cost, shot list, agenda…), notes and an attachment.

Invites are sent through Microsoft 365 from a shared mailbox (set on Connections, e.g. `marketing-events@…`), so that mailbox is the organizer and gets the replies; the Calendar shows each person's reply, checked every 20 minutes. Editing sends an update and cancelling sends a cancellation. **Log this visit** on a referral or office visit opens the Field form already filled in (practice, office, people, what was dropped off, cost), and the visit then goes to Salesforce as usual. Editors, and anyone with field visits switched on, can schedule; viewers can look.

Setup (once, by an admin): register an app in Entra ID with the redirect `https://<hub>/microsoft/callback` and the delegated Graph permissions Calendars.ReadWrite, Calendars.ReadWrite.Shared, User.Read, User.ReadBasic.All, offline_access, openid, profile and email; create a client secret; make the person who signs in a member (Full Access and Send As) of the send-from shared mailbox. Then paste the tenant ID, client ID and secret on **Connections → Microsoft 365** and sign in. Until it's connected, events are saved in the hub without invites.

## Seasonal look

Admins pick it on **Team → Seasonal look**: Automatic (by date), one theme, or off. Ten themes: New Year, Valentine's Day, St. Patrick's Day, Spring, Summer, 4th of July, Back to School, Halloween, Thanksgiving and Christmas. Each has a little cursor buddy (ghost, turkey, snowman, heart, bunny, a tooth with braces…), something falling or floating (leaves, snow, confetti, hearts, petals, bubbles, fireworks, pencils) and, for some, an edge decoration (spiderwebs, string lights, bunting). Each piece can be switched off, the dates are editable (month-day; `thanksgiving` means the fourth Thursday of November), and overlapping seasons show the shorter one. **Preview** shows a look just for you. People can turn it off for themselves on their Account page. It never takes clicks, isn't printed, rests in a corner on phones and stays still for reduced-motion settings.

## Schools on the Map

The Map has **Private schools** and **Charter schools** toggles: every school within the Map radius of our offices, from the U.S. Department of Education's NCES data (Private School Universe Survey; Common Core of Data, charter flag), fetched by the hub itself and refreshed monthly. Closed and online-only schools are left out. Click a pin for the address, grades, students and distance; add it to a route; download the list as Excel from under the map. **Refresh schools** under the map fetches them straight away (first time, after adding offices).
