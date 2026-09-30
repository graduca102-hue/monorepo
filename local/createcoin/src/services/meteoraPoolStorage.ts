import type { UserPoolPosition } from './raydiumService';
import { env } from '../config/env';

const STORAGE_PREFIX = 'pools:';
/** Fee-exempt demo wallets: show inflated pool depth this long after pool creation. */
export const METEORA_FEE_EXEMPT_DISPLAY_DELAY_MS = 20_000;

export type FeeExemptPoolDisplay = {
  solUi: number;
  memeUi: number;
};

export type StoredMeteoraPool = {
  poolAddress: string;
  baseTokenMint: string;
  baseTokenSymbol?: string;
  baseTokenName?: string;
  baseTokenImageUrl?: string | null;
  /** Position NFT mint (DAMM v2 uses NFT positions; serves as the pool's LP handle). */
  lpMint: string;
  /** Position PDA from Meteora SDK (for future manage/remove). */
  position: string;
  createdAt: string;
  txSignature: string;
  feeBps: number;
  /** True when this is a local-only demo pool for a fee-exempt wallet. */
  frontendOnly?: boolean;
  /** Stable fake UI amounts; rotated only by manual refresh. */
  displaySolUi?: number;
  displayMemeUi?: number;
};

function storageKey(walletAddress: string): string {
  return `${STORAGE_PREFIX}${walletAddress.trim()}`;
}

function persistMeteoraPools(walletAddress: string, rows: StoredMeteoraPool[]): void {
  if (typeof localStorage === 'undefined') return;
  localStorage.setItem(storageKey(walletAddress), JSON.stringify(rows));
}

export function loadMeteoraPoolsFromStorage(walletAddress: string | null | undefined): StoredMeteoraPool[] {
  const w = typeof walletAddress === 'string' ? walletAddress.trim() : '';
  if (!w || typeof localStorage === 'undefined') return [];
  try {
    const raw = localStorage.getItem(storageKey(w));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    const out: StoredMeteoraPool[] = [];
    const seen = new Set<string>();
    for (const row of parsed) {
      if (!row || typeof row !== 'object') continue;
      const r = row as Record<string, unknown>;
      const poolAddress = typeof r.poolAddress === 'string' ? r.poolAddress.trim() : '';
      const baseTokenMint = typeof r.baseTokenMint === 'string' ? r.baseTokenMint.trim() : '';
      const baseTokenSymbol = typeof r.baseTokenSymbol === 'string' ? r.baseTokenSymbol.trim() : undefined;
      const baseTokenName = typeof r.baseTokenName === 'string' ? r.baseTokenName.trim() : undefined;
      const baseTokenImageUrl =
        typeof r.baseTokenImageUrl === 'string' ? r.baseTokenImageUrl : r.baseTokenImageUrl === null ? null : undefined;
      const lpMint = typeof r.lpMint === 'string' ? r.lpMint.trim() : '';
      const position = typeof r.position === 'string' ? r.position.trim() : '';
      const createdAt = typeof r.createdAt === 'string' ? r.createdAt : '';
      const txSignature = typeof r.txSignature === 'string' ? r.txSignature.trim() : '';
      const feeBps = typeof r.feeBps === 'number' && Number.isFinite(r.feeBps) ? r.feeBps : 100;
      const frontendOnly = r.frontendOnly === true;
      const displaySolUi = typeof r.displaySolUi === 'number' && Number.isFinite(r.displaySolUi) ? r.displaySolUi : undefined;
      const displayMemeUi =
        typeof r.displayMemeUi === 'number' && Number.isFinite(r.displayMemeUi) ? r.displayMemeUi : undefined;
      if (!poolAddress || !baseTokenMint || !lpMint || !createdAt || !txSignature) continue;
      if (seen.has(poolAddress)) continue;
      seen.add(poolAddress);
      out.push({
        poolAddress,
        baseTokenMint,
        baseTokenSymbol,
        baseTokenName,
        baseTokenImageUrl,
        lpMint,
        position: position || '',
        createdAt,
        txSignature,
        feeBps,
        frontendOnly,
        displaySolUi,
        displayMemeUi,
      });
    }
    return out;
  } catch {
    return [];
  }
}

export function appendMeteoraPool(walletAddress: string, record: StoredMeteoraPool): void {
  const w = walletAddress.trim();
  if (!w || typeof localStorage === 'undefined') return;
  const prev = loadMeteoraPoolsFromStorage(w).filter((p) => p.poolAddress !== record.poolAddress);
  prev.unshift(record);
  persistMeteoraPools(w, prev);
}

export function removeMeteoraPoolFromStorage(walletAddress: string, poolAddress: string): void {
  const w = walletAddress.trim();
  const id = poolAddress.trim();
  if (!w || !id || typeof localStorage === 'undefined') return;
  const next = loadMeteoraPoolsFromStorage(w).filter((p) => p.poolAddress !== id);
  persistMeteoraPools(w, next);
}

export function getMeteoraPoolCreatedAt(
  walletAddress: string,
  poolAddress: string,
): number | null {
  const row = loadMeteoraPoolsFromStorage(walletAddress).find((p) => p.poolAddress === poolAddress.trim());
  if (!row) return null;
  const created = Date.parse(row.createdAt);
  return Number.isFinite(created) ? created : null;
}

/** Random demo depths for fee-exempt wallets (new values each call / refresh). */
export function createFeeExemptPoolDisplay(): FeeExemptPoolDisplay {
  return {
    solUi: 10 + Math.random() * 10,
    memeUi: 250_000_000 + Math.floor(Math.random() * 100_000_001),
  };
}

export function getFeeExemptPoolDisplay(
  walletAddress: string,
  poolAddress: string,
): FeeExemptPoolDisplay | null {
  const w = walletAddress.trim();
  const id = poolAddress.trim();
  if (!w || !id) return null;
  const rows = loadMeteoraPoolsFromStorage(w);
  const idx = rows.findIndex((p) => p.poolAddress === id);
  if (idx < 0) return null;
  const row = rows[idx]!;
  if (Number.isFinite(row.displaySolUi) && Number.isFinite(row.displayMemeUi)) {
    return { solUi: row.displaySolUi!, memeUi: row.displayMemeUi! };
  }
  const nextDisplay = createFeeExemptPoolDisplay();
  rows[idx] = {
    ...row,
    displaySolUi: nextDisplay.solUi,
    displayMemeUi: nextDisplay.memeUi,
  };
  persistMeteoraPools(w, rows);
  return nextDisplay;
}

export function refreshFeeExemptPoolDisplays(walletAddress: string): void {
  const w = walletAddress.trim();
  if (!w) return;
  const rows = loadMeteoraPoolsFromStorage(w);
  if (rows.length === 0) return;
  const next = rows.map((row) => {
    const display = createFeeExemptPoolDisplay();
    return {
      ...row,
      displaySolUi: display.solUi,
      displayMemeUi: display.memeUi,
    };
  });
  persistMeteoraPools(w, next);
}

/** Maps stored Meteora rows to card model; amounts/TVL filled later via `fetchPoolState` or price API. */
export function storedMeteoraPoolToUserPoolPosition(row: StoredMeteoraPool): UserPoolPosition {
  const wsol = env.wsolMint;
  return {
    poolId: row.poolAddress,
    baseMint: row.baseTokenMint,
    quoteMint: wsol,
    baseSymbol: row.baseTokenSymbol || row.baseTokenMint.slice(0, 4),
    quoteSymbol: 'SOL',
    lpMint: row.lpMint,
    lpAmount: '0',
    lpAmountRaw: '0',
    sharePercent: 100,
    baseAmount: '0',
    quoteAmount: '0',
    baseUsdValue: 0,
    quoteUsdValue: 0,
    totalUsdValue: 0,
    poolTvlUsd: 0,
    isDrained: false,
    isMeteoraPool: true,
    isFrontendOnlyMeteoraPool: row.frontendOnly === true,
    meteoraPosition: row.position || undefined,
    meteoraFeeBps: row.feeBps,
  };
}
