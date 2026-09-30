import { Menu, X } from 'lucide-react';
import { useState, useRef, useEffect } from 'react';
import toast from 'react-hot-toast';
import { useSolanaWallet } from '../hooks/useSolanaWallet';

type Page = 'create' | 'liquidity' | 'trending';

interface HeaderProps {
  currentPage: Page;
  onPageChange: (page: Page) => void;
}

const navItems: { label: string; page: Page; hot?: boolean }[] = [
  { label: 'Create Coin', page: 'create' },
  { label: 'Liquidity', page: 'liquidity' },
  { label: 'Copy Trending', page: 'trending', hot: true },
];

export default function Header({ currentPage, onPageChange }: HeaderProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [walletMenuOpen, setWalletMenuOpen] = useState(false);
  const popRef = useRef<HTMLDivElement>(null);
  const { publicKey, connected, connect, disconnect, shortAddress } = useSolanaWallet();

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!popRef.current?.contains(e.target as Node)) setWalletMenuOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const fullAddr = publicKey?.toBase58() ?? '';
  return (
    <header className="border-b border-[#212225] bg-[#111113]/95 backdrop-blur-sm sticky top-0 z-50">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 h-14 flex items-center justify-between gap-4">
        <button
          onClick={() => onPageChange('create')}
          className="flex items-center gap-2 shrink-0"
        >
          <span className="w-9 h-9 rounded-[10px] overflow-hidden shrink-0 flex items-center justify-center">
            <img src="/logo.svg" alt="" className="w-full h-full object-contain" width={36} height={36} />
          </span>
          <span className="font-bold text-lg tracking-tight">
            <span className="text-[#fafafa]">create</span><span className="text-[#86efac]">coin</span><span className="text-[#fafafa]">.fun</span>
          </span>
        </button>

        <nav className="hidden md:flex items-center gap-1">
          {navItems.map(({ label, page, hot }) => (
            <button
              key={page}
              onClick={() => onPageChange(page)}
              className={`relative h-8 px-3 rounded-[10px] text-sm font-medium transition-all duration-150 ${
                currentPage === page
                  ? 'bg-[#212225] text-[#fafafa]'
                  : 'text-[#b0b4ba] hover:text-[#fafafa] hover:bg-[#212225]'
              }`}
            >
              {label}
              {hot && (
                <span className="absolute -top-1.5 -right-1 bg-[#ef4444] text-white text-[9px] font-bold px-1 py-px rounded-[2px] uppercase tracking-wide leading-none">
                  hot
                </span>
              )}
            </button>
          ))}
        </nav>

        <div className="flex items-center gap-2">
          {connected && publicKey ? (
            <div className="relative" ref={popRef}>
              <button
                type="button"
                onClick={() => setWalletMenuOpen((o) => !o)}
                className="h-9 px-3 rounded-[12px] bg-[#212225] border border-[#272a2d] text-sm font-medium text-[#fafafa] flex items-center gap-2 transition-all duration-150 hover:bg-[#272a2d]"
              >
                <span className="w-1.5 h-1.5 bg-[#86efac] rounded-full" />
                {shortAddress}
              </button>
              {walletMenuOpen && (
                <div className="absolute right-0 top-full mt-1 w-52 rounded-[12px] border border-[#272a2d] bg-[#18191b] shadow-[0_8px_32px_rgba(0,0,0,0.45)] py-1 z-50">
                  <button
                    type="button"
                    className="w-full px-4 py-3 text-left text-sm font-bold text-[#fafafa] hover:bg-[#212225] transition-colors"
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(fullAddr);
                        toast.success(`Address copied · ${shortAddress}`);
                        setWalletMenuOpen(false);
                      } catch {
                        toast.error('Could not copy');
                      }
                    }}
                  >
                    Copy address
                  </button>
                  <button
                    type="button"
                    className="w-full px-4 py-3 text-left text-sm font-bold text-[#fafafa] hover:bg-[#212225] transition-colors"
                    onClick={() => {
                      setWalletMenuOpen(false);
                      void disconnect();
                    }}
                  >
                    Disconnect
                  </button>
                </div>
              )}
            </div>
          ) : (
            <button
              onClick={() => connect()}
              className="h-9 px-4 rounded-[12px] bg-[#86efac] text-[#052e16] text-sm font-semibold transition-all duration-150 hover:bg-[#bbf7d0] active:translate-y-px select-none"
            >
              Connect Wallet
            </button>
          )}
          <button
            className="md:hidden w-9 h-9 flex items-center justify-center rounded-[10px] text-[#b0b4ba] hover:text-[#fafafa] hover:bg-[#212225] transition-all duration-150"
            onClick={() => setMenuOpen(!menuOpen)}
          >
            {menuOpen ? <X size={18} /> : <Menu size={18} />}
          </button>
        </div>
      </div>

      {menuOpen && (
        <div className="md:hidden border-t border-[#212225] bg-[#111113] px-4 py-3 flex flex-col gap-1">
          {navItems.map(({ label, page, hot }) => (
            <button
              key={page}
              onClick={() => { onPageChange(page); setMenuOpen(false); }}
              className={`h-10 px-3 rounded-[10px] text-sm font-medium text-left transition-all duration-150 flex items-center gap-2 ${
                currentPage === page
                  ? 'bg-[#212225] text-[#fafafa]'
                  : 'text-[#b0b4ba] hover:text-[#fafafa] hover:bg-[#212225]'
              }`}
            >
              {label}
              {hot && (
                <span className="bg-[#ef4444] text-white text-[9px] font-bold px-1 py-px rounded-[2px] uppercase tracking-wide leading-none">
                  hot
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </header>
  );
}
