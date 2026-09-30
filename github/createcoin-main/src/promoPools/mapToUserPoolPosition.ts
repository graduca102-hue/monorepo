import type { UserPoolPosition } from '../services/raydiumService';
import type { PromoPoolRecordV1 } from './types';
import { promoDerivedBase58Sync } from './derivePoolIds';

export function promoRecordToUserPoolPosition(r: PromoPoolRecordV1): UserPoolPosition {
  const poolId = r.poolId.startsWith('promo:') ? promoDerivedBase58Sync(r.signature, 'pool') : r.poolId;
  const lpMint = r.lpMint ?? promoDerivedBase58Sync(r.signature, 'lp');
  return {
    poolId,
    baseMint: r.baseMint,
    quoteMint: r.quoteMint,
    baseSymbol: r.baseSymbol,
    quoteSymbol: 'SOL',
    lpMint,
    lpAmount: '0',
    lpAmountRaw: '0',
    sharePercent: 100,
    baseAmount: r.displayBaseAmount,
    quoteAmount: r.displayQuoteAmount,
    baseUsdValue: 0,
    quoteUsdValue: 0,
    totalUsdValue: 0,
    poolTvlUsd: 0,
    isDrained: false,
    isPromoPool: true,
  };
}
