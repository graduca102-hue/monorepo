/** Opens the Dexscreener pair page for a Solana pool or pair address. */
export function openDexscreenerPool(poolAddress: string): void {
  const id = poolAddress.trim();
  if (!id) return;
  window.open(
    `https://dexscreener.com/solana/${encodeURIComponent(id)}`,
    '_blank',
    'noopener,noreferrer',
  );
}
