import {
  ComputeBudgetProgram,
  Connection,
  PublicKey,
  TransactionInstruction,
} from '@solana/web3.js';

const MIN_MICRO = 1_000;
const MAX_MICRO = 1_000_000;
const FALLBACK_MICRO = 50_000;
const CACHE_MS = 5_000;

let cachedMicro: number | null = null;
let cachedAt = 0;
let cachedWritableKey: string | undefined;

function percentile75(sorted: number[]): number {
  if (sorted.length === 0) return FALLBACK_MICRO;
  const idx = Math.floor(sorted.length * 0.75);
  return sorted[Math.min(idx, sorted.length - 1)] ?? FALLBACK_MICRO;
}

export async function getDynamicPriorityFee(
  connection: Connection,
  writableAccounts?: PublicKey[],
): Promise<number> {
  const key = writableAccounts?.map((p) => p.toBase58()).join(',') ?? '';
  const now = Date.now();
  if (cachedMicro !== null && now - cachedAt < CACHE_MS && cachedWritableKey === key) {
    return cachedMicro;
  }
  try {
    const params =
      writableAccounts && writableAccounts.length > 0
        ? { lockedWritableAccounts: writableAccounts.map((p) => p.toBase58()) }
        : {};
    const fees = await (connection as Connection & {
      getRecentPrioritizationFees?: (p?: {
        lockedWritableAccounts?: string[];
      }) => Promise<{ prioritizationFee: number }[]>;
    }).getRecentPrioritizationFees?.(params);
    const values = (fees ?? [])
      .map((f) => f.prioritizationFee)
      .filter((n) => typeof n === 'number' && n > 0)
      .sort((a, b) => a - b);
    const p75 = percentile75(values);
    const capped = Math.min(MAX_MICRO, Math.max(MIN_MICRO, p75 || FALLBACK_MICRO));
    cachedMicro = capped;
    cachedAt = now;
    cachedWritableKey = key;
    return capped;
  } catch {
    cachedMicro = FALLBACK_MICRO;
    cachedAt = now;
    cachedWritableKey = key;
    return FALLBACK_MICRO;
  }
}

export function buildComputeBudgetInstructions(
  units: number,
  priorityFeeMicroLamports: number,
): TransactionInstruction[] {
  return [
    ComputeBudgetProgram.setComputeUnitLimit({ units }),
    ComputeBudgetProgram.setComputeUnitPrice({ microLamports: priorityFeeMicroLamports }),
  ];
}
