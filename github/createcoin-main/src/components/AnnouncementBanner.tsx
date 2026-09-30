import { env } from '../config/env';

export default function AnnouncementBanner() {
  const fee = env.fees.tokenCreationSol;
  const text = `⚠️ LAST CHANCE: ${fee} SOL CREATE COIN FEE (BACK TO 0.2 SOL IN 24H)`;

  return (
    <div className="bg-[#166534]/30 border-b border-[#166534]/40 py-2 overflow-hidden">
      {/* Desktop: static centered */}
      <div className="hidden sm:flex max-w-6xl mx-auto px-4 items-center justify-center">
        <p className="text-[#86efac] text-xs font-semibold tracking-wide text-center">
          {text}
        </p>
      </div>

      {/* Mobile: marquee scroll */}
      <div className="sm:hidden flex items-center">
        <div className="flex animate-marquee whitespace-nowrap">
          <span className="text-[#86efac] text-xs font-semibold tracking-wide px-8">{text}</span>
          <span className="text-[#86efac] text-xs font-semibold tracking-wide px-8">{text}</span>
        </div>
      </div>

      <style>{`
        @keyframes marquee {
          0% { transform: translateX(0); }
          100% { transform: translateX(-50%); }
        }
        .animate-marquee {
          animation: marquee 18s linear infinite;
        }
      `}</style>
    </div>
  );
}
