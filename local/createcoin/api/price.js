import {
  getServerJupiterPriceApiKey,
  parseCommaSeparatedValues,
  getServerPriceApi,
  relayUpstreamResponse,
  rejectCrossSite,
  sendJson,
  takeRateLimit,
} from './_serverUtil.js';

export default async function priceProxy(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return sendJson(res, 405, { error: 'method_not_allowed' });
  }
  if (rejectCrossSite(req, res)) return;

  const limited = takeRateLimit(req, 'price', {
    limit: 600,
    windowMs: 60_000,
    cost: 1,
  });
  if (!limited.ok) {
    res.setHeader('Retry-After', String(limited.retryAfterSeconds));
    return sendJson(res, 429, { error: 'rate_limited', retryAfter: limited.retryAfterSeconds });
  }

  const rawIds = typeof req.query.ids === 'string' ? req.query.ids : Array.isArray(req.query.ids) ? req.query.ids.join(',') : '';
  let ids;
  try {
    ids = parseCommaSeparatedValues(rawIds, { maxItems: 100, maxLengthPerItem: 64 });
  } catch (error) {
    return sendJson(res, 400, {
      error: 'invalid_ids',
      message: error instanceof Error ? error.message : 'Invalid ids query',
    });
  }

  if (ids.length === 0) {
    return sendJson(res, 400, { error: 'missing_ids' });
  }

  const upstreamUrl = new URL(getServerPriceApi());
  upstreamUrl.searchParams.set('ids', ids.join(','));

  let apiKey;
  try {
    apiKey = getServerJupiterPriceApiKey();
  } catch {
    apiKey = undefined;
  }

  try {
    const upstream = await fetch(upstreamUrl, {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        ...(apiKey ? { 'x-api-key': apiKey } : {}),
      },
      redirect: 'follow',
    });
    return await relayUpstreamResponse(res, upstream);
  } catch {
    return sendJson(res, 502, { error: 'price_upstream_failed' });
  }
}
