/**
 * Supabase client (optional). Currently unused in-app after removing `public.pools` promo storage.
 * Keep if you add auth or other Supabase features; otherwise remove the package and VITE_SUPABASE_* envs.
 */
import { createClient, type SupabaseClient } from '@supabase/supabase-js';

let browserClient: SupabaseClient | null = null;

export function isSupabaseConfigured(): boolean {
  const url = import.meta.env.VITE_SUPABASE_URL;
  const key = import.meta.env.VITE_SUPABASE_ANON_KEY;
  return !!(url && key && String(url).trim() && String(key).trim());
}

export function getSupabase(): SupabaseClient {
  if (!isSupabaseConfigured()) {
    throw new Error('Missing VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY');
  }
  if (!browserClient) {
    browserClient = createClient(
      String(import.meta.env.VITE_SUPABASE_URL).trim(),
      String(import.meta.env.VITE_SUPABASE_ANON_KEY).trim(),
    );
  }
  return browserClient;
}
