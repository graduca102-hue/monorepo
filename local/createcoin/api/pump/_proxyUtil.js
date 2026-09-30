/**
 * Shared Pump.fun upstream proxy helpers as ESM `.js` — Vercel only globs `.js`/`.mjs`/`.ts` under `/api`.
 */
import { getServerPumpfunAuth, relayUpstreamResponse, sendJson, setNoStore } from '../_serverUtil.js';

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
  setNoStore(res);
  const headers = {
    Accept: 'application/json',
    'User-Agent': UA,
  };
  const auth = getServerPumpfunAuth();
  if (auth) headers.Authorization = `Bearer ${auth}`;
  try {
    const upstream = await fetch(upstreamUrl, { method: 'GET', headers, redirect: 'follow' });
    return await relayUpstreamResponse(res, upstream);
  } catch {
    return sendJson(res, 502, { error: 'pump_upstream_failed' });
  }
}
