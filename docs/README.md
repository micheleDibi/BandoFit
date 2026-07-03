# Documentazione BandoFit

Indice della documentazione del progetto.

| Documento | Contenuto |
|---|---|
| [architecture.md](architecture.md) | Architettura complessiva: componenti, flusso di autenticazione, i due database, decisioni progettuali |
| [database.md](database.md) | Schema del DB primario (utenti, abbonamenti) e riferimento al DB secondario (catalogo bandi, sola lettura) |
| [api.md](api.md) | Tutti gli endpoint del backend: parametri, esempi di richiesta/risposta, codici di errore |
| [frontend.md](frontend.md) | Route, design system (colori, tipografia), pattern (filtri nell'URL, gestione stato) |
| [setup.md](setup.md) | Creazione del progetto Supabase primario, migrazioni, variabili d'ambiente, primo admin, avvio locale |
| [deploy.md](deploy.md) | Deploy su server con Docker Compose: porte configurabili, reverse proxy, aggiornamenti |
| [changelog.md](changelog.md) | Storico delle funzionalità rilasciate |

## Regola di aggiornamento

> **Ogni commit che introduce o modifica una funzionalità deve aggiornare i documenti pertinenti e aggiungere una voce al changelog.** La documentazione non aggiornata è considerata un bug.
