"""Preferenze di filtro/notifica PER UTENTE: valori "seguiti" in aggiunta a
quelli reali dell'azienda (es. un ATECO in più). Ogni id viene validato
contro le lookup del catalogo bandi e denormalizzato in un'etichetta.

La scrittura è a DIFF (delete dei rimossi + insert degli aggiunti): niente
finestra vuota tra delete-all e re-insert, e gli id invariati non si toccano.
"""

import logging

from app.core.errors import BadRequestError
from app.schemas.preferences import FACETS, PreferencesPayload
from app.services import lookup_service

logger = logging.getLogger("bandofit.preferences")

# facet → (attributo di LookupsOut, funzione etichetta)
_FACET_LOOKUPS = {
    "regioni": ("regioni", lambda item: item.nome),
    "settori": ("settori", lambda item: item.nome),
    "beneficiari": ("beneficiari", lambda item: item.nome),
    "codici_ateco": ("codici_ateco", lambda item: f"{item.codice} — {item.descrizione}"),
    "tipologie": ("tipologie_bando", lambda item: item.nome),
    "modalita": ("modalita_erogazione", lambda item: item.nome),
    "programmi": ("programmi", lambda item: item.nome),
}

_FACET_LABELS = {
    "regioni": "Regione",
    "settori": "Settore",
    "beneficiari": "Beneficiario",
    "codici_ateco": "Codice ATECO",
    "tipologie": "Tipologia",
    "modalita": "Modalità di erogazione",
    "programmi": "Programma",
}


async def get_preferences(primary, user_id: str) -> PreferencesPayload:
    resp = (
        await primary.table("user_preferences")
        .select("facet,ref_id")
        .eq("user_id", str(user_id))
        .execute()
    )
    result: dict[str, list[int]] = {facet: [] for facet in FACETS}
    for row in resp.data or []:
        if row["facet"] in result:
            result[row["facet"]].append(row["ref_id"])
    for values in result.values():
        values.sort()
    return PreferencesPayload(**result)


async def save_preferences(
    primary, secondary, user_id: str, data: PreferencesPayload
) -> PreferencesPayload:
    """Sostituisce il set completo delle preferenze dell'utente (a diff)."""
    lookups = await lookup_service.get_lookups(secondary)

    # Validazione + denormalizzazione etichette, faccetta per faccetta.
    desired: dict[tuple[str, int], str] = {}
    for facet in FACETS:
        attr, label_of = _FACET_LOOKUPS[facet]
        items = {item.id: item for item in getattr(lookups, attr)}
        for ref_id in set(getattr(data, facet)):
            item = items.get(ref_id)
            if item is None:
                raise BadRequestError(
                    f"{_FACET_LABELS[facet]} non valido: valore sconosciuto"
                )
            desired[(facet, ref_id)] = label_of(item)

    existing_resp = (
        await primary.table("user_preferences")
        .select("id,facet,ref_id")
        .eq("user_id", str(user_id))
        .execute()
    )
    existing = {(row["facet"], row["ref_id"]): row["id"] for row in existing_resp.data or []}

    to_delete = [pref_id for key, pref_id in existing.items() if key not in desired]
    to_insert = [
        {"user_id": str(user_id), "facet": facet, "ref_id": ref_id, "label": label}
        for (facet, ref_id), label in desired.items()
        if (facet, ref_id) not in existing
    ]

    if to_delete:
        await primary.table("user_preferences").delete().in_("id", to_delete).execute()
    if to_insert:
        await primary.table("user_preferences").insert(to_insert).execute()

    return await get_preferences(primary, user_id)
