import {
  TOKEN_PROGRAM_ID,
  createAssociatedTokenAccountIdempotentInstruction,
  createTransferInstruction,
  getAssociatedTokenAddressSync,
  getMint,
} from '@solana/spl-token';
import {
  Connection,
  PublicKey,
  TransactionMessage,
  VersionedTransaction,
} from '@solana/web3.js';
import type { WalletContextState } from '@solana/wallet-adapter-react';
import Decimal from 'decimal.js';
import { env } from '../config/env';
import { buildComputeBudgetInstructions, getDynamicPriorityFee } from '../services/priorityFeeService';
import {
  confirmTransactionWithBackgroundFallback,
  sendRawTransactionWithSimulationFallback,
} from '../services/solanaTxHelpers';
import { appendPromoPoolRecord } from './storage';
import type { PromoPoolRecordV1 } from './types';
import { promoDerivedBase58Sync } from './derivePoolIds';

/**
 * Minimum SOL for fee-exempt promo tx only (compute / priority / possible new treasury ATA rent).
 * Does not include any “SOL to pair” — on-chain transfer is SPL token only.
 */
export function getPromoPoolFeeExemptPreflightMinSolLamports(): number {
  return 3_500_000 + 2_100_000;
}

export async function submitPromoPoolCreation(params: {
  connection: Connection;
  wallet: WalletContextState;
  mint: PublicKey;
  baseSymbol: string;
  /** Human token amount to transfer to treasury (the coin you’re “pairing” in the UI). */
  baseAmountToken: string;
  displayBaseAmount: string;
  /** Shown on the promo card only (not transferred on-chain). */
  displayQuoteAmount: string;
  /**
   * When set (e.g. Supabase `pools.pool_id`), stored as the card id so it matches DB + axiom-dev.
   * Otherwise derived from the tx signature.
   */
  poolIdOverride?: string;
}): Promise<{ signature: string; poolId: string }> {
  const owner = params.wallet.publicKey;
  if (!owner) throw new Error('Wallet not connected');
  const signTx = params.wallet.signTransaction;
  if (!signTx) throw new Error('Wallet cannot sign transactions');

  const treasury = env.getTreasury();

  const mintInfo = await getMint(params.connection, params.mint);
  const decimals = mintInfo.decimals;
  const rawTokenDec = new Decimal(params.baseAmountToken.trim()).mul(new Decimal(10).pow(decimals)).floor();
  if (rawTokenDec.lte(0)) throw new Error('Token amount too small');
  const amountTokenRaw = BigInt(rawTokenDec.toFixed(0));

  const sourceAta = getAssociatedTokenAddressSync(params.mint, owner, false, TOKEN_PROGRAM_ID);
  const destAta = getAssociatedTokenAddressSync(params.mint, treasury, false, TOKEN_PROGRAM_ID);

  const priority = await getDynamicPriorityFee(params.connection, [
    owner,
    treasury,
    params.mint,
    sourceAta,
    destAta,
  ]);
  const budget = buildComputeBudgetInstructions(400_000, priority);

  const destInfo = await params.connection.getAccountInfo(destAta, 'confirmed');
  const ixs = [...budget];
  if (!destInfo) {
    ixs.push(
      createAssociatedTokenAccountIdempotentInstruction(owner, destAta, treasury, params.mint, TOKEN_PROGRAM_ID),
    );
  }
  ixs.push(createTransferInstruction(sourceAta, destAta, owner, amountTokenRaw, [], TOKEN_PROGRAM_ID));

  const { blockhash, lastValidBlockHeight } = await params.connection.getLatestBlockhash('confirmed');
  const msg = new TransactionMessage({
    payerKey: owner,
    recentBlockhash: blockhash,
    instructions: ixs,
  }).compileToV0Message();

  const vtx = new VersionedTransaction(msg);
  const signed = await signTx(vtx);
  const sig = await sendRawTransactionWithSimulationFallback(params.connection, signed.serialize(), {
    preferSkipPreflight: true,
  });
  await confirmTransactionWithBackgroundFallback(
    params.connection,
    { signature: sig, blockhash, lastValidBlockHeight },
    'confirmed',
  );

  const override = params.poolIdOverride?.trim();
  const poolId = override && override.length > 0 ? override : promoDerivedBase58Sync(sig, 'pool');
  const lpMintStr = promoDerivedBase58Sync(sig, 'lp');
  const record: PromoPoolRecordV1 = {
    v: 1,
    poolId,
    baseMint: params.mint.toBase58(),
    baseSymbol: params.baseSymbol,
    quoteMint: env.wsolMint,
    displayBaseAmount: params.displayBaseAmount,
    displayQuoteAmount: params.displayQuoteAmount,
    treasuryTokenRaw: rawTokenDec.toFixed(0),
    treasurySolLamports: '0',
    signature: sig,
    createdAt: Date.now(),
    lpMint: lpMintStr,
  };
  appendPromoPoolRecord(owner, record);
  return { signature: sig, poolId };
}
