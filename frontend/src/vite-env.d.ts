/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL: string;
  readonly VITE_SUPABASE_ANON_KEY: string;
  readonly VITE_API_BASE_URL: string;
  /** Widget Revolut: 'sandbox' (default) o 'prod'. */
  readonly VITE_REVOLUT_MODE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
