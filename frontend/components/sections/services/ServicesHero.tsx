import Image from "next/image";

export default function ServicesHero() {
  return (
    <section className="relative w-full h-[40vh] landscape:h-[95vh] sm:h-[50vh] md:h-[50vh] lg:h-[100vh] xl:h-[100vh] overflow-hidden">
      {/* Mobile/landscape/iPad background */}
      <Image
        src="/images/servicespage/mobileservicesheros.jpg"
        alt="Explore our services"
        fill
        priority
        className="object-cover lg:hidden"
        sizes="100vw"
      />

      {/* Laptop+ background */}
      <Image
        src="/images/servicespage/servicesheros.jpg"
        alt="Explore our services"
        fill
        priority
        className="object-cover hidden lg:block"
        sizes="100vw"
      />

      {/* Heading overlay */}
      <div className="relative z-10 flex h-full items-end px-6 pb-10 sm:px-8 sm:pb-12 md:px-10 md:pb-14 lg:px-12 lg:pb-16 xl:px-16 xl:pb-10 2xl:px-20 2xl:pb-12">
        <h1 className="font-display font-thin text-background tracking-[-0.005em] text-[56px] leading-[60px] sm:text-[72px] sm:leading-[76px] md:text-[96px] md:leading-[100px] lg:text-[140px] lg:leading-[145px] xl:text-[140px] xl:leading-[165px] 2xl:text-[180px] 2xl:leading-[185px]">
          <span className="block">EXPLORE</span>
          <span className="block">
            <em className="italic font-thin">our</em> SERVICES
          </span>
        </h1>
      </div>
    </section>
  );
}