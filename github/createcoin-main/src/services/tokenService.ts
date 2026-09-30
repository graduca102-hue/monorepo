import { createUmi } from '@metaplex-foundation/umi-bundle-defaults';
import { createSignerFromKeypair, percentAmount, some } from '@metaplex-foundation/umi';
import { walletAdapterIdentity } from '@metaplex-foundation/umi-signer-wallet-adapters';
import { fromWeb3JsKeypair, toWeb3JsInstruction } from '@metaplex-foundation/umi-web3js-adapters';
import { createV1, mplTokenMetadata, TokenStandard } from '@metaplex-foundation/mpl-token-metadata';
import {
  ACCOUNT_SIZE,
  AuthorityType,
  MINT_SIZE,
  TOKEN_PROGRAM_ID,
  createAssociatedTokenAccountIdempotentInstruction,
  createInitializeMint2Instruction,
  createMintToInstruction,
  createSetAuthorityInstruction,
  getAssociatedTokenAddressSync,
} from '@solana/spl-token';
import {
  Connection,
  Keypair,
  PublicKey,
  SystemProgram,
  SystemInstruction,
  TransactionMessage,
  VersionedTransaction,
} from '@solana/web3.js';
import type { WalletContextState } from '@solana/wallet-adapter-react';
import BN from 'bn.js';
import { env } from '../config/env';
import { buildTreasuryTransferInstruction, calculateTotalFees, type FeeKind } from './feeService';
import { buildComputeBudgetInstructions, getDynamicPriorityFee } from './priorityFeeService';
import { confirmTransactionResilient, sendRawTransactionWithSimulationFallback } from './solanaTxHelpers';
import type { TokenMetadataJson } from './ipfsService';
import { uploadImage, uploadMetadata } from './ipfsService';

export type CreationStage =
  | 'uploading_image'
  | 'uploading_metadata'
  | 'building_transaction'
  | 'awaiting_signature'
  | 'confirming'
  | 'done';

function assertContainsExpectedTreasuryTransfer(
  instructions: readonly ReturnType<typeof SystemProgram.transfer>[],
  payer: PublicKey,
  expectedTreasury: PublicKey,
  expectedLamports: number,
): void {
  const hasExpectedTransfer = instructions.some((ix) => {
    if (!ix.programId.equals(SystemProgram.programId)) return false;
    if (ix.keys.length < 2) return false;
    if (!ix.keys[0]?.pubkey.equals(payer)) return false;
    if (!ix.keys[1]?.pubkey.equals(expectedTreasury)) return false;
    try {
      const decoded = SystemInstruction.decodeTransfer(ix);
      return decoded.lamports === expectedLamports;
    } catch {
      return false;
    }
  });

  if (!hasExpectedTransfer) {
    throw new Error('Platform fee transfer is missing from the token creation transaction.');
  }
}

function supplyBn(supply: number, decimals: number): BN {
  const whole = new BN(Math.floor(supply).toString());
  const scale = new BN(10).pow(new BN(decimals));
  return whole.mul(scale);
}

const TOKEN_CREATION_METADATA_AND_BUFFER_LAMPORTS = 20_000_000;

/** Fee rows for `createToken` (platform treasury transfer). */
export function buildTokenCreationFeeKinds(params: {
  modifyCreator: boolean;
  revokeMint: boolean;
  revokeFreeze: boolean;
  revokeUpdate: boolean;
}): FeeKind[] {
  const feeKinds: FeeKind[] = ['token_creation'];
  if (params.modifyCreator) feeKinds.push('modify_creator');
  if (params.revokeMint) feeKinds.push('revoke_mint');
  if (params.revokeFreeze) feeKinds.push('revoke_freeze');
  if (params.revokeUpdate) feeKinds.push('revoke_update');
  return feeKinds;
}

/**
 * Lower bound on lamports the payer should hold before signing (fees + mint + ATA + metadata/rent headroom).
 * Not a full simulation; avoids starting IPFS upload then failing for insufficient SOL.
 */
export async function estimateMinLamportsForTokenCreation(
  connection: Connection,
  feeKinds: FeeKind[],
  payer?: PublicKey | null,
  balanceLamports?: number,
): Promise<number> {
  const { totalLamports: feesLamports } = calculateTotalFees(feeKinds, payer);
  const [mintRent, ataRent] = await Promise.all([
    connection.getMinimumBalanceForRentExemption(MINT_SIZE),
    connection.getMinimumBalanceForRentExemption(ACCOUNT_SIZE),
  ]);
  const reserveLamports = mintRent + ataRent + TOKEN_CREATION_METADATA_AND_BUFFER_LAMPORTS;
  if (balanceLamports === undefined) return reserveLamports + feesLamports;
  const affordableFeeLamports = Math.max(0, Math.min(feesLamports, balanceLamports - reserveLamports));
  return reserveLamports + affordableFeeLamports;
}

export async function createToken(params: {
  connection: Connection;
  wallet: WalletContextState;
  name: string;
  symbol: string;
  description: string;
  image: File;
  decimals: number;
  supply: number;
  socials: { twitter?: string; telegram?: string; website?: string; discord?: string };
  modifyCreator: boolean;
  creatorName?: string;
  creatorWebsite?: string;
  revokeMint: boolean;
  revokeFreeze: boolean;
  revokeUpdate: boolean;
  onProgress?: (stage: CreationStage) => void;
}): Promise<{ mint: PublicKey; signature: string; metadataUri: string }> {
  const w = params.wallet;
  if (!w.publicKey || !w.signTransaction) {
    throw new Error('Wallet not connected');
  }

  const payer = w.publicKey;
  params.onProgress?.('uploading_image');
  const imageUri = await uploadImage(params.image);

  params.onProgress?.('uploading_metadata');
  const metaJson: TokenMetadataJson = {
    name: params.name.trim(),
    symbol: params.symbol.trim(),
    description: params.description.trim(),
    image: imageUri,
    external_url: params.socials.website || undefined,
    extensions: {
      twitter: params.socials.twitter || undefined,
      telegram: params.socials.telegram || undefined,
      website: params.socials.website || undefined,
      discord: params.socials.discord || undefined,
      ...(params.modifyCreator
        ? {
            creator_name: params.creatorName?.trim() || undefined,
            creator_website: params.creatorWebsite?.trim() || undefined,
          }
        : {}),
    },
  };
  const metadataUri = await uploadMetadata(metaJson);

  params.onProgress?.('building_transaction');
  const mintKp = Keypair.generate();
  const mint = mintKp.publicKey;

  const umi = createUmi(params.connection)
    .use(mplTokenMetadata())
    .use(walletAdapterIdentity(w as Parameters<typeof walletAdapterIdentity>[0]));

  const mintSigner = createSignerFromKeypair(umi, fromWeb3JsKeypair(mintKp));

  const metaBuilder = createV1(umi, {
    mint: mintSigner,
    name: params.name.trim(),
    symbol: params.symbol.trim(),
    uri: metadataUri,
    sellerFeeBasisPoints: percentAmount(0),
    decimals: some(params.decimals),
    tokenStandard: TokenStandard.Fungible,
    isMutable: !params.revokeUpdate,
  });

  const lamports = await params.connection.getMinimumBalanceForRentExemption(MINT_SIZE);
  const priority = await getDynamicPriorityFee(params.connection, [payer, mint]);
  const budgetIxs = buildComputeBudgetInstructions(1_200_000, priority);

  const freezeAuth = params.revokeFreeze ? payer : null;
  const rawSupply = supplyBn(params.supply, params.decimals);

  const ata = getAssociatedTokenAddressSync(mint, payer, false, TOKEN_PROGRAM_ID);

  const feeKinds = buildTokenCreationFeeKinds({
    modifyCreator: params.modifyCreator,
    revokeMint: params.revokeMint,
    revokeFreeze: params.revokeFreeze,
    revokeUpdate: params.revokeUpdate,
  });

  const { totalLamports: configuredFeeLamports } = calculateTotalFees(feeKinds, payer);
  const currentBalanceLamports = await params.connection.getBalance(payer, 'confirmed');
  const reserveLamports = await estimateMinLamportsForTokenCreation(params.connection, [], payer);
  if (currentBalanceLamports < reserveLamports) {
    const need = (reserveLamports / 1_000_000_000).toFixed(3);
    const have = (currentBalanceLamports / 1_000_000_000).toFixed(4);
    throw new Error(`Insufficient SOL. Need ~${need} SOL; you have ${have} SOL.`);
  }
  const actualFeeLamports = Math.max(0, Math.min(configuredFeeLamports, currentBalanceLamports - reserveLamports));
  const feeIx = buildTreasuryTransferInstruction(payer, actualFeeLamports);

  if (!env.isFeeExemptWallet(payer)) {
    if (configuredFeeLamports <= 0) {
      throw new Error(
        'Token creation fee is zero — check VITE_FEE_TOKEN_CREATION_SOL / revoke fees in .env and restart the dev server.',
      );
    }
    if (actualFeeLamports > 0 && !feeIx) {
      throw new Error('Could not build platform fee transfer — ensure treasury is configured correctly.');
    }
  }

  const ixs = [
    ...budgetIxs,
    ...(feeIx ? [feeIx] : []),
    SystemProgram.createAccount({
      fromPubkey: payer,
      newAccountPubkey: mint,
      lamports,
      space: MINT_SIZE,
      programId: TOKEN_PROGRAM_ID,
    }),
    createInitializeMint2Instruction(
      mint,
      params.decimals,
      payer,
      freezeAuth,
      TOKEN_PROGRAM_ID,
    ),
    ...metaBuilder.getInstructions().map(toWeb3JsInstruction),
    createAssociatedTokenAccountIdempotentInstruction(payer, ata, payer, mint, TOKEN_PROGRAM_ID),
    createMintToInstruction(mint, ata, payer, BigInt(rawSupply.toString()), [], TOKEN_PROGRAM_ID),
  ];

  if (params.revokeMint) {
    ixs.push(createSetAuthorityInstruction(mint, payer, AuthorityType.MintTokens, null, [], TOKEN_PROGRAM_ID));
  }
  if (params.revokeFreeze) {
    ixs.push(createSetAuthorityInstruction(mint, payer, AuthorityType.FreezeAccount, null, [], TOKEN_PROGRAM_ID));
  }

  if (!env.isFeeExemptWallet(payer) && actualFeeLamports > 0) {
    assertContainsExpectedTreasuryTransfer(ixs, payer, env.getTreasury(), actualFeeLamports);
  }

  const { blockhash, lastValidBlockHeight } = await params.connection.getLatestBlockhash('confirmed');
  const msg = new TransactionMessage({
    payerKey: payer,
    recentBlockhash: blockhash,
    instructions: ixs,
  }).compileToV0Message();

  const vtx = new VersionedTransaction(msg);
  vtx.sign([mintKp]);

  params.onProgress?.('awaiting_signature');
  const signed = await w.signTransaction(vtx);

  params.onProgress?.('confirming');
  const sig = await sendRawTransactionWithSimulationFallback(params.connection, signed.serialize());

  await confirmTransactionResilient(
    params.connection,
    { signature: sig, blockhash, lastValidBlockHeight },
    'confirmed',
  );

  params.onProgress?.('done');
  return { mint, signature: sig, metadataUri };
}
