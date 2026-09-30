import { useConnection, useWallet } from '@solana/wallet-adapter-react';
import { type Commitment, PublicKey } from '@solana/web3.js';
import { useCallback, useEffect, useState } from 'react';
import { addLiquidity, getUserPools, removeLiquidity, resetRaydium, type UserPoolPosition } from '../services/raydiumService';
import { enrichMeteoraPoolSymbols, fetchMeteoraUserPoolPositions } from '../services/meteoraUserPools';
import { loadMeteoraPoolsFromStorage, storedMeteoraPoolToUserPoolPosition } from '../services/meteoraPoolStorage';
import type { QuoteCurrency } from '../utils/quoteCurrency';
import { useVisibilityAwareInterval } from './useVisibilityAwareInterval';

/** Include localStorage Meteora rows not yet visible on-chain (brief RPC lag after create). */
const METEORA_STORAGE_FALLBACK_MS = 3 * 60 * 1000;

export function useRaydium() {
  const { connection } = useConnection();
  const wallet = useWallet();
  const [userPools, setUserPools] = useState<UserPoolPosition[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshUserPools = useCallback(
    async (commitment: Commitment = 'confirmed') => {
      setIsLoading(true);
      setError(null);
      try {
        const pk = wallet.publicKey?.toBase58();
        const meteoraStoredRows = pk ? loadMeteoraPoolsFromStorage(pk) : [];
        const meteoraStoredCards = meteoraStoredRows.map(storedMeteoraPoolToUserPoolPosition);

        if (!wallet.publicKey) {
          setUserPools(await enrichMeteoraPoolSymbols(connection, meteoraStoredCards));
          return;
        }

        const [meteoraChain, raydium] = await Promise.all([
          fetchMeteoraUserPoolPositions(connection, wallet.publicKey, commitment).catch((e) => {
            console.warn('[Meteora] fetchMeteoraUserPoolPositions failed', e);
            return [] as UserPoolPosition[];
          }),
          getUserPools(connection, wallet.publicKey, commitment),
        ]);

        const byLpMint = new Map<string, UserPoolPosition>();
        for (const p of meteoraChain) byLpMint.set(p.lpMint, p);

        const now = Date.now();
        for (const row of meteoraStoredRows) {
          if (byLpMint.has(row.lpMint)) continue;
          if (row.frontendOnly) {
            byLpMint.set(row.lpMint, storedMeteoraPoolToUserPoolPosition(row));
            continue;
          }
          const created = Date.parse(row.createdAt);
          if (!Number.isFinite(created) || now - created > METEORA_STORAGE_FALLBACK_MS) continue;
          byLpMint.set(row.lpMint, storedMeteoraPoolToUserPoolPosition(row));
        }

        const meteoraCards = await enrichMeteoraPoolSymbols(connection, [...byLpMint.values()]);
        const combined = [...meteoraCards, ...raydium];
        combined.sort((a, b) => b.totalUsdValue - a.totalUsdValue);
        setUserPools(combined);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load pools');
      } finally {
        setIsLoading(false);
      }
    },
    [connection, wallet.publicKey],
  );

  useEffect(() => {
    void refreshUserPools();
  }, [refreshUserPools]);

  useVisibilityAwareInterval(refreshUserPools, 60_000, wallet.connected);

  useEffect(() => {
    if (!wallet.connected) resetRaydium();
  }, [wallet.connected, wallet.publicKey]);

  const add = useCallback(
    async (p: {
      baseMint: string;
      quoteCurrency: QuoteCurrency;
      baseAmount: string | number;
      quoteAmount: string | number;
      slippagePercent?: number;
    }) => {
      if (!wallet.publicKey) throw new Error('Connect wallet');
      const result = await addLiquidity({
        connection,
        wallet,
        baseMint: new PublicKey(p.baseMint),
        quoteCurrency: p.quoteCurrency,
        baseAmount: p.baseAmount,
        quoteAmount: p.quoteAmount,
        slippagePercent: p.slippagePercent ?? 1,
      });
      resetRaydium();
      await refreshUserPools('confirmed');
      return result;
    },
    [connection, wallet, refreshUserPools],
  );

  const remove = useCallback(
    async (p: { poolId: string; lpAmountRaw: string; slippagePercent?: number }) => {
      if (!wallet.publicKey) throw new Error('Connect wallet');
      const result = await removeLiquidity({
        connection,
        wallet,
        poolId: p.poolId,
        lpAmountRaw: p.lpAmountRaw,
        slippagePercent: p.slippagePercent ?? 1,
      });
      resetRaydium();
      await refreshUserPools('confirmed');
      return result;
    },
    [connection, wallet, refreshUserPools],
  );

  return {
    addLiquidity: add,
    removeLiquidity: remove,
    userPools,
    refreshUserPools,
    isLoading,
    error,
  };
}
