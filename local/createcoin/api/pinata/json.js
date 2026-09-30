import {
  getServerPinataJwt,
  readRawBody,
  relayUpstreamResponse,
  rejectCrossSite,
  sendJson,
  takeRateLimit,
} from '../_serverUtil.js';

export const config = {
  api: {
    bodyParser: false,
  },
};

export default async function pinataJsonProxy(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return sendJson(res, 405, { error: 'method_not_allowed' });
  }
  if (rejectCrossSite(req, res)) return;

  const limited = takeRateLimit(req, 'pinata:json', {
    limit: 20,
    windowMs: 60_000,
    cost: 4,
  });
  if (!limited.ok) {
    res.setHeader('Retry-After', String(limited.retryAfterSeconds));
    return sendJson(res, 429, { error: 'rate_limited', retryAfter: limited.retryAfterSeconds });
  }

  if (!String(req.headers['content-type'] || '').toLowerCase().includes('application/json')) {
    return sendJson(res, 415, { error: 'unsupported_media_type' });
  }

  let rawBody;
  try {
    rawBody = await readRawBody(req, 512 * 1024);
  } catch (error) {
    return sendJson(res, 413, {
      error: 'payload_too_large',
      message: error instanceof Error ? error.message : 'Metadata payload is too large',
    });
  }

  let jwt;
  try {
    jwt = getServerPinataJwt();
  } catch (error) {
    return sendJson(res, 500, {
      error: 'pinata_not_configured',
      message: error instanceof Error ? error.message : 'PINATA_JWT is missing',
    });
  }

  try {
    const upstream = await fetch('https://api.pinata.cloud/pinning/pinJSONToIPFS', {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${jwt}`,
        'Content-Type': 'application/json',
      },
      body: rawBody,
      redirect: 'follow',
    });
    return await relayUpstreamResponse(res, upstream);
  } catch {
    return sendJson(res, 502, { error: 'pinata_upstream_failed' });
  }
}
