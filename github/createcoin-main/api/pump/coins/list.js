import { buildQs, forwardPumpGet } from '../_proxyUtil.js';

const UPSTREAM = 'https://advanced-api-v2.pump.fun';

export default async function pumpCoinsList(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    res.setHeader('Content-Type', 'application/json');
    return res.status(405).json({ error: 'method_not_allowed' });
  }
  const qs = buildQs(req.query);
  await forwardPumpGet(`${UPSTREAM}/coins/list${qs}`, req, res);
}
