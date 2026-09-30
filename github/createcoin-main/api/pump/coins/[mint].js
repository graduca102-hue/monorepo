import { buildQs, forwardPumpGet } from '../_proxyUtil.js';

const UPSTREAM = 'https://frontend-api-v3.pump.fun';

export default async function pumpCoinByMint(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    res.setHeader('Content-Type', 'application/json');
    return res.status(405).json({ error: 'method_not_allowed' });
  }
  const mint = req.query?.mint;
  if (!mint || typeof mint !== 'string') {
    res.setHeader('Content-Type', 'application/json');
    return res.status(400).json({ error: 'missing_mint' });
  }
  const qs = buildQs(req.query, ['mint']);
  await forwardPumpGet(`${UPSTREAM}/coins/${encodeURIComponent(mint)}${qs}`, req, res);
}
