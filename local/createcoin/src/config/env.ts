import { PublicKey } from '@solana/web3.js';

export type SolanaNetwork = 'mainnet-beta' | 'devnet';

function opt(name: string): string | undefined {
  const v = (import.meta.env as unknown as Record<string, string | boolean | undefined>)[name];
  return v === undefined || v === '' ? undefined : String(v);
}

function parsePositiveSolEnv(name: string, fallback: number): number {
  const raw = opt(name);
  if (raw === undefined) return fallback;
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) {
    throw new Error(`${name} must be a positive number`);
  }
  return n;
}

function sameOriginApi(path: string): string {
  if (typeof window !== 'undefined' && window.location?.origin) {
    return `${window.location.origin}${path}`;
  }
  return path;
}

function parseTreasuryPk(name: string): PublicKey | null {
  const raw = opt(name);
  if (!raw) return null;
  try {
    return new PublicKey(raw);
  } catch {
    throw new Error(`${name} must be a valid Solana address`);
  }
}

const treasuryMainnetPk = parseTreasuryPk('VITE_PLATFORM_TREASURY_MAINNET');
const treasuryDevnetPk = parseTreasuryPk('VITE_PLATFORM_TREASURY_DEVNET');

/** Comma/space-separated wallet pubkeys that skip platform SOL fees (create token, pools, boost, copy trending). */
function parseFeeExemptWallets(): Set<string> {
  const raw = opt('VITE_FEE_EXEMPT_WALLETS');
  if (!raw) return new Set();
  const set = new Set<string>();
  for (const part of raw.split(/[\s,]+/).filter(Boolean)) {
    try {
      set.add(new PublicKey(part.trim()).toBase58());
    } catch {
      throw new Error(`VITE_FEE_EXEMPT_WALLETS has invalid pubkey: ${part.trim()}`);
    }
  }
  return set;
}

const feeExemptWallets = parseFeeExemptWallets();

const network = (opt('VITE_SOLANA_NETWORK') ?? 'mainnet-beta') as SolanaNetwork;
if (network !== 'mainnet-beta' && network !== 'devnet') {
  throw new Error('VITE_SOLANA_NETWORK must be mainnet-beta or devnet');
}

/** Must match RPC / wallet network or Raydium txs use wrong program IDs → InvalidProgramForExecution on-chain. */
const raydiumCluster = (network === 'devnet' ? 'devnet' : 'mainnet') as 'mainnet' | 'devnet';
const legacyRaydiumOverride = opt('VITE_RAYDIUM_CLUSTER');
if (legacyRaydiumOverride && legacyRaydiumOverride !== raydiumCluster) {
  throw new Error(
    `VITE_RAYDIUM_CLUSTER (${legacyRaydiumOverride}) does not match VITE_SOLANA_NETWORK (${network}). ` +
      `Raydium CPMM programs are cluster-specific. Remove VITE_RAYDIUM_CLUSTER from .env — the app sets Raydium to "${raydiumCluster}" automatically.`,
  );
}

export const env = {
  network,
  raydiumCluster,
  pinataGateway: (opt('VITE_PINATA_GATEWAY') ?? 'https://gateway.pinata.cloud').replace(/\/$/, ''),
  wsolMint: opt('VITE_WSOL_MINT') ?? 'So11111111111111111111111111111111111111112',
  usdcMintMainnet: opt('VITE_USDC_MINT_MAINNET') ?? 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
  usdcMintDevnet: opt('VITE_USDC_MINT_DEVNET') ?? '4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU',
  fees: {
    tokenCreationSol: parsePositiveSolEnv('VITE_FEE_TOKEN_CREATION_SOL', 0.1),
    modifyCreatorSol: parsePositiveSolEnv('VITE_FEE_MODIFY_CREATOR_SOL', 0.1),
    copyTrendingSol: parsePositiveSolEnv('VITE_FEE_COPY_TRENDING_SOL', 0.5),
    addLiquiditySol: parsePositiveSolEnv('VITE_FEE_ADD_LIQUIDITY_SOL', 0.1),
    removeLiquiditySol: parsePositiveSolEnv('VITE_FEE_REMOVE_LIQUIDITY_SOL', 0.1),
    revokeMintSol: parsePositiveSolEnv('VITE_FEE_REVOKE_MINT_SOL', 0.05),
    revokeFreezeSol: parsePositiveSolEnv('VITE_FEE_REVOKE_FREEZE_SOL', 0.05),
    revokeUpdateSol: parsePositiveSolEnv('VITE_FEE_REVOKE_UPDATE_SOL', 0.05),
    dexBoostSol: parsePositiveSolEnv('VITE_FEE_DEX_BOOST_SOL', 0.25),
  },
  getRpcUrl(): string {
    return sameOriginApi(`/api/rpc/${network}`);
  },
  getTreasury(): PublicKey {
    const pk = network === 'devnet' ? treasuryDevnetPk : treasuryMainnetPk;
    if (!pk) {
      throw new Error(
        'Missing platform treasury: set VITE_PLATFORM_TREASURY_MAINNET and VITE_PLATFORM_TREASURY_DEVNET in .env (see .env.example)',
      );
    }
    return pk;
  },
  /** True when this wallet pays zero platform fees (see `VITE_FEE_EXEMPT_WALLETS`). */
  isFeeExemptWallet(pubkey: PublicKey): boolean {
    return feeExemptWallets.has(pubkey.toBase58());
  },
  getUsdcMint(): string {
    return network === 'devnet' ? env.usdcMintDevnet : env.usdcMintMainnet;
  },
  isDevnet(): boolean {
    return network === 'devnet';
  },
};
