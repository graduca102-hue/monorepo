import { useState } from 'react';
import { Copy, CheckCircle2, X } from 'lucide-react';
import { env } from '../config/env';

interface LaunchSuccessModalProps {
  mintAddress: string;
  onClose: () => void;
  /** Opens this app’s Liquidity page with this mint pre-selected. */
  onGoToLiquidity: (mint: string) => void;
  isVirtual?: boolean;
}

export default function LaunchSuccessModal({
  mintAddress,
  onClose,
  onGoToLiquidity,
}: LaunchSuccessModalProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(mintAddress);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const clusterQ = env.isDevnet() ? '?cluster=devnet' : '';
  const explorerUrl = `https://explorer.solana.com/address/${mintAddress}${clusterQ}`;
  const solscanUrl = `https://solscan.io/token/${mintAddress}${clusterQ}`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative w-full max-w-sm bg-[#1a1b1e] border border-[#2a2b2f] rounded-[20px] p-6 shadow-2xl">
        {/* Close */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-[#696e77] hover:text-[#fafafa] transition-colors duration-150"
        >
          <X size={18} />
        </button>

        {/* Title */}
        <h2 className="text-[#86efac] font-bold text-lg mb-5">Token Created Successfully!</h2>

        {/* Token Address */}
        <p className="text-[#fafafa] font-semibold text-sm mb-2">Token Address</p>
        <div className="flex items-center gap-2 bg-[#111113] border border-[#212225] rounded-[12px] px-3 py-2.5 mb-5">
          <p className="text-[#e4e4e7] font-mono text-sm flex-1 truncate">{mintAddress}</p>
          <button
            onClick={handleCopy}
            className="shrink-0 w-8 h-8 flex items-center justify-center rounded-[8px] bg-[#212225] hover:bg-[#272a2d] text-[#b0b4ba] hover:text-[#fafafa] transition-all duration-150"
          >
            {copied ? <CheckCircle2 size={14} className="text-[#86efac]" /> : <Copy size={14} />}
          </button>
        </div>

        {/* Buttons */}
        <div className="flex flex-col gap-2">
          <button
            type="button"
            onClick={() => {
              onGoToLiquidity(mintAddress);
              onClose();
            }}
            className="w-full h-11 rounded-[12px] bg-[#2563eb] hover:bg-[#1d4ed8] text-white text-sm font-semibold flex items-center justify-center transition-all duration-150 active:translate-y-px"
          >
            Create Liquidity Pool
          </button>
          <a
            href={explorerUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="w-full h-11 rounded-[12px] bg-[#212225] hover:bg-[#272a2d] text-[#e4e4e7] text-sm font-semibold flex items-center justify-center transition-all duration-150 active:translate-y-px"
          >
            View on Explorer
          </a>
          <a
            href={solscanUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="w-full h-11 rounded-[12px] bg-[#212225] hover:bg-[#272a2d] text-[#e4e4e7] text-sm font-semibold flex items-center justify-center transition-all duration-150 active:translate-y-px"
          >
            View on Solscan
          </a>
        </div>

      </div>
    </div>
  );
}
