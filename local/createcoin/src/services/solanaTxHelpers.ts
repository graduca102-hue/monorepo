import { Transaction, type Connection, type Keypair, type TransactionSignature } from '@solana/web3.js';

const FAST_CONFIRM_WAIT_MS = 1_500;
const SEND_RETRY_DELAYS_MS = [0, 750, 1_500, 3_000];

export type SendRawTransactionOptions = {
  /**
   * Start with skipPreflight on the first send attempt to reduce post-sign latency.
   * Useful when the transaction was already locally checked or UX speed matters more than RPC preflight.
   */
  preferSkipPreflight?: boolean;
};

function isSimulationPreflightFailure(e: unknown): boolean {
  const msg = e instanceof Error ? e.message : String(e);
  return /simulation failed/i.test(msg);
}

function isRetryableSendFailure(e: unknown): boolean {
  const msg = (e instanceof Error ? e.message : String(e)).toLowerCase();
  return (
    msg.includes('429') ||
    msg.includes('too many requests') ||
    msg.includes('rate limit') ||
    msg.includes('network is congested') ||
    msg.includes('node is behind') ||
    msg.includes('timed out') ||
    msg.includes('timeout') ||
    msg.includes('fetch failed') ||
    msg.includes('failed to fetch') ||
    msg.includes('service unavailable') ||
    msg.includes('temporarily unavailable') ||
    msg.includes('gateway timeout') ||
    msg.includes('socket hang up') ||
    msg.includes('econnreset') ||
    msg.includes('unable to confirm transaction')
  );
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Runs RPC simulation before the wallet so failures show real program logs instead of a generic
 * "Unexpected error" from the adapter.
 */
export async function assertLegacyTransactionSimulationOk(
  connection: Connection,
  tx: Transaction,
  partialSigners: Keypair[],
): Promise<void> {
  const serialized = tx.serialize({
    requireAllSignatures: false,
    verifySignatures: false,
  });
  const trial = Transaction.from(serialized);
  for (const kp of partialSigners) {
    trial.partialSign(kp);
  }

  const latest = await connection.getLatestBlockhash('confirmed');
  trial.recentBlockhash = latest.blockhash;
  trial.lastValidBlockHeight = latest.lastValidBlockHeight;

  // Legacy Transaction uses the deprecated overload; VersionedTransaction gets SimulateTransactionConfig.
  const sim = await connection.simulateTransaction(trial);

  const err = sim.value.err;
  if (err == null) return;

  const logs = sim.value.logs?.filter((l): l is string => typeof l === 'string' && l.length > 0) ?? [];
  const tail = logs.slice(-18).join('\n');
  const errJson = typeof err === 'object' && err !== null ? JSON.stringify(err) : String(err);
  throw new Error(
    tail.length ? `Simulation failed (${errJson})\n${tail}` : `Simulation failed: ${errJson}`,
  );
}

/**
 * Some RPCs return flaky preflight simulation for large txs (metadata + mint + fees)
 * even when the same serialized transaction lands successfully. Retry once without preflight.
 */
export async function sendRawTransactionWithSimulationFallback(
  connection: Connection,
  rawTx: Uint8Array,
  options: SendRawTransactionOptions = {},
): Promise<TransactionSignature> {
  let forceSkipPreflight = options.preferSkipPreflight === true;
  let lastError: unknown;

  for (let attempt = 0; attempt < SEND_RETRY_DELAYS_MS.length; attempt++) {
    if (attempt > 0) {
      await sleep(SEND_RETRY_DELAYS_MS[attempt] ?? 0);
    }

    const skipPreflight = forceSkipPreflight || attempt > 0;

    try {
      return await connection.sendRawTransaction(rawTx, {
        skipPreflight,
        maxRetries: skipPreflight ? 6 : 3,
      });
    } catch (e) {
      lastError = e;

      if (isSimulationPreflightFailure(e)) {
        forceSkipPreflight = true;
        continue;
      }

      if (!isRetryableSendFailure(e)) {
        throw e;
      }
    }
  }

  throw lastError instanceof Error ? lastError : new Error(String(lastError ?? 'Transaction send failed'));
}

/**
 * If confirmTransaction throws (WS/RPC glitch) while the signature already landed, poll status instead of failing the flow.
 */
export async function confirmTransactionResilient(
  connection: Connection,
  strategy: { signature: string; blockhash: string; lastValidBlockHeight: number },
  commitment: 'confirmed' | 'finalized' = 'confirmed',
): Promise<void> {
  try {
    await connection.confirmTransaction(strategy, commitment);
    return;
  } catch {
    /* fall through to status polling */
  }

  const sig = strategy.signature;
  const deadline = Date.now() + 60_000;

  while (Date.now() < deadline) {
    const { value } = await connection.getSignatureStatuses([sig], { searchTransactionHistory: true });
    const st = value[0];
    if (st?.err != null) {
      throw new Error(`Transaction failed: ${JSON.stringify(st.err)}`);
    }
    const rawStatus = st?.confirmationStatus as string | undefined;
    const ok =
      rawStatus === 'finalized' ||
      (commitment === 'confirmed' && (rawStatus === 'confirmed' || rawStatus === 'finalized'));
    if (ok) {
      return;
    }
    await new Promise((r) => setTimeout(r, 1500));
  }

  throw new Error('Confirmation timed out');
}

/**
 * Wait briefly for confirmation, then continue in the background so UX is not blocked by slow RPC confirmation.
 * Throws only on an explicit on-chain failure during the bounded wait window.
 */
export async function confirmTransactionWithBackgroundFallback(
  connection: Connection,
  strategy: { signature: string; blockhash: string; lastValidBlockHeight: number },
  commitment: 'confirmed' | 'finalized' = 'confirmed',
  maxWaitMs = FAST_CONFIRM_WAIT_MS,
): Promise<{ confirmed: boolean }> {
  let finished = false;
  const confirmPromise = confirmTransactionResilient(connection, strategy, commitment)
    .then(() => {
      finished = true;
    })
    .catch((error) => {
      finished = true;
      throw error;
    });

  const outcome = await Promise.race([
    confirmPromise.then(() => 'confirmed' as const),
    new Promise<'timeout'>((resolve) => setTimeout(() => resolve('timeout'), maxWaitMs)),
  ]);

  if (outcome === 'confirmed') {
    return { confirmed: true };
  }

  if (!finished) {
    void confirmPromise.catch((error) => {
      console.warn('[confirmTransactionWithBackgroundFallback] background confirmation failed', error);
    });
  }

  return { confirmed: false };
}
