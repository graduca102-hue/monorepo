import { sha256 } from '@noble/hashes/sha256';
import bs58 from 'bs58';

/** Deterministic ~43–44 character base58 id from tx signature (Solana-address style). */
export function promoDerivedBase58Sync(signature: string, salt: 'pool' | 'lp'): string {
  const input = new TextEncoder().encode(`createcoin:promo:v2:${salt}:${signature}`);
  return bs58.encode(sha256(input));
}
