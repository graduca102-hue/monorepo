import { env } from '../config/env';

export type QuoteCurrency = 'WSOL' | 'USDC';

export function getQuoteMint(currency: QuoteCurrency): string {
  return currency === 'WSOL' ? env.wsolMint : env.getUsdcMint();
}

export function getQuoteDecimals(currency: QuoteCurrency): number {
  return currency === 'WSOL' ? 9 : 6;
}

export function getQuoteSymbol(currency: QuoteCurrency): string {
  return currency === 'WSOL' ? 'SOL' : 'USDC';
}
