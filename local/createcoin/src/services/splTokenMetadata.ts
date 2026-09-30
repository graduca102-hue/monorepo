import { createUmi } from '@metaplex-foundation/umi-bundle-defaults';
import { mplTokenMetadata, fetchDigitalAsset } from '@metaplex-foundation/mpl-token-metadata';
import { publicKey as umiPublicKey } from '@metaplex-foundation/umi';
import type { Connection } from '@solana/web3.js';

const cache = new Map<string, { at: number; symbol: string }>();
const TTL_MS = 5 * 60 * 1000;

/**
 * On-chain Metaplex symbol for an SPL mint (cached). Falls back to first 4 chars of mint on failure.
 */
export async function fetchSplMintSymbol(connection: Connection, mint: string): Promise<string> {
  const now = Date.now();
  const hit = cache.get(mint);
  if (hit && now - hit.at < TTL_MS) {
    return hit.symbol;
  }
  try {
    const umi = createUmi(connection).use(mplTokenMetadata());
    const asset = await fetchDigitalAsset(umi, umiPublicKey(mint));
    const sym = asset.metadata.symbol.replace(/\0/g, '').trim() || mint.slice(0, 4);
    cache.set(mint, { at: now, symbol: sym });
    return sym;
  } catch {
    const fallback = mint.slice(0, 4);
    cache.set(mint, { at: now, symbol: fallback });
    return fallback;
  }
}
