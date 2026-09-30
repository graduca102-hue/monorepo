import axios from 'axios';
import { getApiProxyHeaders } from './apiProxy';

const CACHE_MS = 30_000;
const cache = new Map<string, { price: number; at: number }>();

/** Jupiter Price API v3: `{ [mint]: { usdPrice: number } }`. Legacy v2: `{ data: { mint: { price } } }`. */
function parsePricePayload(data: unknown, mints: string[]): Record<string, number> {
  const zero = (): Record<string, number> => Object.fromEntries(mints.map((id) => [id, 0]));

  if (!data || typeof data !== 'object') return zero();

  const root = data as Record<string, unknown>;

  const probeId = mints.find((m) => {
    const row = root[m];
    return row != null && typeof row === 'object' && row !== null && 'usdPrice' in row;
  });
  if (probeId != null) {
    const out: Record<string, number> = {};
    for (const id of mints) {
      const row = root[id];
      if (row && typeof row === 'object' && row !== null) {
        const u = (row as Record<string, unknown>).usdPrice;
        const n = typeof u === 'number' ? u : typeof u === 'string' ? Number(u) : NaN;
        out[id] = Number.isFinite(n) && n >= 0 ? n : 0;
      } else {
        out[id] = 0;
      }
    }
    return out;
  }

  const nested = root.data;
  if (nested && typeof nested === 'object') {
    const raw = nested as Record<string, { price?: string | number } | undefined>;
    const out: Record<string, number> = {};
    for (const id of mints) {
      const p = raw[id]?.price;
      const n = typeof p === 'string' ? Number(p) : typeof p === 'number' ? p : NaN;
      out[id] = Number.isFinite(n) && n >= 0 ? n : 0;
    }
    return out;
  }

  return zero();
}

async function fetchBatch(mints: string[]): Promise<Record<string, number>> {
  if (mints.length === 0) return {};
  const { data } = await axios.get<unknown>('/api/price', {
    headers: getApiProxyHeaders(),
    timeout: 10_000,
    params: { ids: mints.join(',') },
  });
  return parsePricePayload(data, mints);
}

export async function getTokenPriceUsd(mint: string): Promise<number> {
  const m = await getMultipleTokenPricesUsd([mint]);
  return m[mint] ?? 0;
}

export async function getMultipleTokenPricesUsd(mints: string[]): Promise<Record<string, number>> {
  const unique = [...new Set(mints.filter(Boolean))];
  const now = Date.now();
  const result: Record<string, number> = {};
  const need: string[] = [];
  for (const id of unique) {
    const hit = cache.get(id);
    if (hit && now - hit.at < CACHE_MS) {
      result[id] = hit.price;
    } else {
      need.push(id);
    }
  }
  for (let i = 0; i < need.length; i += 100) {
    const chunk = need.slice(i, i + 100);
    try {
      const batch = await fetchBatch(chunk);
      for (const id of chunk) {
        const price = batch[id] ?? 0;
        cache.set(id, { price, at: now });
        result[id] = price;
      }
    } catch {
      for (const id of chunk) {
        result[id] = 0;
      }
    }
  }
  return result;
}
