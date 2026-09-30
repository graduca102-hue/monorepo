import { buildQs, forwardPumpGet } from '../_proxyUtil.js';
import { parsePositiveInt, rejectCrossSite, sendJson, takeRateLimit } from '../../_serverUtil.js';

const UPSTREAM = 'https://frontend-api-v3.pump.fun';

export default async function pumpCoinsSearch(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    res.setHeader('Content-Type', 'application/json');
    return res.status(405).json({ error: 'method_not_allowed' });
  }
  if (rejectCrossSite(req, res)) return;

  const limited = takeRateLimit(req, 'pump:search', { limit: 180, windowMs: 60_000, cost: 1 });
  if (!limited.ok) {
    res.setHeader('Retry-After', String(limited.retryAfterSeconds));
    return sendJson(res, 429, { error: 'rate_limited', retryAfter: limited.retryAfterSeconds });
  }

  const term = typeof req.query.searchTerm === 'string' ? req.query.searchTerm.trim() : '';
  if (term.length > 64) {
    return sendJson(res, 400, { error: 'invalid_search_term' });
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
  await forwardPumpGet(`${UPSTREAM}/coins/search${qs}`, req, res);
}
