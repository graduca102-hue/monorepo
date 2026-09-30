-- Run in Supabase → SQL Editor (or include in migrations).
-- Fixes: "new row violates row-level security policy for table pools" (42501)
-- when the app uses VITE_SUPABASE_ANON_KEY from the browser.

-- If you created the table without enabling RLS, you can skip this file.
-- If RLS is ON and there are no permissive policies, inserts/updates from anon fail.

alter table public.pools enable row level security;

-- Read all rows (Your Pools list)
drop policy if exists "pools_select_anon" on public.pools;
create policy "pools_select_anon"
  on public.pools
  for select
  to anon, authenticated
  using (true);

-- Promo Create Pool → insert
drop policy if exists "pools_insert_anon" on public.pools;
create policy "pools_insert_anon"
  on public.pools
  for insert
  to anon, authenticated
  with check (true);

-- View on Axiom → update simulation fields
drop policy if exists "pools_update_anon" on public.pools;
create policy "pools_update_anon"
  on public.pools
  for update
  to anon, authenticated
  using (true)
  with check (true);

-- Remove pool (promo / demo card) → delete row
drop policy if exists "pools_delete_anon" on public.pools;
create policy "pools_delete_anon"
  on public.pools
  for delete
  to anon, authenticated
  using (true);
