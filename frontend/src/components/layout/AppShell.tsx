import { Menu } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, Outlet } from "react-router-dom";
import { cn } from "../../lib/cn";
import { InviteBanner } from "../shared/InviteBanner";
import { UpgradeBanner } from "../shared/UpgradeBanner";
import { Drawer } from "../ui/Drawer";
import { IconButton } from "../ui/IconButton";
import { Logo } from "./Logo";
import { NotificationBell } from "./NotificationBell";
import { Sidebar } from "./Sidebar";

/** Il logo su una piastrina chiara: il PNG del marchio è navy su trasparente e
 *  sulla barra navy sparirebbe. Variante orizzontale, che sta nei 56px della
 *  barra mobile e della testata del cassetto. */
function LogoSuPiastrina() {
  return (
    <span className="inline-flex rounded-control bg-sheet px-2.5 py-1">
      <Logo variant="horizontal" />
    </span>
  );
}

/** Classi del cassetto mobile in navy, come la barra laterale. Il pulsante
 *  «Chiudi» della testata è di `Drawer` (chiaro): qui lo si ripassa in bianco. */
const cassettoNavy = cn(
  "bg-navy-900 text-white",
  "[&_[aria-label=Chiudi]]:text-white/80 [&_[aria-label=Chiudi]:hover]:bg-white/10",
  "[&_[aria-label=Chiudi]:hover]:text-white [&_[aria-label=Chiudi]:focus-visible]:outline-white",
);

/** Cornice dell'app: barra laterale navy da 248px (da `lg`) e contenuto sul
 *  piano `desk`, dove stanno le card. Sotto `lg` la barra diventa un cassetto
 *  navy aperto da una barra in alto di 56px, anch'essa navy (menu, logo,
 *  Notifiche). Nessuna barra in alto su desktop. */
export function AppShell() {
  const [menuAperto, setMenuAperto] = useState(false);
  const chiudiMenu = () => setMenuAperto(false);

  // Da `lg` in su la barra laterale è visibile: il cassetto aperto si chiude da
  // solo quando la finestra supera la soglia (rotazione, ridimensionamento).
  useEffect(() => {
    if (!menuAperto) return;
    const desktop = window.matchMedia("(min-width: 1024px)");
    if (desktop.matches) {
      setMenuAperto(false);
      return;
    }
    const onChange = (e: MediaQueryListEvent) => {
      if (e.matches) setMenuAperto(false);
    };
    desktop.addEventListener("change", onChange);
    return () => desktop.removeEventListener("change", onChange);
  }, [menuAperto]);

  return (
    <div className="flex min-h-dvh bg-desk">
      <aside className="sticky top-0 hidden h-dvh w-[248px] shrink-0 flex-col overflow-y-auto bg-navy-900 px-3 pb-4 pt-5 text-white lg:flex">
        <Sidebar />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-40 flex h-14 shrink-0 items-center gap-2 bg-navy-900 px-2 text-white shadow-card lg:hidden">
          <IconButton
            label="Apri il menu"
            icon={<Menu />}
            onClick={() => setMenuAperto(true)}
            aria-haspopup="dialog"
            aria-expanded={menuAperto}
            className="text-white/80 hover:bg-white/10 hover:text-white focus-visible:outline-white"
          />
          <Link
            to="/app"
            aria-label="BandoFit — vai alla Home"
            className="rounded-control focus-visible:outline-white"
          >
            <LogoSuPiastrina />
          </Link>
          <div className="ml-auto">
            <NotificationBell variante="icona" />
          </div>
        </header>
        <Drawer
          open={menuAperto}
          onClose={chiudiMenu}
          lato="sinistra"
          titolo="Menu"
          intestazione={<LogoSuPiastrina />}
          className={cassettoNavy}
        >
          {/* Montata solo da aperto: una sola barra viva alla volta. */}
          {menuAperto && <Sidebar conLogo={false} onNavigate={chiudiMenu} />}
        </Drawer>

        {/* Banner globali con i margini e la larghezza di `Page` (elenco e
            dettaglio: 1280px di contenuto, centrati; 1360 = 1280 + i 40px di
            padding per lato da `lg`); senza banner il contenitore è vuoto e
            sparisce (`empty:hidden`). */}
        <div className="mx-auto flex w-full max-w-[1360px] flex-col gap-3 px-4 pt-6 sm:px-6 lg:px-10 lg:pt-8 empty:hidden">
          <InviteBanner />
          <UpgradeBanner />
        </div>

        <main className="flex flex-1 flex-col">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
