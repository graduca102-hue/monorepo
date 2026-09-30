import { useConnection, useWallet } from '@solana/wallet-adapter-react';
import { useCallback, useState } from 'react';
import { createToken, type CreationStage } from '../services/tokenService';

export function useTokenCreation() {
  const { connection } = useConnection();
  const wallet = useWallet();
  const [isCreating, setIsCreating] = useState(false);
  const [currentStage, setCurrentStage] = useState<CreationStage | null>(null);
  const [lastResult, setLastResult] = useState<{ mint: string; signature: string; isVirtual: boolean; confirmed: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runCreate = useCallback(
    async (params: Omit<Parameters<typeof createToken>[0], 'connection' | 'wallet' | 'onProgress'>) => {
      setIsCreating(true);
      setError(null);
      setCurrentStage(null);
      setLastResult(null);
      try {
        const res = await createToken({
          ...params,
          connection,
          wallet,
          onProgress: (s) => setCurrentStage(s),
        });
        setLastResult({
          mint: res.mint.toBase58(),
          signature: res.signature,
          isVirtual: res.isVirtual,
          confirmed: res.confirmed,
        });
        return res;
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Unknown error');
        setCurrentStage(null);
        throw e;
      } finally {
        setIsCreating(false);
      }
    },
    [connection, wallet],
  );

  return {
    createToken: runCreate,
    isCreating,
    currentStage,
    lastResult,
    error,
    clearError: () => setError(null),
  };
}
