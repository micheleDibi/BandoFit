"""Checklist documentale del consorzio per forma di aggregazione (WP8,
docs/partenariati.md V3, appendice A).

Modulo PURO. La fonte dei documenti è il vocabolario
(`partenariato_vocabolario`: `DOCUMENTI_BASE`, `FORME[forma].documenti`,
titoli in `DOCUMENTI`, costituzione e note delle forme); qui si aggiunge solo
QUANDO serve ciascun documento (`fase`) e se è indispensabile
(`obbligatorio`):
- documenti di base (tutte le forme): NDA, lettera d'intenti e term sheet
  sono accordi tra i partner prima della domanda, consigliati; le
  dichiarazioni sostitutive servono con la domanda;
- documenti costitutivi della forma: con la domanda se il bando chiede il
  raggruppamento già costituito (`costituita_richiesta`), prima della
  concessione se ammette la forma costituenda (`costituenda_ammessa`);
  se il bando non lo indica, la fase tipica della forma (appendice A);
- documenti che il bando chiede espressamente (`documenti_richiesti` dello
  snapshot confermato): obbligatori, con il momento indicato dal bando, e
  aggiunti se la forma non li prevede. Le voci `altro` del bando non entrano:
  non hanno un codice del vocabolario (stato non salvabile).
Lo stato per documento (`partner_call_documenti`) lo scrive solo il creatore;
`con_stato` lo unisce alla checklist.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from app.schemas.partenariato_consorzio import (
    DocumentoConsorzioOut,
    FaseDocumento,
    FonteDocumento,
)
from app.services import partenariato_vocabolario as voc

# Codice dei documenti come la CHECK di `partner_call_documenti.codice`.
CODICE_DOCUMENTO_RE = r"^[a-z_]{2,40}$"

_FASE_BASE: dict[str, FaseDocumento] = {
    "nda": "accordo_preliminare",
    "lettera_intenti": "accordo_preliminare",
    "term_sheet_mou": "accordo_preliminare",
    "dichiarazione_sostitutiva": "domanda",
}
_OBBLIGATORI_BASE = frozenset({"dichiarazione_sostitutiva"})
_NOTE_BASE: dict[str, str] = {
    "nda": "Prima di scambiare informazioni riservate sul progetto.",
    "lettera_intenti": "Impegno dei partner a partecipare al progetto.",
    "term_sheet_mou": "Ruoli, quote e impegni concordati tra i partner.",
    "dichiarazione_sostitutiva": "Una per ogni partner, con i requisiti chiesti dal bando.",
}

# Documenti costitutivi: la fase dipende da `costituzione`; qui la fase tipica
# della forma quando il bando non lo indica (appendice A).
_COSTITUTIVI: dict[str, dict[str, FaseDocumento]] = {
    "ats": {"mandato_collettivo": "concessione", "atto_costitutivo": "concessione"},
    "ati_rti": {"mandato_collettivo": "concessione"},
    "rete_contratto": {"contratto_rete": "domanda", "programma_rete": "domanda"},
    "rete_soggetto": {
        "contratto_rete": "domanda",
        "programma_rete": "domanda",
        "fondo_patrimoniale": "domanda",
        "organo_comune": "domanda",
        "iscrizione_registro_imprese": "domanda",
    },
    "accordo_partenariato": {"accordo_partenariato": "domanda", "mandato_collettivo": "domanda"},
}
# Documenti con una fase fissa per la forma.
_FASI_FISSE: dict[str, dict[str, FaseDocumento]] = {
    "ati_rti": {"impegno_costituire": "domanda"},
    "consorzio": {"atto_costitutivo": "domanda", "statuto": "domanda"},
    "consorzio_ue": {
        "consortium_agreement": "concessione",
        "dichiarazioni_affiliated_entities": "domanda",
        "lettere_associated_partners": "domanda",
    },
}
_FASE_COSTITUZIONE: dict[str, FaseDocumento] = {
    "costituita_richiesta": "domanda",
    "costituenda_ammessa": "concessione",
}
# Documenti che servono solo se nel consorzio c'è quel ruolo.
_SOLO_CON_RUOLO: dict[str, tuple[str, str]] = {
    "dichiarazioni_affiliated_entities": (
        "affiliated_entity", "Solo se nel consorzio ci sono entità affiliate."
    ),
    "lettere_associated_partners": (
        "associated_partner", "Solo se nel consorzio ci sono partner associati."
    ),
}
_FASE_MOMENTO: dict[str, FaseDocumento] = {
    "domanda": "domanda",
    "concessione": "concessione",
    "prima_erogazione": "prima_erogazione",
}


@dataclass(frozen=True)
class DocumentoRichiesto:
    codice: str
    titolo: str
    fase: FaseDocumento
    obbligatorio: bool
    nota: str | None = None
    fonte: FonteDocumento = "base"


def _campo(voce: Any, nome: str) -> Any:
    if isinstance(voce, Mapping):
        return voce.get(nome)
    return getattr(voce, nome, None)


def _documento_forma(
    forma: str, codice: str, costituzione: str | None, ruoli: frozenset[str] | None
) -> DocumentoRichiesto:
    voce = voc.FORME[forma]
    obbligatorio = True
    nota: str | None = None
    if codice in _COSTITUTIVI.get(forma, {}):
        fase = _FASE_COSTITUZIONE.get(costituzione or "", _COSTITUTIVI[forma][codice])
        nota = f"{voce.costituzione}."
    else:
        fase = _FASI_FISSE.get(forma, {}).get(codice, "domanda")
    if codice == "impegno_costituire" and costituzione == "costituita_richiesta":
        obbligatorio = False
        nota = "Non serve se il bando chiede il raggruppamento già costituito."
    if codice in _SOLO_CON_RUOLO:
        ruolo, testo = _SOLO_CON_RUOLO[codice]
        obbligatorio = ruoli is not None and ruolo in ruoli
        nota = testo
    return DocumentoRichiesto(
        codice=codice, titolo=voc.DOCUMENTI[codice], fase=fase, obbligatorio=obbligatorio,
        nota=nota, fonte="forma",
    )


def checklist(
    forma: str | None,
    costituzione: str | None,
    *,
    richiesti: Iterable[Any] = (),
    ruoli: Iterable[str] | None = None,
) -> list[DocumentoRichiesto]:
    """Checklist dei documenti: prima quelli di base, poi gli specifici della
    forma (nell'ordine del vocabolario), poi quelli chiesti dal bando che la
    forma non prevede.

    - `forma`: `partner_calls.forma_aggregazione_prevista` (None o «altra» →
      solo i documenti di base e quelli del bando);
    - `costituzione`: valore della voce `costituzione` dello snapshot
      (`costituenda_ammessa | costituita_richiesta | non_indicato`), None se
      assente;
    - `richiesti`: `documenti_richiesti` dello snapshot (`DocumentoSnapshot`
      o la sua forma jsonb);
    - `ruoli`: ruoli dei membri non usciti, per i documenti che servono solo
      con entità affiliate o partner associati (None = non noti: non
      obbligatori)."""
    ruoli_presenti = frozenset(ruoli) if ruoli is not None else None
    lista: list[DocumentoRichiesto] = [
        DocumentoRichiesto(
            codice=codice, titolo=voc.DOCUMENTI[codice], fase=_FASE_BASE[codice],
            obbligatorio=codice in _OBBLIGATORI_BASE, nota=_NOTE_BASE.get(codice),
        )
        for codice in voc.DOCUMENTI_BASE
    ]
    if forma in voc.FORME:
        specifici = [c for c in voc.documenti_forma(forma) if c not in voc.DOCUMENTI_BASE]
        documenti = [_documento_forma(forma, c, costituzione, ruoli_presenti) for c in specifici]
        nota_forma = voc.FORME[forma].nota
        if documenti and nota_forma:
            primo = documenti[0]
            documenti[0] = replace(
                primo, nota=f"{primo.nota} {nota_forma}." if primo.nota else f"{nota_forma}."
            )
        lista.extend(documenti)
    per_codice = {d.codice: i for i, d in enumerate(lista)}
    for voce in richiesti:
        codice = _campo(voce, "tipo")
        if codice not in voc.DOCUMENTI:  # «altro» o codice ignoto
            continue
        fase = _FASE_MOMENTO.get(_campo(voce, "momento") or "")
        if codice in per_codice:
            attuale = lista[per_codice[codice]]
            lista[per_codice[codice]] = replace(
                attuale, obbligatorio=True, fase=fase or attuale.fase, fonte="bando"
            )
            continue
        per_codice[codice] = len(lista)
        lista.append(DocumentoRichiesto(
            codice=codice, titolo=voc.DOCUMENTI[codice], fase=fase or "domanda",
            obbligatorio=True, nota="Richiesto dal bando.", fonte="bando",
        ))
    return lista


def _istante(valore: Any) -> datetime | None:
    if isinstance(valore, datetime):
        return valore
    if isinstance(valore, str) and valore:
        try:
            return datetime.fromisoformat(valore)
        except ValueError:
            return None
    return None


def con_stato(
    documenti: Iterable[DocumentoRichiesto], righe: Iterable[Mapping]
) -> list[DocumentoConsorzioOut]:
    """La checklist con lo stato salvato (`partner_call_documenti`): i
    documenti senza riga sono «da fare»; le righe di documenti che la
    checklist non prevede più (per esempio dopo un cambio di forma) non
    escono."""
    stati = {
        riga.get("codice"): riga for riga in righe if isinstance(riga, Mapping)
    }
    uscita = []
    for doc in documenti:
        riga = stati.get(doc.codice) or {}
        stato = riga.get("stato")
        uscita.append(DocumentoConsorzioOut(
            codice=doc.codice,
            titolo=doc.titolo,
            fase=doc.fase,
            obbligatorio=doc.obbligatorio,
            nota=doc.nota,
            fonte=doc.fonte,
            stato=stato if stato in ("da_fare", "in_corso", "fatto", "non_applicabile")
            else "da_fare",
            note=riga.get("note") if isinstance(riga.get("note"), str) else None,
            updated_at=_istante(riga.get("updated_at")),
        ))
    return uscita
