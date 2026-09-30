import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { SolanaNetwork } from '../config/env';

export type CreatedToken = {
  mint: string;
  name: string;
  symbol: string;
  decimals: number;
  supply: string;
  /** Local UI balance used by preview/demo flows. Falls back to `supply` when absent. */
  walletBalance?: string;
  metadataUri: string;
  imageUri: string;
  createdAt: number;
  signature: string;
  network: SolanaNetwork;
  source: 'created' | 'copied';
  sourceMint?: string;
  isVirtual?: boolean;
  authoritiesRevoked: { mint: boolean; freeze: boolean; update: boolean };
};

export type TransactionRecord = {
  signature: string;
  kind: string;
  at: number;
  network: SolanaNetwork;
};

type State = {
  userTokensByWallet: Record<string, CreatedToken[]>;
  transactionsByWallet: Record<string, TransactionRecord[]>;
  selectedTrendingMint: string | null;
  currentWallet: string | null;
  setCurrentWallet: (address: string | null) => void;
  addUserToken: (wallet: string, token: CreatedToken) => void;
  setUserTokenWalletBalance: (wallet: string, mint: string, walletBalance: string) => void;
  recordTransaction: (wallet: string, tx: TransactionRecord) => void;
  setSelectedTrendingMint: (mint: string | null) => void;
  clearWalletData: (address: string) => void;
  getCurrentWalletTokens: () => CreatedToken[];
  getCurrentWalletTransactions: () => TransactionRecord[];
};

export const useAppStore = create<State>()(
  persist(
    (set, get) => ({
      userTokensByWallet: {},
      transactionsByWallet: {},
      selectedTrendingMint: null,
      currentWallet: null,

      setCurrentWallet: (address) => set({ currentWallet: address }),

      addUserToken: (wallet, token) =>
        set((s) => {
          const prev = s.userTokensByWallet[wallet] ?? [];
          const next = [token, ...prev.filter((t) => t.mint !== token.mint)];
          return {
            userTokensByWallet: { ...s.userTokensByWallet, [wallet]: next },
          };
        }),

      setUserTokenWalletBalance: (wallet, mint, walletBalance) =>
        set((s) => {
          const prev = s.userTokensByWallet[wallet] ?? [];
          if (!prev.some((t) => t.mint === mint)) return s;
          return {
            userTokensByWallet: {
              ...s.userTokensByWallet,
              [wallet]: prev.map((t) => (t.mint === mint ? { ...t, walletBalance } : t)),
            },
          };
        }),

      recordTransaction: (wallet, tx) =>
        set((s) => {
          const prev = s.transactionsByWallet[wallet] ?? [];
          const merged = [tx, ...prev.filter((t) => t.signature !== tx.signature)].slice(0, 100);
          return {
            transactionsByWallet: { ...s.transactionsByWallet, [wallet]: merged },
          };
        }),

      setSelectedTrendingMint: (mint) => set({ selectedTrendingMint: mint }),

      clearWalletData: (address) =>
        set((s) => {
          const nextT = { ...s.userTokensByWallet };
          const nextX = { ...s.transactionsByWallet };
          delete nextT[address];
          delete nextX[address];
          return { userTokensByWallet: nextT, transactionsByWallet: nextX };
        }),

      getCurrentWalletTokens: () => {
        const w = get().currentWallet;
        if (!w) return [];
        return get().userTokensByWallet[w] ?? [];
      },

      getCurrentWalletTransactions: () => {
        const w = get().currentWallet;
        if (!w) return [];
        return get().transactionsByWallet[w] ?? [];
      },
    }),
    { name: 'createcoin-app' },
  ),
);
