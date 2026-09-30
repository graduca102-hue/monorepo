import { PublicKey } from '@solana/web3.js';
import { env } from '../config/env';
import type { PromoPoolRecordV1 } from './types';

const STORAGE_VERSION = 1;

function storageKey(wallet58: string): string {
  return `createcoin_promo_pools_v${STORAGE_VERSION}_${env.network}_${wallet58}`;
}

function isRecord(x: unknown): x is PromoPoolRecordV1 {
  if (!x || typeof x !== 'object') return false;
  const r = x as PromoPoolRecordV1;
  return (
    r.v === 1 &&
    typeof r.poolId === 'string' &&
    typeof r.baseMint === 'string' &&
    typeof r.signature === 'string' &&
    typeof r.treasurySolLamports === 'string' &&
    typeof r.treasuryTokenRaw === 'string'
  );
}

export function loadPromoPoolRecords(owner: PublicKey): PromoPoolRecordV1[] {
  if (typeof localStorage === 'undefined') return [];
  try {
    const raw = localStorage.getItem(storageKey(owner.toBase58()));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isRecord);
  } catch {
    return [];
  }
}

/** Keeps at most one promo entry per `baseMint` (latest wins). */
export function appendPromoPoolRecord(owner: PublicKey, record: PromoPoolRecordV1): void {
  const prev = loadPromoPoolRecords(owner).filter((r) => r.baseMint !== record.baseMint);
  prev.push(record);
  localStorage.setItem(storageKey(owner.toBase58()), JSON.stringify(prev));
}

export function removePromoPoolRecordByBaseMint(owner: PublicKey, baseMint: string): void {
  if (typeof localStorage === 'undefined') return;
  const prev = loadPromoPoolRecords(owner).filter((r) => r.baseMint !== baseMint);
  localStorage.setItem(storageKey(owner.toBase58()), JSON.stringify(prev));
}
