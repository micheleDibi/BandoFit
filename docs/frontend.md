# Frontend

> Documento in costruzione: viene ampliato ad ogni fase di sviluppo.

Stack: Vite + React + TypeScript, Tailwind CSS, TanStack Query, react-router, `@supabase/supabase-js` (solo autenticazione), Axios.

## Design system

- **Colore primario**: blu `#1E5EFF` (hover `#164BDB`, tint di sfondo `#EEF3FF`); neutri slate; sfondo app `#F7F9FC`.
- **Semantici**: bando aperto = verde smeraldo, chiuso = grigio slate, in apertura prossimamente = ambra; scadenze imminenti (≤7 giorni) evidenziate in rosso/ambra.
- **Tipografia**: Sora per i titoli (600/700), Inter per il testo; cifre tabellari per gli importi.
- **Superfici**: card con radius 12px e ombre morbide; topbar bianca con bordo.
- **Localizzazione**: interfaccia in italiano; importi `Intl.NumberFormat('it-IT', {currency: 'EUR'})`, date in formato italiano.
- Ogni vista prevede stati di caricamento (skeleton), vuoto (con azione di reset) ed errore (con retry).
