const rateBuckets = globalThis.__createcoinRateBuckets ?? new Map();
globalThis.__createcoinRateBuckets = rateBuckets;

const SAME_ORIGIN_PROXY_HEADER = 'x-createcoin-proxy';
const SAME_ORIGIN_PROXY_HEADER_VALUE = '1';
const SAFE_FETCH_SITES = new Set(['same-origin', 'same-site', 'none']);
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);

export function setNoStore(res) {
  res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate');
  res.setHeader('Pragma', 'no-cache');
  res.setHeader('Expires', '0');
}

export function sendJson(res, status, payload) {
  setNoStore(res);
  res.setHeader('Content-Type', 'application/json');
  return res.status(status).json(payload);
}

export function normalizePriceApiUrl(raw) {
  const trimmed = String(raw || 'https://api.jup.ag/price/v3').trim().replace(/\/$/, '');
  return trimmed.replace(/\/price\/v2$/i, '/price/v3');
}

function readServerEnv(name) {
  const raw = process.env[name];
  return raw == null || String(raw).trim() === '' ? undefined : String(raw).trim();
}

const PUBLIC_RPC_URLS = {
  'mainnet-beta': 'https://api.mainnet-beta.solana.com',
  devnet: 'https://api.devnet.solana.com',
};

export function requireHttpsUrlEnv(name) {
  const value = readServerEnv(name);
  if (!value) {
    throw new Error(`Missing required server env ${name}.`);
  }
  if (!value.startsWith('https://')) {
    throw new Error(`${name} must use https://`);
  }
  if (value.includes('YOUR_KEY')) {
    throw new Error(`${name} still contains YOUR_KEY.`);
  }
  new URL(value);
  return value;
}

export function getServerRpcUrl(network) {
  const url = PUBLIC_RPC_URLS[network];
  if (!url) {
    throw new Error(`Unsupported Solana network: ${network}`);
  }
  return url;
}

export function getServerPinataJwt() {
  const jwt = readServerEnv('PINATA_JWT');
  if (!jwt) throw new Error('Missing required server env PINATA_JWT.');
  return jwt;
}

export function getServerPumpfunAuth() {
  return readServerEnv('PUMPFUN_AUTH');
}

export function getServerPriceApi() {
  return normalizePriceApiUrl(readServerEnv('PRICE_API'));
}

export function getServerJupiterPriceApiKey() {
  return readServerEnv('JUPITER_PRICE_API_KEY');
}

function normalizeHost(value) {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim().toLowerCase();
  if (!trimmed) return null;
  const withoutWww = trimmed.replace(/^www\./, '');
  try {
    return new URL(`https://${withoutWww}`).hostname.replace(/^www\./, '');
  } catch {
    return withoutWww.split(':')[0] || null;
  }
}

function firstHeaderValue(value) {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    const first = value.find((entry) => typeof entry === 'string' && entry.trim());
    return typeof first === 'string' ? first : undefined;
  }
  return undefined;
}

function collectAllowedHosts(req) {
  return new Set(
    [
      req.headers['x-forwarded-host'],
      req.headers.host,
      req.headers['x-vercel-deployment-url'],
      process.env.VERCEL_PROJECT_PRODUCTION_URL,
    ]
      .flatMap((value) => (Array.isArray(value) ? value : [value]))
      .map(normalizeHost)
      .filter(Boolean),
  );
}

function extractSourceHosts(req) {
  const hosts = [];

  for (const raw of [req.headers.origin, req.headers.referer]) {
    const value = firstHeaderValue(raw);
    if (typeof value !== 'string' || value.trim() === '') continue;
    try {
      const url = new URL(value);
      const host = normalizeHost(url.host);
      if (!host) return { error: 'invalid_origin_header' };
      hosts.push(host);
    } catch {
      return { error: 'invalid_origin_header' };
    }
  }

  return { hosts };
}

export function rejectCrossSite(req, res, opts = {}) {
  const {
    requireProxyHeader = true,
    requireSourceHeaders = true,
  } = opts;

  const proxyHeader = firstHeaderValue(req.headers[SAME_ORIGIN_PROXY_HEADER]);
  if (requireProxyHeader && proxyHeader !== SAME_ORIGIN_PROXY_HEADER_VALUE) {
    sendJson(res, 403, { error: 'missing_proxy_header' });
    return true;
  }

  const secFetchSite = firstHeaderValue(req.headers['sec-fetch-site'])?.trim().toLowerCase();
  if (secFetchSite && !SAFE_FETCH_SITES.has(secFetchSite)) {
    sendJson(res, 403, { error: 'cross_site_request_blocked' });
    return true;
  }

  const allowedHosts = collectAllowedHosts(req);
  if (allowedHosts.size === 0) return false;

  const sourceHosts = extractSourceHosts(req);
  if ('error' in sourceHosts) {
    sendJson(res, 403, { error: sourceHosts.error });
    return true;
  }

  if (sourceHosts.hosts.length === 0) {
    const allLocal = [...allowedHosts].every((host) => LOCAL_HOSTS.has(host));
    if (!requireSourceHeaders && allLocal) return false;
    if (requireSourceHeaders) {
      sendJson(res, 403, { error: 'missing_origin_header' });
      return true;
    }
    return false;
  }

  for (const host of sourceHosts.hosts) {
    if (!allowedHosts.has(host)) {
      sendJson(res, 403, { error: 'cross_site_request_blocked' });
      return true;
    }
  }

  return false;
}

export function getClientIp(req) {
  const forwarded = req.headers['x-forwarded-for'];
  if (typeof forwarded === 'string' && forwarded.trim()) {
    return forwarded.split(',')[0].trim();
  }
  if (Array.isArray(forwarded) && forwarded.length > 0) {
    return String(forwarded[0]).split(',')[0].trim();
  }
  return 'unknown';
}

export function takeRateLimit(req, bucketName, { limit, windowMs, cost = 1 }) {
  const now = Date.now();
  const key = `${bucketName}:${getClientIp(req)}`;
  const current = rateBuckets.get(key);
  const bucket =
    current && current.resetAt > now
      ? current
      : { count: 0, resetAt: now + windowMs };

  if (bucket.count + cost > limit) {
    const retryAfterSeconds = Math.max(1, Math.ceil((bucket.resetAt - now) / 1000));
    return { ok: false, retryAfterSeconds };
  }

  bucket.count += cost;
  rateBuckets.set(key, bucket);
  return { ok: true, remaining: Math.max(0, limit - bucket.count) };
}

export async function readRawBody(req, maxBytes = 1024 * 1024) {
  const chunks = [];
  let total = 0;

  for await (const chunk of req) {
    const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    total += buf.length;
    if (total > maxBytes) {
      throw new Error(`Request body exceeds ${maxBytes} bytes`);
    }
    chunks.push(buf);
  }

  return Buffer.concat(chunks);
}

export async function relayUpstreamResponse(res, upstream) {
  const body = Buffer.from(await upstream.arrayBuffer());
  setNoStore(res);

  const contentType = upstream.headers.get('content-type');
  if (contentType) res.setHeader('Content-Type', contentType);

  const retryAfter = upstream.headers.get('retry-after');
  if (retryAfter) res.setHeader('Retry-After', retryAfter);

  return res.status(upstream.status).send(body);
}

export function isValidBase58LikeAddress(value) {
  return typeof value === 'string' && /^[1-9A-HJ-NP-Za-km-z]{32,64}$/.test(value);
}

export function parsePositiveInt(value, fallback, { min = 0, max = Number.MAX_SAFE_INTEGER } = {}) {
  const n = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : fallback;
  if (!Number.isFinite(n)) return fallback;
  const int = Math.trunc(n);
  if (int < min || int > max) return fallback;
  return int;
}

export function parseCommaSeparatedValues(value, { maxItems, maxLengthPerItem } = {}) {
  if (typeof value !== 'string') return [];
  const seen = new Set();
  const out = [];

  for (const raw of value.split(',')) {
    const item = raw.trim();
    if (!item) continue;
    if (maxLengthPerItem && item.length > maxLengthPerItem) {
      throw new Error(`Item exceeds ${maxLengthPerItem} characters`);
    }
    if (seen.has(item)) continue;
    seen.add(item);
    out.push(item);
    if (maxItems && out.length > maxItems) {
      throw new Error(`Too many items (max ${maxItems})`);
    }
  }

  return out;
}
