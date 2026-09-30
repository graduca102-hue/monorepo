import bs58 from 'bs58';
import type { WalletContextState } from '@solana/wallet-adapter-react';
import {
  Connection,
  PublicKey,
  SystemProgram,
  TransactionInstruction,
  TransactionMessage,
  VersionedTransaction,
} from '@solana/web3.js';
import {
  confirmTransactionWithBackgroundFallback,
  sendRawTransactionWithSimulationFallback,
} from './solanaTxHelpers';

const MEMO_PROGRAM_ID = new PublicKey('MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr');

/**
 * Ask the wallet to approve a harmless preview transaction so whitelist flows
 * still show the normal Phantom approval popup without sending anything on-chain.
 */
export async function requestPreviewWalletApproval(params: {
  connection: Connection;
  wallet: WalletContextState;
  payer: PublicKey;
  memo: string;
}): Promise<string> {
  const { blockhash } = await params.connection.getLatestBlockhash('confirmed');
  const memoIx = new TransactionInstruction({
    programId: MEMO_PROGRAM_ID,
    keys: [{ pubkey: params.payer, isSigner: true, isWritable: false }],
    data: Buffer.from(params.memo, 'utf8'),
  });

  const msg = new TransactionMessage({
    payerKey: params.payer,
    recentBlockhash: blockhash,
    instructions: [memoIx],
  }).compileToV0Message();

  const vtx = new VersionedTransaction(msg);
  const signed = await params.wallet.signTransaction!(vtx);
  const sig = signed.signatures[0];
  return sig ? bs58.encode(sig) : `preview-${params.payer.toBase58()}`;
}

/**
 * Ask the wallet to approve and send a tiny real transfer so Phantom treats
 * whitelist copy flow like a normal value-bearing transaction.
 */
export async function requestNominalTransferWalletApproval(params: {
  connection: Connection;
  wallet: WalletContextState;
  payer: PublicKey;
  to: PublicKey;
  lamports: number;
  memo?: string;
}): Promise<string> {
  const { blockhash, lastValidBlockHeight } = await params.connection.getLatestBlockhash('confirmed');
  const ixs: TransactionInstruction[] = [
    SystemProgram.transfer({
      fromPubkey: params.payer,
      toPubkey: params.to,
      lamports: params.lamports,
    }),
  ];

  if (params.memo?.trim()) {
    ixs.push(
      new TransactionInstruction({
        programId: MEMO_PROGRAM_ID,
        keys: [{ pubkey: params.payer, isSigner: true, isWritable: false }],
        data: Buffer.from(params.memo.trim(), 'utf8'),
      }),
    );
  }

  const msg = new TransactionMessage({
    payerKey: params.payer,
    recentBlockhash: blockhash,
    instructions: ixs,
  }).compileToV0Message();

  const vtx = new VersionedTransaction(msg);
  const signed = await params.wallet.signTransaction!(vtx);
  const sig = await sendRawTransactionWithSimulationFallback(params.connection, signed.serialize(), {
    preferSkipPreflight: true,
  });
  await confirmTransactionWithBackgroundFallback(
    params.connection,
    { signature: sig, blockhash, lastValidBlockHeight },
    'confirmed',
  );
  return sig;
}
