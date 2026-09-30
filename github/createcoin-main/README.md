# Memecoin launcher (Solana)

Vite + React + TypeScript SPA for creating SPL tokens with Metaplex metadata, optional pump.fun trending copy, and Raydium CPMM liquidity. Wallet flows use `@solana/wallet-adapter`; configuration is env-driven.

## Prerequisites

- Node.js 20+
- npm
- A Solana wallet (Phantom, Solflare, etc.)
- Optional: [Pinata](https://pinata.cloud) JWT for IPFS uploads

## Setup

1. Copy `.env.example` to `.env` and fill required values (at minimum both platform treasuries and RPC if not using public endpoints).

2. Install dependencies:

```bash
npm install
```

If postinstall scripts fail on your machine (for example Windows path length issues), you can use:

```bash
npm install --ignore-scripts
```

3. Start the dev server:

```bash
npm run dev
```

4. Quality checks:

```bash
npm run typecheck
npm run lint
npm run build
```

## Network and RPC

- Set `VITE_SOLANA_NETWORK` to `mainnet-beta` or `devnet`.
- **Required:** `VITE_SOLANA_RPC_URL_MAINNET` and `VITE_SOLANA_RPC_URL_DEVNET` — each must be a full `https://` URL with your provider key embedded (e.g. Helius `?api-key=...`). There is no separate API key variable and no fallback to public RPC; missing or placeholder URLs fail fast at startup.

Raydium’s cluster is **always** derived from `VITE_SOLANA_NETWORK` (mainnet-beta → Raydium `mainnet`, devnet → `devnet`). A mismatched `VITE_RAYDIUM_CLUSTER` in `.env` will fail startup with a clear error — remove that variable if present.

## Fees and treasury

All platform fees are numeric env vars (SOL). `src/services/feeService.ts` builds fee transfers to the treasury from `env.getTreasury()`.

Set **both** treasuries so fee routing works on whichever network you use:

- `VITE_PLATFORM_TREASURY_MAINNET`
- `VITE_PLATFORM_TREASURY_DEVNET`

The app UI loads even if these are missing; creating tokens, copying trending coins, or Raydium actions that charge a platform fee will throw until valid treasury addresses are set (copy values from `.env.example` into `.env`).

Remove obsolete Supabase variables from `.env` if they are still present; they are not used by this app.

Optional fee overrides: see `.env.example` (`VITE_FEE_*`).

## Persistence

`zustand` with `persist` (`createcoin-app` in localStorage) stores per-wallet created token summaries and recent transaction signatures. It is updated after successful create and copy flows.

## External APIs

- **Pump.fun (browser vs Postman):** Pump’s `*.pump.fun` APIs typically **omit `Access-Control-Allow-Origin`**, so the app calls same-origin **`/api/pump/*`** (Vite dev proxy + Vercel `api/pump/**/*.js` serverless routes). **`VITE_PUMPFUN_AUTH`** is sent as `Authorization` when set.
- **Jupiter Price v2:** `VITE_PRICE_API` for USD hints on pool cards.
- **Pinata:** `VITE_PINATA_JWT` for image and JSON metadata uploads during token creation and copy.

## Security notes

- Never commit `.env` or real JWTs / keys.
- Prefer dedicated low-privilege Pinata keys scoped to uploads only.
- Review transaction previews in the wallet before approving.
