-- Run in Supabase → SQL Editor after deploying app changes.
-- My Pools lists rows where owner_wallet matches the connected Solana wallet.

alter table public.pools add column if not exists owner_wallet text;

create index if not exists pools_owner_wallet_idx on public.pools (owner_wallet);

comment on column public.pools.owner_wallet is 'Solana wallet address (base58) that owns this pool row';
