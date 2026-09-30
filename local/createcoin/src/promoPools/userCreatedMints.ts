import { PublicKey } from '@solana/web3.js';
import { env } from '../config/env';

const STORAGE_VERSION = 1;

function storageKey(wallet58: string): string {
  return `createcoin_user_created_mints_v${STORAGE_VERSION}_${env.network}_${wallet58}`;
}

/** Call after a successful in-app token mint so flows can treat the SPL as user-created (real token). */
export function registerUserCreatedTokenMint(owner: PublicKey, mint: PublicKey | string): void {
  if (typeof localStorage === 'undefined') return;
  const mint58 = typeof mint === 'string' ? mint : mint.toBase58();
  const key = storageKey(owner.toBase58());
  try {
    const raw = localStorage.getItem(key);
    const list: string[] = raw ? (JSON.parse(raw) as string[]) : [];
    if (!Array.isArray(list)) return;
    if (!list.includes(mint58)) {
      list.push(mint58);
      localStorage.setItem(key, JSON.stringify(list));
    }
  } catch {
    /* ignore corrupt storage */
  }
}

export function isUserCreatedTokenMint(owner: PublicKey | null | undefined, mint: string | null | undefined): boolean {
  if (!owner || !mint || typeof localStorage === 'undefined') return false;
  try {
    const raw = localStorage.getItem(storageKey(owner.toBase58()));
    if (!raw) return false;
    const list: unknown = JSON.parse(raw);
    if (!Array.isArray(list)) return false;
    return list.includes(mint);
  } catch {
    return false;
  }
}
