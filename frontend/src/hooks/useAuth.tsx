import type { Session } from "@supabase/supabase-js";
import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { supabase } from "../lib/supabase";

interface AuthContextValue {
  /** undefined = in caricamento, null = non autenticato */
  session: Session | null | undefined;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue>({
  session: undefined,
  signOut: async () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const queryClient = useQueryClient();
  const lastUserId = useRef<string | null>(null);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      lastUserId.current = data.session?.user?.id ?? null;
      setSession(data.session);
    });
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, newSession) => {
      // Se la sessione passa a un UTENTE DIVERSO (es. link d'invito aperto
      // nello stesso browser), la cache dell'utente precedente va svuotata.
      const newUserId = newSession?.user?.id ?? null;
      if (newUserId && lastUserId.current && newUserId !== lastUserId.current) {
        queryClient.clear();
      }
      if (newUserId) lastUserId.current = newUserId;
      setSession(newSession);
    });
    return () => subscription.unsubscribe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const signOut = async () => {
    await supabase.auth.signOut();
    queryClient.clear();
  };

  return (
    <AuthContext.Provider value={{ session, signOut }}>{children}</AuthContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}
