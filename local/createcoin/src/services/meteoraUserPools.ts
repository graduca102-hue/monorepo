import { CpAmm, derivePositionAddress, getAllPositionNftAccountByOwner } from '@meteora-ag/cp-amm-sdk';
import { TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID } from '@solana/spl-token';
import { Connection, PublicKey, type Commitment } from '@solana/web3.js';
import { env } from '../config/env';
import type { UserPoolPosition } from './raydiumService';
import { fetchSplMintSymbol } from './splTokenMetadata';

type ParsedTokenExt = {
  parsed?: {
    info?: {
      mint?: string;
      tokenAmount?: { amount?: string; decimals?: number };
    };
  };
};

function addMintDedupe(seen: Set<string>, out: PublicKey[], mintStr: string | undefined): void {
  if (!mintStr || mintStr.length < 32) return;
  if (seen.has(mintStr)) return;
  seen.add(mintStr);
  out.push(new PublicKey(mintStr));
}

/**
 * Collect SPL mints that might be Meteora DAMM v2 position NFTs (balance 1, decimals 0).
 * Token-2022 path uses the SDK helper; legacy Tokenkeg accounts are scanned for the same shape.
 */
async function collectPositionNftMintCandidates(
  connection: Connection,
  owner: PublicKey,
  commitment: Commitment,
): Promise<PublicKey[]> {
  const seen = new Set<string>();
  const out: PublicKey[] = [];

  const sdkRows = await getAllPositionNftAccountByOwner(connection, owner);
  for (const row of sdkRows) {
    addMintDedupe(seen, out, row.positionNft.toBase58());
  }

  const [classic, token2022] = await Promise.all([
    connection.getParsedTokenAccountsByOwner(owner, { programId: TOKEN_PROGRAM_ID }, commitment),
    connection.getParsedTokenAccountsByOwner(owner, { programId: TOKEN_2022_PROGRAM_ID }, commitment),
  ]);

  for (const { account } of [...classic.value, ...token2022.value]) {
    const data = account.data as ParsedTokenExt;
    const mint = data.parsed?.info?.mint;
    const amt = data.parsed?.info?.tokenAmount?.amount;
    const dec = data.parsed?.info?.tokenAmount?.decimals ?? 0;
    if (amt === '1' && dec === 0) addMintDedupe(seen, out, mint);
  }

  return out;
}

/** Resolve Metaplex ticker for each Meteora row’s meme mint (parallel, cached per mint). */
export async function enrichMeteoraPoolSymbols(
  connection: Connection,
  pools: UserPoolPosition[],
): Promise<UserPoolPosition[]> {
  const meteora = pools.filter((p) => p.isMeteoraPool);
  if (meteora.length === 0) return pools;

  const mints = [...new Set(meteora.map((p) => p.baseMint))];
  const symByMint = new Map<string, string>();
  await Promise.all(
    mints.map(async (m) => {
      symByMint.set(m, await fetchSplMintSymbol(connection, m));
    }),
  );

  return pools.map((p) =>
    p.isMeteoraPool
      ? p.isFrontendOnlyMeteoraPool
        ? p
        : { ...p, baseSymbol: symByMint.get(p.baseMint) ?? p.baseSymbol }
      : p,
  );
}

/**
 * Discover Meteora DAMM v2 positions from chain (wallet-held position NFTs), same wallet + cluster on any origin.
 */
export async function fetchMeteoraUserPoolPositions(
  connection: Connection,
  owner: PublicKey,
  commitment: Commitment = 'confirmed',
): Promise<UserPoolPosition[]> {
  const wsolPk = new PublicKey(env.wsolMint);
  const candidates = await collectPositionNftMintCandidates(connection, owner, commitment);
  if (candidates.length === 0) return [];

  const cpAmm = new CpAmm(connection);
  const rows: UserPoolPosition[] = [];

  for (const nftMint of candidates) {
    const positionPk = derivePositionAddress(nftMint);
    let positionState;
    try {
      positionState = await cpAmm.fetchPositionState(positionPk);
    } catch {
      continue;
    }
    if (!positionState.nftMint.equals(nftMint)) continue;

    let poolState;
    try {
      poolState = await cpAmm.fetchPoolState(positionState.pool);
    } catch {
      continue;
    }

    const tokenA = poolState.tokenAMint;
    const tokenB = poolState.tokenBMint;
    if (!tokenA.equals(wsolPk) && !tokenB.equals(wsolPk)) continue;

    const baseMintPk = tokenA.equals(wsolPk) ? tokenB : tokenA;

    rows.push({
      poolId: positionState.pool.toBase58(),
      baseMint: baseMintPk.toBase58(),
      quoteMint: wsolPk.toBase58(),
      baseSymbol: baseMintPk.toBase58().slice(0, 4),
      quoteSymbol: 'SOL',
      lpMint: nftMint.toBase58(),
      lpAmount: '1',
      lpAmountRaw: '1',
      sharePercent: 100,
      baseAmount: '0',
      quoteAmount: '0',
      baseUsdValue: 0,
      quoteUsdValue: 0,
      totalUsdValue: 0,
      poolTvlUsd: 0,
      isDrained: false,
      isMeteoraPool: true,
      meteoraPosition: positionPk.toBase58(),
    });
  }

  return rows;
}
