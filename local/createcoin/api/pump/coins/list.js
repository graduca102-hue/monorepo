import { buildQs, forwardPumpGet } from '../_proxyUtil.js';
import { parsePositiveInt, rejectCrossSite, sendJson, takeRateLimit } from '../../_serverUtil.js';

const UPSTREAM = 'https://advanced-api-v2.pump.fun';

export default async function pumpCoinsList(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    res.setHeader('Content-Type', 'application/json');
    return res.status(405).json({ error: 'method_not_allowed' });
  }
  if (rejectCrossSite(req, res)) return;

  const limit = takeRateLimit(req, 'pump:list', { limit: 240, windowMs: 60_000, cost: 1 });
  if (!limit.ok) {
    res.setHeader('Retry-After', String(limit.retryAfterSeconds));
    return sendJson(res, 429, { error: 'rate_limited', retryAfter: limit.retryAfterSeconds });
  }

  if (
    req.query.limit != null &&
    parsePositiveInt(req.query.limit, -1, { min: 1, max: 50 }) === -1
  ) {
    return sendJson(res, 400, { error: 'invalid_limit' });
  }
  if (
    req.query.offset != null &&
    parsePositiveInt(req.query.offset, -1, { min: 0, max: 1000 }) === -1
  ) {
    return sendJson(res, 400, { error: 'invalid_offset' });
  }

  const qs = buildQs(req.query);
  await forwardPumpGet(`${UPSTREAM}/coins/list${qs}`, req, res);
}
