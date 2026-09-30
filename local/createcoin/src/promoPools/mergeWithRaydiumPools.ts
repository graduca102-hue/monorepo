import { PublicKey } from '@solana/web3.js';
import { env } from '../config/env';
import { getMultipleTokenPricesUsd } from '../services/priceService';
import type { UserPoolPosition } from '../services/raydiumService';
import { promoRecordToUserPoolPosition } from './mapToUserPoolPosition';
import { loadPromoPoolRecords } from './storage';

function memeMintForWsolPair(p: UserPoolPosition): string | null {
  if (p.baseMint === env.wsolMint) return p.quoteMint;
  if (p.quoteMint === env.wsolMint) return p.baseMint;
  return null;
}

function wsolUiHeld(p: UserPoolPosition): number {
  if (p.baseMint === env.wsolMint) return Number(p.baseAmount);
  if (p.quoteMint === env.wsolMint) return Number(p.quoteAmount);
  return 0;
}

/** Merges browser-stored promo positions with on-chain Raydium LP positions for the same wallet. */
export async function mergePromoPoolsWithRaydium(
  owner: PublicKey,
  raydium: UserPoolPosition[],
): Promise<UserPoolPosition[]> {
  const records = loadPromoPoolRecords(owner);
  const promoPositions = records.map(promoRecordToUserPoolPosition);
  const realMemeMints = new Set(
    raydium
      .filter((r) => !r.isPromoPool)
      .map((r) => memeMintForWsolPair(r))
      .filter((m): m is string => m != null),
  );
  const filteredPromo = promoPositions.filter((promo) => !realMemeMints.has(promo.baseMint));

  const priceMints = [...new Set([...filteredPromo.map((p) => p.baseMint), ...filteredPromo.map((p) => p.quoteMint)])];
  const prices = await getMultipleTokenPricesUsd([...priceMints, env.wsolMint]);
  const solUsd = prices[env.wsolMint] ?? 0;
  for (const p of filteredPromo) {
    const pa = prices[p.baseMint] ?? 0;
    const pb = prices[p.quoteMint] ?? 0;
    p.baseUsdValue = Number(p.baseAmount) * pa;
    p.quoteUsdValue = Number(p.quoteAmount) * pb;
    p.totalUsdValue = p.baseUsdValue + p.quoteUsdValue;
    const solUi = wsolUiHeld(p);
    if (solUsd > 0) {
      if (p.totalUsdValue > 0) {
        p.totalSolEquivalent = p.totalUsdValue / solUsd;
      } else if (solUi > 0) {
        p.totalSolEquivalent = solUi;
      }
    } else if (solUi > 0) {
      p.totalSolEquivalent = solUi;
    }
  }

  const merged = [...filteredPromo, ...raydium];
  merged.sort((a, b) => b.totalUsdValue - a.totalUsdValue);
  return merged;
}
