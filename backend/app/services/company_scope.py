"""Overlay `company_profile_id` per le tabelle del Gruppo A (bandi salvati,
calendario, preferenze) — le tabelle chiavate su `user_id`, non sull'azienda.

Per un Advisor multi-azienda (`active.is_multi`) le righe sono scopate sulla
company ATTIVA (segregazione tra clienti); per tutti gli altri restano a
`company_profile_id NULL` — comportamento IDENTICO a prima della multi-azienda
(le righe legacy sono NULL: il filtro `is null` le prende tutte, l'insert non
tocca la colonna). `active` è passato per duck-typing: nessun import da
`app.api.deps` (evita il ciclo deps→services)."""


def scope_value(active) -> str | None:
    """`company_profile_id` da scrivere: la company attiva per un Advisor,
    None (legacy) per tutti gli altri. Se un Advisor non ha ancora un'azienda
    attiva (company_id None) resta None: la UI lo spinge a crearne una."""
    if getattr(active, "is_multi", False):
        return active.company_id
    return None


def filter_read(query, active):
    """Applica il filtro di lettura del Gruppo A a una query PostgREST: uguaglianza
    sulla company per un Advisor, `is null` (righe legacy) per gli altri."""
    value = scope_value(active)
    if value is not None:
        return query.eq("company_profile_id", value)
    return query.is_("company_profile_id", "null")
