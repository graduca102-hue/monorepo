use serde_json::{json, Value};
use vpn_core::model::{Node, Transport};

use crate::port_for;

/// Everything the client side needs to build one outbound / share link.
pub struct ClientEndpoint<'a> {
    pub node: &'a Node,
    pub transport: Transport,
    pub vless_uuid: &'a str,
    pub reality_short_id: &'a str,
    pub proto_password: &'a str,
}

impl ClientEndpoint<'_> {
    /// Host clients dial: the CDN hostname for XHTTP, the raw IP otherwise.
    pub fn dial_host(&self) -> &str {
        match self.transport {
            Transport::VlessXhttp => self.node.cdn_host.as_deref().unwrap_or(&self.node.ipv4),
            _ => &self.node.ipv4,
        }
    }

    pub fn sni(&self) -> &str {
        self.node
            .reality_server_names
            .first()
            .map(String::as_str)
            .unwrap_or(&self.node.reality_dest)
    }
}

/// A sing-box style outbound object for this endpoint.
pub fn client_outbound(ep: &ClientEndpoint) -> Value {
    let tag = format!("{}-{}", ep.node.label, transport_tag(ep.transport));
    match ep.transport {
        Transport::VlessRealityTcp => json!({
            "type": "vless",
            "tag": tag,
            "server": ep.dial_host(),
            "server_port": port_for(ep.transport),
            "uuid": ep.vless_uuid,
            "flow": "xtls-rprx-vision",
            "tls": {
                "enabled": true,
                "server_name": ep.sni(),
                "utls": { "enabled": true, "fingerprint": "chrome" },
                "reality": {
                    "enabled": true,
                    "public_key": ep.node.reality_public_key,
                    "short_id": ep.reality_short_id
                }
            }
        }),
        Transport::VlessXhttp => json!({
            "type": "vless",
            "tag": tag,
            "server": ep.dial_host(),
            "server_port": port_for(ep.transport),
            "uuid": ep.vless_uuid,
            "transport": { "type": "xhttp", "path": "/" },
            "tls": {
                "enabled": true,
                "server_name": ep.sni(),
                "utls": { "enabled": true, "fingerprint": "chrome" },
                "reality": {
                    "enabled": true,
                    "public_key": ep.node.reality_public_key,
                    "short_id": ep.reality_short_id
                }
            }
        }),
        Transport::Hysteria2 => json!({
            "type": "hysteria2",
            "tag": tag,
            "server": ep.dial_host(),
            "server_port": port_for(ep.transport),
            "password": ep.proto_password,
            "tls": { "enabled": true, "server_name": ep.sni() }
        }),
    }
}

fn transport_tag(t: Transport) -> &'static str {
    match t {
        Transport::VlessRealityTcp => "reality",
        Transport::VlessXhttp => "xhttp",
        Transport::Hysteria2 => "hy2",
    }
}
