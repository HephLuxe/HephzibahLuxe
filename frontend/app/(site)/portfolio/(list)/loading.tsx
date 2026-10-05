export default function PortfolioLoading() {
  return (
    <main>
      <section className="bg-background text-primary" aria-busy="true" aria-live="polite">
        <div className="px-4 sm:px-6 md:px-8 lg:px-12 xl:px-16 2xl:px-20 py-10 sm:py-12 md:py-12 lg:py-14 xl:py-20 2xl:py-24">
          <span className="sr-only">Loading portfolio…</span>
          <div className="h-4 w-40 bg-primary/10 animate-pulse" />
          <div className="mt-4 h-8 w-3/4 max-w-[900px] bg-primary/10 animate-pulse" />
          <div className="mt-12 lg:mt-16 grid grid-cols-2 lg:grid-cols-3 gap-x-4 sm:gap-x-5 md:gap-x-6 lg:gap-8 xl:gap-10 2xl:gap-12 gap-y-10">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className={i === 2 ? "hidden lg:block" : undefined}>
                <div className="aspect-[3/4] w-full bg-primary/10 animate-pulse" />
                <div className="mt-4 h-3 w-1/3 bg-primary/10 animate-pulse" />
                <div className="mt-2 h-5 w-5/6 bg-primary/10 animate-pulse" />
              </div>
            ))}
          </div>
        </div>
      </section>
    </main>
  );
}
