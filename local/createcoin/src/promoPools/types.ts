export type PromoPoolRecordV1 = {
  v: 1;
  poolId: string;
  baseMint: string;
  baseSymbol: string;
  quoteMint: string;
  displayBaseAmount: string;
  displayQuoteAmount: string;
  /** Raw token amount (base units) sent to treasury. */
  treasuryTokenRaw: string;
  /** Always "0" in fee-exempt promo — only SPL is sent; SOL amount on the card is display-only. */
  treasurySolLamports: string;
  signature: string;
  createdAt: number;
  /** Synthetic LP mint id (base58); optional on older stored rows. */
  lpMint?: string;
};
