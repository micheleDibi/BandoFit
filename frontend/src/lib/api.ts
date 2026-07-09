import axios, { AxiosError } from "axios";
import { supabase } from "./supabase";

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1",
});

api.interceptors.request.use(async (config) => {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    if (error.response?.status === 401) {
      // Sessione non più valida: pulizia e ritorno al login.
      await supabase.auth.signOut();
      if (!window.location.pathname.startsWith("/login")) {
        window.location.assign("/login");
      }
    }
    return Promise.reject(error);
  },
);

interface ApiErrorBody {
  error?: { code?: string; message?: string };
}

/** Estrae il messaggio leggibile dal formato errori del backend. */
export function apiErrorMessage(err: unknown, fallback = "Si è verificato un errore, riprova."): string {
  if (axios.isAxiosError(err)) {
    const body = err.response?.data as ApiErrorBody | undefined;
    if (body?.error?.message) return body.error.message;
    if (err.code === "ERR_NETWORK") return "Impossibile raggiungere il server. Controlla la connessione.";
    // Timeout della richiesta: il server può aver completato l'operazione.
    if (err.code === "ECONNABORTED")
      return "L'operazione sta impiegando più del previsto. Riprova tra qualche minuto: se è già andata a buon fine, i dati saranno aggiornati.";
  }
  return fallback;
}

/** Codice d'errore del backend (`draft_not_found`, `import_cooldown`, …), per
 *  chi deve reagire all'errore e non solo mostrarlo. */
export function apiErrorCode(err: unknown): string | undefined {
  if (!axios.isAxiosError(err)) return undefined;
  return (err.response?.data as ApiErrorBody | undefined)?.error?.code;
}
