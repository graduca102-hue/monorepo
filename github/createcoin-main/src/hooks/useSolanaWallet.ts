import { useConnection, useWallet } from '@solana/wallet-adapter-react';
import { LAMPORTS_PER_SOL, PublicKey } from '@solana/web3.js';
import { TOKEN_PROGRAM_ID, getAccount, getAssociatedTokenAddressSync, getMint } from '@solana/spl-token';
import { useCallback, useMemo, useState } from 'react';
import { useWalletModal } from '@solana/wallet-adapter-react-ui';
import { env } from '../config/env';
import { useVisibilityAwareInterval } from './useVisibilityAwareInterval';

export function useSolanaWallet() {
  const { connection } = useConnection();
  const wallet = useWallet();
  const { setVisible } = useWalletModal();
  const [solBalance, setSolBalance] = useState<number | null>(null);

  const refreshSol = useCallback(async () => {
    if (!wallet.publicKey) {
      setSolBalance(null);
      return;
    }
    const lamports = await connection.getBalance(wallet.publicKey, 'confirmed');
    setSolBalance(lamports / LAMPORTS_PER_SOL);
  }, [connection, wallet.publicKey]);

  useVisibilityAwareInterval(refreshSol, 15_000, wallet.connected);

  const shortAddress = useMemo(() => {
    const a = wallet.publicKey?.toBase58();
    if (!a) return '';
    return `${a.slice(0, 4)}...${a.slice(-4)}`;
  }, [wallet.publicKey]);

  const getTokenBalance = useCallback(
    async (mint: string) => {
      if (!wallet.publicKey) return { ui: 0, raw: '0' };
      const mintPk = new PublicKey(mint);
      const ata = getAssociatedTokenAddressSync(mintPk, wallet.publicKey, false, TOKEN_PROGRAM_ID);
      try {
        const acc = await getAccount(connection, ata);
        const dec = (await getMint(connection, mintPk)).decimals;
        const raw = acc.amount.toString();
        const ui = Number(raw) / 10 ** dec;
        return { ui, raw };
      } catch {
        return { ui: 0, raw: '0' };
      }
    },
    [connection, wallet.publicKey],
  );

  return {
    publicKey: wallet.publicKey,
    connected: wallet.connected,
    connecting: wallet.connecting,
    network: env.network,
    connect: () => setVisible(true),
    disconnect: () => wallet.disconnect(),
    signTransaction: wallet.signTransaction,
    signAllTransactions: wallet.signAllTransactions,
    sendTransaction: wallet.sendTransaction,
    solBalance,
    getTokenBalance,
    shortAddress,
    wallet,
  };
}
