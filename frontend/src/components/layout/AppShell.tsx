import { Menu } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, Outlet } from "react-router-dom";
import { InviteBanner } from "../shared/InviteBanner";
import { UpgradeBanner } from "../shared/UpgradeBanner";
import { Drawer } from "../ui/Drawer";
import { IconButton } from "../ui/IconButton";
import { Logo } from "./Logo";
import { NotificationBell } from "./NotificationBell";
import { Sidebar } from "./Sidebar";

/** Cornice dell'app: barra laterale da 248px su `desk` (da `lg`) e contenuto
 *  su `sheet`. Sotto `lg` la barra diventa un cassetto aperto da una barra in
 *  alto di 56px (menu, logo, Notifiche). Nessuna barra in alto su desktop. */
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
    <div className="flex min-h-dvh bg-sheet">
      <aside className="sticky top-0 hidden h-dvh w-[248px] shrink-0 flex-col overflow-y-auto border-r border-line bg-desk px-3 pb-4 pt-5 lg:flex">
        <Sidebar />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-40 flex h-14 shrink-0 items-center gap-2 border-b border-line bg-sheet px-2 lg:hidden">
          <IconButton
            label="Apri il menu"
            icon={<Menu />}
            onClick={() => setMenuAperto(true)}
            aria-haspopup="dialog"
            aria-expanded={menuAperto}
          />
          <Link to="/app" aria-label="BandoFit — vai alla Home" className="rounded-mark">
            <Logo variant="stack" />
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
          intestazione={<Logo variant="stack" />}
        >
          {/* Montata solo da aperto: una sola barra viva alla volta. */}
          {menuAperto && <Sidebar conLogo={false} onNavigate={chiudiMenu} />}
        </Drawer>

        {/* Banner globali con i margini e la larghezza di `Page` (elenco e
            dettaglio, 1112px: `box-content` la conta senza il padding); senza
            banner il contenitore è vuoto e sparisce (`empty:hidden`). */}
        <div className="box-content flex max-w-[1112px] flex-col gap-3 px-4 pt-6 sm:px-6 lg:px-10 lg:pt-8 empty:hidden">
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
