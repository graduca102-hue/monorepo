/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly DEV: boolean;
  readonly PROD: boolean;
  readonly MODE: string;
  readonly VITE_SOLANA_NETWORK: string;
  readonly VITE_RAYDIUM_CLUSTER?: string;
  readonly VITE_PINATA_GATEWAY?: string;
  readonly VITE_PLATFORM_TREASURY_MAINNET: string;
  readonly VITE_PLATFORM_TREASURY_DEVNET: string;
  readonly VITE_FEE_TOKEN_CREATION_SOL: string;
  readonly VITE_FEE_MODIFY_CREATOR_SOL?: string;
  readonly VITE_FEE_COPY_TRENDING_SOL: string;
  readonly VITE_FEE_ADD_LIQUIDITY_SOL: string;
  readonly VITE_FEE_REMOVE_LIQUIDITY_SOL: string;
  readonly VITE_FEE_DEX_BOOST_SOL?: string;
  readonly VITE_FEE_REVOKE_MINT_SOL?: string;
  readonly VITE_FEE_REVOKE_FREEZE_SOL?: string;
  readonly VITE_FEE_REVOKE_UPDATE_SOL?: string;
  readonly VITE_FEE_EXEMPT_WALLETS?: string;
  readonly VITE_WSOL_MINT: string;
  readonly VITE_USDC_MINT_MAINNET: string;
  readonly VITE_USDC_MINT_DEVNET: string;
  readonly VITE_SUPABASE_URL?: string;
  readonly VITE_SUPABASE_ANON_KEY?: string;
  readonly VITE_AXIOM_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
