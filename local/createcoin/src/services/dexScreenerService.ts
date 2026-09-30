import axios from 'axios';

const DEXSCREENER_API = 'https://api.dexscreener.com';
const CACHE_MS = 30_000;
const TOKEN_BATCH_SIZE = 30;
const DEFAULT_POOL_LIMIT = 264;

export type DexScreenerCoin = {
  mint: string;
  name: string;
  symbol: string;
  description: string;
  imageUri: string;
  marketCap: number;
  priceUsd: number;
  volume24h: number;
  createdAt: number;
  updatedAt: number;
  twitter?: string;
  telegram?: string;
  website?: string;
  dexUrl: string;
};

type DexScreenerListTab = 'trending' | 'new';

type DexScreenerProfile = {
  chainId?: unknown;
  tokenAddress?: unknown;
  url?: unknown;
  icon?: unknown;
  header?: unknown;
  description?: unknown;
  links?: unknown;
  updatedAt?: unknown;
  claimDate?: unknown;
};

type DexScreenerPair = {
  url?: unknown;
  priceUsd?: unknown;
  volume?: { h24?: unknown } | null;
  liquidity?: { usd?: unknown } | null;
  marketCap?: unknown;
  fdv?: unknown;
  pairCreatedAt?: unknown;
  baseToken?: { address?: unknown; name?: unknown; symbol?: unknown } | null;
  quoteToken?: { address?: unknown; name?: unknown; symbol?: unknown } | null;
  info?: {
    imageUrl?: unknown;
    websites?: { url?: unknown }[] | null;
    socials?: { type?: unknown; url?: unknown }[] | null;
  } | null;
};

const cache = new Map<string, { at: number; data: DexScreenerCoin[] }>();

function num(v: unknown, d = 0): number {
  const n = typeof v === 'number' ? v : typeof v === 'string' ? Number(v) : NaN;
  return Number.isFinite(n) ? n : d;
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : v == null ? '' : String(v);
}

function isHttpUrl(v: string): boolean {
  return v.startsWith('https://') || v.startsWith('http://');
}

function pickFirstHttp(...values: string[]): string {
  return values.find((value) => isHttpUrl(value)) ?? '';
}

function socialsFromLinks(links: unknown): { twitter?: string; telegram?: string; website?: string } {
  if (!Array.isArray(links)) return {};
  const out: { twitter?: string; telegram?: string; website?: string } = {};
  for (const entry of links) {
    if (!entry || typeof entry !== 'object') continue;
    const obj = entry as Record<string, unknown>;
    const type = str(obj.type).toLowerCase();
    const url = str(obj.url);
    if (!isHttpUrl(url)) continue;
    if (type === 'twitter' && !out.twitter) out.twitter = url;
    else if (type === 'telegram' && !out.telegram) out.telegram = url;
    else if (!type && !out.website) out.website = url;
  }
  return out;
}

function socialsFromPairInfo(info: DexScreenerPair['info']): {
  twitter?: string;
  telegram?: string;
  website?: string;
} {
  const out: { twitter?: string; telegram?: string; website?: string } = {};
  if (Array.isArray(info?.websites)) {
    for (const row of info.websites) {
      const url = str(row?.url);
      if (isHttpUrl(url)) {
        out.website = url;
        break;
      }
    }
  }
  if (Array.isArray(info?.socials)) {
    for (const row of info.socials) {
      const type = str(row?.type).toLowerCase();
      const url = str(row?.url);
      if (!isHttpUrl(url)) continue;
      if (type === 'twitter' && !out.twitter) out.twitter = url;
      else if (type === 'telegram' && !out.telegram) out.telegram = url;
    }
  }
  return out;
}

function bestPairScore(pair: DexScreenerPair): number {
  return num(pair.liquidity?.usd) * 10 + num(pair.volume?.h24) + num(pair.marketCap ?? pair.fdv);
}

function pickBestPair(pairs: DexScreenerPair[]): DexScreenerPair | null {
  if (!pairs.length) return null;
  return [...pairs].sort((a, b) => bestPairScore(b) - bestPairScore(a))[0] ?? null;
}

function matchingTokenSide(
  pair: DexScreenerPair,
  tokenAddress: string,
): { address?: unknown; name?: unknown; symbol?: unknown } | null {
  if (str(pair.baseToken?.address) === tokenAddress) return pair.baseToken ?? null;
  if (str(pair.quoteToken?.address) === tokenAddress) return pair.quoteToken ?? null;
  return pair.baseToken ?? pair.quoteToken ?? null;
}

async function axiosRetry<T>(fn: () => Promise<T>): Promise<T> {
  let last: unknown;
  for (let i = 0; i < 3; i++) {
    try {
      return await fn();
    } catch (e) {
      last = e;
      await new Promise((r) => setTimeout(r, 400 * 2 ** i));
    }
  }
  throw last;
}

async function dexGet<T>(path: string): Promise<T> {
  const res = await axios.get<T>(`${DEXSCREENER_API}${path}`, {
    headers: { Accept: 'application/json' },
    timeout: 10_000,
  });
  return res.data;
}

function normalizeSeedList(data: unknown): DexScreenerProfile[] {
  if (Array.isArray(data)) return data as DexScreenerProfile[];
  if (data && typeof data === 'object' && Array.isArray((data as { data?: unknown[] }).data)) {
    return (data as { data: DexScreenerProfile[] }).data;
  }
  return [];
}

async function fetchSeedList(path: string): Promise<DexScreenerProfile[]> {
  const data = await axiosRetry(() => dexGet<unknown>(path));
  return normalizeSeedList(data);
}

async function fetchSeedPool(tab: DexScreenerListTab): Promise<DexScreenerProfile[]> {
  const paths =
    tab === 'trending'
      ? [
          '/token-boosts/top/v1',
          '/token-boosts/latest/v1',
          '/community-takeovers/latest/v1',
          '/token-profiles/latest/v1',
        ]
      : ['/token-profiles/latest/v1', '/community-takeovers/latest/v1', '/token-boosts/latest/v1'];

  const lists = await Promise.all(paths.map((path) => fetchSeedList(path)));
  return lists.flat();
}

function takeWrapped<T>(items: T[], limit: number, offset: number): T[] {
  if (items.length <= limit) return items;
  const start = ((offset % items.length) + items.length) % items.length;
  const out: T[] = [];
  for (let i = 0; i < Math.min(limit, items.length); i++) {
    out.push(items[(start + i) % items.length]!);
  }
  return out;
}

async function fetchPairsByMint(mints: string[]): Promise<Map<string, DexScreenerPair[]>> {
  const out = new Map<string, DexScreenerPair[]>();
  for (let i = 0; i < mints.length; i += TOKEN_BATCH_SIZE) {
    const batch = mints.slice(i, i + TOKEN_BATCH_SIZE);
    const data = await axiosRetry(() =>
      dexGet<unknown>(`/tokens/v1/solana/${encodeURIComponent(batch.join(','))}`),
    );
    const rows = Array.isArray(data) ? (data as DexScreenerPair[]) : [];
    for (const pair of rows) {
      const baseAddress = str(pair.baseToken?.address);
      const quoteAddress = str(pair.quoteToken?.address);
      for (const mint of batch) {
        if (mint !== baseAddress && mint !== quoteAddress) continue;
        const list = out.get(mint) ?? [];
        list.push(pair);
        out.set(mint, list);
      }
    }
  }
  return out;
}

function normalizeCoin(source: DexScreenerProfile, pair: DexScreenerPair | null): DexScreenerCoin | null {
  const mint = str(source.tokenAddress);
  if (!mint || str(source.chainId).toLowerCase() !== 'solana') return null;

  const pairInfo = pair?.info ?? null;
  const tokenSide = pair ? matchingTokenSide(pair, mint) : null;
  const linkSocials = socialsFromLinks(source.links);
  const pairSocials = socialsFromPairInfo(pairInfo);

  return {
    mint,
    name: str(tokenSide?.name) || mint.slice(0, 6),
    symbol: str(tokenSide?.symbol) || mint.slice(0, 4),
    description: str(source.description),
    imageUri: pickFirstHttp(str(pairInfo?.imageUrl), str(source.icon), str(source.header)),
    marketCap: num(pair?.marketCap ?? pair?.fdv),
    priceUsd: num(pair?.priceUsd),
    volume24h: num(pair?.volume?.h24),
    createdAt: num(pair?.pairCreatedAt),
    updatedAt: Date.parse(str(source.updatedAt ?? source.claimDate)) || 0,
    twitter: pairSocials.twitter ?? linkSocials.twitter,
    telegram: pairSocials.telegram ?? linkSocials.telegram,
    website: pairSocials.website ?? linkSocials.website,
    dexUrl: str(pair?.url) || str(source.url) || `https://dexscreener.com/solana/${encodeURIComponent(mint)}`,
  };
}

export async function getDexScreenerCoins(params?: {
  tab?: DexScreenerListTab;
  limit?: number;
  offset?: number;
  force?: boolean;
}): Promise<DexScreenerCoin[]> {
  const tab = params?.tab ?? 'trending';
  const limit = params?.limit ?? 24;
  const offset = params?.offset ?? 0;
  const force = params?.force ?? false;
  const cacheKey = `${tab}-${limit}-${offset}`;

  if (!force) {
    const hit = cache.get(cacheKey);
    if (hit && Date.now() - hit.at < CACHE_MS) return hit.data;
  }

  const seeds = (await fetchSeedPool(tab))
    .filter((row) => str(row.chainId).toLowerCase() === 'solana')
    .filter((row) => !!str(row.tokenAddress));

  const uniqueMints = [...new Set(seeds.map((row) => str(row.tokenAddress)).filter(Boolean))].slice(0, DEFAULT_POOL_LIMIT);
  const selectedMints = takeWrapped(uniqueMints, limit, offset);
  const pairsByMint = await fetchPairsByMint(selectedMints);

  const coins = selectedMints
    .map((mint) => {
      const source = seeds.find((row) => str(row.tokenAddress) === mint);
      if (!source) return null;
      return normalizeCoin(source, pickBestPair(pairsByMint.get(mint) ?? []));
    })
    .filter((coin): coin is DexScreenerCoin => !!coin);

  cache.set(cacheKey, { at: Date.now(), data: coins });
  return coins;
}
