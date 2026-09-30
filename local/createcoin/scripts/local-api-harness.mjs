import http from 'node:http';
import { URL } from 'node:url';

import health from '../api/health.js';
import price from '../api/price.js';
import pinataFile from '../api/pinata/file.js';
import pinataJson from '../api/pinata/json.js';
import pumpList from '../api/pump/coins/list.js';
import pumpSearch from '../api/pump/coins/search.js';
import pumpCoin from '../api/pump/coins/[mint].js';
import pumpMetadata from '../api/pump/coins/metadata/[mint].js';
import rpcProxy from '../api/rpc/[network].js';

function assignQuery(req, url) {
  const query = {};
  for (const [key, value] of url.searchParams.entries()) {
    if (key in query) {
      const current = query[key];
      query[key] = Array.isArray(current) ? [...current, value] : [current, value];
    } else {
      query[key] = value;
    }
  }
  req.query = query;
}

function decorateRes(res) {
  res.status = (code) => {
    res.statusCode = code;
    return res;
  };
  res.json = (payload) => {
    if (!res.getHeader('Content-Type')) {
      res.setHeader('Content-Type', 'application/json');
    }
    res.end(JSON.stringify(payload));
    return res;
  };
  res.send = (payload) => {
    if (Buffer.isBuffer(payload) || typeof payload === 'string') {
      res.end(payload);
      return res;
    }
    if (payload == null) {
      res.end();
      return res;
    }
    if (!res.getHeader('Content-Type')) {
      res.setHeader('Content-Type', 'application/json');
    }
    res.end(JSON.stringify(payload));
    return res;
  };
  return res;
}

async function route(req, res) {
  const url = new URL(req.url, 'http://127.0.0.1');
  assignQuery(req, url);

  if (url.pathname === '/api/health') return health(req, decorateRes(res));
  if (url.pathname === '/api/price') return price(req, decorateRes(res));
  if (url.pathname === '/api/pinata/file') return pinataFile(req, decorateRes(res));
  if (url.pathname === '/api/pinata/json') return pinataJson(req, decorateRes(res));
  if (url.pathname === '/api/pump/coins/list') return pumpList(req, decorateRes(res));
  if (url.pathname === '/api/pump/coins/search') return pumpSearch(req, decorateRes(res));

  const rpcMatch = url.pathname.match(/^\/api\/rpc\/([^/]+)$/);
  if (rpcMatch) {
    req.query.network = rpcMatch[1];
    return rpcProxy(req, decorateRes(res));
  }

  const coinMatch = url.pathname.match(/^\/api\/pump\/coins\/([^/]+)$/);
  if (coinMatch) {
    req.query.mint = decodeURIComponent(coinMatch[1]);
    return pumpCoin(req, decorateRes(res));
  }

  const metadataMatch = url.pathname.match(/^\/api\/pump\/coins\/metadata\/([^/]+)$/);
  if (metadataMatch) {
    req.query.mint = decodeURIComponent(metadataMatch[1]);
    return pumpMetadata(req, decorateRes(res));
  }

  decorateRes(res).status(404).json({ error: 'not_found' });
}

export async function startLocalApiHarness(port = 4310) {
  const server = http.createServer((req, res) => {
    Promise.resolve(route(req, res)).catch((error) => {
      decorateRes(res).status(500).json({
        error: 'harness_failed',
        message: error instanceof Error ? error.message : String(error),
      });
    });
  });

  await new Promise((resolve) => server.listen(port, '127.0.0.1', resolve));
  return {
    port,
    server,
    close: () => new Promise((resolve, reject) => server.close((err) => (err ? reject(err) : resolve()))),
  };
}

if (import.meta.url === new URL(`file://${process.argv[1].replace(/\\/g, '/')}`).href) {
  const port = Number(process.env.LOCAL_API_HARNESS_PORT || 4310);
  startLocalApiHarness(port).then(() => {
    // Keep process alive when launched directly.
    process.stdout.write(`local-api-harness listening on http://127.0.0.1:${port}\n`);
  });
}
