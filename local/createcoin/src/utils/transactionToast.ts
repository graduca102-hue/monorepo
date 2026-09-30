import toast from 'react-hot-toast';
import { parseSolanaError } from './errorParser';

function toastBodyFromParsed(parsed: ReturnType<typeof parseSolanaError>): string {
  const m = parsed.message.trim();
  const genericSim =
    /^transaction simulation failed\.?$/i.test(m) ||
    (parsed.title === 'Simulation' &&
      m.length < 60 &&
      !m.includes('Instruction') &&
      !m.includes('Program') &&
      !m.includes('custom'));
  const tech = parsed.technicalDetails?.trim();

  if (genericSim && tech && tech.length > m.length) {
    if (/^transaction simulation failed\.?$/i.test(m)) {
      const extra = tech.length > 560 ? `${tech.slice(0, 557)}…` : tech;
      return `${m}\n${extra}`;
    }
    return tech.length > 720 ? `${tech.slice(0, 717)}…` : tech;
  }
  if (tech && tech.length > m.length + 40 && !m.includes('\n')) {
    const extra = tech.slice(0, 360);
    return `${m}\n${extra}${tech.length > 360 ? '…' : ''}`;
  }
  return parsed.message;
}

export type TransactionToastOptions<T = unknown> = {
  /** If set, shown on success instead of the default success toast. */
  successMessage?: string | ((result: T) => string);
  /** Toast duration in ms when a custom success message is used (default 4000). */
  successDuration?: number;
  /** When false, do not append ` · abc…xyz` after a custom successMessage when a signature exists. Default true. */
  successAppendSignature?: boolean;
  /** If set, shown on failure (non-user-rejection) instead of parsed error text. */
  errorMessage?: string | ((error: unknown, parsed: ReturnType<typeof parseSolanaError>) => string | undefined);
  /** When true, do not show the default error toast (caller handles UX). */
  skipParsedErrorToast?: (error: unknown) => boolean;
};

function resolveSuccessContent<T>(
  options: TransactionToastOptions<T> | undefined,
  res: T,
): string | undefined {
  const sm = options?.successMessage;
  if (typeof sm === 'function') return sm(res);
  return sm;
}

function formatSignatureTail(sig: string): string {
  if (sig.length <= 14) return sig;
  return `${sig.slice(0, 4)}…${sig.slice(-4)}`;
}

/** When no `successMessage` is set, show a short line from the loading label plus a tx hint. */
function defaultSuccessBody(label: string): string {
  const low = label.toLowerCase();
  if (low.includes('approve boost')) return `Boost payment confirmed`;
  if (low.includes('creating pool')) return `Pool transaction confirmed`;
  if (low.includes('adding liquidity')) return `Liquidity add confirmed`;
  if (low.includes('removing liquidity')) return `Liquidity removed`;
  if (low.includes('copying trending')) return `Token created`;
  return `Transaction confirmed`;
}

export async function withTransactionToast<T>(
  label: string,
  fn: () => Promise<T>,
  options?: TransactionToastOptions<T>,
): Promise<T> {
  const id = toast.loading(label);
  try {
    const res = await fn();
    toast.dismiss(id);
    const sig =
      res && typeof res === 'object' && 'signature' in res && typeof (res as { signature?: string }).signature === 'string'
        ? (res as { signature: string }).signature
        : undefined;
    const custom = resolveSuccessContent(options, res);
    if (sig) {
      if (custom) {
        const withSig =
          options?.successAppendSignature === false
            ? custom
            : `${custom} · ${formatSignatureTail(sig)}`;
        toast.success(withSig, {
          duration: options?.successDuration ?? 4000,
        });
      } else {
        toast.success(defaultSuccessBody(label), { duration: 6000 });
      }
    } else {
      toast.success(custom ?? defaultSuccessBody(label));
    }
    return res;
  } catch (e) {
    toast.dismiss(id);
    if (import.meta.env.DEV) {
      console.error('[withTransactionToast]', e);
    }
    const skipDefault = options?.skipParsedErrorToast?.(e) === true;
    if (!skipDefault) {
      const parsed = parseSolanaError(e);
      if (parsed.isUserRejection) {
        toast('Transaction cancelled', { icon: '—' });
      } else {
        const resolvedError =
          typeof options?.errorMessage === 'function'
            ? options.errorMessage(e, parsed)
            : options?.errorMessage;
        toast.error(resolvedError ?? toastBodyFromParsed(parsed), {
          duration: 14_000,
          style: { maxWidth: 560, whiteSpace: 'pre-wrap' },
        });
      }
    }
    throw e;
  }
}
