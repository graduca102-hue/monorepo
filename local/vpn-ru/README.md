# vpn-ru

Control plane for a commercial anti-censorship VPN aimed at users in Russia.

Rust handles **everything except the data plane**. The actual tunnels are served by
Xray-core / sing-box / hysteria on the nodes; this workspace generates their configs,
provisions and rotates nodes, watches them from inside RF, builds per-user subscription
links and runs the Telegram billing bot.

## Design goals

- **Survivability over cleverness.** Blocks are assumed. What matters is: per-user keys,
  a warm pool of spare IPs, sub-minute detection from inside RF, automated node
  replacement, and pushing fresh subscription links to clients.
- **No single burnable secret.** Every user gets their own UUID / short-id / password.
  A leaked credential is revoked per-user, never per-node.
- **Multi-transport by default.** Each subscription bundles VLESS-Reality (TCP),
  VLESS-XHTTP (CDN) and Hysteria2 so the client app fails over on its own.

## Workspace layout

| Crate | Kind | Responsibility |
|-------|------|----------------|
| `core` | lib | Domain models, config loading, error types, shared DB pool |
| `xray-config` | lib | Render Xray/sing-box/hysteria server + client config fragments |
| `subscription` | lib | Build subscription documents (sing-box JSON, v2ray base64, clash) |
| `api` | bin | Internal REST API + Postgres; source of truth for users/nodes/plans |
| `orchestrator` | bin | Node lifecycle: provision, health rollup, IP rotation, config push |
| `node-agent` | bin | Runs on every VPS: applies pushed config, reports local health |
| `probe` | bin | Runs inside RF: measures reachability per node/protocol, reports back |
| `billing-bot` | bin | Telegram bot: signup, payments (Stars / USDT), plan lifecycle, links |

## Data plane (not in this repo)

- Xray-core with VLESS + XTLS-Vision + REALITY, transport TCP and XHTTP
- Hysteria2 (masquerade as HTTP/3)
- Optional AmneziaWG for a low-risk "daytime" profile
- Panel: Remnawave or Marzban (the `api` crate talks to it over HTTP; see `deploy/`)

## Status

Skeleton only. Nothing here provisions real infrastructure yet. See `deploy/README.md`
before pointing this at servers, and get owner approval per the remote-operator rules.
