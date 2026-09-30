use serde_json::{json, Value};
use uuid::Uuid;
use vpn_core::model::{Node, Transport};

use crate::port_for;

/// One entry in an inbound's client list.
pub struct InboundUser {
    pub vless_uuid: Uuid,
    pub email: String,
    pub reality_short_id: String,
    pub proto_password: String,
}

/// Build the full set of Xray inbounds for a node, one per transport, each carrying
/// every active user. Hysteria2 is emitted as a separate config (different daemon).
pub fn node_inbounds(node: &Node, users: &[InboundUser]) -> Value {
    json!({
        "xray_inbounds": [
            vless_reality_tcp(node, users),
            vless_xhttp(node, users),
        ],
        "hysteria2": hysteria2(node, users),
    })
}

fn vless_reality_tcp(node: &Node, users: &[InboundUser]) -> Value {
    json!({
        "tag": "vless-reality-tcp",
        "listen": "0.0.0.0",
        "port": port_for(Transport::VlessRealityTcp),
        "protocol": "vless",
        "settings": {
            "clients": users.iter().map(|u| json!({
                "id": u.vless_uuid,
                "email": u.email,
                "flow": "xtls-rprx-vision"
            })).collect::<Vec<_>>(),
            "decryption": "none"
        },
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "show": false,
                "dest": node.reality_dest,
                "serverNames": node.reality_server_names,
                "privateKey": node.reality_private_key,
                "shortIds": short_ids(users),
            }
        },
        "sniffing": { "enabled": true, "destOverride": ["http", "tls", "quic"] }
    })
}

fn vless_xhttp(node: &Node, users: &[InboundUser]) -> Value {
    json!({
        "tag": "vless-xhttp",
        "listen": "0.0.0.0",
        "port": port_for(Transport::VlessXhttp),
        "protocol": "vless",
        "settings": {
            "clients": users.iter().map(|u| json!({
                "id": u.vless_uuid,
                "email": u.email
            })).collect::<Vec<_>>(),
            "decryption": "none"
        },
        "streamSettings": {
            "network": "xhttp",
            "security": "reality",
            "xhttpSettings": { "path": "/", "mode": "auto" },
            "realitySettings": {
                "show": false,
                "dest": node.reality_dest,
                "serverNames": node.reality_server_names,
                "privateKey": node.reality_private_key,
                "shortIds": short_ids(users),
            }
        }
    })
}

fn hysteria2(node: &Node, users: &[InboundUser]) -> Value {
    json!({
        "listen": ":443",
        "tls": { "cert": "/etc/hysteria/fullchain.pem", "key": "/etc/hysteria/privkey.pem" },
        "auth": {
            "type": "userpass",
            "userpass": users.iter()
                .map(|u| (u.email.clone(), u.proto_password.clone()))
                .collect::<std::collections::BTreeMap<_, _>>()
        },
        "masquerade": {
            "type": "proxy",
            "proxy": { "url": format!("https://{}", node.reality_dest), "rewriteHost": true }
        }
    })
}

fn short_ids(users: &[InboundUser]) -> Vec<String> {
    let mut ids: Vec<String> = users.iter().map(|u| u.reality_short_id.clone()).collect();
    ids.sort();
    ids.dedup();
    ids
}
