"""Classificazione degli errori PostgREST del catalogo, comune ai servizi che
lo leggono (lookup delle faccette, card dei bandi salvati).

Un errore di contratto (colonna o tabella sparite, permesso negato) è un
cambio del catalogo da cui si degrada; ogni altro codice, la rete e i timeout
sono guasti che risalgono.
"""

# Codici PostgREST di un cambio di contratto del catalogo: colonna assente,
# permesso negato; la famiglia PGRST2xx (cache dello schema: tabella, funzione
# o relazione assenti) si riconosce dal prefisso.
_CODICI_CONTRATTO = frozenset({"42703", "42501"})


def errore_di_contratto(exc: BaseException) -> bool:
    """Vero se l'errore è un cambio di contratto del catalogo (`42703`,
    `42501`, `PGRST2xx`) e non un guasto (rete, timeout, altri codici)."""
    codice = getattr(exc, "code", None)
    return isinstance(codice, str) and (
        codice in _CODICI_CONTRATTO or codice.startswith("PGRST2")
    )
