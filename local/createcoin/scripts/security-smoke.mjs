import { startLocalApiHarness } from './local-api-harness.mjs';

const ORIGIN = 'http://127.0.0.1:4310';
const HEADERS = {
  origin: ORIGIN,
  referer: `${ORIGIN}/`,
  'x-createcoin-proxy': '1',
};

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function readJson(res) {
  const text = await res.text();
  try {
    return text ? JSON.parse(text) : {};
  } catch {
    return { raw: text };
  }
}

async function main() {
  const harness = await startLocalApiHarness(4310);
  try {
    const noHeaderRes = await fetch(`${ORIGIN}/api/rpc/mainnet-beta`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'getHealth', params: [] }),
    });
    const noHeaderJson = await readJson(noHeaderRes);
    assert(noHeaderRes.status === 403, `expected headerless RPC to be 403, got ${noHeaderRes.status}`);
    assert(noHeaderJson.error === 'missing_proxy_header', `expected missing_proxy_header, got ${JSON.stringify(noHeaderJson)}`);

    const crossSiteRes = await fetch(`${ORIGIN}/api/rpc/mainnet-beta`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-createcoin-proxy': '1',
        origin: 'https://evil.example',
        referer: 'https://evil.example/',
      },
      body: JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'getHealth', params: [] }),
    });
    const crossSiteJson = await readJson(crossSiteRes);
    assert(crossSiteRes.status === 403, `expected cross-site RPC to be 403, got ${crossSiteRes.status}`);
    assert(crossSiteJson.error === 'cross_site_request_blocked', `expected cross_site_request_blocked, got ${JSON.stringify(crossSiteJson)}`);

    const healthRes = await fetch(`${ORIGIN}/api/rpc/mainnet-beta`, {
      method: 'POST',
      headers: {
        ...HEADERS,
        'content-type': 'application/json',
      },
      body: JSON.stringify({ jsonrpc: '2.0', id: 3, method: 'getHealth', params: [] }),
    });
    const healthJson = await readJson(healthRes);
    assert(healthRes.status === 200, `expected same-origin getHealth to be 200, got ${healthRes.status}`);
    assert(healthJson.result === 'ok', `expected getHealth result ok, got ${JSON.stringify(healthJson)}`);

    const blockedMethodRes = await fetch(`${ORIGIN}/api/rpc/mainnet-beta`, {
      method: 'POST',
      headers: {
        ...HEADERS,
        'content-type': 'application/json',
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: 4,
        method: 'getProgramAccountsV2',
        params: ['11111111111111111111111111111111', {}],
      }),
    });
    const blockedMethodJson = await readJson(blockedMethodRes);
    assert(blockedMethodRes.status === 403, `expected getProgramAccountsV2 to be 403, got ${blockedMethodRes.status}`);
    assert(blockedMethodJson.error === 'rpc_method_blocked', `expected rpc_method_blocked, got ${JSON.stringify(blockedMethodJson)}`);

    const priceRes = await fetch(`${ORIGIN}/api/price?ids=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`, {
      headers: HEADERS,
    });
    assert(priceRes.status === 200, `expected /api/price to be 200, got ${priceRes.status}`);

    const pumpRes = await fetch(`${ORIGIN}/api/pump/coins/list?limit=1&offset=0`, {
      headers: HEADERS,
    });
    assert(pumpRes.status === 200, `expected /api/pump/coins/list to be 200, got ${pumpRes.status}`);

    const pinataNoHeaderRes = await fetch(`${ORIGIN}/api/pinata/json`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ hello: 'world' }),
    });
    const pinataNoHeaderJson = await readJson(pinataNoHeaderRes);
    assert(pinataNoHeaderRes.status === 403, `expected headerless Pinata request to be 403, got ${pinataNoHeaderRes.status}`);
    assert(pinataNoHeaderJson.error === 'missing_proxy_header', `expected missing_proxy_header on Pinata, got ${JSON.stringify(pinataNoHeaderJson)}`);

    const pinataSameOriginRes = await fetch(`${ORIGIN}/api/pinata/json`, {
      method: 'POST',
      headers: {
        ...HEADERS,
        'content-type': 'application/json',
      },
      body: JSON.stringify({ hello: 'world' }),
    });
    assert(
      [500, 502].includes(pinataSameOriginRes.status),
      `expected same-origin Pinata request to reach upstream/auth gate, got ${pinataSameOriginRes.status}`,
    );

    process.stdout.write('security smoke passed\n');
  } finally {
    await harness.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
