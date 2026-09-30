import { useMemo, useState, type ReactNode } from 'react';
import { RefreshCw } from 'lucide-react';
import { useTrendingCoins } from '../hooks/useTrendingCoins';

interface TrendingToken {
  id: string;
  name: string;
  symbol: string;
  description: string;
  imageUrl: string;
  marketCap: number;
  activityLabel: string;
  activityText: string;
  dexUrl: string | null;
  xUrl: string | null;
  telegramUrl: string | null;
}

function formatMarketCap(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
  return `$${n.toLocaleString()}`;
}

function formatCreatedAgo(ts: number): string {
  if (!ts) return '—';
  const sec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function normalizeSocialUrl(raw: string | undefined, kind: 'twitter' | 'telegram'): string | null {
  if (!raw) return null;
  const t = raw.trim();
  if (!t) return null;
  if (t.startsWith('http://') || t.startsWith('https://')) return t;
  if (kind === 'twitter') {
    const h = t.replace(/^@/, '');
    return `https://x.com/${h}`;
  }
  const h = t.replace(/^@/, '');
  return `https://t.me/${h}`;
}

function SocialLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="transition-opacity duration-150 hover:opacity-60"
      onClick={(e) => e.stopPropagation()}
    >
      {children}
    </a>
  );
}

function DexScreenerBadge() {
  return (
    <img
      src="/dexscreener.png"
      alt="Dexscreener"
      className="h-4 w-4 object-contain"
    />
  );
}

function XIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-4 h-4 fill-[#696e77]" xmlns="http://www.w3.org/2000/svg">
      <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.747l7.73-8.835L1.254 2.25H8.08l4.253 5.622 5.91-5.622Zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
    </svg>
  );
}

function TelegramIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-4 h-4 fill-[#696e77]" xmlns="http://www.w3.org/2000/svg">
      <path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm5.894 8.221-1.97 9.28c-.145.658-.537.818-1.084.508l-3-2.21-1.447 1.394c-.16.16-.295.295-.605.295l.213-3.053 5.56-5.023c.242-.213-.054-.333-.373-.12L7.28 13.47l-2.96-.924c-.643-.204-.657-.643.136-.953l11.57-4.461c.537-.194 1.006.131.868.089z" />
    </svg>
  );
}

function TokenCard({ token }: { token: TrendingToken }) {
  return (
    <div className="bg-[#18191b] rounded-[16px] p-5 flex flex-col gap-4 border border-[#212225] hover:border-[#272a2d] transition-all duration-150">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <img
            src={token.imageUrl}
            alt={token.name}
            className="w-12 h-12 rounded-full object-cover shrink-0 border border-[#212225]"
          />
          <div>
            <p className="text-[#fafafa] font-semibold text-sm leading-tight">{token.name}</p>
            <div className="flex items-center gap-1.5 mt-1 text-[#696e77] text-xs">
              <span className="text-[#b0b4ba]">{token.symbol}</span>
            </div>
          </div>
        </div>
        <div className="text-right shrink-0">
          <p className="text-[#696e77] text-xs mb-1">Market Cap</p>
          <p className="text-[#86efac] font-semibold text-sm">{formatMarketCap(token.marketCap)}</p>
        </div>
      </div>

      <p className="text-[#696e77] text-xs leading-relaxed">{token.description}</p>

      <div className="flex items-center gap-1.5 text-[#fb923c] text-xs">
        <span className="w-3 h-3 rounded-full border border-[#fb923c] flex items-center justify-center text-[7px] font-bold leading-none">
          !
        </span>
        <span>{token.activityLabel}: {token.activityText}</span>
      </div>

      <div className="flex items-center gap-2.5">
        {token.dexUrl && (
          <SocialLink href={token.dexUrl}>
            <DexScreenerBadge />
          </SocialLink>
        )}
        {token.xUrl && (
          <SocialLink href={token.xUrl}>
            <XIcon />
          </SocialLink>
        )}
        {token.telegramUrl && (
          <SocialLink href={token.telegramUrl}>
            <TelegramIcon />
          </SocialLink>
        )}
      </div>
    </div>
  );
}

const PLACEHOLDER_IMG =
  'https://images.pexels.com/photos/8370752/pexels-photo-8370752.jpeg?auto=compress&cs=tinysrgb&w=80&h=80&fit=crop';

export default function TrendingFeed() {
  const [tab, setTab] = useState<'trending' | 'new'>('trending');
  const [refreshing, setRefreshing] = useState(false);

  const { coins, loading, refetch } = useTrendingCoins(tab);

  const tokens: TrendingToken[] = useMemo(
    () =>
      coins.map((c) => ({
        id: c.mint,
        name: c.name,
        symbol: c.symbol.startsWith('$') ? c.symbol : `$${c.symbol}`,
        description: c.description || '—',
        imageUrl: c.imageUri || PLACEHOLDER_IMG,
        marketCap: c.marketCap,
        activityLabel: 'Pair age',
        activityText: formatCreatedAgo(c.createdAt || c.updatedAt),
        dexUrl: c.dexUrl,
        xUrl: normalizeSocialUrl(c.twitter, 'twitter'),
        telegramUrl: normalizeSocialUrl(c.telegram, 'telegram'),
      })),
    [coins],
  );

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await refetch();
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <section className="min-h-screen bg-[#111113] px-4 sm:px-8 pt-12 pb-20">
      <div className="max-w-6xl mx-auto">
        <h1 className="text-3xl font-bold text-[#fafafa] text-center mb-8 tracking-tight">Trending on Dexscreener</h1>

        <div className="flex items-center justify-end mb-6">
          <div className="hidden">
            <div className="flex items-center gap-1 bg-[#212225] rounded-[12px] p-1">
              {(['trending', 'new'] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`h-8 px-4 rounded-[10px] text-sm font-semibold transition-all duration-150 capitalize select-none ${
                    tab === t ? 'bg-[#86efac] text-[#052e16]' : 'text-[#696e77] hover:text-[#fafafa]'
                  }`}
                >
                  {t.charAt(0).toUpperCase() + t.slice(1)}
                </button>
              ))}
            </div>
          </div>

          <button
            type="button"
            onClick={handleRefresh}
            className="w-9 h-9 flex items-center justify-center rounded-[10px] bg-[#212225] hover:bg-[#272a2d] transition-all duration-150 active:translate-y-px"
          >
            <RefreshCw size={15} className={`text-[#b0b4ba] ${refreshing ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-48 bg-[#18191b] border border-[#212225] rounded-[16px] animate-pulse" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {tokens.map((token) => (
              <TokenCard key={token.id} token={token} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
