import { Navigate, Route, Routes } from "react-router-dom";
import { AdminRoute, ProgettistaRoute, ProtectedRoute } from "./components/layout/guards";
import { AppShell } from "./components/layout/AppShell";
import Abbonamento from "./pages/Abbonamento";
import AccettaInvito from "./pages/AccettaInvito";
import AdminAddon from "./pages/AdminAddon";
import AdminPiani from "./pages/AdminPiani";
import AdminUtenti from "./pages/AdminUtenti";
import AiCheck from "./pages/AiCheck";
import Azienda from "./pages/Azienda";
import BandiList from "./pages/BandiList";
import BandoDetail from "./pages/BandoDetail";
import Calendario from "./pages/Calendario";
import ConfermaEmail from "./pages/ConfermaEmail";
import Disponibilita from "./pages/progettista/Disponibilita";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import NotFound from "./pages/NotFound";
import Preferenze from "./pages/Preferenze";
import Profilo from "./pages/Profilo";
import RecuperaPassword from "./pages/RecuperaPassword";
import Register from "./pages/Register";
import ReimpostaPassword from "./pages/ReimpostaPassword";
import Salvati from "./pages/Salvati";

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
      <Route
        path="/app"
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      >
        <Route index element={<Navigate to="/app/bandi" replace />} />
        <Route path="bandi" element={<BandiList />} />
        <Route path="bandi/:slug" element={<BandoDetail />} />
        <Route path="salvati" element={<Salvati />} />
        <Route path="calendario" element={<Calendario />} />
        <Route path="azienda" element={<Azienda />} />
        <Route path="ai-check" element={<AiCheck />} />
        <Route path="preferenze" element={<Preferenze />} />
        <Route path="abbonamento" element={<Abbonamento />} />
        <Route path="profilo" element={<Profilo />} />
        <Route
          path="progettista/disponibilita"
          element={
            <ProgettistaRoute>
              <Disponibilita />
            </ProgettistaRoute>
          }
        />
        <Route
          path="admin/utenti"
          element={
            <AdminRoute>
              <AdminUtenti />
            </AdminRoute>
          }
        />
        <Route
          path="admin/piani"
          element={
            <AdminRoute>
              <AdminPiani />
            </AdminRoute>
          }
        />
        <Route
          path="admin/addon"
          element={
            <AdminRoute>
              <AdminAddon />
            </AdminRoute>
          }
        />
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
