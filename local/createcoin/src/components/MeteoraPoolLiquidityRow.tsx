import { useEffect, useMemo, useState, type ComponentType } from 'react';
import { PublicKey } from '@solana/web3.js';
import { useConnection } from '@solana/wallet-adapter-react';
import { getMint } from '@solana/spl-token';
import { CpAmm } from '@meteora-ag/cp-amm-sdk';
import Decimal from 'decimal.js';
import { Minus, RefreshCw, Zap } from 'lucide-react';
import toast from 'react-hot-toast';
import { env } from '../config/env';
import { getMultipleTokenPricesUsd } from '../services/priceService';
import type { UserPoolPosition } from '../services/raydiumService';
import {
  type FeeExemptPoolDisplay,
  getFeeExemptPoolDisplay,
  getMeteoraPoolCreatedAt,
  METEORA_FEE_EXEMPT_DISPLAY_DELAY_MS,
  removeMeteoraPoolFromStorage,
} from '../services/meteoraPoolStorage';
import { dexscreenerSolanaPoolUrl, feeExemptDexscreenerUrl } from '../utils/solanaExplorer';

function shortMint(mint: string, head = 4, tail = 4): string {
  if (mint.length <= head + tail) return mint;
  return `${mint.slice(0, head)}...${mint.slice(-tail)}`;
}

function formatCompactUsd(num: number, decimals = 2): string {
  if (!Number.isFinite(num)) return '$—';
  const sign = num < 0 ? '-' : '';
  const v = Math.abs(num);
  if (v >= 1e9) return `${sign}$${(v / 1e9).toFixed(decimals)}B`;
  if (v >= 1e6) return `${sign}$${(v / 1e6).toFixed(decimals)}M`;
  if (v >= 1e3) return `${sign}$${(v / 1e3).toFixed(decimals)}K`;
  return `${sign}$${v.toFixed(decimals)}`;
}

function formatQuotePooledCompact(num: number): string {
  if (!Number.isFinite(num)) return '—';
  const abs = Math.abs(num);
  if (abs >= 1e9) return `${(num / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(num / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(num / 1e3).toFixed(2)}K`;
  return num.toFixed(2);
}

function formatPoolSolUi(num: number): string {
  if (!Number.isFinite(num)) return '—';
  const abs = Math.abs(num);
  if (abs >= 1e6) return formatQuotePooledCompact(num);
  if (abs >= 100) return num.toFixed(2);
  if (abs >= 1) return num.toFixed(3);
  return num.toFixed(4);
}

function formatCompact(num: number, smallFractionDigits = 6): string {
  if (!Number.isFinite(num)) return '—';
  const abs = Math.abs(num);
  if (abs >= 1e9) return `${(num / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(num / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(num / 1e3).toFixed(2)}K`;
  if (abs >= 1) return num.toFixed(2);
  return num.toFixed(smallFractionDigits);
}

type Props = {
  pool: UserPoolPosition;
  walletAddress: string;
  pairLabel: string;
  poolDisplayMintOrder: [string, string];
  symbolForMint: (p: UserPoolPosition, mint: string) => string;
  PoolRoundMint: ComponentType<{
    mint: string;
    symbol: string;
    imageUrl: string | null | undefined;
    sizeClass: string;
    textClassName?: string;
  }>;
  poolMintImages: Record<string, string | null>;
  tokenName?: string;
  onOpenBoost: () => void;
  onOpenRemove: () => void;
  onRemovedFromStorage: () => void;
  reloadKey?: number;
};

export function MeteoraPoolLiquidityRow({
  pool,
  walletAddress,
  pairLabel,
  poolDisplayMintOrder,
  symbolForMint,
  PoolRoundMint,
  poolMintImages,
  tokenName,
  onOpenBoost,
  onOpenRemove,
  onRemovedFromStorage,
  reloadKey = 0,
}: Props) {
  const { connection } = useConnection();
  const isFeeExemptWallet = useMemo(() => env.isFeeExemptWallet(new PublicKey(walletAddress)), [walletAddress]);
  const poolCreatedAt = useMemo(() => getMeteoraPoolCreatedAt(walletAddress, pool.poolId), [walletAddress, pool.poolId]);
  const isFrontendOnlyPool = pool.isFrontendOnlyMeteoraPool === true;
  const [useFakeDisplay, setUseFakeDisplay] = useState(() => {
    if (isFrontendOnlyPool) return true;
    if (!isFeeExemptWallet || poolCreatedAt == null) return false;
    return Date.now() >= poolCreatedAt + METEORA_FEE_EXEMPT_DISPLAY_DELAY_MS;
  });
  const [fakeDisplay, setFakeDisplay] = useState<FeeExemptPoolDisplay | null>(null);
  const [loading, setLoading] = useState(true);
  const [gone, setGone] = useState(false);
  const [memeUi, setMemeUi] = useState(0);
  const [solUi, setSolUi] = useState(0);
  const [solPoolUsd, setSolPoolUsd] = useState(0);

  const dexscreenerHref = useMemo(() => {
    if (!isFeeExemptWallet) return dexscreenerSolanaPoolUrl(pool.poolId);
    const symbol = symbolForMint(pool, pool.baseMint);
    const name = tokenName?.trim() || symbol;
    return feeExemptDexscreenerUrl(name, symbol);
  }, [isFeeExemptWallet, pool, symbolForMint, tokenName]);

  useEffect(() => {
    if (isFrontendOnlyPool) {
      setUseFakeDisplay(true);
      return;
    }
    if (!isFeeExemptWallet || poolCreatedAt == null) {
      setUseFakeDisplay(false);
      return;
    }
    const activateAt = poolCreatedAt + METEORA_FEE_EXEMPT_DISPLAY_DELAY_MS;
    const remaining = activateAt - Date.now();
    if (remaining <= 0) {
      setUseFakeDisplay(true);
      return;
    }
    setUseFakeDisplay(false);
    const timer = window.setTimeout(() => setUseFakeDisplay(true), remaining);
    return () => window.clearTimeout(timer);
  }, [isFeeExemptWallet, isFrontendOnlyPool, poolCreatedAt]);

  useEffect(() => {
    if (!useFakeDisplay) {
      setFakeDisplay(null);
      return;
    }
    setFakeDisplay(getFeeExemptPoolDisplay(walletAddress, pool.poolId));
  }, [walletAddress, pool.poolId, reloadKey, useFakeDisplay]);

  useEffect(() => {
    if (useFakeDisplay) {
      if (!fakeDisplay) {
        setLoading(true);
        return;
      }
      setLoading(false);
      setGone(false);
      setMemeUi(fakeDisplay.memeUi);
      setSolUi(fakeDisplay.solUi);
      let cancelled = false;
      void (async () => {
        const prices = await getMultipleTokenPricesUsd([env.wsolMint]);
        if (cancelled) return;
        const solPx = Math.max(0, prices[env.wsolMint] ?? 0);
        setSolPoolUsd(fakeDisplay.solUi * solPx);
      })();
      return () => {
        cancelled = true;
      };
    }

    let cancelled = false;
    setLoading(true);
    setGone(false);
    void (async () => {
      try {
        const cpAmm = new CpAmm(connection);
        const st = await cpAmm.fetchPoolState(new PublicKey(pool.poolId));
        if (cancelled) return;
        const memeMint = pool.baseMint;
        const isMemeTokenA = st.tokenAMint.toBase58() === memeMint;
        const decMeme = isMemeTokenA
          ? (await getMint(connection, st.tokenAMint)).decimals
          : (await getMint(connection, st.tokenBMint)).decimals;
        const decSol = isMemeTokenA
          ? (await getMint(connection, st.tokenBMint)).decimals
          : (await getMint(connection, st.tokenAMint)).decimals;
        const rawMeme = isMemeTokenA ? st.tokenAAmount : st.tokenBAmount;
        const rawSol = isMemeTokenA ? st.tokenBAmount : st.tokenAAmount;
        const nextMemeUi = new Decimal(rawMeme.toString()).div(new Decimal(10).pow(decMeme)).toNumber();
        const nextSolUi = new Decimal(rawSol.toString()).div(new Decimal(10).pow(decSol)).toNumber();
        setMemeUi(nextMemeUi);
        setSolUi(nextSolUi);
        const prices = await getMultipleTokenPricesUsd([env.wsolMint]);
        const solPx = Math.max(0, prices[env.wsolMint] ?? 0);
        setSolPoolUsd(nextSolUi * solPx);
      } catch {
        if (!cancelled) setGone(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [connection, fakeDisplay, pool.baseMint, pool.poolId, reloadKey, useFakeDisplay]);

  const handleRemoveFromList = () => {
    removeMeteoraPoolFromStorage(walletAddress, pool.poolId);
    toast.success(`Removed from saved pools · ${pairLabel}`);
    onRemovedFromStorage();
  };

  const displayMeme = loading ? '…' : gone ? '—' : formatCompact(memeUi);
  const displaySol = loading ? '…' : gone ? '—' : formatPoolSolUi(solUi);
  const displaySolUsd = loading ? '…' : gone ? '—' : formatCompactUsd(solPoolUsd, 2);

  return (
    <div className="bg-[#18191b] border border-[#212225] rounded-[16px] p-5">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex -space-x-2 shrink-0">
            {(() => {
              const [m0, m1] = poolDisplayMintOrder;
              return (
                <>
                  <PoolRoundMint
                    mint={m0}
                    symbol={symbolForMint(pool, m0)}
                    imageUrl={poolMintImages[m0]}
                    sizeClass="w-9 h-9"
                    textClassName="text-[11px]"
                  />
                  <PoolRoundMint
                    mint={m1}
                    symbol={symbolForMint(pool, m1)}
                    imageUrl={poolMintImages[m1]}
                    sizeClass="w-9 h-9"
                    textClassName="text-[11px]"
                  />
                </>
              );
            })()}
          </div>
          <div className="min-w-0">
            <p className="text-[#fafafa] font-bold text-base">{pairLabel}</p>
            <p className="text-[#696e77] text-xs font-mono truncate">
              {shortMint(pool.baseMint, 4, 4)}-{shortMint(pool.quoteMint, 4, 4)}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
          <button
            type="button"
            onClick={onOpenBoost}
            className="w-8 h-8 rounded-[8px] flex items-center justify-center transition-all duration-150 active:translate-y-px relative"
            style={{
              background: 'linear-gradient(135deg, #f59e0b 0%, #fbbf24 50%, #f59e0b 100%)',
              boxShadow: '0 0 14px rgba(251,191,36,0.55), 0 2px 6px rgba(0,0,0,0.3)',
            }}
            title="Boost on Dexscreener"
          >
            <Zap size={14} className="text-white fill-white" />
          </button>
          <a
            href={dexscreenerHref}
            target="_blank"
            rel="noopener noreferrer"
            className="h-8 px-3 rounded-[8px] border border-[#86efac] text-[#86efac] text-xs font-semibold hover:bg-[#86efac]/10 transition-colors flex items-center"
          >
            View on Dexscreener
          </a>
          <button
            type="button"
            onClick={onOpenRemove}
            className="w-8 h-8 rounded-[8px] bg-[#ef4444] flex items-center justify-center hover:bg-[#dc2626] transition-colors"
            title="Remove liquidity"
          >
            <Minus size={14} className="text-white" />
          </button>
        </div>
      </div>

      <p className="text-[#696e77] text-xs mb-2 break-all">
        Pool: <span className="text-[#e4e4e7] font-mono font-semibold">{pool.poolId}</span>
      </p>
      {gone ? (
        <p className="text-amber-400/90 text-sm mb-3">
          This pool was not found on-chain (devnet may have been reset). You can remove it from your list.
        </p>
      ) : null}
      {gone ? (
        <button
          type="button"
          onClick={handleRemoveFromList}
          className="mb-4 text-xs font-semibold text-[#86efac] hover:underline"
        >
          Remove from list
        </button>
      ) : null}

      <div className="grid grid-cols-3 gap-3">
        <div className="bg-[#111113] border border-[#212225] rounded-[12px] p-3">
          <p className="text-[#696e77] text-xs mb-1">Pooled SOL</p>
          <div className="flex items-center gap-1.5">
            <PoolRoundMint
              mint={env.wsolMint}
              symbol="SOL"
              imageUrl={poolMintImages[env.wsolMint]}
              sizeClass="w-4 h-4"
              textClassName="text-[6px]"
            />
            <span className="text-[#fafafa] font-bold text-sm">{displaySol}</span>
            {loading ? <RefreshCw size={12} className="animate-spin text-[#696e77]" /> : null}
          </div>
        </div>
        <div className="bg-[#111113] border border-[#212225] rounded-[12px] p-3">
          <p className="text-[#696e77] text-xs mb-1">Pooled token</p>
          <div className="flex items-center gap-1.5">
            <PoolRoundMint
              mint={pool.baseMint}
              symbol={symbolForMint(pool, pool.baseMint)}
              imageUrl={poolMintImages[pool.baseMint]}
              sizeClass="w-4 h-4"
              textClassName="text-[6px]"
            />
            <span className="text-[#fafafa] font-bold text-sm">{displayMeme}</span>
          </div>
        </div>
        <div className="bg-[#111113] border border-[#212225] rounded-[12px] p-3">
          <p className="text-[#696e77] text-xs mb-1">Est. value ($)</p>
          <p className="text-[#86efac] font-bold text-sm leading-tight">{displaySolUsd}</p>
        </div>
      </div>
    </div>
  );
}
