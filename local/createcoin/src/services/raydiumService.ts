import type { ApiV3PoolInfoItem, ApiV3PoolInfoStandardItemCpmm } from '@raydium-io/raydium-sdk-v2';
import {
  CREATE_CPMM_POOL_FEE_ACC,
  CREATE_CPMM_POOL_PROGRAM,
  DEVNET_PROGRAM_ID,
  Percent,
  PoolFetchType,
  Raydium,
  getCpmmPdaPoolId,
  type CpmmKeys,
  type RaydiumLoadParams,
  TxVersion,
} from '@raydium-io/raydium-sdk-v2';
import { TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID, getMint } from '@solana/spl-token';
import {
  ComputeBudgetProgram,
  Connection,
  Keypair,
  LAMPORTS_PER_SOL,
  PublicKey,
  SystemProgram,
  type Commitment,
  type Transaction,
  type TransactionInstruction,
  type VersionedTransaction,
} from '@solana/web3.js';
import type { WalletContextState } from '@solana/wallet-adapter-react';
import axios from 'axios';
import BN from 'bn.js';
import Decimal from 'decimal.js';
import { env } from '../config/env';
import type { QuoteCurrency } from '../utils/quoteCurrency';
import { getFeeLamports } from './feeService';
import { getDynamicPriorityFee } from './priorityFeeService';
import { getMultipleTokenPricesUsd } from './priceService';

export type UserPoolPosition = {
  poolId: string;
  baseMint: string;
  quoteMint: string;
  baseSymbol: string;
  quoteSymbol: string;
  lpMint: string;
  lpAmount: string;
  lpAmountRaw: string;
  sharePercent: number;
  baseAmount: string;
  quoteAmount: string;
  baseUsdValue: number;
  quoteUsdValue: number;
  totalUsdValue: number;
  /** USD ÷ SOL price when SOL/USD is available from the price API. */
  totalSolEquivalent?: number;
  poolTvlUsd: number;
  /**
   * True when the pool’s on-chain vault reserves (post-fee) are zero while the user still holds LP — rare.
   * Also used when the CPMM pair exists but cannot accept deposits (fully drained); see `detectPoolState`.
   */
  isDrained: boolean;
  /** Legacy / unused local promo card (no on-chain LP). */
  isPromoPool?: boolean;
  /** Legacy Supabase demo row; table removed from app flows. */
  isSupabasePool?: boolean;
  /** Meteora DAMM v2 pool created via `createDammV2Pool` (position NFT = liquidity handle). */
  isMeteoraPool?: boolean;
  /** Browser-stored fee-exempt demo pool; no on-chain Meteora position exists. */
  isFrontendOnlyMeteoraPool?: boolean;
  /** Meteora position PDA (optional; used for manage/remove). */
  meteoraPosition?: string;
  meteoraFeeBps?: number;
};

let raydiumCache: Raydium | null = null;
let rayOwner: string | null = null;

function signAllForWallet(wallet: WalletContextState): NonNullable<RaydiumLoadParams['signAllTransactions']> {
  if (wallet.signAllTransactions) {
    return wallet.signAllTransactions.bind(wallet) as NonNullable<RaydiumLoadParams['signAllTransactions']>;
  }
  if (!wallet.signTransaction) throw new Error('Wallet cannot sign transactions');
  const st = wallet.signTransaction.bind(wallet);
  const poly = async (txs: (Transaction | VersionedTransaction)[]) => Promise.all(txs.map((tx) => st(tx)));
  return poly as NonNullable<RaydiumLoadParams['signAllTransactions']>;
}

export function resetRaydium(): void {
  raydiumCache = null;
  rayOwner = null;
}

export async function getRaydium(
  connection: Connection,
  owner: PublicKey,
  wallet: WalletContextState,
): Promise<Raydium> {
  const tag = owner.toBase58();
  if (raydiumCache && rayOwner === tag) return raydiumCache;
  if (!wallet.signTransaction && !wallet.signAllTransactions) {
    throw new Error('Wallet cannot sign transactions');
  }
  raydiumCache = await Raydium.load({
    connection,
    owner,
    signAllTransactions: signAllForWallet(wallet),
    cluster: env.raydiumCluster,
    disableFeatureCheck: true,
    disableLoadToken: true,
    blockhashCommitment: 'confirmed',
  });
  rayOwner = tag;
  return raydiumCache;
}

function slipPercent(slippagePercent: number): Percent {
  const bps = Math.min(50, Math.max(0, slippagePercent)) * 100;
  return new Percent(new BN(bps), new BN(10_000));
}

/** Parse positive human amount (string avoids `Number()` precision loss) → raw integer token units. */
function uiAmountStringToBn(label: string, amountStr: string, decimals: number): BN {
  const s = amountStr.trim();
  if (!s) throw new Error(`${label} is required`);
  const d = new Decimal(s);
  if (!d.isFinite() || d.lte(0)) throw new Error(`Invalid ${label}`);
  const scaled = d.mul(new Decimal(10).pow(decimals));
  return new BN(scaled.floor().toFixed(0));
}

/**
 * Map Raydium CPMM vault-A / vault-B reserves (mintA / mintB on-chain order) to semantic base vs quote.
 * - With `userIntendedBaseMint`: base is always that mint (must be mintA or mintB).
 * - For positions (no intent): exotic token = base; WSOL or USDC = quote; dual-exotic → mintA = base.
 */
function resolveBaseQuoteSides(
  mintA: string,
  mintB: string,
  reserveForMintA: BN,
  reserveForMintB: BN,
  mintDecimalA: number,
  mintDecimalB: number,
  vaultA: string,
  vaultB: string,
  opts: { userIntendedBaseMint?: PublicKey; wsolMint: string; usdcMint: string },
): {
  baseMint: string;
  quoteMint: string;
  baseReserve: BN;
  quoteReserve: BN;
  baseDecimals: number;
  quoteDecimals: number;
  baseVault: string;
  quoteVault: string;
} {
  const mapAasBase = () => ({
    baseMint: mintA,
    quoteMint: mintB,
    baseReserve: reserveForMintA,
    quoteReserve: reserveForMintB,
    baseDecimals: mintDecimalA,
    quoteDecimals: mintDecimalB,
    baseVault: vaultA,
    quoteVault: vaultB,
  });
  const mapBasBase = () => ({
    baseMint: mintB,
    quoteMint: mintA,
    baseReserve: reserveForMintB,
    quoteReserve: reserveForMintA,
    baseDecimals: mintDecimalB,
    quoteDecimals: mintDecimalA,
    baseVault: vaultB,
    quoteVault: vaultA,
  });

  if (opts.userIntendedBaseMint) {
    const intended = opts.userIntendedBaseMint.toBase58();
    if (intended === mintA) return mapAasBase();
    if (intended === mintB) return mapBasBase();
    throw new Error(`Base mint ${intended} is not part of pool pair ${mintA} / ${mintB}`);
  }

  const isQuote = (m: string) => m === opts.wsolMint || m === opts.usdcMint;
  const exoticA = !isQuote(mintA);
  const exoticB = !isQuote(mintB);
  if (exoticA && !exoticB) return mapAasBase();
  if (!exoticA && exoticB) return mapBasBase();
  return mapAasBase();
}

function symbolForMintFromRow(
  row: { mintA?: { address?: string; symbol?: string }; mintB?: { address?: string; symbol?: string } },
  mint: string,
): string {
  if (row.mintA?.address === mint) return row.mintA.symbol ?? mint.slice(0, 4);
  if (row.mintB?.address === mint) return row.mintB.symbol ?? mint.slice(0, 4);
  return mint.slice(0, 4);
}

function cpmmProgram(): PublicKey {
  return env.raydiumCluster === 'devnet'
    ? DEVNET_PROGRAM_ID.CREATE_CPMM_POOL_PROGRAM
    : CREATE_CPMM_POOL_PROGRAM;
}

function cpmmFeeAccount(): PublicKey {
  return env.raydiumCluster === 'devnet'
    ? DEVNET_PROGRAM_ID.CREATE_CPMM_POOL_FEE_ACC
    : CREATE_CPMM_POOL_FEE_ACC;
}

/** Raydium REST is cluster-specific; mainnet API returns nothing for devnet LP mints. */
function raydiumApiV3Base(): string {
  return env.raydiumCluster === 'devnet' ? 'https://api-v3-devnet.raydium.io' : 'https://api-v3.raydium.io';
}

function tip(kind: 'add_liquidity' | 'remove_liquidity', owner: PublicKey) {
  const lamports = getFeeLamports(kind, 1, owner);
  return {
    address: env.getTreasury(),
    amount: new BN(lamports),
  };
}

function tipRaydiumConfig(kind: 'add_liquidity' | 'remove_liquidity', feePayer: PublicKey) {
  const t = tip(kind, feePayer);
  return {
    address: t.address,
    amount: t.amount,
    feePayer,
  };
}

async function microBudget(connection: Connection, keys: PublicKey[]) {
  const micro = await getDynamicPriorityFee(connection, keys);
  return { units: 600_000, microLamports: micro } as const;
}

export function assertActionInstructionsAreReal(instructions: TransactionInstruction[]): void {
  const realActionCount = instructions.filter(
    (ix) =>
      !ix.programId.equals(ComputeBudgetProgram.programId) && !ix.programId.equals(SystemProgram.programId),
  ).length;
  if (realActionCount === 0) {
    throw new Error('Transaction contains no action instructions. Refusing to charge platform fee.');
  }
}

type PoolState =
  | { kind: 'no_pool' }
  | {
      kind: 'drained';
      poolId: string;
      poolInfo: ApiV3PoolInfoStandardItemCpmm;
      poolKeys: CpmmKeys;
      lpSupply: BN;
      baseReserve: BN;
      quoteReserve: BN;
    }
  | {
      kind: 'active';
      poolId: string;
      poolInfo: ApiV3PoolInfoStandardItemCpmm;
      poolKeys: CpmmKeys;
      baseReserve: BN;
      quoteReserve: BN;
      lpSupply: BN;
    };

function isCpmmStandardPool(p: ApiV3PoolInfoItem): p is ApiV3PoolInfoStandardItemCpmm {
  const pid = cpmmProgram().toBase58();
  return p.type === 'Standard' && p.programId === pid && 'config' in p && !('marketId' in p);
}

async function walletLpAmountByMint(
  connection: Connection,
  owner: PublicKey,
  commitment: Commitment = 'confirmed',
): Promise<Map<string, BN>> {
  const [classic, token2022] = await Promise.all([
    connection.getParsedTokenAccountsByOwner(owner, { programId: TOKEN_PROGRAM_ID }, commitment),
    connection.getParsedTokenAccountsByOwner(owner, { programId: TOKEN_2022_PROGRAM_ID }, commitment),
  ]);
  const byMint = new Map<string, BN>();
  for (const { account } of [...classic.value, ...token2022.value]) {
    const data = account.data as { parsed?: { info?: { mint?: string; tokenAmount?: { amount?: string } } } };
    const mint = data.parsed?.info?.mint;
    const rawStr = data.parsed?.info?.tokenAmount?.amount ?? '0';
    if (!mint) continue;
    const raw = new BN(rawStr);
    if (raw.isZero()) continue;
    const prev = byMint.get(mint) ?? new BN(0);
    byMint.set(mint, prev.add(raw));
  }
  return byMint;
}

function dedupePoolsById(pools: ApiV3PoolInfoStandardItemCpmm[]): ApiV3PoolInfoStandardItemCpmm[] {
  const seen = new Set<string>();
  return pools.filter((p) => {
    if (seen.has(p.id)) return false;
    seen.add(p.id);
    return true;
  });
}

function pickCpmmPoolForPair(
  candidates: ApiV3PoolInfoStandardItemCpmm[],
  userLpByMint: Map<string, BN>,
): ApiV3PoolInfoStandardItemCpmm {
  const cpmm = dedupePoolsById(candidates.filter(isCpmmStandardPool));
  if (cpmm.length === 0) throw new Error('No Raydium CPMM pools found for this mint pair');

  const scored = cpmm.map((pool) => {
    const lpMint = pool.lpMint.address;
    return { pool, bal: userLpByMint.get(lpMint) ?? new BN(0) };
  });
  const positive = scored.filter((s) => !s.bal.isZero());
  if (positive.length === 1) return positive[0].pool;
  if (positive.length > 1) {
    positive.sort((a, b) => b.bal.cmp(a.bal));
    return positive[0].pool;
  }

  return cpmm.reduce((best, p) => ((p.tvl ?? 0) > (best.tvl ?? 0) ? p : best));
}

/** On-chain CPMM pool account pubkeys for this mint pair across all fee configs (API may omit some). */
async function listOnChainCpmmPoolIdsForPair(
  raydium: Raydium,
  connection: Connection,
  baseMint: PublicKey,
  quoteMint: PublicKey,
): Promise<string[]> {
  const cfgs = await raydium.api.getCpmmConfigs();
  const list = Array.isArray(cfgs) ? cfgs : [];
  const [lo, hi] =
    Buffer.compare(baseMint.toBuffer(), quoteMint.toBuffer()) < 0
      ? [baseMint, quoteMint]
      : [quoteMint, baseMint];
  const ids: string[] = [];
  for (const feeConfig of list) {
    if (!feeConfig?.id) continue;
    const poolPk = getCpmmPdaPoolId(cpmmProgram(), new PublicKey(feeConfig.id), lo, hi).publicKey;
    const acc = await connection.getAccountInfo(poolPk, 'confirmed');
    if (acc?.data.length) ids.push(poolPk.toBase58());
  }
  return [...new Set(ids)];
}

async function detectPoolState(
  raydium: Raydium,
  connection: Connection,
  baseMint: PublicKey,
  quoteMint: PublicKey,
  owner: PublicKey,
  commitment: Commitment = 'confirmed',
): Promise<PoolState> {
  const fetchRes = await raydium.api.fetchPoolByMints({
    mint1: baseMint.toBase58(),
    mint2: quoteMint.toBase58(),
    type: PoolFetchType.Standard,
  });

  if (import.meta.env.DEV) {
    const data = fetchRes.data ?? [];
    console.log('[Raydium detectPoolState] fetchPoolByMints (full)', {
      mint1: baseMint.toBase58(),
      mint2: quoteMint.toBase58(),
      response: fetchRes,
      count: data.length,
      cpmmPoolIds: data.filter(isCpmmStandardPool).map((p) => p.id),
      feeConfigIds: data
        .filter(isCpmmStandardPool)
        .map((p) => ({ poolId: p.id, feeConfigId: p.config?.id, tvl: p.tvl })),
    });
  }

  const apiCandidates: ApiV3PoolInfoStandardItemCpmm[] = (fetchRes.data ?? []).filter(isCpmmStandardPool);

  if (apiCandidates.length === 0) {
    const onChainIds = await listOnChainCpmmPoolIdsForPair(raydium, connection, baseMint, quoteMint);
    for (const id of onChainIds) {
      try {
        const { poolInfo } = await raydium.cpmm.getPoolInfoFromRpc(id);
        apiCandidates.push(poolInfo);
      } catch {
        /* skip bad id */
      }
    }
  }

  if (apiCandidates.length === 0) return { kind: 'no_pool' };

  const userLpByMint = await walletLpAmountByMint(connection, owner, commitment);
  const selected = pickCpmmPoolForPair(apiCandidates, userLpByMint);

  const { poolInfo, poolKeys } = await raydium.cpmm.getPoolInfoFromRpc(selected.id);
  const poolId = poolInfo.id;

  const rpcMap = await raydium.cpmm.getRpcPoolInfos([poolId], true);
  const rpc = rpcMap[poolId];
  if (!rpc) throw new Error('Could not load CPMM pool from RPC');

  const lpSupply = rpc.lpAmount;
  const baseReserve = rpc.baseReserve;
  const quoteReserve = rpc.quoteReserve;

  if (import.meta.env.DEV) {
    const lpMints = dedupePoolsById(apiCandidates).map((c) => c.lpMint.address);
    const held = lpMints.filter((m) => !(userLpByMint.get(m) ?? new BN(0)).isZero());
    console.log('[Raydium detectPoolState] selection', {
      selectedPoolId: poolId,
      candidatePoolIds: dedupePoolsById(apiCandidates).map((c) => c.id),
      lpMintsHeldInWalletForPair: held,
      selectedFeeConfigId: poolInfo.config?.id,
    });
  }

  if (lpSupply.isZero() || baseReserve.isZero() || quoteReserve.isZero()) {
    return {
      kind: 'drained',
      poolId,
      poolInfo,
      poolKeys,
      lpSupply,
      baseReserve,
      quoteReserve,
    };
  }

  return {
    kind: 'active',
    poolId,
    poolInfo,
    poolKeys,
    baseReserve,
    quoteReserve,
    lpSupply,
  };
}

async function logAddLiquidityPostMortem(
  connection: Connection,
  payer: PublicKey,
  signature: string,
  intendedPoolId: string,
  devMeta?: { intendedFeeConfigId?: string },
): Promise<void> {
  const txDetails = await connection.getParsedTransaction(signature, {
    maxSupportedTransactionVersion: 0,
    commitment: 'confirmed',
  });

  const userAddress = payer.toBase58();
  const lpMintsIncreased = (txDetails?.meta?.postTokenBalances ?? [])
    .filter((b) => b.owner === userAddress)
    .filter((b) => {
      const pre = txDetails?.meta?.preTokenBalances?.find((p) => p.accountIndex === b.accountIndex);
      const preAmount = BigInt(pre?.uiTokenAmount?.amount ?? '0');
      const postAmount = BigInt(b.uiTokenAmount.amount);
      return postAmount > preAmount;
    });

  for (const b of lpMintsIncreased) {
    const rows = await fetchLpsRowsWithRetry(raydiumApiV3Base(), [b.mint]);
    const row = rows.find((r) => r?.id && lpMintAddressFromPoolRow(r as LpApiRow) === b.mint) ?? rows[0];
    const actualPoolId = row?.id;
    const feeConfigOfActual =
      row && typeof row === 'object' && 'config' in row
        ? (row as { config?: { id?: string } }).config?.id
        : undefined;
    console.log('[addLiquidity post-mortem]', {
      intendedPoolId,
      actualPoolId,
      lpMint: b.mint,
      matches: actualPoolId === intendedPoolId,
      feeConfigOfActual,
    });
    if (import.meta.env.DEV && actualPoolId && actualPoolId !== intendedPoolId) {
      console.error('[addLiquidity CRITICAL] LP mint ties to a different pool than intended (sibling fee config / wrong poolId)', {
        intendedPoolId,
        actualPoolId,
        intendedPoolFeeConfig: devMeta?.intendedFeeConfigId ?? null,
        actualPoolFeeConfig: feeConfigOfActual ?? null,
      });
    }
  }
}

/**
 * Raydium CPMM `deposit` cannot re-bootstrap when LP supply or either reserve is zero (see IMPLEMENTATION_NOTES.md).
 * Explicit branch so we never bundle a treasury transfer without a real deposit.
 */
async function buildSeedDrainedPoolInstructions(params: {
  poolId: string;
  poolInfo: ApiV3PoolInfoStandardItemCpmm;
  baseMint: PublicKey;
  quoteMint: PublicKey;
  baseAmount: BN;
  quoteAmount: BN;
  useSOLBalance: boolean;
}): Promise<never> {
  void params;
  throw new Error(
    'This Raydium CPMM pool has no valid liquidity state (on-chain deposit requires non-zero LP supply and both reserves). ' +
      'After a full drain the pool account still exists but cannot accept new deposits through this program. ',
  );
}

type V0CpmmBuilt = Awaited<ReturnType<Raydium['cpmm']['addLiquidity']>>;

async function executeV0WithPlatformFeeGuard(
  built: V0CpmmBuilt | Awaited<ReturnType<Raydium['cpmm']['createPool']>> | Awaited<ReturnType<Raydium['cpmm']['withdrawLiquidity']>>,
  owner: PublicKey,
  feeKind: 'add_liquidity' | 'remove_liquidity',
): Promise<string> {
  assertActionInstructionsAreReal(built.builder.allInstructions);
  if (getFeeLamports(feeKind, 1, owner) > 0) {
    built.builder.addTipInstruction(tipRaydiumConfig(feeKind, owner));
  }
  if (import.meta.env.DEV && feeKind === 'add_liquidity') {
    built.builder.allInstructions.forEach((ix, i) => {
      console.log(`[addLiquidity ix ${i}]`, {
        programId: ix.programId.toBase58(),
        keys: ix.keys.map((k) => k.pubkey.toBase58()),
        dataLength: ix.data.length,
      });
    });
  }
  const rebuilt = await built.builder.buildV0({});
  const { txId } = await rebuilt.execute({ sendAndConfirm: false });
  return txId;
}

/** Raydium CPMM charges this SOL when **creating** a new pool; adding to an existing pool does not. */
export const RAYDIUM_CPMM_NEW_POOL_LAMPORTS = Math.round(0.15 * LAMPORTS_PER_SOL);

/**
 * Whether the next add-liquidity action will call `createPool` (Raydium takes {@link RAYDIUM_CPMM_NEW_POOL_LAMPORTS}).
 * Uses the same detection as {@link addLiquidity}.
 */
export async function previewWillCreateNewCpmmPool(params: {
  connection: Connection;
  wallet: WalletContextState;
  baseMint: PublicKey;
  quoteCurrency: QuoteCurrency;
}): Promise<boolean> {
  const owner = params.wallet.publicKey;
  if (!owner) throw new Error('Wallet not connected');
  const raydium = await getRaydium(params.connection, owner, params.wallet);
  const quoteMintStr = params.quoteCurrency === 'WSOL' ? env.wsolMint : env.getUsdcMint();
  const quoteMint = new PublicKey(quoteMintStr);
  const state = await detectPoolState(raydium, params.connection, params.baseMint, quoteMint, owner);
  return state.kind === 'no_pool';
}

export async function addLiquidity(params: {
  connection: Connection;
  wallet: WalletContextState;
  baseMint: PublicKey;
  quoteCurrency: QuoteCurrency;
  /** Human units as string (preferred) or number — e.g. whole tokens, SOL as decimal SOL. */
  baseAmount: string | number;
  quoteAmount: string | number;
  slippagePercent: number;
  startTime?: number;
}): Promise<{ poolId: string; signature: string; isNewPool: boolean }> {
  const owner = params.wallet.publicKey;
  if (!owner) throw new Error('Wallet not connected');

  const raydium = await getRaydium(params.connection, owner, params.wallet);
  const quoteMintStr = params.quoteCurrency === 'WSOL' ? env.wsolMint : env.getUsdcMint();
  const quoteMint = new PublicKey(quoteMintStr);
  const baseMint = params.baseMint;

  const baseDecimals = (await getMint(params.connection, baseMint)).decimals;
  const quoteDecimals = (await getMint(params.connection, quoteMint)).decimals;

  const baseAmtStr = typeof params.baseAmount === 'number' ? String(params.baseAmount) : params.baseAmount;
  const quoteAmtStr = typeof params.quoteAmount === 'number' ? String(params.quoteAmount) : params.quoteAmount;
  const baseRaw = uiAmountStringToBn('Token amount', baseAmtStr, baseDecimals);
  const quoteRaw = uiAmountStringToBn('SOL amount', quoteAmtStr, quoteDecimals);

  if (import.meta.env.DEV) {
    const baseFromDec = new Decimal(baseAmtStr.trim()).mul(new Decimal(10).pow(baseDecimals)).floor();
    console.log('[addLiquidity input STEP 2–4 service]', {
      baseAmountReceived: params.baseAmount,
      quoteAmountReceived: params.quoteAmount,
      baseDecimals,
      quoteDecimals,
      baseRaw: baseRaw.toString(),
      quoteRaw: quoteRaw.toString(),
      baseRawMatchesDecimalOnlyPath: baseRaw.toString() === baseFromDec.toFixed(0),
    });
  }

  const state = await detectPoolState(raydium, params.connection, baseMint, quoteMint, owner);

  const computeBudgetConfig = await microBudget(params.connection, [owner, baseMint, quoteMint]);

  let built: V0CpmmBuilt | Awaited<ReturnType<Raydium['cpmm']['createPool']>>;
  let poolId: string;
  let isNewPool: boolean;

  if (state.kind === 'drained') {
    if (import.meta.env.DEV) {
      console.log('[Raydium addLiquidity]', {
        poolState: state.kind,
        poolId: state.poolId,
        lpSupply: state.lpSupply.toString(),
        baseReserve: state.baseReserve.toString(),
        quoteReserve: state.quoteReserve.toString(),
        actionInstructionCount: 0,
      });
    }
    await buildSeedDrainedPoolInstructions({
      poolId: state.poolId,
      poolInfo: state.poolInfo,
      baseMint,
      quoteMint,
      baseAmount: baseRaw,
      quoteAmount: quoteRaw,
      useSOLBalance: quoteMintStr === env.wsolMint,
    });
  }

  if (state.kind === 'no_pool') {
    const cfgs = await raydium.api.getCpmmConfigs();
    const list = Array.isArray(cfgs) ? cfgs : [];
    const feeConfig = list[0];
    if (!feeConfig) throw new Error('No CPMM fee config from Raydium API');

    const mintAInfo = {
      address: baseMint.toBase58(),
      decimals: baseDecimals,
      programId: TOKEN_PROGRAM_ID.toBase58(),
    };
    const mintBInfo = {
      address: quoteMint.toBase58(),
      decimals: quoteDecimals,
      programId: TOKEN_PROGRAM_ID.toBase58(),
    };

    built = await raydium.cpmm.createPool({
      programId: cpmmProgram(),
      poolFeeAccount: cpmmFeeAccount(),
      mintA: mintAInfo,
      mintB: mintBInfo,
      mintAAmount: baseRaw,
      mintBAmount: quoteRaw,
      startTime: new BN(params.startTime ?? 0),
      feeConfig,
      associatedOnly: false,
      ownerInfo: { useSOLBalance: quoteMintStr === env.wsolMint },
      txVersion: TxVersion.V0,
      computeBudgetConfig,
    });
    poolId = built.extInfo.address.poolId.toBase58();
    isNewPool = true;
  } else if (state.kind === 'active') {
    const poolInfo = state.poolInfo;
    const vk = state.poolKeys;
    const sides = resolveBaseQuoteSides(
      poolInfo.mintA.address,
      poolInfo.mintB.address,
      state.baseReserve,
      state.quoteReserve,
      poolInfo.mintA.decimals ?? 0,
      poolInfo.mintB.decimals ?? 0,
      vk.vault.A,
      vk.vault.B,
      { userIntendedBaseMint: baseMint, wsolMint: env.wsolMint, usdcMint: env.getUsdcMint() },
    );
    const userBaseIsMintA = sides.baseMint === poolInfo.mintA.address;
    const epochInfo = await params.connection.getEpochInfo();
    const memeHuman = new Decimal(baseRaw.toString()).div(new Decimal(10).pow(baseDecimals));
    const quoteHuman = new Decimal(quoteRaw.toString()).div(new Decimal(10).pow(quoteDecimals));

    /**
     * SDK `addLiquidity` fixes one raw leg; the other follows the curve. Prefer fixing **meme** so large
     * token inputs are honored. If that implies more quote (SOL/USDC) than the user typed, the tx would
     * try to wrap/transfer far more SOL than available → instruction failures (e.g. huge `CreateAccountWithSeed`
     * funding the WSOL ATA). In that case fix the **quote** at `quoteRaw` and let the meme leg float down.
     */
    const estFromMeme = raydium.cpmm.computePairAmount({
      poolInfo,
      baseReserve: state.baseReserve,
      quoteReserve: state.quoteReserve,
      amount: memeHuman.toString(),
      slippage: slipPercent(0),
      epochInfo,
      baseIn: userBaseIsMintA,
    });

    const otherMintAddr = userBaseIsMintA ? poolInfo.mintB.address : poolInfo.mintA.address;
    const otherMintIsQuote = otherMintAddr === quoteMintStr;
    const otherRawNeeded = estFromMeme.anotherAmount.amount;

    let inputAmount: BN;
    let baseIn: boolean;
    let fixedLeg: string;

    if (otherMintIsQuote && otherRawNeeded.gt(quoteRaw)) {
      const quoteBaseIn = poolInfo.mintA.address === quoteMintStr;
      const estFromQuote = raydium.cpmm.computePairAmount({
        poolInfo,
        baseReserve: state.baseReserve,
        quoteReserve: state.quoteReserve,
        amount: quoteHuman.toString(),
        slippage: slipPercent(0),
        epochInfo,
        baseIn: quoteBaseIn,
      });
      const memeRawNeeded = estFromQuote.anotherAmount.amount;
      if (memeRawNeeded.gt(baseRaw)) {
        throw new Error(
          `For this pool, ${quoteHuman.toFixed()} ${params.quoteCurrency} requires more of your token than you entered (need ${new Decimal(memeRawNeeded.toString()).div(new Decimal(10).pow(baseDecimals)).toSignificantDigits(12)} tokens). Increase the token amount or the ${params.quoteCurrency} amount.`,
        );
      }
      inputAmount = quoteRaw;
      baseIn = quoteBaseIn;
      fixedLeg = 'quote cap (SOL/USDC); meme from curve';
      if (import.meta.env.DEV) {
        console.warn('[addLiquidity] Meme-fixed deposit would exceed user quote budget; using quote-fixed leg', {
          otherRawNeeded: otherRawNeeded.toString(),
          quoteRawCap: quoteRaw.toString(),
          memeRawMax: baseRaw.toString(),
          memeRawNeededAtQuoteCap: memeRawNeeded.toString(),
        });
      }
    } else {
      inputAmount = baseRaw;
      baseIn = userBaseIsMintA;
      fixedLeg = 'meme (base); quote from curve';
    }

    if (import.meta.env.DEV) {
      console.log('[addLiquidity input STEP 5 SDK call]', {
        inputAmount: inputAmount.toString(),
        baseIn,
        userBaseIsMintA,
        quoteRaw: quoteRaw.toString(),
        otherRawNeededFromMemeFixedEst: otherRawNeeded.toString(),
        fixedLeg,
      });
    }

    built = await raydium.cpmm.addLiquidity({
      poolInfo,
      poolKeys: state.poolKeys,
      inputAmount,
      baseIn,
      slippage: slipPercent(params.slippagePercent),
      txVersion: TxVersion.V0,
      computeBudgetConfig,
    });
    poolId = poolInfo.id;
    isNewPool = false;
  } else {
    throw new Error('Invalid CPMM pool state');
  }

  if (import.meta.env.DEV) {
    console.log('[Raydium addLiquidity]', {
      poolState: state.kind,
      poolId: state.kind === 'no_pool' ? null : state.poolId,
      lpSupply: state.kind === 'active' ? state.lpSupply.toString() : null,
      baseReserve: state.kind === 'active' ? state.baseReserve.toString() : null,
      quoteReserve: state.kind === 'active' ? state.quoteReserve.toString() : null,
      actionInstructionCount: built.builder.allInstructions.length,
    });
  }

  if (built.builder.allInstructions.length === 0) {
    throw new Error('No deposit instructions were built. Aborting to prevent fee-only transaction.');
  }

  const txId = await executeV0WithPlatformFeeGuard(built, owner, 'add_liquidity');

  let returnedPoolId = poolId;
  if (isNewPool) {
    const after = await raydium.api.fetchPoolByMints({
      mint1: baseMint.toBase58(),
      mint2: quoteMint.toBase58(),
      type: PoolFetchType.Standard,
    });
    const candidates = (after.data ?? []).filter(isCpmmStandardPool);
    const userLp = await walletLpAmountByMint(params.connection, owner, 'confirmed');
    returnedPoolId =
      candidates.length > 0 ? pickCpmmPoolForPair(candidates, userLp).id : poolId;
  }

  if (import.meta.env.DEV) {
    try {
      await logAddLiquidityPostMortem(params.connection, owner, txId, returnedPoolId, {
        intendedFeeConfigId: state.kind === 'active' ? state.poolInfo.config?.id : undefined,
      });
    } catch (e) {
      console.warn('[addLiquidity post-mortem] failed', e);
    }
  }

  return { poolId: returnedPoolId, signature: txId, isNewPool };
}

export async function removeLiquidity(params: {
  connection: Connection;
  wallet: WalletContextState;
  poolId: string;
  /** Exact LP token amount to burn (raw / smallest units), from wallet balance math — avoids UI rounding errors at 100%. */
  lpAmountRaw: string;
  slippagePercent: number;
}): Promise<{ signature: string; baseReceived: string; quoteReceived: string }> {
  const owner = params.wallet.publicKey;
  if (!owner) throw new Error('Wallet not connected');

  const raydium = await getRaydium(params.connection, owner, params.wallet);
  const { poolInfo, poolKeys } = await raydium.cpmm.getPoolInfoFromRpc(params.poolId);
  const lpRaw = new BN(params.lpAmountRaw);
  if (lpRaw.lten(0)) throw new Error('Invalid LP amount');

  const computeBudgetConfig = await microBudget(params.connection, [owner, new PublicKey(params.poolId)]);

  const built = await raydium.cpmm.withdrawLiquidity({
    poolInfo,
    poolKeys,
    lpAmount: lpRaw,
    slippage: slipPercent(params.slippagePercent),
    txVersion: TxVersion.V0,
    computeBudgetConfig,
  });

  if (built.builder.allInstructions.length === 0) {
    throw new Error('No withdraw instructions were built. Aborting to prevent fee-only transaction.');
  }
  const txId = await executeV0WithPlatformFeeGuard(built, owner, 'remove_liquidity');
  return { signature: txId, baseReceived: '—', quoteReceived: '—' };
}

function wsolUiHeld(p: UserPoolPosition): number {
  if (p.baseMint === env.wsolMint) return Number(p.baseAmount);
  if (p.quoteMint === env.wsolMint) return Number(p.quoteAmount);
  return 0;
}

/** Raydium `GET /pools/info/lps` row shape (subset). */
type LpApiRow = {
  id?: string;
  programId?: string;
  mintA?: { address?: string; symbol?: string; decimals?: number };
  mintB?: { address?: string; symbol?: string; decimals?: number };
  lpMint?: { address?: string } | string;
  tvl?: number;
};

function lpMintAddressFromPoolRow(row: LpApiRow): string | null {
  const lm = row.lpMint;
  if (typeof lm === 'string' && lm.length >= 32) return lm;
  if (lm && typeof lm === 'object' && typeof lm.address === 'string') return lm.address;
  return null;
}

/**
 * Maps batch LP lookup results to wallet mint addresses.
 * Prefer matching API rows by `lpMint` — response order is not always aligned with the query string.
 */
function mergePoolRowsIntoMap(requestedMints: string[], rows: (LpApiRow | null | undefined)[], poolByLp: Map<string, LpApiRow>) {
  const byExplicitLp = new Map<string, LpApiRow>();
  for (const row of rows) {
    if (!row?.id || !row.mintA?.address || !row.mintB?.address) continue;
    const lpAddr = lpMintAddressFromPoolRow(row);
    if (lpAddr) byExplicitLp.set(lpAddr, row);
  }
  requestedMints.forEach((mint, i) => {
    if (poolByLp.has(mint)) return;
    const hitIdx = rows[i];
    const row = byExplicitLp.get(mint) ?? (hitIdx?.id && hitIdx.mintA?.address && hitIdx.mintB?.address ? hitIdx : undefined);
    if (row?.id) poolByLp.set(mint, row);
  });
}

async function fetchLpsRows(base: string, mints: string[]): Promise<(LpApiRow | null | undefined)[]> {
  if (mints.length === 0) return [];
  const q = mints.join(',');
  const { data } = await axios.get<{ success?: boolean; data?: (LpApiRow | null)[] }>(
    `${base}/pools/info/lps?lps=${q}`,
    { timeout: 15_000 },
  );
  return data?.data ?? [];
}

async function fetchLpsRowsWithRetry(base: string, mints: string[]): Promise<(LpApiRow | null | undefined)[]> {
  let lastErr: unknown;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      return await fetchLpsRows(base, mints);
    } catch (e) {
      lastErr = e;
      await new Promise((r) => setTimeout(r, 350 * 2 ** attempt));
    }
  }
  console.warn('[Raydium] pools/info/lps batch failed after retries', mints.length, lastErr);
  return [];
}

export async function getUserPools(
  connection: Connection,
  owner: PublicKey,
  commitment: Commitment = 'confirmed',
): Promise<UserPoolPosition[]> {
  const [classic, token2022] = await Promise.all([
    connection.getParsedTokenAccountsByOwner(owner, { programId: TOKEN_PROGRAM_ID }, commitment),
    connection.getParsedTokenAccountsByOwner(owner, { programId: TOKEN_2022_PROGRAM_ID }, commitment),
  ]);

  /** Same LP mint can appear in more than one ATA; merge raw amounts for correct share math. */
  const lpByMint = new Map<string, { amountRaw: string; amountUi: number; decimals: number }>();
  for (const { account } of [...classic.value, ...token2022.value]) {
    const data = account.data as { parsed?: { info?: { mint?: string; tokenAmount?: { amount?: string; decimals?: number; uiAmount?: number } } } };
    const mint = data.parsed?.info?.mint;
    const dec = data.parsed?.info?.tokenAmount?.decimals ?? 0;
    const raw = data.parsed?.info?.tokenAmount?.amount ?? '0';
    const ui = Number(data.parsed?.info?.tokenAmount?.uiAmount ?? 0);
    if (!mint || ui <= 0) continue;
    const prev = lpByMint.get(mint);
    if (!prev) {
      lpByMint.set(mint, { amountRaw: raw, amountUi: ui, decimals: dec });
    } else {
      lpByMint.set(mint, {
        amountRaw: new BN(prev.amountRaw).add(new BN(raw)).toString(10),
        amountUi: prev.amountUi + ui,
        decimals: dec,
      });
    }
  }

  const lpMints = [...lpByMint.entries()].map(([mint, v]) => ({ mint, ...v }));
  if (!lpMints.length) return [];

  const apiBase = raydiumApiV3Base();
  const poolByLp = new Map<string, LpApiRow>();
  const chunks: string[][] = [];
  for (let i = 0; i < lpMints.length; i += 20) {
    chunks.push(lpMints.slice(i, i + 20).map((x) => x.mint));
  }

  for (const group of chunks) {
    const rows = await fetchLpsRowsWithRetry(apiBase, group);
    mergePoolRowsIntoMap(group, rows, poolByLp);
  }

  for (const row of lpMints) {
    if (poolByLp.has(row.mint)) continue;
    const rows = await fetchLpsRowsWithRetry(apiBase, [row.mint]);
    mergePoolRowsIntoMap([row.mint], rows, poolByLp);
  }

  const positions: UserPoolPosition[] = [];
  const priceIds = new Set<string>();

  let rayReadonly: Raydium | null = null;
  try {
    const dummy = Keypair.generate();
    rayReadonly = await Raydium.load({
      connection,
      owner: dummy.publicKey,
      signAllTransactions: (async (txs) => txs) as NonNullable<RaydiumLoadParams['signAllTransactions']>,
      cluster: env.raydiumCluster,
      disableFeatureCheck: true,
      disableLoadToken: true,
    });
  } catch {
    rayReadonly = null;
  }

  const cpmmPid = cpmmProgram().toBase58();

  for (const row of lpMints) {
    const pool = poolByLp.get(row.mint);
    if (!pool?.id || !pool.mintA?.address || !pool.mintB?.address) continue;
    if (pool.programId && pool.programId !== cpmmPid) continue;

    if (!rayReadonly) continue;

    let rpc: Awaited<ReturnType<Raydium['cpmm']['getRpcPoolInfo']>> | null = null;
    try {
      rpc = await rayReadonly.cpmm.getRpcPoolInfo(pool.id, true);
    } catch {
      continue;
    }
    if (!rpc) continue;

    priceIds.add(rpc.mintA.toBase58());
    priceIds.add(rpc.mintB.toBase58());

    const lpTotal = new BN(rpc.lpAmount.toString());
    const userLp = new BN(row.amountRaw);
    const hasUserLp = !userLp.isZero();
    const isDrained = hasUserLp && (rpc.baseReserve.isZero() || rpc.quoteReserve.isZero());
    const share = lpTotal.isZero() ? 0 : userLp.muln(10000).div(lpTotal).toNumber() / 100;

    const sides = resolveBaseQuoteSides(
      rpc.mintA.toBase58(),
      rpc.mintB.toBase58(),
      rpc.baseReserve,
      rpc.quoteReserve,
      rpc.mintDecimalA,
      rpc.mintDecimalB,
      rpc.vaultA.toBase58(),
      rpc.vaultB.toBase58(),
      { wsolMint: env.wsolMint, usdcMint: env.getUsdcMint() },
    );

    const baseAmt = new Decimal(sides.baseReserve.toString())
      .mul(userLp.toString())
      .div(lpTotal.toString())
      .div(10 ** sides.baseDecimals);
    const quoteAmt = new Decimal(sides.quoteReserve.toString())
      .mul(userLp.toString())
      .div(lpTotal.toString())
      .div(10 ** sides.quoteDecimals);

    if (import.meta.env.DEV) {
      const [vaultABalance, vaultBBalance] = await Promise.all([
        connection.getTokenAccountBalance(rpc.vaultA, commitment),
        connection.getTokenAccountBalance(rpc.vaultB, commitment),
      ]);
      console.log('[getUserPools vault diagnostics]', {
        poolId: pool.id,
        mintA: rpc.mintA.toBase58(),
        mintB: rpc.mintB.toBase58(),
        vaultA: rpc.vaultA.toBase58(),
        vaultB: rpc.vaultB.toBase58(),
        rawVaultA: vaultABalance.value.uiAmount,
        rawVaultB: vaultBBalance.value.uiAmount,
        wsolMint: env.wsolMint,
        isWsolMintA: rpc.mintA.toBase58() === env.wsolMint,
        isWsolMintB: rpc.mintB.toBase58() === env.wsolMint,
        semanticBaseMint: sides.baseMint,
        semanticQuoteMint: sides.quoteMint,
        computedBaseAmount: baseAmt.toFixed(6),
        computedQuoteAmount: quoteAmt.toFixed(6),
      });
    }

    positions.push({
      poolId: pool.id,
      baseMint: sides.baseMint,
      quoteMint: sides.quoteMint,
      baseSymbol: symbolForMintFromRow(pool, sides.baseMint),
      quoteSymbol: symbolForMintFromRow(pool, sides.quoteMint),
      lpMint: row.mint,
      lpAmount: row.amountUi.toFixed(4),
      lpAmountRaw: row.amountRaw,
      sharePercent: share,
      baseAmount: baseAmt.toFixed(6),
      quoteAmount: quoteAmt.toFixed(6),
      baseUsdValue: 0,
      quoteUsdValue: 0,
      totalUsdValue: 0,
      poolTvlUsd: typeof pool.tvl === 'number' ? pool.tvl : 0,
      isDrained,
    });
  }

  const prices = await getMultipleTokenPricesUsd([...priceIds, env.wsolMint]);
  const solUsd = prices[env.wsolMint] ?? 0;
  for (const p of positions) {
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
        /** Pair leg has no USD oracle (e.g. fresh meme); still show SOL side of the position. */
        p.totalSolEquivalent = solUi;
      }
    } else if (solUi > 0) {
      p.totalSolEquivalent = solUi;
    }
  }

  positions.sort((a, b) => b.totalUsdValue - a.totalUsdValue);
  return positions;
}
