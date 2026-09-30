# deploy/

Infrastructure for the data plane. **Nothing here is wired up yet** — these are the
slots the `orchestrator` crate shells out to.

## Contents (to be added)

```
deploy/
  terraform/
    main.tf            # one module per provider (hetzner, vultr, aeza, ...)
    variables.tf
  ansible/
    node.yml           # installs xray-core + hysteria + vpn-node-agent, hardens ssh
    templates/
  spin_node.sh <provider> <region> <label>   # terraform apply for one node
  node_ip.sh   <label>                        # print the node's public IPv4
  kill_node.sh <label>                        # terraform destroy for one node
```

## Node bootstrap (ansible/node.yml should do)

1. Base hardening: no password ssh, ufw allow 443/tcp+udp, 8443/tcp, mgmt WG only.
2. Install `xray-core` (VLESS + XTLS-Vision + REALITY, TCP + XHTTP inbounds).
3. Install `hysteria` server (masquerade → `reality_dest`).
4. Generate REALITY keypair, register the node with `vpn-api` (`POST /nodes`).
5. Install `vpn-node-agent` as a systemd unit bound to `127.0.0.1` + mgmt WG addr.
6. Obtain a cert for the Hysteria masquerade host (or reuse REALITY-only where possible).

## Anti-block operational notes

- **Never** put more than ~2 active nodes in one provider / AS. RKN blocks subnets.
- Keep `reality_dest` pointed at a large foreign site that is reachable from RF and
  that RKN will not blackhole wholesale (Microsoft, Apple, Cloudflare consumer).
- Rotate `reality_dest` / `serverNames` when a node is retired, not just the IP.
- The CDN (`cdn_host`) path is a separate provider account (Cloudflare / Gcore) with
  its own origin cert; used for the XHTTP transport and domain-fronting.
- Management plane (orchestrator ↔ agents) rides its own WireGuard net, never the
  public internet in the clear.

## Provider shortlist (spread across all of them)

Low-latency to RF, historically less-aggressively-blocked ranges: Finland, Netherlands,
Germany, Sweden; plus Armenia / Kazakhstan as alternates. Prefer smaller ISPs and
"clean" ranges over the big three clouds.
