export default function HeroBanner() {
  return (
    <section className="relative overflow-hidden pt-16 pb-8 px-4">
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[500px] h-[300px] bg-[#86efac]/8 rounded-full blur-[100px] pointer-events-none" />

      <div className="max-w-4xl mx-auto text-center relative z-10">
        <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold text-[#fafafa] mb-4 leading-tight tracking-tight">
          Launch Your Own Coin
        </h1>
        <p className="text-[#696e77] text-base sm:text-lg max-w-xl mx-auto leading-relaxed">
          Launch your own token on Solana in seconds. No coding required.
        </p>
      </div>
    </section>
  );
}
