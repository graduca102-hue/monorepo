//! Render Xray-core / hysteria server config fragments and matching client outbounds.
//!
//! We keep our own thin representation instead of pulling an Xray schema crate: the
//! surface we use is small and stable, and we want the JSON to stay diffable.

mod server;
mod client;

pub use client::{client_outbound, ClientEndpoint};
pub use server::{node_inbounds, InboundUser};

use vpn_core::model::Transport;

/// The listen port we standardise on per transport across every node.
pub fn port_for(transport: Transport) -> u16 {
    match transport {
        Transport::VlessRealityTcp => 443,
        Transport::VlessXhttp => 8443,
        Transport::Hysteria2 => 443, // UDP
    }
}
