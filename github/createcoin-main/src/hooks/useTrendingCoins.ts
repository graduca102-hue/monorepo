import { useCallback, useEffect, useState } from 'react';
import { getTrendingCoins, type PumpFunCoin } from '../services/pumpFunService';
import { useVisibilityAwareInterval } from './useVisibilityAwareInterval';

export function useTrendingCoins(tab: 'trending' | 'new') {
  const [coins, setCoins] = useState<PumpFunCoin[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (opts?: { force?: boolean }) => {
    const force = opts?.force ?? false;
    setLoading(true);
    setError(null);
    try {
      const sort = tab === 'trending' ? 'last_trade_timestamp' : 'created_timestamp';
      const order = 'DESC' as const;
      const list = await getTrendingCoins({ limit: 24, offset: 0, sort, order, force });
      setCoins(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
      setCoins([]);
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => {
    void load();
  }, [load]);

  useVisibilityAwareInterval(load, 60_000, true);

  return { coins, loading, error, refetch: () => load({ force: true }) };
}
