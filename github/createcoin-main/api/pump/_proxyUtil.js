/**
 * Shared Pump.fun upstream proxy helpers as ESM `.js` — Vercel only globs `.js`/`.mjs`/`.ts` under `/api`.
 */
const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

export function buildQs(queryObj, omitKeys = []) {
  const qs = new URLSearchParams();
  for (const [key, val] of Object.entries(queryObj || {})) {
    if (omitKeys.includes(key)) continue;
    if (val === undefined) continue;
    if (Array.isArray(val)) {
      for (const v of val) {
        if (v != null) qs.append(key, String(v));
      }
    } else {
      qs.set(key, String(val));
    }
  }
  const s = qs.toString();
  return s ? `?${s}` : '';
}

export async function forwardPumpGet(upstreamUrl, req, res) {
  const headers = {
    Accept: 'application/json',
    'User-Agent': UA,
  };
  if (typeof req.headers.authorization === 'string') {
    headers.Authorization = req.headers.authorization;
  }
  try {
    const r = await fetch(upstreamUrl, { method: 'GET', headers, redirect: 'follow' });
    const body = Buffer.from(await r.arrayBuffer());
    res.setHeader('Content-Type', 'application/json');
    res.status(r.status).send(body);
  } catch {
    res.setHeader('Content-Type', 'application/json');
    res.status(502).json({ error: 'pump_upstream_failed' });
  }
}
