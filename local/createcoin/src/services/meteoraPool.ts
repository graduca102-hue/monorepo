import {
  ActivationType,
  BaseFeeMode,
  CollectFeeMode,
  CP_AMM_PROGRAM_ID,
  CpAmm,
  DepositTokenNotAcceptedError,
  cpAmmCoder,
  deriveCustomizablePoolAddress,
  derivePositionAddress,
  MAX_SQRT_PRICE,
  MIN_SQRT_PRICE,
  derivePositionNftAccount,
  calculateInitSqrtPrice,
  calculateTransferFeeIncludedAmount,
  getAmountWithSlippage,
  getBaseFeeParams,
  getCurrentPoint,
  getFirstKey,
  getLiquidityDeltaFromAmountA,
  getLiquidityDeltaFromAmountB,
  getSecondKey,
  getTokenProgram,
  SwapMode,
  type VestingState,
} from '@meteora-ag/cp-amm-sdk';
import {
  NATIVE_MINT,
  ACCOUNT_SIZE,
  MINT_SIZE,
  getAccount,
  getAssociatedTokenAddressSync,
  getMint,
  getTransferFeeConfig,
  TOKEN_2022_PROGRAM_ID,
  type Mint,
} from '@solana/spl-token';
import {
  Connection,
  Keypair,
  LAMPORTS_PER_SOL,
  PublicKey,
  SendTransactionError,
  Transaction,
} from '@solana/web3.js';
import type { WalletContextState } from '@solana/wallet-adapter-react';
import BN from 'bn.js';
import bs58 from 'bs58';
import { buildFeeTransferInstruction } from './feeService';
import { buildComputeBudgetInstructions, getDynamicPriorityFee } from './priorityFeeService';
import {
  assertLegacyTransactionSimulationOk,
  confirmTransactionWithBackgroundFallback,
} from './solanaTxHelpers';
import { env } from '../config/env';

/** Enough CU for Meteora pool init / new position + wrap SOL + platform fee ix. */
const METEORA_POOL_TX_COMPUTE_UNITS = 2_000_000;

/** Anchor 8-byte account discriminator. */
const ANCHOR_ACCOUNT_DISCRIMINATOR_SIZE = 8;

/**
 * Serialized account data lengths from Meteora `damm-v2` (`programs/cp-amm/src/state/pool.rs`,
 * `position.rs`): `const_assert_eq!(Pool::INIT_SPACE, 1104)`, `const_assert_eq!(Position::INIT_SPACE, 400)`.
 */
const METEORA_POOL_ACCOUNT_DATA_SIZE = ANCHOR_ACCOUNT_DISCRIMINATOR_SIZE + 1104;
const METEORA_POSITION_ACCOUNT_DATA_SIZE = ANCHOR_ACCOUNT_DISCRIMINATOR_SIZE + 400;

/** Small cushion for auxiliary accounts / rounding (not a duplicate of on-chain rent). */
const METEORA_POOL_CREATE_OVERHEAD_BUFFER_LAMPORTS = 25_000;
const HELIUS_GPA_V2_PAGE_LIMIT = 5000;
const METEORA_VESTING_ACCOUNT_DISCRIMINATOR = bs58.encode(
  Uint8Array.from([100, 149, 66, 138, 95, 200, 128, 241]),
);
const GPA_OVERLOAD_MARKERS = ['account index service overloaded', 'getprogramaccountsv2'];

type MeteoraVesting = { account: PublicKey; vestingState: VestingState };

function isGpaOverloadError(err: unknown): boolean {
  const msg = (err instanceof Error ? err.message : String(err)).toLowerCase();
  return GPA_OVERLOAD_MARKERS.some((marker) => msg.includes(marker));
}

function canUseHeliusV2(connection: Connection): boolean {
  try {
    const endpoint = connection.rpcEndpoint;
    if (typeof endpoint !== 'string') return false;
    return new URL(endpoint).hostname.includes('helius');
  } catch {
    return false;
  }
}

async function fetchMeteoraVestingsByPositionV2(
  connection: Connection,
  position: PublicKey,
): Promise<MeteoraVesting[]> {
  let paginationKey: string | null | undefined = null;
  const vestings: MeteoraVesting[] = [];

  do {
    const config = {
      encoding: 'base64',
      commitment: 'confirmed',
      limit: HELIUS_GPA_V2_PAGE_LIMIT,
      filters: [
        { memcmp: { offset: 0, bytes: METEORA_VESTING_ACCOUNT_DISCRIMINATOR } },
        { memcmp: { offset: ANCHOR_ACCOUNT_DISCRIMINATOR_SIZE, bytes: position.toBase58() } },
      ],
      ...(paginationKey ? { paginationKey } : {}),
    };

    const res = await fetch(connection.rpcEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: `meteora-vesting-${Date.now()}`,
        method: 'getProgramAccountsV2',
        params: [CP_AMM_PROGRAM_ID.toBase58(), config],
      }),
    });

    const json = (await res.json()) as {
      error?: { message?: string; code?: number };
      result?: {
        accounts?: Array<{ pubkey: string; account: { data: [string, string] } }>;
        paginationKey?: string | null;
      };
    };

    if (json.error) {
      throw new Error(json.error.message ?? `getProgramAccountsV2 failed (${json.error.code ?? 'unknown'})`);
    }

    const page = json.result;
    if (!page) throw new Error('getProgramAccountsV2 returned no result');

    for (const row of page.accounts ?? []) {
      const data = Buffer.from(row.account.data[0], 'base64');
      const vestingState = cpAmmCoder.accounts.decode('vesting', data) as VestingState;
      vestings.push({ account: new PublicKey(row.pubkey), vestingState });
    }

    paginationKey = page.paginationKey ?? null;
  } while (paginationKey);

  return vestings;
}

async function fetchMeteoraVestingsByPosition(
  cpAmm: CpAmm,
  connection: Connection,
  position: PublicKey,
): Promise<MeteoraVesting[]> {
  try {
    const vestings = await cpAmm.getAllVestingsByPosition(position);
    return vestings.map((v) => ({ account: v.publicKey, vestingState: v.account }));
  } catch (err) {
    if (!isGpaOverloadError(err) || !canUseHeliusV2(connection)) throw err;
    return fetchMeteoraVestingsByPositionV2(connection, position);
  }
}

export type MeteoraCustomPoolCreateOverheadEstimate = {
  newAccountRentLamports: number;
  priorityFeeLamports: number;
  baseSignatureFeeLamports: number;
  overheadBufferLamports: number;
  totalOverheadLamports: number;
};

/**
 * Pre-flight SOL required beyond platform fee + user SOL seed: rent-exempt minimums for accounts created in
 * `initialize_customizable_pool`, estimated priority fee (same CU + fee heuristic as the real tx), and base signature fee.
 *
 * @param includeWsolAtaRent — When true (default), includes one SPL token-account rent for a possibly new wSOL ATA (upper bound).
 */
export async function estimateMeteoraCustomPoolCreateOverheadLamports(
  connection: Connection,
  params: {
    payer: PublicKey;
    baseTokenMint: PublicKey;
    includeWsolAtaRent?: boolean;
  },
): Promise<MeteoraCustomPoolCreateOverheadEstimate> {
  const includeWsol = params.includeWsolAtaRent !== false;
  const pool = deriveExpectedCustomizablePoolPda(params.baseTokenMint);
  const accountSizes = [
    METEORA_POOL_ACCOUNT_DATA_SIZE,
    METEORA_POSITION_ACCOUNT_DATA_SIZE,
    MINT_SIZE,
    ACCOUNT_SIZE,
    ACCOUNT_SIZE,
    ACCOUNT_SIZE,
  ];
  if (includeWsol) {
    accountSizes.push(ACCOUNT_SIZE);
  }
  const rents = await Promise.all(
    accountSizes.map((dataLen) => connection.getMinimumBalanceForRentExemption(dataLen)),
  );
  const newAccountRentLamports = rents.reduce((sum, x) => sum + x, 0);

  const micro = await getDynamicPriorityFee(connection, [params.payer, pool]);
  const priorityFeeLamports = Math.ceil((METEORA_POOL_TX_COMPUTE_UNITS * micro) / 1_000_000);
  const baseSignatureFeeLamports = 5_000;
  const overheadBufferLamports = METEORA_POOL_CREATE_OVERHEAD_BUFFER_LAMPORTS;
  const totalOverheadLamports =
    newAccountRentLamports + priorityFeeLamports + baseSignatureFeeLamports + overheadBufferLamports;

  return {
    newAccountRentLamports,
    priorityFeeLamports,
    baseSignatureFeeLamports,
    overheadBufferLamports,
    totalOverheadLamports,
  };
}

async function prependComputeBudgetAndPlatformFee(
  connection: Connection,
  feeIx: ReturnType<typeof buildFeeTransferInstruction>,
  priorityWritableAccounts: PublicKey[],
  tx: Transaction,
): Promise<void> {
  const micro = await getDynamicPriorityFee(connection, priorityWritableAccounts);
  const budgetIxs = buildComputeBudgetInstructions(METEORA_POOL_TX_COMPUTE_UNITS, micro);
  const prefix = [...budgetIxs, ...(feeIx ? [feeIx] : [])];
  tx.instructions = [...prefix, ...tx.instructions];
}

/**
 * DAMM v2 customizable pools derive the pool PDA from the sorted mint pair only (`cpool`, tokenA, tokenB).
 * There is no separate config/fee-tier seed; one on-chain pool exists per pair for this program path.
 */
export const METEORA_SUPPORTS_MULTIPLE_POOL_CONFIGS_PER_PAIR = false;

export type PoolAlreadyExistsAction = 'add_liquidity';

/** Thrown when the derived customizable pool account already exists; UI should offer add-liquidity instead of init. */
export class PoolAlreadyExistsError extends Error {
  override readonly name = 'PoolAlreadyExistsError';
  readonly poolAddress: PublicKey;
  readonly action: PoolAlreadyExistsAction;

  constructor(opts: { poolAddress: PublicKey; message?: string; action?: PoolAlreadyExistsAction }) {
    super(
      opts.message ??
        'A pool for this token pair already exists. You can add liquidity to it instead of creating a new one.',
    );
    this.poolAddress = opts.poolAddress;
    this.action = opts.action ?? 'add_liquidity';
  }
}

/** Expected customizable pool PDA for `baseTokenMint` vs wrapped SOL (mint order sorted by SDK). */
export function deriveExpectedCustomizablePoolPda(baseTokenMint: PublicKey): PublicKey {
  const wsolMint = new PublicKey(env.wsolMint);
  const tokenAMint = new PublicKey(getFirstKey(baseTokenMint, wsolMint));
  const tokenBMint = new PublicKey(getSecondKey(baseTokenMint, wsolMint));
  return deriveCustomizablePoolAddress(tokenAMint, tokenBMint);
}

function unwrapWalletSendError(err: unknown): unknown {
  if (err instanceof SendTransactionError) return err;
  if (err && typeof err === 'object') {
    const o = err as { cause?: unknown; message?: string; logs?: string[] };
    if (o.cause instanceof SendTransactionError) return o.cause;
    if (Array.isArray(o.logs) && o.logs.length) {
      const last = [...o.logs].reverse().find((l) => typeof l === 'string' && l.includes('Program log:'));
      if (last) return new Error(last.replace(/^Program log:\s*/i, '').trim());
    }
    const msg = typeof o.message === 'string' ? o.message : '';
    const sigMatch = /Transaction simulation failed: (.+)/i.exec(msg)?.[1];
    if (sigMatch) return new Error(sigMatch);
  }
  return err;
}

function mapMeteoraError(err: unknown): string {
  if (err instanceof PoolAlreadyExistsError) return err.message;
  const msg = err instanceof Error ? err.message : String(err);
  const lower = msg.toLowerCase();
  // Simulation / logs often mention InstructionError or hex codes — never replace those with a generic line.
  if (
    lower.includes('simulation failed') ||
    lower.includes('program log:') ||
    lower.includes('instructionerror')
  ) {
    return msg.length > 650 ? `${msg.slice(0, 647)}…` : msg;
  }
  if (lower.includes('insufficient') && lower.includes('sol')) return msg;
  if (lower.includes('insufficient') && lower.includes('fund')) {
    return 'Insufficient SOL for this transaction (rent, wrapping, and network fees). Add SOL and try again.';
  }
  if (lower.includes('slippage') || lower.includes('exceed')) {
    return 'Amount error or slippage: try slightly different amounts or check the minimum liquidity rules.';
  }
  if (lower.includes('blockhash') || lower.includes('expired')) {
    return 'Transaction expired. Confirm your RPC connection and try again.';
  }
  if (lower.includes('user rejected') || lower.includes('cancel')) {
    return 'Transaction was cancelled in the wallet.';
  }
  if (lower.includes('already in use') || lower.includes('already initialized')) {
    return 'This token pair already has a pool on-chain. Use “Add liquidity to existing pool” instead of creating a new pool.';
  }
  return msg.length > 220 ? `${msg.slice(0, 217)}…` : msg;
}

async function tokenProgramId(connection: Connection, mint: PublicKey): Promise<PublicKey> {
  const ai = await connection.getAccountInfo(mint, 'confirmed');
  if (!ai?.owner) throw new Error(`Mint account not found: ${mint.toBase58()}`);
  return ai.owner;
}

function bnMin(a: BN, b: BN): BN {
  return a.lt(b) ? a : b;
}

/**
 * Mint + epoch for Meteora pool math when Token-2022 transfer fees reduce vault credits vs gross debits.
 */
async function poolCreationTransferFeeMintInfo(
  connection: Connection,
  mint: PublicKey,
  tokenProgram: PublicKey,
): Promise<{ mint: Mint; currentEpoch: number } | undefined> {
  if (!tokenProgram.equals(TOKEN_2022_PROGRAM_ID)) return undefined;
  const m = await getMint(connection, mint, 'confirmed', tokenProgram);
  if (!getTransferFeeConfig(m)) return undefined;
  const { epoch } = await connection.getEpochInfo('confirmed');
  return { mint: m, currentEpoch: epoch };
}

/** SPL credits that land in pool vaults after Token-2022 transfer fees (WSOL/classic SPL unchanged). */
function vaultCreditsAfterTransferFee(
  grossTokenA: BN,
  grossTokenB: BN,
  infoA: { mint: Mint; currentEpoch: number } | undefined,
  infoB: { mint: Mint; currentEpoch: number } | undefined,
): { vaultA: BN; vaultB: BN } {
  const feeA =
    infoA != null
      ? calculateTransferFeeIncludedAmount(grossTokenA, infoA.mint, infoA.currentEpoch).transferFee
      : new BN(0);
  const feeB =
    infoB != null
      ? calculateTransferFeeIncludedAmount(grossTokenB, infoB.mint, infoB.currentEpoch).transferFee
      : new BN(0);
  return {
    vaultA: grossTokenA.sub(feeA),
    vaultB: grossTokenB.sub(feeB),
  };
}

/** Never deposit more meme raw lamports than the owner's ATA holds (fixes UI decimal mismatch & Token-2022 quirks). */
async function clampMemeDepositAmountForSortedPair(params: {
  connection: Connection;
  owner: PublicKey;
  memeMint: PublicKey;
  tokenAMint: PublicKey;
  tokenBMint: PublicKey;
  tokenAAmount: BN;
  tokenBAmount: BN;
}): Promise<{ tokenAAmount: BN; tokenBAmount: BN }> {
  const prog = await tokenProgramId(params.connection, params.memeMint);
  const ata = getAssociatedTokenAddressSync(params.memeMint, params.owner, false, prog);
  const acc = await getAccount(params.connection, ata, 'confirmed', prog);
  const bal = new BN(acc.amount.toString());
  if (params.tokenAMint.equals(params.memeMint)) {
    return { tokenAAmount: BN.min(params.tokenAAmount, bal), tokenBAmount: params.tokenBAmount };
  }
  return { tokenAAmount: params.tokenAAmount, tokenBAmount: BN.min(params.tokenBAmount, bal) };
}

export type CreateDammV2PoolResult = {
  poolAddress: PublicKey;
  lpMint: PublicKey;
  txSignature: string;
  position: PublicKey;
  /** Always true when this helper completes pool initialization (existing pools raise {@link PoolAlreadyExistsError}). */
  createdNewPool: boolean;
};

/**
 * Open a new DAMM v2 position on an existing customizable pool (same tx pattern as first-time add after pool exists).
 */
export async function addLiquidityToExistingMeteoraPool(params: {
  connection: Connection;
  wallet: WalletContextState;
  poolAddress: PublicKey;
  baseTokenMint: PublicKey;
  baseTokenAmount: BN;
  solAmount: BN;
}): Promise<{ lpMint: PublicKey; txSignature: string; position: PublicKey }> {
  const wsolMint = new PublicKey(env.wsolMint);
  if (!wsolMint.equals(NATIVE_MINT)) {
    throw new Error('VITE_WSOL_MINT must be the standard wrapped SOL mint (So111…).');
  }
  const memeMint = params.baseTokenMint;
  if (memeMint.equals(wsolMint)) {
    throw new Error('Base token must not be wrapped SOL.');
  }

  const tokenAMint = new PublicKey(getFirstKey(memeMint, wsolMint));
  const tokenBMint = new PublicKey(getSecondKey(memeMint, wsolMint));

  let tokenAAmount: BN;
  let tokenBAmount: BN;
  if (tokenAMint.equals(memeMint)) {
    tokenAAmount = params.baseTokenAmount;
    tokenBAmount = params.solAmount;
  } else {
    tokenAAmount = params.solAmount;
    tokenBAmount = params.baseTokenAmount;
  }

  const expected = deriveCustomizablePoolAddress(tokenAMint, tokenBMint);
  if (!expected.equals(params.poolAddress)) {
    throw new Error('Pool address does not match this token pair.');
  }

  const [tokenAProgram, tokenBProgram] = await Promise.all([
    tokenProgramId(params.connection, tokenAMint),
    tokenProgramId(params.connection, tokenBMint),
  ]);

  return addLiquidityNewPositionExistingPool({
    connection: params.connection,
    wallet: params.wallet,
    pool: params.poolAddress,
    tokenAMint,
    tokenBMint,
    tokenAAmount,
    tokenBAmount,
    tokenAProgram,
    tokenBProgram,
  });
}

async function addLiquidityNewPositionExistingPool(params: {
  connection: Connection;
  wallet: WalletContextState;
  pool: PublicKey;
  tokenAMint: PublicKey;
  tokenBMint: PublicKey;
  tokenAAmount: BN;
  tokenBAmount: BN;
  tokenAProgram: PublicKey;
  tokenBProgram: PublicKey;
}): Promise<{ lpMint: PublicKey; txSignature: string; position: PublicKey }> {
  const owner = params.wallet.publicKey;
  if (!owner) throw new Error('Connect your wallet first.');
  if (!params.wallet.sendTransaction) {
    throw new Error('Your wallet cannot send transactions.');
  }

  const cpAmm = new CpAmm(params.connection);
  const poolState = await cpAmm.fetchPoolState(params.pool);

  /** Single-sided deposit quote matches how Meteora computes paired amounts at the current sqrt price. */
  const memeMint = params.tokenAMint.equals(NATIVE_MINT) ? params.tokenBMint : params.tokenAMint;
  const memeIsA = params.tokenAMint.equals(memeMint);

  let tokenAAmount = params.tokenAAmount;
  let tokenBAmount = params.tokenBAmount;
  const capped = await clampMemeDepositAmountForSortedPair({
    connection: params.connection,
    owner,
    memeMint,
    tokenAMint: params.tokenAMint,
    tokenBMint: params.tokenBMint,
    tokenAAmount,
    tokenBAmount,
  });
  tokenAAmount = capped.tokenAAmount;
  tokenBAmount = capped.tokenBAmount;
  if (memeIsA ? tokenAAmount.lten(0) : tokenBAmount.lten(0)) {
    throw new Error('Insufficient meme token balance for this deposit.');
  }

  let liquidityDelta: BN;
  try {
    const quote = cpAmm.getDepositQuote({
      inAmount: memeIsA ? tokenAAmount : tokenBAmount,
      isTokenA: memeIsA,
      inputTokenInfo: undefined,
      outputTokenInfo: undefined,
      minSqrtPrice: poolState.sqrtMinPrice,
      maxSqrtPrice: poolState.sqrtMaxPrice,
      sqrtPrice: poolState.sqrtPrice,
      collectFeeMode: poolState.collectFeeMode,
      tokenAAmount: poolState.tokenAAmount,
      tokenBAmount: poolState.tokenBAmount,
      liquidity: poolState.liquidity,
    });

    const otherMax = memeIsA ? tokenBAmount : tokenAAmount;
    if (quote.outputAmount.gt(otherMax)) {
      const pairedMintIsSol = memeIsA ? params.tokenBMint.equals(NATIVE_MINT) : params.tokenAMint.equals(NATIVE_MINT);
      if (pairedMintIsSol) {
        const needSol = quote.outputAmount.toNumber() / LAMPORTS_PER_SOL;
        const maxSol = otherMax.toNumber() / LAMPORTS_PER_SOL;
        throw new Error(
          `At this pool price, that token amount needs about ${needSol.toFixed(4)} SOL paired liquidity; you allowed ${maxSol.toFixed(4)} SOL. Increase SOL or reduce tokens.`,
        );
      }
      throw new Error(
        'At this pool price, the paired amount exceeds what you entered. Reduce the token amount or increase the paired side.',
      );
    }

    liquidityDelta = quote.liquidityDelta;
  } catch (e) {
    if (e instanceof DepositTokenNotAcceptedError) {
      throw new Error(
        'Pool price is at the edge of the tradable range, so this side cannot be deposited right now. Try a smaller amount or wait until price moves back in range.',
      );
    }
    throw e;
  }

  if (liquidityDelta.lten(0)) {
    throw new Error(
      'Could not add liquidity with these amounts. Try slightly larger amounts or adjust the ratio to match the pool price.',
    );
  }

  const positionNft = Keypair.generate();

  /** On-chain `add_liquidity` requires `total_amount_{a,b} <= threshold` — thresholds are MAX debits (see Meteora `ix_add_liquidity.rs`). */
  const tokenAAmountThreshold = tokenAAmount;
  const tokenBAmountThreshold = tokenBAmount;

  try {
    const tx = await cpAmm.createPositionAndAddLiquidity({
      owner,
      pool: params.pool,
      positionNft: positionNft.publicKey,
      liquidityDelta,
      maxAmountTokenA: tokenAAmount,
      maxAmountTokenB: tokenBAmount,
      tokenAAmountThreshold,
      tokenBAmountThreshold,
      tokenAMint: params.tokenAMint,
      tokenBMint: params.tokenBMint,
      tokenAProgram: params.tokenAProgram,
      tokenBProgram: params.tokenBProgram,
    });

    const feeIx = buildFeeTransferInstruction(owner, 'add_liquidity');
    await prependComputeBudgetAndPlatformFee(
      params.connection,
      feeIx,
      [owner, params.pool],
      tx,
    );

    tx.feePayer = owner;
    const latest = await params.connection.getLatestBlockhash('confirmed');
    tx.recentBlockhash = latest.blockhash;

    await assertLegacyTransactionSimulationOk(params.connection, tx, [positionNft]);

    const txSignature = await params.wallet.sendTransaction(tx, params.connection, {
      signers: [positionNft],
      skipPreflight: true,
      maxRetries: 3,
    });

    await confirmTransactionWithBackgroundFallback(
      params.connection,
      { signature: txSignature, blockhash: latest.blockhash, lastValidBlockHeight: latest.lastValidBlockHeight },
      'confirmed',
    );

    const position = derivePositionAddress(positionNft.publicKey);
    return { lpMint: positionNft.publicKey, txSignature, position };
  } catch (e) {
    if (e instanceof PoolAlreadyExistsError) throw e;
    const inner = unwrapWalletSendError(e);
    if (inner instanceof PoolAlreadyExistsError) throw inner;
    if (inner instanceof SendTransactionError) {
      const logs = await inner.getLogs(params.connection).catch(() => undefined);
      const extra = logs?.length ? ` (${logs[logs.length - 1] ?? ''})` : '';
      throw new Error(mapMeteoraError(new Error(`${inner.message}${extra}`)));
    }
    throw new Error(mapMeteoraError(inner));
  }
}

/**
 * Meteora DAMM v2 customizable pool: user memecoin vs wrapped SOL (native mint).
 * If a customizable pool for this pair already exists, throws {@link PoolAlreadyExistsError} (UI should offer add liquidity).
 */
export async function createDammV2Pool(params: {
  connection: Connection;
  wallet: WalletContextState;
  baseTokenMint: PublicKey;
  baseTokenAmount: BN;
  solAmount: BN;
  feeBps?: number;
}): Promise<CreateDammV2PoolResult> {
  const owner = params.wallet.publicKey;
  if (!owner) throw new Error('Connect your wallet first.');
  if (!params.wallet.sendTransaction) {
    throw new Error('Your wallet cannot send transactions.');
  }

  const wsolMint = new PublicKey(env.wsolMint);
  if (!wsolMint.equals(NATIVE_MINT)) {
    throw new Error('VITE_WSOL_MINT must be the standard wrapped SOL mint (So111…).');
  }

  const memeMint = params.baseTokenMint;
  if (memeMint.equals(wsolMint)) {
    throw new Error('Base token must not be wrapped SOL.');
  }

  if (params.baseTokenAmount.lten(0)) {
    throw new Error('Initial token amount must be greater than zero.');
  }
  if (params.solAmount.lten(0)) {
    throw new Error('Initial SOL amount must be greater than zero.');
  }

  const feeBps = params.feeBps ?? 100;
  if (!Number.isFinite(feeBps) || feeBps < 1 || feeBps > 20_000) {
    throw new Error('Pool fee must be between 1 and 20000 basis points.');
  }

  const tokenAMint = new PublicKey(getFirstKey(memeMint, wsolMint));
  const tokenBMint = new PublicKey(getSecondKey(memeMint, wsolMint));

  let tokenAAmount: BN;
  let tokenBAmount: BN;
  if (tokenAMint.equals(memeMint)) {
    tokenAAmount = params.baseTokenAmount;
    tokenBAmount = params.solAmount;
  } else {
    tokenAAmount = params.solAmount;
    tokenBAmount = params.baseTokenAmount;
  }

  const [tokenAProgram, tokenBProgram] = await Promise.all([
    tokenProgramId(params.connection, tokenAMint),
    tokenProgramId(params.connection, tokenBMint),
  ]);

  const { decimals: decB } = await getMint(params.connection, tokenBMint, 'confirmed', tokenBProgram);

  const cappedCreate = await clampMemeDepositAmountForSortedPair({
    connection: params.connection,
    owner,
    memeMint,
    tokenAMint,
    tokenBMint,
    tokenAAmount,
    tokenBAmount,
  });
  tokenAAmount = cappedCreate.tokenAAmount;
  tokenBAmount = cappedCreate.tokenBAmount;
  if (tokenAMint.equals(memeMint) ? tokenAAmount.lten(0) : tokenBAmount.lten(0)) {
    throw new Error('Insufficient meme token balance for this deposit.');
  }

  const expectedPoolPda = deriveCustomizablePoolAddress(tokenAMint, tokenBMint);
  const existingPool = await params.connection.getAccountInfo(expectedPoolPda, 'confirmed');
  const poolAlreadyInitialized =
    existingPool != null && existingPool.data.length > 0 && existingPool.owner.equals(CP_AMM_PROGRAM_ID);

  if (poolAlreadyInitialized) {
    throw new PoolAlreadyExistsError({
      poolAddress: expectedPoolPda,
      message:
        'A pool for this token pair already exists. You can add liquidity to it instead of creating a new one.',
      action: 'add_liquidity',
    });
  }

  const cpAmm = new CpAmm(params.connection);

  const [transferFeeMintInfoA, transferFeeMintInfoB] = await Promise.all([
    poolCreationTransferFeeMintInfo(params.connection, tokenAMint, tokenAProgram),
    poolCreationTransferFeeMintInfo(params.connection, tokenBMint, tokenBProgram),
  ]);
  const { vaultA, vaultB } = vaultCreditsAfterTransferFee(
    tokenAAmount,
    tokenBAmount,
    transferFeeMintInfoA,
    transferFeeMintInfoB,
  );
  if (vaultA.lten(0) || vaultB.lten(0)) {
    throw new Error(
      'Deposit amount is too small after token transfer fees. Try slightly larger token and SOL amounts.',
    );
  }
  /**
   * `preparePoolCreationParams` uses gross amounts for `initSqrtPrice` but net legs for liquidity when fee info
   * is passed; without fee info, vault credits are lower than assumed for Token-2022 + transfer fee mints,
   * which triggers on-chain ExceededSlippage (6002). Price + liquidity must both derive from vault credits.
   */
  const initSqrtPrice = calculateInitSqrtPrice(vaultA, vaultB, MIN_SQRT_PRICE, MAX_SQRT_PRICE);
  const liquidityDelta = bnMin(
    getLiquidityDeltaFromAmountA(vaultA, initSqrtPrice, MAX_SQRT_PRICE, CollectFeeMode.BothToken),
    getLiquidityDeltaFromAmountB(vaultB, MIN_SQRT_PRICE, initSqrtPrice, CollectFeeMode.BothToken),
  );

  const baseFee = getBaseFeeParams(
    {
      baseFeeMode: BaseFeeMode.FeeTimeSchedulerLinear,
      feeTimeSchedulerParam: {
        startingFeeBps: feeBps,
        endingFeeBps: feeBps,
        numberOfPeriod: 0,
        totalDuration: 0,
      },
    },
    decB,
    ActivationType.Slot,
  );

  const positionNft = Keypair.generate();

  try {
    const { tx, pool, position } = await cpAmm.createCustomPool({
      payer: owner,
      creator: owner,
      positionNft: positionNft.publicKey,
      tokenAMint,
      tokenBMint,
      tokenAAmount,
      tokenBAmount,
      sqrtMinPrice: MIN_SQRT_PRICE,
      sqrtMaxPrice: MAX_SQRT_PRICE,
      initSqrtPrice,
      liquidityDelta,
      poolFees: {
        baseFee,
        compoundingFeeBps: 0,
        padding: 0,
        dynamicFee: null,
      },
      hasAlphaVault: false,
      collectFeeMode: CollectFeeMode.BothToken,
      activationPoint: null,
      activationType: ActivationType.Slot,
      tokenAProgram,
      tokenBProgram,
      isLockLiquidity: false,
    });

    const feeIx = buildFeeTransferInstruction(owner, 'add_liquidity');
    await prependComputeBudgetAndPlatformFee(params.connection, feeIx, [owner, pool], tx);

    tx.feePayer = owner;
    const latest = await params.connection.getLatestBlockhash('confirmed');
    tx.recentBlockhash = latest.blockhash;

    await assertLegacyTransactionSimulationOk(params.connection, tx, [positionNft]);

    const txSignature = await params.wallet.sendTransaction(tx, params.connection, {
      signers: [positionNft],
      skipPreflight: true,
      maxRetries: 3,
    });

    await confirmTransactionWithBackgroundFallback(
      params.connection,
      { signature: txSignature, blockhash: latest.blockhash, lastValidBlockHeight: latest.lastValidBlockHeight },
      'confirmed',
    );

    return {
      poolAddress: pool,
      lpMint: positionNft.publicKey,
      txSignature,
      position,
      createdNewPool: true,
    };
  } catch (e) {
    if (e instanceof PoolAlreadyExistsError) throw e;
    const inner = unwrapWalletSendError(e);
    if (inner instanceof PoolAlreadyExistsError) throw inner;
    if (inner instanceof SendTransactionError) {
      const logs = await inner.getLogs(params.connection).catch(() => undefined);
      const extra = logs?.length ? ` (${logs[logs.length - 1] ?? ''})` : '';
      throw new Error(mapMeteoraError(new Error(`${inner.message}${extra}`)));
    }
    throw new Error(mapMeteoraError(inner));
  }
}

/**
 * Remove liquidity from a DAMM v2 position (partial), or withdraw all liquidity and close the position (100%).
 */
export async function removeMeteoraLiquidity(params: {
  connection: Connection;
  wallet: WalletContextState;
  poolAddress: PublicKey;
  positionAddress: PublicKey;
  positionNftMint: PublicKey;
  pct: number;
  slippagePercent: number;
}): Promise<{ signature: string; fullyClosed: boolean }> {
  const owner = params.wallet.publicKey;
  if (!owner) throw new Error('Connect your wallet first.');
  if (!params.wallet.sendTransaction) {
    throw new Error('Your wallet cannot send transactions.');
  }

  const pctInt = Math.min(100, Math.max(0, Math.round(params.pct)));
  if (pctInt <= 0) throw new Error('Choose how much liquidity to remove.');

  const slipBps = Math.min(5000, Math.max(0, Math.round(params.slippagePercent * 100)));
  const cpAmm = new CpAmm(params.connection);

  const poolState = await cpAmm.fetchPoolState(params.poolAddress);
  const positionState = await cpAmm.fetchPositionState(params.positionAddress);

  if (!positionState.pool.equals(params.poolAddress)) {
    throw new Error('Position does not belong to this pool.');
  }
  if (!positionState.nftMint.equals(params.positionNftMint)) {
    throw new Error('Position NFT does not match this pool entry.');
  }

  const positionNftAccount = derivePositionNftAccount(params.positionNftMint);
  const vestings = await fetchMeteoraVestingsByPosition(
    cpAmm,
    params.connection,
    params.positionAddress,
  );

  const totalPosLiq = positionState.unlockedLiquidity
    .add(positionState.vestedLiquidity)
    .add(positionState.permanentLockedLiquidity);
  const liquidityDelta =
    pctInt >= 100 ? totalPosLiq : totalPosLiq.muln(pctInt).divn(100);
  if (liquidityDelta.isZero()) {
    throw new Error('Nothing to remove for this percentage.');
  }

  const quote = cpAmm.getWithdrawQuote({
    liquidityDelta,
    sqrtPrice: poolState.sqrtPrice,
    maxSqrtPrice: poolState.sqrtMaxPrice,
    minSqrtPrice: poolState.sqrtMinPrice,
    tokenATokenInfo: undefined,
    tokenBTokenInfo: undefined,
    collectFeeMode: poolState.collectFeeMode,
    tokenAAmount: poolState.tokenAAmount,
    tokenBAmount: poolState.tokenBAmount,
    liquidity: poolState.liquidity,
  });

  const tokenAAmountThreshold = getAmountWithSlippage(quote.outAmountA, slipBps, SwapMode.ExactIn);
  const tokenBAmountThreshold = getAmountWithSlippage(quote.outAmountB, slipBps, SwapMode.ExactIn);

  const tokenAProgram = getTokenProgram(poolState.tokenAFlag);
  const tokenBProgram = getTokenProgram(poolState.tokenBFlag);
  const currentPoint = await getCurrentPoint(params.connection, poolState.activationType);

  let tx;
  if (pctInt >= 100) {
    tx = await cpAmm.removeAllLiquidityAndClosePosition({
      owner,
      position: params.positionAddress,
      positionNftAccount,
      poolState,
      positionState,
      tokenAAmountThreshold,
      tokenBAmountThreshold,
      vestings,
      currentPoint,
    });
  } else {
    tx = await cpAmm.removeLiquidity({
      owner,
      pool: params.poolAddress,
      position: params.positionAddress,
      positionNftAccount,
      liquidityDelta,
      tokenAAmountThreshold,
      tokenBAmountThreshold,
      tokenAMint: poolState.tokenAMint,
      tokenBMint: poolState.tokenBMint,
      tokenAVault: poolState.tokenAVault,
      tokenBVault: poolState.tokenBVault,
      tokenAProgram,
      tokenBProgram,
      vestings,
      currentPoint,
    });
  }

  const feeIx = buildFeeTransferInstruction(owner, 'remove_liquidity');
  await prependComputeBudgetAndPlatformFee(params.connection, feeIx, [owner, params.poolAddress], tx);

  tx.feePayer = owner;
  const latest = await params.connection.getLatestBlockhash('confirmed');
  tx.recentBlockhash = latest.blockhash;

  try {
    await assertLegacyTransactionSimulationOk(params.connection, tx, []);

    const txSignature = await params.wallet.sendTransaction(tx, params.connection, {
      skipPreflight: true,
      maxRetries: 3,
    });

    await confirmTransactionWithBackgroundFallback(
      params.connection,
      { signature: txSignature, blockhash: latest.blockhash, lastValidBlockHeight: latest.lastValidBlockHeight },
      'confirmed',
    );

    return { signature: txSignature, fullyClosed: pctInt >= 100 };
  } catch (e) {
    if (e instanceof PoolAlreadyExistsError) throw e;
    const inner = unwrapWalletSendError(e);
    if (inner instanceof PoolAlreadyExistsError) throw inner;
    if (inner instanceof SendTransactionError) {
      const logs = await inner.getLogs(params.connection).catch(() => undefined);
      const extra = logs?.length ? ` (${logs[logs.length - 1] ?? ''})` : '';
      throw new Error(mapMeteoraError(new Error(`${inner.message}${extra}`)));
    }
    throw new Error(mapMeteoraError(inner));
  }
}
