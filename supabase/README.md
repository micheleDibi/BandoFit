# Migrazioni DB primario

Le migrazioni SQL del database primario di BandoFit, da eseguire **in ordine** sul progetto Supabase primario.

## Come applicarle

Opzione A — SQL Editor (consigliata per il primo setup):
1. Aprire il progetto su [supabase.com](https://supabase.com) → **SQL Editor**.
2. Incollare ed eseguire il contenuto di ogni file in ordine crescente (`0001_...`, poi `0002_...`).

Opzione B — Supabase CLI:
```bash
supabase link --project-ref <ref-del-progetto>
supabase db push
```

I dettagli dello schema sono documentati in [docs/database.md](../docs/database.md); i passi completi di setup in [docs/setup.md](../docs/setup.md).
