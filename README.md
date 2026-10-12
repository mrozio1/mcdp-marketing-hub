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

## Maintenance

- Backups: automatic once a day (database + media) via the `postiz-backup`
  container into `./backups`, newest 14 kept. No cron needed (NAS crontabs often
  aren't available to non-root users). Run one now with `sh backup.sh`;
  check with `docker logs postiz-backup`.
- Update: `docker compose --profile tunnel pull && docker compose --profile tunnel up -d`.
- Logs: `docker compose logs postiz --tail 100`.
