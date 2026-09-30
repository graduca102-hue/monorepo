import { buildQs, forwardPumpGet } from '../_proxyUtil.js';
import { isValidBase58LikeAddress, rejectCrossSite, sendJson, takeRateLimit } from '../../_serverUtil.js';

const UPSTREAM = 'https://frontend-api-v3.pump.fun';

export default async function pumpCoinByMint(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    res.setHeader('Content-Type', 'application/json');
    return res.status(405).json({ error: 'method_not_allowed' });
  }
  if (rejectCrossSite(req, res)) return;

  const limited = takeRateLimit(req, 'pump:coin', { limit: 240, windowMs: 60_000, cost: 1 });
  if (!limited.ok) {
    res.setHeader('Retry-After', String(limited.retryAfterSeconds));
    return sendJson(res, 429, { error: 'rate_limited', retryAfter: limited.retryAfterSeconds });
  }

  const mint = req.query?.mint;
  if (!mint || typeof mint !== 'string' || !isValidBase58LikeAddress(mint)) {
    res.setHeader('Content-Type', 'application/json');
    return res.status(400).json({ error: 'missing_mint' });
  }
  const qs = buildQs(req.query, ['mint']);
  await forwardPumpGet(`${UPSTREAM}/coins/${encodeURIComponent(mint)}${qs}`, req, res);
}
