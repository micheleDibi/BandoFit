import { useEffect, useState } from "react";
import { useAuth } from "./useAuth";

export type HashSessionState = "waiting" | "ready" | "invalid";

/** Gestione delle pagine di atterraggio dei link Supabase (invito, recovery,
 *  conferma email): cattura l'hash dell'URL PRIMA che supabase-js lo consumi
 *  (per riconoscere i link scaduti), attende la sessione creata dal link e
 *  recupera anche una sessione che arriva dopo il timeout (dispositivi lenti). */
export function useHashSession(timeoutMs = 6000): HashSessionState {
  const { session } = useAuth();
  const [initialHash] = useState(() => window.location.hash);
  const hashError =
    initialHash.includes("error_code=otp_expired") || initialHash.includes("error=access_denied");
  const [state, setState] = useState<HashSessionState>(hashError ? "invalid" : "waiting");

  useEffect(() => {
    if (hashError) return;
    if (session && (state === "waiting" || state === "invalid")) {
      setState("ready");
      return;
    }
    if (state !== "waiting") return;
    const timer = setTimeout(() => {
      setState((current) => (current === "waiting" ? "invalid" : current));
    }, timeoutMs);
    return () => clearTimeout(timer);
  }, [session, state, hashError, timeoutMs]);

  return state;
}
