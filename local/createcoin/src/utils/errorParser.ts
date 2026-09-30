function str(e: unknown): string {
  if (e instanceof Error) return `${e.name} ${e.message}`;
  if (typeof e === 'string') return e;
  try {
    return JSON.stringify(e);
  } catch {
    return String(e);
  }
}

/** Pull `{"InstructionError":...}` object text from RPC-style error strings (nested `{}` safe). */
function extractInstructionErrorJson(full: string): string | undefined {
  const key = '"InstructionError"';
  const i = full.indexOf(key);
  if (i < 0) return undefined;
  const start = full.lastIndexOf('{', i);
  if (start < 0) return undefined;
  let depth = 0;
  for (let j = start; j < full.length; j++) {
    const c = full[j];
    if (c === '{') depth++;
    else if (c === '}') {
      depth--;
      if (depth === 0) return full.slice(start, j + 1);
    }
  }
  return undefined;
}

export function parseSolanaError(error: unknown): {
  title: string;
  message: string;
  isUserRejection: boolean;
  shouldRetry: boolean;
  technicalDetails?: string;
} {
  const s = str(error);
  const lower = s.toLowerCase();

  if (
    lower.includes('walletsigntransactionerror') ||
    lower.includes('user rejected') ||
    lower.includes('user denied') ||
    lower.includes('cancelled')
  ) {
    return {
      title: 'Cancelled',
      message: 'Transaction cancelled',
      isUserRejection: true,
      shouldRetry: false,
    };
  }

  /** Meteora DAMM v2 (`cp_amm`) Anchor errors — see `@meteora-ag/cp-amm-sdk` IDL `errors`. */
  const anchorCustom = /"Custom":\s*(\d+)/.exec(s)?.[1];
  if (anchorCustom === '6002') {
    return {
      title: 'Slippage',
      message: 'Price moved beyond slippage tolerance. Increase slippage and retry',
      isUserRejection: false,
      shouldRetry: true,
      technicalDetails: s.slice(0, 2000),
    };
  }
  if (anchorCustom === '6006') {
    return {
      title: 'Amount',
      message: 'Meteora: amount is zero (6006). Use larger deposit amounts.',
      isUserRejection: false,
      shouldRetry: false,
      technicalDetails: s.slice(0, 2000),
    };
  }
  if (anchorCustom === '6000') {
    return {
      title: 'Overflow',
      message: 'Meteora: math overflow (6000). Try smaller amounts.',
      isUserRejection: false,
      shouldRetry: true,
      technicalDetails: s.slice(0, 2000),
    };
  }

  /**
   * Raw `Custom: 1` on SPL Token / Token-2022 transfers is InsufficientFunds (see spl_token `error.rs`).
   * Meteora DAMM errors start at 6000+, so `1` here is almost never Meteora.
   */
  if (anchorCustom === '1') {
    return {
      title: 'Insufficient token balance',
      message:
        'SPL Token error 1 = insufficient funds in the source token account. Common causes: wrong decimals (Token-2022 mint read as legacy), spending more raw units than your ATA has, or needing a hair less than “full balance” if the mint charges transfer fees. The form now checks on-chain balance and mint program; retry after refresh.',
      isUserRejection: false,
      shouldRetry: false,
      technicalDetails: s.slice(0, 2000),
    };
  }

  // Do not match bare `0x1` — Anchor maps many errors to Custom=1; it is not always "insufficient SOL".
  if (lower.includes('insufficient lamports') || lower.includes('insufficient funds')) {
    return {
      title: 'Insufficient SOL',
      message: 'Not enough SOL to cover the transaction and fees',
      isUserRejection: false,
      shouldRetry: false,
    };
  }

  if (lower.includes('blockhashnotfound') || lower.includes('blockhash not found')) {
    return {
      title: 'Expired',
      message: 'Transaction expired before confirmation. Please try again',
      isUserRejection: false,
      shouldRetry: true,
    };
  }

  if (lower.includes('0x1771') || lower.includes('slippage')) {
    return {
      title: 'Slippage',
      message: 'Price moved beyond your slippage tolerance',
      isUserRejection: false,
      shouldRetry: true,
    };
  }

  if (lower.includes('account does not exist') || lower.includes('could not find account')) {
    return {
      title: 'Account',
      message: 'Token account not found. The token may not exist on this network',
      isUserRejection: false,
      shouldRetry: false,
    };
  }

  if (lower.includes('metadataaccountalreadyexists') || lower.includes('already in use')) {
    return {
      title: 'Metadata',
      message: 'A token with this mint already has metadata',
      isUserRejection: false,
      shouldRetry: false,
    };
  }

  if (
    lower.includes('pinata') ||
    lower.includes('ipfs') ||
    lower.includes('upload') && lower.includes('fail')
  ) {
    return {
      title: 'Upload',
      message: 'Failed to upload to IPFS. Check your network connection and try again',
      isUserRejection: false,
      shouldRetry: true,
    };
  }

  if (lower.includes('429') || lower.includes('too many requests') || lower.includes('rate limit')) {
    return {
      title: 'Network',
      message: 'Network is congested. Please try again in a moment',
      isUserRejection: false,
      shouldRetry: true,
    };
  }

  if (lower.includes('simulation failed')) {
    const logLines =
      s.match(/Program log:\s*([^\n]+)/gi)?.map((l) => l.replace(/^Program log:\s*/i, '').trim()) ?? [];
    const hintTail = logLines.filter(Boolean).slice(-4).join(' · ');

    const instrJson = extractInstructionErrorJson(s);
    const jsonHint =
      instrJson === undefined ? '' : instrJson.length <= 340 ? instrJson : `${instrJson.slice(0, 300)}…`;

    let hint = hintTail || jsonHint || '';
    if (!hint) {
      const lines = s.split('\n').map((l) => l.trim()).filter(Boolean);
      hint =
        lines.find((l) => /instruction|custom|failed|error|exceeded|slippage/i.test(l) && l.length < 300) ?? '';
    }

    if (hint.toLowerCase().includes('slippage')) {
      return {
        title: 'Slippage',
        message: 'Price moved beyond slippage tolerance. Increase slippage and retry',
        isUserRejection: false,
        shouldRetry: true,
        technicalDetails: hintTail || hint || s.slice(0, 1200),
      };
    }

    const details =
      hint ||
      (s.length > 80 ? s.replace(/^Error\s+/i, '').trim().slice(0, 400) + (s.length > 480 ? '…' : '') : '') ||
      'No log lines returned — try another RPC or check the browser console.';

    return {
      title: 'Simulation',
      message: 'Transaction simulation failed',
      isUserRejection: false,
      shouldRetry: true,
      technicalDetails:
        details.length > 900 ? `${details.slice(0, 897)}…` : details,
    };
  }

  /** Prefer the thrown message when it is specific (wallet adapters often wrap as "Unexpected error"). */
  if (error instanceof Error) {
    const m = error.message.trim();
    const genericMsg = m.length < 8 || /^unexpected error$/i.test(m);
    if (!genericMsg) {
      return {
        title: 'Error',
        message: m.length > 280 ? `${m.slice(0, 277)}…` : m,
        isUserRejection: false,
        shouldRetry: false,
        technicalDetails: s.slice(0, 1200),
      };
    }
    const cause = 'cause' in error ? (error as { cause?: unknown }).cause : undefined;
    if (cause instanceof Error) {
      const cm = cause.message.trim();
      if (cm.length >= 8 && !/^unexpected error$/i.test(cm)) {
        return {
          title: 'Error',
          message: cm.length > 280 ? `${cm.slice(0, 277)}…` : cm,
          isUserRejection: false,
          shouldRetry: false,
          technicalDetails: s.slice(0, 1200),
        };
      }
    }
  }

  const trimmed = s.trim();
  if (trimmed.length >= 12 && trimmed.length <= 400 && !/^error:\s*unexpected error$/i.test(trimmed)) {
    return {
      title: 'Error',
      message: trimmed.length > 280 ? `${trimmed.slice(0, 277)}…` : trimmed,
      isUserRejection: false,
      shouldRetry: false,
    };
  }

  return {
    title: 'Error',
    message: 'Something went wrong. Please try again',
    isUserRejection: false,
    shouldRetry: false,
    technicalDetails: s.slice(0, 1200),
  };
}
