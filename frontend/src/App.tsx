import { lazy, Suspense } from "react";
import { Outlet, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import { CorniceProvvisoria } from "./components/layout/CorniceProvvisoria";
import {
  AdminRoute,
  PartenariatiRoute,
  ProgettistaRoute,
  ProtectedRoute,
} from "./components/layout/guards";
import { RedirectLegacy } from "./components/layout/RedirectLegacy";
import Abbonamento from "./pages/Abbonamento";
import Collegati from "./pages/Collegati";
import MieiAddon from "./pages/MieiAddon";
import AccettaInvito from "./pages/AccettaInvito";
import Acquisti from "./pages/Acquisti";
import AdminAddon from "./pages/AdminAddon";
import AdminPagamenti from "./pages/AdminPagamenti";
import AdminPartenariati from "./pages/AdminPartenariati";
import AdminPiani from "./pages/AdminPiani";
import AdminUtenti from "./pages/AdminUtenti";
import AiCheck from "./pages/AiCheck";
import Azienda from "./pages/Azienda";
import Aziende from "./pages/Aziende";
import BandiList from "./pages/BandiList";
import BandoDetail from "./pages/BandoDetail";
import Calendario from "./pages/Calendario";
import CallPartenariato from "./pages/CallPartenariato";
import CallWizard from "./pages/CallWizard";
import Checkout from "./pages/Checkout";
import CheckoutEsito from "./pages/CheckoutEsito";
import ConfermaEmail from "./pages/ConfermaEmail";
import Consulenze from "./pages/Consulenze";
import ConsulenzaDetail from "./pages/ConsulenzaDetail";
import ConversazionePartenariato from "./pages/ConversazionePartenariato";
import Fatturazione from "./pages/Fatturazione";
import Home from "./pages/Home";
import Landing from "./pages/Landing";
import Richieste from "./pages/progettista/Richieste";
import RichiestaDetail from "./pages/progettista/RichiestaDetail";
import Login from "./pages/Login";
import NonDisponibile from "./pages/NonDisponibile";
import NotFound from "./pages/NotFound";
import Notifiche from "./pages/Notifiche";
import Partenariati from "./pages/Partenariati";
import Preferenze from "./pages/Preferenze";
import Profilo from "./pages/Profilo";
import RecuperaPassword from "./pages/RecuperaPassword";
import Register from "./pages/Register";
import ReimpostaPassword from "./pages/ReimpostaPassword";
import Salvati from "./pages/Salvati";
import SegnalazionePartenariato from "./pages/SegnalazionePartenariato";

// Vetrina dei componenti: solo in sviluppo e caricata a parte, così non entra
// nel bundle di produzione (il ramo è morto quando `DEV` è false). Sta fuori
// dall'autenticazione e dalla cornice: serve alla verifica visiva senza login.
const Vetrina = import.meta.env.DEV ? lazy(() => import("./pages/dev/Vetrina")) : null;

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/registrati" element={<Register />} />
      <Route path="/accetta-invito" element={<AccettaInvito />} />
      <Route path="/recupera-password" element={<RecuperaPassword />} />
      <Route path="/reimposta-password" element={<ReimpostaPassword />} />
      <Route path="/conferma-email" element={<ConfermaEmail />} />
      {Vetrina && (
        <Route
          path="/_vetrina"
          element={
            <Suspense fallback={null}>
              <Vetrina />
            </Suspense>
          }
        />
      )}
      <Route
        path="/app"
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      >
        <Route index element={<Home />} />

        {/* Pagine non ancora rifatte: nella cornice provvisoria con i margini
            di prima. Ogni pagina migrata a `Page` esce da questo gruppo. I
            guard stanno SOPRA la cornice, così un accesso negato rende
            `NonDisponibile` con la sola impaginazione di `Page`. */}
        <Route element={<CorniceProvvisoria />}>
          <Route path="bandi" element={<BandiList />} />
          <Route path="bandi/:slug" element={<BandoDetail />} />
          <Route path="salvati" element={<Salvati />} />
          <Route path="calendario" element={<Calendario />} />
          <Route path="notifiche" element={<Notifiche />} />
          <Route path="azienda" element={<Azienda />} />
          <Route path="aziende" element={<Aziende />} />
          <Route path="ai-check" element={<AiCheck />} />
          <Route path="preferenze" element={<Preferenze />} />
          <Route path="abbonamento" element={<Abbonamento />} />
          <Route path="addon" element={<MieiAddon />} />
          <Route path="collegati" element={<Collegati />} />
          <Route path="fatturazione" element={<Fatturazione />} />
          <Route path="checkout" element={<Checkout />} />
          <Route path="checkout/esito/:purchaseId" element={<CheckoutEsito />} />
          <Route path="acquisti" element={<Acquisti />} />
          {/* Il vecchio deep-link «Account collegati» (#collegati) ha ora una
              pagina sua: redirect, altrimenti il profilo di sempre. */}
          <Route
            path="profilo"
            element={
              <RedirectLegacy to="/app/collegati" quandoHash="#collegati">
                <Profilo />
              </RedirectLegacy>
            }
          />
          <Route path="consulenze" element={<Consulenze />} />
          <Route path="consulenze/:id" element={<ConsulenzaDetail />} />
        </Route>

        {/* Modulo partenariati: a modulo spento le pagine «non esistono». */}
        <Route
          element={
            <PartenariatiRoute>
              <Outlet />
            </PartenariatiRoute>
          }
        >
          <Route element={<CorniceProvvisoria />}>
            <Route path="partenariati" element={<Partenariati />} />
            <Route path="partenariati/call/nuova" element={<CallWizard />} />
            <Route path="partenariati/call/:id/modifica" element={<CallWizard />} />
            <Route path="partenariati/call/:id" element={<CallPartenariato />} />
            <Route path="partenariati/conversazioni/:id" element={<ConversazionePartenariato />} />
            <Route path="partenariati/segnalazioni/:id" element={<SegnalazionePartenariato />} />
          </Route>
        </Route>

        <Route
          element={
            <ProgettistaRoute>
              <Outlet />
            </ProgettistaRoute>
          }
        >
          <Route element={<CorniceProvvisoria />}>
            <Route path="progettista/richieste" element={<Richieste />} />
            <Route path="progettista/richieste/:id" element={<RichiestaDetail />} />
          </Route>
        </Route>

        <Route
          element={
            <AdminRoute>
              <Outlet />
            </AdminRoute>
          }
        >
          <Route element={<CorniceProvvisoria />}>
            <Route path="admin/utenti" element={<AdminUtenti />} />
            <Route path="admin/piani" element={<AdminPiani />} />
            <Route path="admin/addon" element={<AdminAddon />} />
            <Route path="admin/pagamenti" element={<AdminPagamenti />} />
          </Route>
          {/* Pannello admin dei partenariati (WP9): admin E modulo acceso. */}
          <Route
            element={
              <PartenariatiRoute>
                <Outlet />
              </PartenariatiRoute>
            }
          >
            <Route element={<CorniceProvvisoria />}>
              <Route path="admin/partenariati" element={<AdminPartenariati />} />
            </Route>
          </Route>
        </Route>

        {/* La gestione delle disponibilità vive nel calendario: redirect di
            cortesia per bookmark e vecchie notifiche, con query e hash. */}
        <Route
          path="progettista/disponibilita"
          element={<RedirectLegacy to="/app/calendario" />}
        />
        {/* Qualunque altro URL sotto /app: la stessa pagina degli accessi negati. */}
        <Route path="*" element={<NonDisponibile />} />
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
