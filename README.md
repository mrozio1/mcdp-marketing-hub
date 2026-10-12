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

### Free AI images via Cloudflare Workers AI

Gemini's free tier allows no image generation. `cf-image.py` is a tiny adapter
that serves Postiz's image requests (including the Polotno design editor's AI
image tab) from Cloudflare Workers AI's free daily allowance (Flux schnell,
roughly 150+ images/day), while text stays on Gemini.

```sh
sh enable-cf-images.sh CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN   # token: "Workers AI" template
sh enable-cf-images.sh off
```

## Maintenance

- Backups: automatic once a day (database + media) via the `postiz-backup`
  container into `./backups`, newest 14 kept. No cron needed (NAS crontabs often
  aren't available to non-root users). Run one now with `sh backup.sh`;
  check with `docker logs postiz-backup`.
- Update: `docker compose --profile tunnel pull && docker compose --profile tunnel up -d`.
- Logs: `docker compose logs postiz --tail 100`.
