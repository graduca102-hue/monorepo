//! Build per-user subscription documents.
//!
//! A subscription bundles every active node × every transport so the client app
//! fails over on its own. When a node is rotated, the URL stays the same and the
//! next poll returns the new set — clients recover without user action.

use base64::Engine;
use serde_json::{json, Value};
use vpn_core::model::{Node, NodeStatus, Transport, User};
use vpn_xray_config::{client_outbound, ClientEndpoint};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Format {
    /// sing-box full config JSON.
    SingBox,
    /// newline-delimited `vless://` / `hysteria2://` URIs, base64-wrapped.
    V2rayBase64,
}

pub fn render(user: &User, nodes: &[Node], format: Format) -> String {
    let uuid = user.vless_uuid.to_string();
    let usable: Vec<&Node> = nodes
        .iter()
        .filter(|n| matches!(n.status, NodeStatus::Active))
        .collect();
    let eps = endpoints(&uuid, user, &usable);

    match format {
        Format::SingBox => singbox(&eps),
        Format::V2rayBase64 => v2ray_b64(&eps),
    }
}

fn endpoints<'a>(uuid: &'a str, user: &'a User, nodes: &'a [&'a Node]) -> Vec<ClientEndpoint<'a>> {
    let mut out = Vec::new();
    for node in nodes {
        for transport in Transport::ALL {
            if transport == Transport::VlessXhttp && node.cdn_host.is_none() {
                continue;
            }
            out.push(ClientEndpoint {
                node,
                transport,
                vless_uuid: uuid,
                reality_short_id: &user.reality_short_id,
                proto_password: &user.proto_password,
            });
        }
    }
    out
}

fn singbox(eps: &[ClientEndpoint]) -> String {
    let proxy_outbounds: Vec<Value> = eps.iter().map(client_outbound).collect();
    let tags: Vec<String> = proxy_outbounds
        .iter()
        .filter_map(|o| o.get("tag").and_then(Value::as_str).map(str::to_string))
        .collect();

    let mut selector_targets = vec!["auto".to_string()];
    selector_targets.extend(tags.clone());

    let mut outbounds = vec![
        json!({
            "type": "urltest",
            "tag": "auto",
            "outbounds": tags,
            "url": "https://www.gstatic.com/generate_204",
            "interval": "3m",
            "tolerance": 100
        }),
        json!({ "type": "selector", "tag": "proxy", "outbounds": selector_targets }),
        json!({ "type": "direct", "tag": "direct" }),
    ];
    outbounds.extend(proxy_outbounds);

    let doc = json!({
        "log": { "level": "warn" },
        "outbounds": outbounds,
        "route": {
            "final": "proxy",
            "rules": [
                { "protocol": "dns", "action": "hijack-dns" },
                { "ip_is_private": true, "outbound": "direct" }
            ]
        }
    });
    serde_json::to_string_pretty(&doc).unwrap()
}

fn v2ray_b64(eps: &[ClientEndpoint]) -> String {
    let lines: Vec<String> = eps.iter().filter_map(share_uri).collect();
    base64::engine::general_purpose::STANDARD.encode(lines.join("\n"))
}

fn share_uri(ep: &ClientEndpoint) -> Option<String> {
    let host = ep.dial_host();
    let sni = ep.sni();
    let label = urlencode(&format!("{}-{:?}", ep.node.label, ep.transport));
    match ep.transport {
        Transport::VlessRealityTcp => Some(format!(
            "vless://{uuid}@{host}:443?encryption=none&flow=xtls-rprx-vision&security=reality&sni={sni}&fp=chrome&pbk={pbk}&sid={sid}&type=tcp#{label}",
            uuid = ep.vless_uuid,
            pbk = ep.node.reality_public_key,
            sid = ep.reality_short_id,
        )),
        Transport::VlessXhttp => Some(format!(
            "vless://{uuid}@{host}:8443?encryption=none&security=reality&sni={sni}&fp=chrome&pbk={pbk}&sid={sid}&type=xhttp&path=%2F#{label}",
            uuid = ep.vless_uuid,
            pbk = ep.node.reality_public_key,
            sid = ep.reality_short_id,
        )),
        Transport::Hysteria2 => Some(format!(
            "hysteria2://{pw}@{host}:443?sni={sni}#{label}",
            pw = ep.proto_password,
        )),
    }
}

fn urlencode(s: &str) -> String {
    s.chars()
        .map(|c| match c {
            'a'..='z' | 'A'..='Z' | '0'..='9' | '-' | '_' | '.' | '~' => c.to_string(),
            _ => format!("%{:02X}", c as u32),
        })
        .collect()
}
