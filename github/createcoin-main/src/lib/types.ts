/**
 * Row shape for public.pools (demo metadata + simulation flags).
 *
 * Example DDL:
 * create table public.pools (
 *   pool_id text primary key,
 *   token_symbol text not null,
 *   token_name text not null,
 *   token_address text not null,
 *   token_image_url text,
 *   initial_sol_amount numeric not null,
 *   initial_token_amount numeric not null,
 *   initial_market_cap_usd numeric not null,
 *   initial_liquidity_usd numeric not null,
 *   current_market_cap_usd numeric not null,
 *   current_liquidity_usd numeric not null,
 *   current_price_usd numeric not null,
 *   current_supply numeric not null,
 *   ath_market_cap_usd numeric,
 *   is_simulation_active boolean not null default false,
 *   simulation_started_at timestamptz,
 *   simulation_ends_at timestamptz,
 *   created_at timestamptz not null default now()
 * );
 */
export type PoolRow = {
  pool_id: string;
  token_symbol: string;
  token_name: string;
  token_address: string;
  token_image_url: string | null;
  initial_sol_amount: number;
  initial_token_amount: number;
  initial_market_cap_usd: number;
  initial_liquidity_usd: number;
  current_market_cap_usd: number;
  current_liquidity_usd: number;
  current_price_usd: number;
  /** Circulating supply for MC display (= POOL_TOTAL_SUPPLY at launch). */
  current_supply: number;
  ath_market_cap_usd: number;
  is_simulation_active: boolean;
  simulation_started_at: string | null;
  simulation_ends_at: string | null;
  created_at: string;
  /** Per-pool UI stats; set once at creation. */
  top_10_holders_pct: number;
  dev_holders_pct: number;
  snipers_pct: number;
  insiders_pct: number;
  bundlers_pct: number;
  lp_burned_pct: number;
  holders_count: number;
  pro_traders_count: number;
  dex_paid: boolean;
  global_fees_paid: number;
};
