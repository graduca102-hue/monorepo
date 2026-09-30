import { ConnectionProvider, WalletProvider, useWallet } from '@solana/wallet-adapter-react';
import { WalletModalProvider, useWalletModal } from '@solana/wallet-adapter-react-ui';
import {
  CoinbaseWalletAdapter,
  LedgerWalletAdapter,
  NightlyWalletAdapter,
  PhantomWalletAdapter,
  SolflareWalletAdapter,
  TrustWalletAdapter,
} from '@solana/wallet-adapter-wallets';
import { WalletAdapterNetwork } from '@solana/wallet-adapter-base';
import { useEffect, useMemo } from 'react';
import { env } from '../config/env';
import { API_PROXY_HEADER_NAME, API_PROXY_HEADER_VALUE } from '../services/apiProxy';

import '@solana/wallet-adapter-react-ui/styles.css';

/** Close the wallet *picker* after connect. Does not open the picker on load or when disconnected. */
function WalletModalCloseWhenConnected() {
  const { connected } = useWallet();
  const { setVisible } = useWalletModal();

  useEffect(() => {
    if (connected) setVisible(false);
  }, [connected, setVisible]);

  return null;
}

export default function SolanaWalletProvider({ children }: { children: React.ReactNode }) {
  const endpoint = env.getRpcUrl();
  const wallets = useMemo(
    () => [
      new PhantomWalletAdapter(),
      new SolflareWalletAdapter({
        network: env.isDevnet() ? WalletAdapterNetwork.Devnet : WalletAdapterNetwork.Mainnet,
      }),
      new NightlyWalletAdapter(),
      new TrustWalletAdapter(),
      new CoinbaseWalletAdapter(),
      new LedgerWalletAdapter(),
    ],
    [],
  );

  return (
    <ConnectionProvider
      endpoint={endpoint}
      config={{
        commitment: 'confirmed',
        httpHeaders: {
          [API_PROXY_HEADER_NAME]: API_PROXY_HEADER_VALUE,
        },
      }}
    >
      <WalletProvider wallets={wallets} autoConnect>
        <WalletModalProvider>
          <WalletModalCloseWhenConnected />
          {children}
        </WalletModalProvider>
      </WalletProvider>
    </ConnectionProvider>
  );
}
