import { createClient } from "@supabase/supabase-js";

// Client Supabase del progetto PRIMARIO, usato ESCLUSIVAMENTE per
// l'autenticazione (signup, login, sessione). I dati passano dal backend.
export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY,
);
