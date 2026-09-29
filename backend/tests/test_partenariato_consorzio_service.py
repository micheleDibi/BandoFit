"""Consorzio della call (WP8, docs/partenariati.md V1-V3, T3, Q11, Q20):
servizio vero sul primario finto del WP7 esteso con le RPC della 0040.

Qui vive il primario FINTO del WP8 (`FakePrimaryWP8`): le RPC dei membri
(`fn_partner_membro_aggiorna|conferma|esci|esterno`), dei documenti, del
salvataggio della validazione, dell'aggiornamento del budget, il
`fn_partner_decidi` che all'accettazione scrive le righe del consorzio e
l'esclusività estesa ai membri, con le stesse guardie e gli stessi detail
della 0040. Le guardie vere, i lock e i vincoli li verifica
`tests/db/test_migration_0040.py`.

Verifica: proiezioni per destinatario (creatore, membro, controparte in
sola lettura, estranei 404) con canary — nessun valore esatto di bilancio di
altri membri, nessun `company_profile_id` né `codice_pubblico` di altri,
pseudonimi per call con la rivelazione spenta, matrice senza la copertura
del creatore verso gli altri, regole finanziarie esatte solo per sé
(l'oracolo su budget e quote non rivela più della fascia); ricalcolo e
salvataggio della validazione dopo ogni mutazione (in lettura solo se
cambiata); notifiche in-app con dedup; prerequisiti del WP8 («azienda viva»
con il titolare attivo, candidature ricevute nelle card, membri usciti non
più impegnati sul bando)."""

import copy
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.core.errors import AppError, ForbiddenError, NotFoundError
from app.schemas.partenariato_consorzio import (
    BudgetIn,
    DocumentoStatoIn,
    EsternoIn,
    MembroAggiornaIn,
    MembroConfermaIn,
)
from app.schemas.partner_call import RegoleCallSnapshot
from app.services import partenariato_accesso as acc
from app.services import partenariato_candidature_service as candidature
from app.services import partenariato_collegamenti, partenariato_indice
from app.services import partenariato_consorzio_service as svc
from app.services import partner_call_service as pcs
from app.services.partenariato_accesso import pseudonimo
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariato_candidature_service import (  # noqa: F401 — fixture
    FM_X,
    MEMBRO_X,
    FakePrimaryWP7,
    attiva,
    candidatura_in,
    errore,
    fixture_fondo,
    utente,
)
from tests.test_partenariato_indice import (  # noqa: F401 — fixture autouse
    EMAIL,
    PIVA,
    RAGIONE,
    ambiente_wp6,
    carica_guida,
    secondario_guida,
)

EMAIL_MEMBRO_X = "membro.x@example.test"
# Valori esatti di bilancio, grezzi e nel formato dei testi del validatore
# («2.500.000 €»): di Y (fatturati, fatturato medio e patrimonio netto) e di X
# (fatturato 2024, fatturato medio e patrimonio netto). Il fatturato 2023 di
# X (3,1 M€) coincide con il budget esatto della call, che le controparti
# vedono.
NUMERI_Y = ("2400000", "2.400.000", "2600000", "2.600.000", "2500000", "2.500.000",
            "812345", "812.345")
NUMERI_X = ("3300000", "3.300.000", "3200000", "3.200.000", "1500000", "1.500.000")
NUMERI_T: tuple[str, ...] = ()

# Voce confermata: citazione ritrovata su una pagina di un documento ufficiale
# (una voce della scheda del catalogo non si può confermare).
CIT = {"sezione": "D1-p2", "testo": "Il partenariato è composto da almeno due soggetti",
       "verificata": True}


def conf(**voce) -> dict:
    return {"origine_voce": "confermata", "citazione": CIT, **voce}


def regole(*, finanziaria: bool = False, **extra) -> dict:
    """Lo snapshot dell'esempio guida (punto 5): almeno 2 partner, almeno un
    organismo di ricerca e una PMI, quota di ogni partner tra 10% e 70%;
    con `finanziaria` anche la regola F (costo della quota ≤ 60% del
    fatturato medio, per ciascun partner)."""
    dati = {
        "modalita": conf(valore="obbligatorio"),
        "partner_min": conf(valore=2),
        "composizione": [
            conf(id="C1", tipo_soggetto="organismo_ricerca", minimo=1, ruolo="qualsiasi"),
            conf(id="C2", tipo_soggetto="pmi", minimo=1, ruolo="qualsiasi"),
        ],
        "quote": [conf(id="Q1", ambito="per_partner", categoria=None, min_percentuale=10,
                       max_percentuale=70, base_calcolo="costo_totale_progetto",
                       effetto_violazione="inammissibilita_progetto")],
        **extra,
    }
    if finanziaria:
        dati["regole_finanziarie"] = [conf(**g.REGOLA_F)]
    return RegoleCallSnapshot.model_validate(dati).model_dump(mode="json")


# ------------------------------------------------------------ primario finto

_STATI_MODIFICABILI = ("pubblicata", "scaduta", "chiusa_completata")
_RUOLI = ("capofila", "partner", "affiliated_entity", "associated_partner")
_WHITELIST_PUBBLICATA = (
    "descrizione_pubblica", "dettagli_riservati", "profilo_partner_ideale", "scadenza_call",
    "visibilita", "budget_fascia", "budget_progetto_eur", "quota_creatore_pct",
)
_CHIAVI_ESTERNO = {"membro_id", "denominazione", "paese", "tipi_soggetto", "ruolo", "quota",
                   "posizione_id"}


def _ora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _quota(valore) -> Decimal | None:
    """numeric(5,2), come la RPC (`round(p_quota, 2)`)."""
    if valore is None:
        return None
    return Decimal(str(valore)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _testo_quota(valore) -> str | None:
    q = _quota(valore)
    return None if q is None else str(q)


class FakePrimaryWP8(FakePrimaryWP7):
    """Il primario finto del WP7 con le RPC della 0040 (stesse guardie e
    detail): righe del consorzio all'accettazione, membri, esterni,
    documenti, validazione salvata, budget; esclusività e «call aperta» come
    ridefinite dalla 0040 (membri non usciti, titolare attivo)."""

    def __init__(self):
        super().__init__()
        note = dict(self.colonne_note)
        note["partner_calls"] = (*note["partner_calls"], "validazione_esito", "validazione_at",
                                 "copertura_gap_ratio")
        self.colonne_note = note

    # -- aiuti
    def membri(self, call_id: str = g.CALL_GUIDA_ID) -> list[dict]:
        return self.righe("partner_call_membri", partner_call_id=call_id)

    def membro_di(self, company: str, call_id: str = g.CALL_GUIDA_ID) -> dict:
        return self.una("partner_call_membri", partner_call_id=call_id,
                        company_profile_id=company)

    def _nuovo_membro(self, **campi) -> dict:
        adesso = _ora()
        riga = {
            "id": str(uuid.uuid4()), "partner_call_id": None, "company_profile_id": None,
            "candidatura_id": None, "esterno_denominazione": None, "esterno_paese": None,
            "esterno_tipi_soggetto": None, "posizione_id": None, "ruolo": "partner",
            "quota_percentuale": None, "stato": "proposto", "confermato_da_user_id": None,
            "confermato_at": None, "aggiornato_da_user_id": None, "created_at": adesso,
            "updated_at": adesso, **campi,
        }
        self.tabelle.setdefault("partner_call_membri", []).append(riga)
        return riga

    def _capofila_altro(self, call_id: str, escluso) -> bool:
        return any(m["ruolo"] == "capofila" and m["stato"] != "uscito" and m["id"] != escluso
                   for m in self.membri(call_id))

    def _inserisci_membro(self, call_id, company, candidatura, posizione, ruolo, quota, attore,
                          riammetti) -> str | None:
        """fn_partner_membro_inserisci."""
        ruolo = ruolo or "partner"
        if ruolo == "capofila" and any(
            m["ruolo"] == "capofila" and m["stato"] != "uscito"
            and m["company_profile_id"] != company for m in self.membri(call_id)
        ):
            ruolo = "partner"
        esistente = next((m for m in self.membri(call_id) if m["company_profile_id"] == company),
                         None)
        if esistente is None:
            return self._nuovo_membro(
                partner_call_id=call_id, company_profile_id=company, candidatura_id=candidatura,
                posizione_id=posizione, ruolo=ruolo, quota_percentuale=_testo_quota(quota),
                aggiornato_da_user_id=attore)["id"]
        if riammetti and esistente["stato"] == "uscito":
            esistente.update(candidatura_id=candidatura, stato="proposto", posizione_id=posizione,
                             ruolo=ruolo, quota_percentuale=_testo_quota(quota),
                             confermato_da_user_id=None, confermato_at=None,
                             aggiornato_da_user_id=attore, updated_at=_ora())
            return esistente["id"]
        return None

    def _membro_creatore(self, call_id: str, attore) -> str | None:
        call = next(iter(self.righe("partner_calls", id=call_id)), None)
        if call is None:
            return None
        return self._inserisci_membro(
            call_id, call["company_profile_id"], None, None,
            "capofila" if call["ruolo_creatore"] == "capofila" else "partner",
            call.get("quota_creatore_pct"), attore, False)

    def backfill_membri(self) -> int:
        """fn_partner_backfill_membri: il creatore di ogni call pubblicata e
        il membro di ogni candidatura accettata."""
        n = 0
        for call in sorted(self.tabelle.get("partner_calls", []),
                           key=lambda c: (str(c.get("pubblicata_at")), c["id"])):
            if call.get("pubblicata_at") and self._membro_creatore(call["id"], None):
                n += 1
        for k in self.tabelle.get("partner_candidature", []):
            if k["stato"] != "accettata":
                continue
            pos = next(iter(self.righe("partner_call_posizioni", id=k.get("posizione_id"))), {})
            if self._inserisci_membro(k["partner_call_id"], k["company_profile_id"], k["id"],
                                      k.get("posizione_id"),
                                      "capofila" if pos.get("ruolo") == "capofila"
                                      else "partner", pos.get("quota_ipotizzata_pct"), None,
                                      False):
                n += 1
        return n

    def _blocca_call(self, owner, company, call_id) -> dict:
        """fn_partner_call_blocca (owner → azienda viva → call dell'azienda)."""
        if not self.righe("profiles", id=owner):
            raise errore("owner_not_found")
        azienda = self._viva(company)
        if azienda is None or azienda["parent_id"] != owner:
            raise errore("company_not_found")
        call = next((c for c in self.righe("partner_calls", id=call_id)
                     if c["company_profile_id"] == company and c["family_parent_id"] == owner),
                    None)
        if call is None:
            raise errore("call_not_found")
        return call

    def _blocca_azienda(self, owner, company) -> None:
        """fn_partner_call_blocca_azienda(…, false): anche non viva."""
        if not self.righe("profiles", id=owner):
            raise errore("owner_not_found")
        azienda = next(iter(self.righe("company_profiles", id=company)), None)
        if azienda is None or azienda["parent_id"] != owner:
            raise errore("company_not_found")

    # -- ridefinizioni della 0040
    def _call_aperta(self, call) -> bool:
        """fn_partner_call_aperta (0040): anche il titolare del creatore attivo."""
        return super()._call_aperta(call) and self._controparte_viva(call["company_profile_id"])

    def _esclusivita_violata(self, company, call) -> bool:
        """fn_partner_esclusivita_violata (0040): altra call propria pubblicata
        o non annullata con altri membri non usciti, membro non uscito (non
        creatore) di un'altra call non annullata, accettata senza riga nel
        consorzio; simmetrica."""
        for altra in self.tabelle.get("partner_calls", []):
            if altra["id"] == call["id"] or altra["bando_id"] != call["bando_id"]:
                continue
            if not (call.get("esclusivita") or altra.get("esclusivita")):
                continue
            if altra["company_profile_id"] == company and (
                altra["stato"] == "pubblicata"
                or (altra["stato"] not in ("bozza", "chiusa_annullata")
                    and any(m["stato"] != "uscito" and m["company_profile_id"] != company
                            for m in self.membri(altra["id"])))
            ):
                return True
            riga = next((m for m in self.membri(altra["id"])
                         if m["company_profile_id"] == company), None)
            if altra["stato"] == "chiusa_annullata":
                continue
            if riga is not None:
                if riga["stato"] != "uscito" and altra["company_profile_id"] != company:
                    return True
            elif any(k["stato"] == "accettata" for k in self.righe(
                    "partner_candidature", partner_call_id=altra["id"],
                    company_profile_id=company)):
                return True
        return False

    def _fn_partner_decidi(self, p):
        esito = super()._fn_partner_decidi(p)
        riga = esito["candidatura"]
        if riga["stato"] != "accettata":
            return esito
        self._membro_creatore(riga["partner_call_id"], None)
        pos = next(iter(self.righe("partner_call_posizioni", id=riga.get("posizione_id"))), {})
        membro = self._inserisci_membro(
            riga["partner_call_id"], riga["company_profile_id"], riga["id"],
            riga.get("posizione_id"), "capofila" if pos.get("ruolo") == "capofila" else "partner",
            pos.get("quota_ipotizzata_pct"), p["p_attore"], True)
        if membro is None:
            membro = self.membro_di(riga["company_profile_id"], riga["partner_call_id"])["id"]
        return {**esito, "membro_id": membro}

    # -- RPC della 0040
    def _fn_partner_membro_aggiorna(self, p):
        q = _quota(p.get("p_quota"))
        if (not p.get("p_membro") or p.get("p_ruolo") not in _RUOLI
                or (q is not None and not Decimal(0) < q <= Decimal(100))):
            raise errore("parametri_non_validi")
        if p["p_attore"] != p["p_owner"]:
            raise errore("attore_non_titolare")
        m = next(iter(self.righe("partner_call_membri", id=p["p_membro"])), None)
        call = next(iter(self.righe("partner_calls", id=(m or {}).get("partner_call_id"))), None)
        if (m is None or call is None or call["company_profile_id"] != p["p_company"]
                or call["family_parent_id"] != p["p_owner"]):
            raise errore("membro_non_trovato")
        call = self._blocca_call(p["p_owner"], p["p_company"], m["partner_call_id"])
        if call["stato"] not in _STATI_MODIFICABILI:
            raise errore("call_non_modificabile")
        if m["stato"] == "uscito":
            raise errore("membro_uscito")
        creatore = m["company_profile_id"] == call["company_profile_id"]
        if creatore and p["p_ruolo"] not in ("capofila", "partner"):
            raise errore("ruolo_non_ammesso")
        posizione = p.get("p_posizione")
        if posizione and not self.righe("partner_call_posizioni", id=posizione,
                                        call_id=call["id"]):
            raise errore("posizione_non_valida")
        if p["p_ruolo"] == "capofila" and self._capofila_altro(call["id"], m["id"]):
            raise errore("capofila_gia_presente")
        prima = m["stato"]
        if (m["ruolo"] == p["p_ruolo"] and m["posizione_id"] == posizione
                and _quota(m["quota_percentuale"]) == q):
            return {"membro": copy.deepcopy(m), "stato_precedente": prima, "modificato": False}
        stato = m["stato"] if creatore and q is not None else "proposto"
        m.update(ruolo=p["p_ruolo"], posizione_id=posizione, quota_percentuale=_testo_quota(q),
                 stato=stato, aggiornato_da_user_id=p["p_attore"], updated_at=_ora())
        if stato != "confermato":
            m.update(confermato_da_user_id=None, confermato_at=None)
        self._audit(p["p_attore"], "partenariato.membro_aggiornato", p["p_owner"], p["p_owner"],
                    {"membro_id": m["id"], "call_id": call["id"], "stato": stato,
                     "stato_precedente": prima})
        return {"membro": copy.deepcopy(m), "stato_precedente": prima, "modificato": True}

    def _fn_partner_membro_conferma(self, p):
        q = _quota(p.get("p_quota"))
        if (not p.get("p_membro") or p.get("p_ruolo") not in _RUOLI
                or (q is not None and not Decimal(0) < q <= Decimal(100))):
            raise errore("parametri_non_validi")
        if p["p_attore"] != p["p_owner"]:
            raise errore("attore_non_titolare")
        m = next(iter(self.righe("partner_call_membri", id=p["p_membro"])), None)
        if m is None:
            raise errore("membro_non_trovato")
        call = self.una("partner_calls", id=m["partner_call_id"])
        creatore = (call["company_profile_id"] == p["p_company"]
                    and call["family_parent_id"] == p["p_owner"])
        if not (m["company_profile_id"] == p["p_company"]
                or (creatore and m["company_profile_id"] is None)):
            raise errore("membro_non_trovato")
        if creatore:
            call = self._blocca_call(p["p_owner"], p["p_company"], call["id"])
        else:
            self._blocca(p["p_owner"], p["p_company"])
        if call["stato"] not in _STATI_MODIFICABILI:
            raise errore("call_non_modificabile")
        prima = m["stato"]
        if m["stato"] == "uscito":
            raise errore("membro_uscito")
        if (m["ruolo"] != p["p_ruolo"] or m["posizione_id"] != p.get("p_posizione")
                or _quota(m["quota_percentuale"]) != q):
            raise errore("membro_modificato")
        if m["stato"] == "confermato":
            return {"membro": copy.deepcopy(m), "stato_precedente": prima, "modificato": False}
        if m["quota_percentuale"] is None and m["ruolo"] != "associated_partner":
            raise errore("quota_mancante")
        m.update(stato="confermato", confermato_da_user_id=p["p_attore"], confermato_at=_ora(),
                 aggiornato_da_user_id=p["p_attore"], updated_at=_ora())
        self._audit(p["p_attore"], "partenariato.membro_confermato", call["family_parent_id"],
                    p["p_owner"], {"membro_id": m["id"], "call_id": call["id"]})
        return {"membro": copy.deepcopy(m), "stato_precedente": prima, "modificato": True}

    def _fn_partner_membro_esci(self, p):
        if not p.get("p_membro"):
            raise errore("parametri_non_validi")
        if p["p_attore"] != p["p_owner"]:
            raise errore("attore_non_titolare")
        m = next(iter(self.righe("partner_call_membri", id=p["p_membro"])), None)
        if m is None:
            raise errore("membro_non_trovato")
        call = self.una("partner_calls", id=m["partner_call_id"])
        creatore = (call["company_profile_id"] == p["p_company"]
                    and call["family_parent_id"] == p["p_owner"])
        if creatore:
            if m["company_profile_id"] == p["p_company"]:
                raise errore("membro_non_rimovibile")
            origine = "creatore"
            call = self._blocca_call(p["p_owner"], p["p_company"], call["id"])
            if call["stato"] not in _STATI_MODIFICABILI:
                raise errore("call_non_modificabile")
        elif m["company_profile_id"] == p["p_company"]:
            origine = "membro"
            self._blocca_azienda(p["p_owner"], p["p_company"])
        else:
            raise errore("membro_non_trovato")
        prima = m["stato"]
        if m["stato"] == "uscito":
            return {"membro": copy.deepcopy(m), "stato_precedente": prima, "modificato": False,
                    "origine": origine}
        m.update(stato="uscito", confermato_da_user_id=None, confermato_at=None,
                 aggiornato_da_user_id=p["p_attore"], updated_at=_ora())
        self._audit(p["p_attore"], "partenariato.membro_uscito", p["p_owner"], p["p_owner"],
                    {"membro_id": m["id"], "call_id": call["id"], "origine": origine})
        return {"membro": copy.deepcopy(m), "stato_precedente": prima, "modificato": True,
                "origine": origine}

    def _fn_partner_membro_esterno(self, p):
        pl = p["p_payload"]
        tipi = pl.get("tipi_soggetto") if isinstance(pl, dict) else None
        if (not isinstance(pl, dict) or set(pl) - _CHIAVI_ESTERNO
                or not isinstance(tipi, (list, type(None)))
                or any(not isinstance(t, str) for t in tipi or [])):
            raise errore("parametri_non_validi")
        tipi = list(tipi or [])
        nome = (pl.get("denominazione") or "").strip()
        paese = pl.get("paese") or ""
        ruolo = pl.get("ruolo") or "partner"
        q = _quota(pl.get("quota"))
        if (not 2 <= len(nome) <= 200 or len(paese) != 2 or not paese.isupper()
                or ruolo not in _RUOLI or (q is not None and not Decimal(0) < q <= Decimal(100))
                or len(tipi) > 5 or len(set(tipi)) != len(tipi)):
            raise errore("parametri_non_validi")
        if p["p_attore"] != p["p_owner"]:
            raise errore("attore_non_titolare")
        call = self._blocca_call(p["p_owner"], p["p_company"], p["p_call"])
        if call["stato"] not in _STATI_MODIFICABILI:
            raise errore("call_non_modificabile")
        posizione = pl.get("posizione_id")
        if posizione and not self.righe("partner_call_posizioni", id=posizione,
                                        call_id=call["id"]):
            raise errore("posizione_non_valida")
        membro_id, prima, m = pl.get("membro_id"), None, None
        if membro_id:
            m = next((r for r in self.membri(call["id"])
                      if r["id"] == membro_id and r["company_profile_id"] is None), None)
            if m is None:
                raise errore("membro_non_trovato")
            prima = m["stato"]
            if (m["stato"] != "uscito" and m["esterno_denominazione"] == nome
                    and m["esterno_paese"] == paese and m["esterno_tipi_soggetto"] == tipi
                    and m["ruolo"] == ruolo and _quota(m["quota_percentuale"]) == q
                    and m["posizione_id"] == posizione):
                return {"membro": copy.deepcopy(m), "creato": False, "stato_precedente": prima,
                        "modificato": False}
        if membro_id is None or prima == "uscito":
            attivi = [r for r in self.membri(call["id"]) if r["stato"] != "uscito"]
            esterni = [r for r in self.membri(call["id"]) if r["company_profile_id"] is None]
            if len(attivi) >= 30 or (membro_id is None and len(esterni) >= 60):
                raise errore("limite_membri")
        if ruolo == "capofila" and self._capofila_altro(call["id"], membro_id):
            raise errore("capofila_gia_presente")
        campi = {"esterno_denominazione": nome, "esterno_paese": paese,
                 "esterno_tipi_soggetto": tipi, "posizione_id": posizione, "ruolo": ruolo,
                 "quota_percentuale": _testo_quota(q), "aggiornato_da_user_id": p["p_attore"]}
        if m is None:
            m = self._nuovo_membro(partner_call_id=call["id"], **campi)
        else:
            m.update(**campi, stato="proposto", confermato_da_user_id=None, confermato_at=None,
                     updated_at=_ora())
        self._audit(p["p_attore"], "partenariato.membro_aggiunto" if membro_id is None
                    else "partenariato.membro_aggiornato", p["p_owner"], p["p_owner"],
                    {"membro_id": m["id"], "call_id": call["id"], "esterno": True})
        return {"membro": copy.deepcopy(m), "creato": membro_id is None,
                "stato_precedente": prima, "modificato": True}

    def _fn_partner_documento_stato(self, p):
        codice = p.get("p_codice") or ""
        if not (2 <= len(codice) <= 40 and all(c.islower() or c == "_" for c in codice)):
            raise errore("documento_non_valido")
        note = (p.get("p_note") or "").strip() or None
        if (p.get("p_stato") not in ("da_fare", "in_corso", "fatto", "non_applicabile")
                or (note and len(note) > 500)):
            raise errore("parametri_non_validi")
        if p["p_attore"] != p["p_owner"]:
            raise errore("attore_non_titolare")
        call = self._blocca_call(p["p_owner"], p["p_company"], p["p_call"])
        if call["stato"] not in _STATI_MODIFICABILI:
            raise errore("call_non_modificabile")
        riga = next(iter(self.righe("partner_call_documenti", partner_call_id=call["id"],
                                    codice=codice)), None)
        if riga is None:
            riga = {"partner_call_id": call["id"], "codice": codice}
            self.tabelle.setdefault("partner_call_documenti", []).append(riga)
        riga.update(stato=p["p_stato"], note=note, aggiornato_da_user_id=p["p_attore"],
                    updated_at=_ora())
        return copy.deepcopy(riga)

    def _fn_partner_call_validazione_salva(self, p):
        copertura = p.get("p_copertura")
        if (not p.get("p_call") or p.get("p_esito") not in ("verde", "rosso", "grigio")
                or (copertura is not None and not 0 <= Decimal(str(copertura)) <= 1)):
            raise errore("parametri_non_validi")
        call = next(iter(self.righe("partner_calls", id=p["p_call"])), None)
        if call is None:
            return False
        call.update(validazione_esito=p["p_esito"], validazione_at=_ora(),
                    copertura_gap_ratio=None if copertura is None
                    else str(Decimal(str(copertura)).quantize(Decimal("0.001"))))
        return True

    def _fn_partner_call_aggiorna(self, p):
        """fn_partner_call_aggiorna (0037), solo quanto serve al budget."""
        if p["p_attore"] != p["p_owner"]:
            raise errore("attore_non_titolare")
        call = self._blocca_call(p["p_owner"], p["p_company"], p["p_call"])
        if call["stato"] not in ("bozza", "pubblicata"):
            raise errore("stato_call_non_valido")
        cambiati = {k: v for k, v in p["p_campi"].items() if str(call.get(k)) != str(v)}
        if call["stato"] == "pubblicata" and set(cambiati) - set(_WHITELIST_PUBBLICATA):
            raise errore("campo_non_modificabile")
        if cambiati:
            call.update(cambiati)
            call["versione"] = (call.get("versione") or 0) + 1
        return copy.deepcopy(call)


async def scenario_wp8(**limiti) -> tuple[FakePrimaryWP8, object]:
    """L'esempio guida sul primario del WP8 (come `scenario_wp7`: membro di X
    con visibilità, chiavi dei collegamenti calcolate) con il backfill dei
    membri della 0040: il creatore di ogni call pubblicata è già nel suo
    consorzio (X capofila all'80%, la quota iniziale della call)."""
    db = carica_guida(FakePrimaryWP8())
    db.tabelle["profiles"].append({"id": MEMBRO_X, "email": EMAIL_MEMBRO_X, "is_active": True})
    db.tabelle.setdefault("family_members", []).append({
        "id": FM_X, "parent_id": g.OWNER["X"], "member_id": MEMBRO_X, "status": "active",
        "denominazione": "Giulia del gruppo X"})
    db.tabelle.setdefault("family_member_company_access", []).append(
        {"family_member_id": FM_X, "company_profile_id": g.COMPANY["X"]})
    assert (await partenariato_collegamenti.backfill(db))["errori"] == 0
    db.limiti.update({g.OWNER[nome]: valore for nome, valore in limiti.items()})
    assert db.backfill_membri() == 3
    db.ops.clear()
    db.rpcs.clear()
    return db, secondario_guida()


def imposta_regole(db, snapshot: dict) -> None:
    """Lo snapshot delle regole sulla call della guida, con i requisiti
    COERENTI: in produzione un requisito finanziario nasce solo da una regola
    confermata dello snapshot (partner_call_gap, fn_partner_call_regola_ok),
    quindi senza la regola F1 la call non ha il requisito F."""
    db.una("partner_calls", id=g.CALL_GUIDA_ID)["regole_partenariato"] = snapshot
    confermate = {r["id"] for r in snapshot.get("regole_finanziarie") or []}
    # I requisiti tolti restano da parte: uno snapshot successivo con la
    # regola li rimette.
    riserva = db.__dict__.setdefault("requisiti_senza_regola", [])
    tutti = [*db.tabelle["partner_call_requisiti"], *riserva]
    riserva.clear()
    tenuti = []
    for r in tutti:
        if (r["call_id"] == g.CALL_GUIDA_ID
                and (r.get("criterio") or {}).get("tipo") == "regola_finanziaria"
                and r["criterio"]["regola"]["id"] not in confermate):
            riserva.append(r)
        else:
            tenuti.append(r)
    db.tabelle["partner_call_requisiti"] = tenuti


async def accetta(db, sec, nome: str = "Y") -> dict:
    """`nome` si candida alla call della guida e X accetta: la riga del
    consorzio nasce dalla RPC di decisione (proposto, P1, quota 20%)."""
    cand = await candidature.invia_candidatura(db, sec, attiva(nome), utente(nome),
                                               g.CALL_GUIDA_ID, candidatura_in())
    await candidature.decidi(db, sec, attiva("X"), utente("X"), cand.id, "accetta")
    return db.membro_di(g.COMPANY[nome])


async def consorzio_xy(db, sec, *, quote=("70", "30"), snapshot=None) -> tuple[dict, dict]:
    """Y accettata, lo snapshot delle regole sulla call e le quote di X e Y
    impostate dal creatore. → (riga di X, riga di Y)."""
    y = await accetta(db, sec)
    imposta_regole(db, snapshot if snapshot is not None else regole())
    x = db.membro_di(g.COMPANY["X"])
    for riga, quota, ruolo in ((x, quote[0], "capofila"), (y, quote[1], "partner")):
        await svc.aggiorna_membro(
            db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, riga["id"],
            MembroAggiornaIn(ruolo=ruolo, posizione_id=riga["posizione_id"],
                             quota_percentuale=None if quota is None else Decimal(quota)))
    return db.membro_di(g.COMPANY["X"]), db.membro_di(g.COMPANY["Y"])


async def leggi(db, sec, nome: str = "X", **k):
    return await svc.get_consorzio(db, sec, attiva(nome, **k), utente(nome), g.CALL_GUIDA_ID)


def canary(testo: str, *nomi: str, numeri: tuple[str, ...] = ()) -> None:
    for nome in nomi:
        for valore in (g.COMPANY[nome], g.OWNER[nome], PIVA[nome], RAGIONE[nome],
                       RAGIONE[nome].upper(), g.CODICE_PUBBLICO[nome], EMAIL[nome]):
            assert valore not in testo, (nome, valore)
    for numero in numeri:
        assert numero not in testo, numero
    for campo in ("company_profile_id", "candidatura_id", "family_parent_id", "_user_id"):
        assert campo not in testo, campo


def termini(db, membro_id) -> MembroConfermaIn:
    """Ruolo, posizione e quota della riga come li mostra la pagina (il body
    della conferma)."""
    m = db.una("partner_call_membri", id=str(membro_id))
    return MembroConfermaIn(
        ruolo=m["ruolo"], posizione_id=m["posizione_id"],
        quota_percentuale=None if m["quota_percentuale"] is None
        else Decimal(str(m["quota_percentuale"])))


def voce(out, id_: str):
    return next(v for v in out.validazione.voci if v.id == id_)


def salvataggi(db) -> int:
    return len(db.chiamate("fn_partner_call_validazione_salva"))


# ------------------------------------------------------------ proiezioni


class TestProiezioni:
    async def test_creatore_vede_tutto_senza_dati_esatti_degli_altri(self, fondo):
        db, sec = await scenario_wp8()
        x, y = await consorzio_xy(db, sec)
        out = await leggi(db, sec)
        testo = out.model_dump_json()
        assert out.sei_creatore is True and out.editable is True
        mx, my = out.membri
        assert (mx.sei_tu, mx.creatore, mx.nome, mx.pseudonimo) == (
            True, True, RAGIONE["X"], None)
        assert (my.sei_tu, my.nome, my.pseudonimo) == (False, "Azienda anonima", pseudonimo(
            g.CALL_GUIDA_ID, g.CODICE_PUBBLICO["Y"]))
        # profilo pubblico anonimo di Y (Q12): fascia di fatturato, niente nome
        assert my.profilo.anonimo is True and my.profilo.denominazione is None
        assert my.profilo.fasce.fatturato == "2m_10m"
        assert "codice_pubblico" not in my.profilo.model_dump()
        assert (my.puo_modificare, my.puo_confermare, my.puo_uscire) == (True, False, True)
        assert (mx.puo_modificare, mx.puo_uscire) == (True, False)
        # budget esatto (riservato) al creatore
        assert out.budget.esatto == Decimal("3100000.00") and out.budget.modificabile is True
        # matrice con tutti i requisiti (anche la copertura del creatore); senza la
        # regola F1 nello snapshot la call non ha il requisito F (imposta_regole)
        assert [r.etichetta for r in out.matrice.righe] == ["A", "B", "C", "D", "E"]
        canary(testo, "Y", numeri=NUMERI_Y)

    async def test_membro_vede_se_stesso_col_nome_e_gli_altri_anonimi(self, fondo):
        db, sec = await scenario_wp8()
        await consorzio_xy(db, sec)
        await accetta(db, sec, "T")
        out = await leggi(db, sec, "Y")
        testo = out.model_dump_json()
        assert out.sei_creatore is False and out.editable is True
        per_nome = {m.nome: m for m in out.membri if m.sei_tu}
        assert list(per_nome) == [RAGIONE["Y"]]
        creatore = next(m for m in out.membri if m.creatore)
        assert (creatore.nome, creatore.pseudonimo, creatore.profilo) == (
            "Azienda anonima", None, None)
        t = next(m for m in out.membri if not (m.sei_tu or m.creatore))
        # pseudonimo della call (rivelazione spenta), mai il codice pubblico
        assert t.pseudonimo == pseudonimo(g.CALL_GUIDA_ID, g.CODICE_PUBBLICO["T"])
        assert t.profilo is not None and t.profilo.denominazione is None
        assert (t.puo_modificare, t.puo_confermare, t.puo_uscire) == (False, False, False)
        # budget esatto: le controparti accettate lo vedono (come nel WP7)
        assert out.budget.esatto == Decimal("3100000.00") and out.budget.modificabile is False
        # matrice senza la copertura del creatore: solo i requisiti visibili
        assert [r.etichetta for r in out.matrice.righe] == ["A", "C", "E"]
        canary(testo, "X", "T", numeri=NUMERI_X)

    async def test_rivelazione_spenta_anche_tra_membri(self, fondo):
        db, sec = await scenario_wp8()
        await consorzio_xy(db, sec)
        await accetta(db, sec, "T")
        for nome, altri in (("X", ("Y", "T")), ("Y", ("X", "T")), ("T", ("X", "Y"))):
            testo = (await leggi(db, sec, nome)).model_dump_json()
            canary(testo, *altri)
        # anche un profilo nominativo resta anonimo verso gli altri membri
        db.una("company_partner_profiles", company_profile_id=g.COMPANY["T"])["anonimo"] = False
        out = await leggi(db, sec, "Y")
        t = next(m for m in out.membri if not (m.sei_tu or m.creatore))
        assert t.profilo.anonimo is True and t.profilo.denominazione is None
        canary(out.model_dump_json(), "T")

    async def test_membro_della_creatrice_in_sola_lettura(self, fondo):
        db, sec = await scenario_wp8()
        await consorzio_xy(db, sec)
        out = await svc.get_consorzio(db, sec, attiva("X", editable=False),
                                      {"id": MEMBRO_X}, g.CALL_GUIDA_ID)
        assert out.sei_creatore is True and out.editable is False
        assert not any(m.puo_modificare or m.puo_confermare or m.puo_uscire
                       for m in out.membri)
        assert out.budget.modificabile is False

    @pytest.mark.parametrize("nome", ["O", "T"])
    async def test_estranei_e_candidati_in_attesa_404(self, fondo, nome):
        db, sec = await scenario_wp8()
        await accetta(db, sec)
        if nome == "T":  # candidatura in attesa: non è una controparte
            await candidature.invia_candidatura(db, sec, attiva("T"), utente("T"),
                                                g.CALL_GUIDA_ID, candidatura_in())
        with pytest.raises(NotFoundError):
            await leggi(db, sec, nome)

    @pytest.mark.parametrize("chi_esce", ["Y", "X"])
    async def test_membro_uscito_vede_solo_la_propria_riga_uscita(self, fondo, chi_esce):
        """Y esce da sé o il creatore la toglie: la risposta all'uscita ha
        solo la sua riga (nessun altro membro, voce, requisito, documento né
        budget esatto) e da lì Y non è più controparte: consorzio 404 e
        dettaglio della call nella vista pubblica, senza riservati. La
        candidatura resta accettata (non si ritira)."""
        db, sec = await scenario_wp8()
        _, y = await consorzio_xy(db, sec)
        await accetta(db, sec, "T")
        uscita = await svc.esci(db, sec, attiva(chi_esce), utente(chi_esce), g.CALL_GUIDA_ID,
                                y["id"])
        if chi_esce == "Y":
            assert [m.sei_tu for m in uscita.membri] == [True]
            assert uscita.membri[0].stato == "uscito" and not uscita.membri[0].puo_uscire
            assert (uscita.validazione.voci, uscita.matrice.righe, uscita.documenti) == (
                [], [], [])
            assert uscita.budget.esatto is None and uscita.validazione_at is None
            canary(uscita.model_dump_json(), "X", "T", numeri=NUMERI_X)
        with pytest.raises(NotFoundError):
            await leggi(db, sec, "Y")
        dettaglio = await pcs.dettaglio(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID)
        assert "dettagli_riservati" not in dettaglio.model_dump()
        assert "3100000" not in dettaglio.model_dump_json()
        assert db.una("partner_candidature", company_profile_id=g.COMPANY["Y"],
                      partner_call_id=g.CALL_GUIDA_ID)["stato"] == "accettata"
        # la validazione non conta chi è uscito
        vista_t = await leggi(db, sec, "T")
        assert str(y["id"]) not in {str(i) for v in vista_t.validazione.voci
                                   for i in v.membri_coinvolti}
        # gli altri membri non vedono le righe uscite; il creatore sì
        assert [m.stato for m in vista_t.membri] == ["proposto", "proposto"]
        assert str(y["id"]) not in {str(m.id) for m in vista_t.membri}
        assert len((await leggi(db, sec)).membri) == 3

    async def test_esterni_col_nome_dichiarato_ripulito_verso_gli_altri(self, fondo):
        db, sec = await scenario_wp8()
        await consorzio_xy(db, sec)
        await svc.aggiungi_o_modifica_esterno(
            db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
            EsternoIn(denominazione="Fraunhofer Institut", paese="de",
                      tipi_soggetto=["organismo_ricerca"], quota_percentuale=Decimal("10")))
        # un nome che cita il creatore (scritto a DB da un percorso qualunque)
        # esce ripulito verso gli altri membri
        db._nuovo_membro(partner_call_id=g.CALL_GUIDA_ID, esterno_denominazione=RAGIONE["X"],
                         esterno_paese="IT", esterno_tipi_soggetto=["impresa"])
        creatore = await leggi(db, sec)
        membro = await leggi(db, sec, "Y")
        esterni_x = [m for m in creatore.membri if m.esterno]
        esterni_y = [m for m in membro.membri if m.esterno]
        assert [m.nome for m in esterni_x] == ["Fraunhofer Institut", RAGIONE["X"]]
        assert [m.nome for m in esterni_y][0] == "Fraunhofer Institut"
        assert RAGIONE["X"] not in esterni_y[1].nome
        assert esterni_y[0].paese == "DE" and esterni_y[0].tipi_soggetto == ["organismo_ricerca"]
        assert esterni_y[0].pseudonimo is None and esterni_y[0].profilo is None


# ------------------------------------------------------------ validatore e oracolo


class TestValidazione:
    async def test_regola_finanziaria_esatta_per_se_sulla_fascia_per_gli_altri(self, fondo):
        """Oracolo (revisioni WP6/WP8): il creatore controlla budget e quote,
        ma vede l'esito di Y solo sulla fascia; Y vede il proprio sui valori
        esatti, con il dettaglio privato solo per sé."""
        db, sec = await scenario_wp8()
        _, y = await consorzio_xy(db, sec, quote=("40", "60"), snapshot=regole(finanziaria=True))
        creatore = await leggi(db, sec)
        membro = await leggi(db, sec, "Y")
        vx, vy = voce(creatore, "finanziaria:F1"), voce(membro, "finanziaria:F1")
        esiti_x = {str(e.membro_id): e.esito for e in vx.esiti_membri}
        esiti_y = {str(e.membro_id): e.esito for e in vy.esiti_membri}
        # costo della quota di Y 1,86 M€: 0,744 del fatturato medio esatto (rosso
        # per Y); sulla fascia 2-10 M€ tra 0,19 e 0,93 (grigio per il creatore)
        assert esiti_x[str(y["id"])] == "grigio"
        assert esiti_y[str(y["id"])] == "rosso"
        assert vx.dettaglio_privato is None or "2.500.000" not in vx.dettaglio_privato
        assert vy.dettaglio_privato
        assert vy.dettaglio_privato not in creatore.model_dump_json()
        canary(creatore.model_dump_json(), "Y", numeri=NUMERI_Y)
        canary(membro.model_dump_json(), "X", numeri=NUMERI_X)
        # l'esito salvato sulla call è quello del creatore
        assert db.una("partner_calls", id=g.CALL_GUIDA_ID)["validazione_esito"] == \
            creatore.validazione.esito

    async def test_budget_diverso_non_cambia_la_vista_del_creatore_oltre_la_fascia(self, fondo):
        db, sec = await scenario_wp8()
        _, y = await consorzio_xy(db, sec, quote=("40", "60"), snapshot=regole(finanziaria=True))
        esiti = set()
        for budget in ("2100000", "2600000", "3100000", "4900000"):
            await svc.aggiorna_budget(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
                                      BudgetIn(budget_fascia="2m_5m",
                                               budget_progetto_eur=Decimal(budget)))
            out = await leggi(db, sec)
            esiti.add(next(e.esito for e in voce(out, "finanziaria:F1").esiti_membri
                           if str(e.membro_id) == str(y["id"])))
        # il fatturato medio esatto di Y (2,5 M€) direbbe verde a 2,1 M€ e rosso
        # oltre 2,5 M€: sulla fascia il creatore non vede la soglia
        assert esiti == {"grigio"}

    async def test_indipendenza_e_esclusivita_dai_dati_letti(self, fondo):
        db, sec = await scenario_wp8()
        await consorzio_xy(db, sec)
        out = await leggi(db, sec)
        assert voce(out, "indipendenza").esito == "verde"
        # Y impegnata su un'altra call ESCLUSIVA dello stesso bando (membro
        # non uscito): voce rossa; il dettaglio privato solo per Y
        altra = copy.deepcopy(db.una("partner_calls", id=g.CALL_ALTRA_ID))
        altra.update(id=str(uuid.uuid4()), bando_id=g.BANDO_GUIDA, esclusivita=True,
                     stato="scaduta")
        db.tabelle["partner_calls"].append(altra)
        db._nuovo_membro(partner_call_id=altra["id"], company_profile_id=g.COMPANY["Y"],
                         quota_percentuale="30.00")
        creatore = await leggi(db, sec)
        membro = await leggi(db, sec, "Y")
        assert voce(creatore, "esclusivita").esito == "rosso"
        assert voce(creatore, "esclusivita").dettaglio_privato is None
        assert voce(membro, "esclusivita").dettaglio_privato
        # uscita da quella call: non è più impegnata (la riga prevale)
        db.membro_di(g.COMPANY["Y"], altra["id"])["stato"] = "uscito"
        assert all(v.codice != "esclusivita" or v.esito != "rosso"
                   for v in (await leggi(db, sec)).validazione.voci)

    async def test_impegni_altrove_con_la_regola_della_0040(self, fondo):
        """Le tre forme di impegno sullo stesso bando, mai questa call:
        creatrice di un'altra call pubblicata, membro non uscito (non
        creatore) di un'altra call non annullata, accettata senza riga nel
        consorzio; la riga del membro prevale e una call annullata non
        impegna."""
        db, sec = await scenario_wp8()
        await accetta(db, sec)
        call = db.una("partner_calls", id=g.CALL_GUIDA_ID)

        def altra_call(**campi) -> dict:
            riga = copy.deepcopy(db.una("partner_calls", id=g.CALL_ALTRA_ID))
            riga.update({"id": str(uuid.uuid4()), "bando_id": g.BANDO_GUIDA,
                         "esclusivita": False, **campi})
            db.tabelle["partner_calls"].append(riga)
            return riga

        ids = [g.COMPANY[n] for n in ("X", "Y", "T")]
        assert await svc.impegni_altrove(db, call, ids) == {c: () for c in ids}
        # T: creatrice di un'altra call pubblicata ed esclusiva (a)
        altra_call(company_profile_id=g.COMPANY["T"], family_parent_id=g.OWNER["T"],
                   stato="pubblicata", esclusivita=True)
        # Y: membro non uscito di una call scaduta (b) …
        scaduta = altra_call(stato="scaduta")
        db._nuovo_membro(partner_call_id=scaduta["id"], company_profile_id=g.COMPANY["Y"])
        # … e accettata su una call annullata (non conta) e su una senza riga (c)
        for stato in ("chiusa_annullata", "chiusa_completata"):
            riga = altra_call(stato=stato, esclusivita=True)
            db._riga_candidatura(partner_call_id=riga["id"], tipo="candidatura",
                                 company_profile_id=g.COMPANY["Y"],
                                 creatore_company_profile_id=riga["company_profile_id"],
                                 stato="accettata")
        # X: accettata su un'altra call, ma uscita dal suo consorzio
        uscita = altra_call(stato="pubblicata")
        db._riga_candidatura(partner_call_id=uscita["id"], tipo="candidatura",
                             company_profile_id=g.COMPANY["X"],
                             creatore_company_profile_id=uscita["company_profile_id"],
                             stato="accettata")
        db._nuovo_membro(partner_call_id=uscita["id"], company_profile_id=g.COMPANY["X"],
                         stato="uscito")
        impegni = await svc.impegni_altrove(db, call, ids)
        assert impegni[g.COMPANY["T"]] == (True,)
        assert sorted(impegni[g.COMPANY["Y"]]) == [False, True]
        assert impegni[g.COMPANY["X"]] == ()
        # un errore di lettura vale «non noto» (voce grigia), mai un'eccezione
        db.guasti[("partner_call_membri", "select")] = errore("XX000")
        assert await svc.impegni_altrove(db, call, ids) == {c: None for c in ids}

    async def test_creatore_di_una_call_completata_con_partner_impegnato(self, fondo):
        """Come fn_partner_esclusivita_violata (0040): la call propria non
        pubblicata e non annullata impegna il creatore solo se ha ancora un
        altro membro non uscito (in piattaforma o esterno)."""
        db, sec = await scenario_wp8()
        await accetta(db, sec)
        call = db.una("partner_calls", id=g.CALL_GUIDA_ID)
        altra = copy.deepcopy(db.una("partner_calls", id=g.CALL_ALTRA_ID))
        altra.update(id=str(uuid.uuid4()), bando_id=g.BANDO_GUIDA, esclusivita=True,
                     company_profile_id=g.COMPANY["T"], family_parent_id=g.OWNER["T"],
                     stato="chiusa_completata")
        db.tabelle["partner_calls"].append(altra)
        db._nuovo_membro(partner_call_id=altra["id"], company_profile_id=g.COMPANY["T"])
        # solo la riga del creatore: T non è impegnata
        assert (await svc.impegni_altrove(db, call, [g.COMPANY["T"]]))[g.COMPANY["T"]] == ()
        esterno = db._nuovo_membro(partner_call_id=altra["id"], esterno_denominazione="Ente",
                                   esterno_paese="DE", esterno_tipi_soggetto=["impresa"])
        assert (await svc.impegni_altrove(db, call, [g.COMPANY["T"]]))[g.COMPANY["T"]] == (
            True,)
        esterno["stato"] = "uscito"
        assert (await svc.impegni_altrove(db, call, [g.COMPANY["T"]]))[g.COMPANY["T"]] == ()
        altra["stato"] = "chiusa_annullata"
        esterno["stato"] = "proposto"
        assert (await svc.impegni_altrove(db, call, [g.COMPANY["T"]]))[g.COMPANY["T"]] == ()

    async def test_membro_non_piu_attivo_senza_dati_e_segnalato(self, fondo):
        """Il titolare di Y viene disattivato: nel consorzio Y resta (la toglie
        il creatore), ma senza profilo pubblico e senza valutazioni sui suoi
        dati; X lo vede segnalato."""
        db, sec = await scenario_wp8()
        _, y = await consorzio_xy(db, sec)
        await accetta(db, sec, "T")
        prima = await leggi(db, sec, "T")
        y_vista = next(m for m in prima.membri if str(m.id) == str(y["id"]))
        assert y_vista.profilo is not None
        db.una("profiles", id=g.OWNER["Y"])["is_active"] = False
        per_x = await leggi(db, sec)
        per_t = await leggi(db, sec, "T")
        assert next(m for m in per_t.membri if str(m.id) == str(y["id"])).profilo is None
        segnalata = voce(per_x, "membri_attivi")
        assert segnalata.esito == "grigio"
        assert [str(i) for i in segnalata.membri_coinvolti] == [str(y["id"])]
        assert voce(per_t, "membri_attivi").membri_coinvolti == []
        assert voce(per_x, "indipendenza").esito == "grigio"
        assert all(c.esito in ("dato_mancante", "non_valutabile") or not c.si_applica
                   for r in per_x.matrice.righe for c in r.celle
                   if str(c.membro_id) == str(y["id"]))
        assert per_x.validazione.esito != "verde"

    async def test_marker_non_valido_indipendenza_grigia(self, fondo):
        db, sec = await scenario_wp8()
        await consorzio_xy(db, sec)
        db.tabelle["company_collegamenti_stato"] = [
            r for r in db.tabelle["company_collegamenti_stato"]
            if r["company_profile_id"] != g.COMPANY["Y"]]
        out = await leggi(db, sec)
        assert voce(out, "indipendenza").esito == "grigio"


# ------------------------------------------------------------ mutazioni


class TestMutazioni:
    async def test_validazione_salvata_dopo_ogni_mutazione(self, fondo):
        db, sec = await scenario_wp8()
        await accetta(db, sec)
        imposta_regole(db, regole())
        x, y = db.membro_di(g.COMPANY["X"]), db.membro_di(g.COMPANY["Y"])
        ax, ay = attiva("X"), attiva("Y")
        operazioni = [
            lambda: svc.aggiorna_membro(db, sec, ax, utente("X"), g.CALL_GUIDA_ID, x["id"],
                                        MembroAggiornaIn(ruolo="capofila",
                                                         quota_percentuale=Decimal("70"))),
            lambda: svc.aggiorna_membro(db, sec, ax, utente("X"), g.CALL_GUIDA_ID, y["id"],
                                        MembroAggiornaIn(ruolo="partner", posizione_id=g.POS_P1,
                                                         quota_percentuale=Decimal("30"))),
            lambda: svc.conferma(db, sec, ay, utente("Y"), g.CALL_GUIDA_ID, y["id"],
                                 termini(db, y["id"])),
            lambda: svc.aggiungi_o_modifica_esterno(
                db, sec, ax, utente("X"), g.CALL_GUIDA_ID,
                EsternoIn(denominazione="Ente esterno", paese="DE", tipi_soggetto=["impresa"])),
            lambda: svc.aggiorna_budget(db, sec, ax, utente("X"), g.CALL_GUIDA_ID,
                                        BudgetIn(budget_fascia="2m_5m",
                                                 budget_progetto_eur=Decimal("3000000"))),
            lambda: svc.set_documento(db, sec, ax, utente("X"), g.CALL_GUIDA_ID, "nda",
                                      DocumentoStatoIn(stato="fatto")),
            lambda: svc.esci(db, sec, ay, utente("Y"), g.CALL_GUIDA_ID, y["id"]),
        ]
        for i, operazione in enumerate(operazioni, start=1):
            db.una("partner_calls", id=g.CALL_GUIDA_ID)["validazione_esito"] = None
            out = await operazione()
            assert salvataggi(db) == i, i
            call = db.una("partner_calls", id=g.CALL_GUIDA_ID)
            vista_creatore = await leggi(db, sec)
            assert call["validazione_esito"] == vista_creatore.validazione.esito, i
            # dopo la propria uscita Y vede solo la sua riga (niente validazione)
            assert (out.validazione_at is None) == (i == len(operazioni)), i
        # dopo 70/30 e la conferma il consorzio era verde; uscita di Y: rosso
        assert db.una("partner_calls", id=g.CALL_GUIDA_ID)["validazione_esito"] == "rosso"

    async def test_in_lettura_si_salva_solo_se_cambiata(self, fondo):
        db, sec = await scenario_wp8()
        await consorzio_xy(db, sec)
        prima = salvataggi(db)
        await leggi(db, sec)
        await leggi(db, sec, "Y")
        assert salvataggi(db) == prima
        # cambia un dato che la validazione usa (un nuovo membro accettato)
        await accetta(db, sec, "T")
        await leggi(db, sec, "Y")
        assert salvataggi(db) == prima + 1
        await leggi(db, sec)
        assert salvataggi(db) == prima + 1

    async def test_una_lettura_non_sovrascrive_un_salvataggio_piu_recente(self, fondo,
                                                                         monkeypatch):
        """Una GET partita prima di una scrittura (qui: letta «un'ora fa») non
        sovrascrive l'esito salvato da quella scrittura dopo l'inizio della
        lettura, anche se ha calcolato altro sui dati vecchi."""
        db, sec = await scenario_wp8()
        await consorzio_xy(db, sec)
        call = db.una("partner_calls", id=g.CALL_GUIDA_ID)
        salvato = datetime.now(timezone.utc)
        call.update(validazione_esito="rosso", validazione_at=salvato.isoformat())
        prima = salvataggi(db)
        monkeypatch.setattr(svc, "_adesso", lambda: salvato - timedelta(hours=1))
        out = await leggi(db, sec)
        assert out.validazione.esito == "verde"
        assert salvataggi(db) == prima and call["validazione_esito"] == "rosso"
        # una lettura iniziata dopo il salvataggio lo aggiorna
        monkeypatch.setattr(svc, "_adesso", lambda: salvato + timedelta(seconds=1))
        await leggi(db, sec)
        assert salvataggi(db) == prima + 1 and call["validazione_esito"] == "verde"

    async def test_salvataggio_best_effort(self, fondo, caplog):
        db, sec = await scenario_wp8()
        _, y = await consorzio_xy(db, sec)
        db.rpc_guasti["fn_partner_call_validazione_salva"] = errore("XX000")
        out = await svc.conferma(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID, y["id"],
                                 termini(db, y["id"]))
        assert out.validazione_at is None
        assert db.membro_di(g.COMPANY["Y"])["stato"] == "confermato"
        assert "validazione non salvata" in caplog.text

    async def test_modifica_riporta_a_proposto_con_notifica_e_dedup(self, fondo):
        db, sec = await scenario_wp8()
        _, y = await consorzio_xy(db, sec)
        await svc.conferma(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID, y["id"],
                           termini(db, y["id"]))
        assert db.membro_di(g.COMPANY["Y"])["stato"] == "confermato"
        notifiche = lambda: db.righe("notifications", tipo=svc.TIPO_CONSORZIO_AGGIORNATO)  # noqa: E731
        prima = len(notifiche())
        dati = MembroAggiornaIn(ruolo="partner", posizione_id=g.POS_P1,
                                quota_percentuale=Decimal("25"))
        await svc.aggiorna_membro(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, y["id"],
                                  dati)
        assert db.membro_di(g.COMPANY["Y"])["stato"] == "proposto"
        nuove = notifiche()[prima:]
        assert [n["user_id"] for n in nuove] == [g.OWNER["Y"]]
        [n] = nuove
        assert n["url"] == (f"/app/partenariati/call/{g.CALL_GUIDA_ID}?tab=consorzio"
                            f"&azienda={g.COMPANY['Y']}")
        assert n["company_profile_id"] == g.COMPANY["Y"]
        canary(f"{n['titolo']} {n['corpo']}", "X")
        # la stessa scrittura (nulla cambia) non notifica di nuovo
        await svc.aggiorna_membro(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, y["id"],
                                  dati)
        assert len(notifiche()) == prima + 1
        # la stessa notifica ripetuta (stesso evento) resta una
        membro = db.membro_di(g.COMPANY["Y"])
        await svc._notifica(db, db.una("partner_calls", id=g.CALL_GUIDA_ID),
                            company_id=g.COMPANY["Y"], titolo="t", corpo="c",
                            dedup=f"partner-consorzio:{membro['id']}:conferma:"
                                  f"{membro['updated_at']}")
        assert len(notifiche()) == prima + 1

    async def test_la_propria_riga_del_creatore_non_chiede_conferme(self, fondo):
        db, sec = await scenario_wp8()
        x, _ = await consorzio_xy(db, sec)
        await svc.conferma(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, x["id"],
                           termini(db, x["id"]))
        prima = len(db.righe("notifications", tipo=svc.TIPO_CONSORZIO_AGGIORNATO))
        await svc.aggiorna_membro(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, x["id"],
                                  MembroAggiornaIn(ruolo="capofila",
                                                   quota_percentuale=Decimal("65")))
        assert db.membro_di(g.COMPANY["X"])["stato"] == "confermato"
        assert len(db.righe("notifications", tipo=svc.TIPO_CONSORZIO_AGGIORNATO)) == prima

    async def test_uscite_notificano_l_altra_parte(self, fondo):
        db, sec = await scenario_wp8()
        _, y = await consorzio_xy(db, sec)
        t = await accetta(db, sec, "T")
        await svc.esci(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID, y["id"])
        al_creatore = db.righe("notifications", tipo=svc.TIPO_CONSORZIO_AGGIORNATO,
                               company_profile_id=g.COMPANY["X"])
        assert {n["user_id"] for n in al_creatore} == {g.OWNER["X"], MEMBRO_X}
        await svc.esci(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, t["id"])
        a_t = db.righe("notifications", tipo=svc.TIPO_CONSORZIO_AGGIORNATO,
                       company_profile_id=g.COMPANY["T"])
        assert [n["user_id"] for n in a_t] == [g.OWNER["T"]]
        for n in db.righe("notifications", tipo=svc.TIPO_CONSORZIO_AGGIORNATO):
            canary(f"{n['titolo']} {n['corpo']}", "X", "Y", "T")
        # uscire due volte non riscrive e non notifica: chi è uscito non è più
        # controparte (404, come per chiunque non sia nel consorzio)
        prima = len(db.righe("notifications", tipo=svc.TIPO_CONSORZIO_AGGIORNATO))
        with pytest.raises(NotFoundError):
            await svc.esci(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID, y["id"])
        assert len(db.righe("notifications", tipo=svc.TIPO_CONSORZIO_AGGIORNATO)) == prima
        assert db.chiamate("fn_partner_membro_esci")[-1]["p_membro"] == str(t["id"])

    async def test_errori_delle_rpc_mappati(self, fondo):
        db, sec = await scenario_wp8()
        x, y = await consorzio_xy(db, sec, quote=("70", None))
        casi = [
            (svc.conferma(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID, y["id"],
                          termini(db, y["id"])),
             409, "quota_mancante"),
            (svc.aggiorna_membro(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, y["id"],
                                 MembroAggiornaIn(ruolo="capofila")),
             409, "capofila_gia_presente"),
            (svc.aggiorna_membro(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, x["id"],
                                 MembroAggiornaIn(ruolo="affiliated_entity")),
             409, "ruolo_non_ammesso"),
            (svc.esci(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, x["id"]),
             409, "membro_non_rimovibile"),
            (svc.aggiorna_membro(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, y["id"],
                                 MembroAggiornaIn(ruolo="partner", posizione_id=g.POS_ALTRA)),
             400, "bad_request"),
            (svc.set_documento(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
                               "statuto_inesistente", DocumentoStatoIn(stato="fatto")),
             400, "documento_non_valido"),
            (svc.set_documento(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, "nda",
                               DocumentoStatoIn(stato="fatto", note="Scrivere a a@b.it")),
             400, "testo_non_conforme"),
            (svc.aggiungi_o_modifica_esterno(
                db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
                EsternoIn(denominazione=f"Partner di {RAGIONE['X']}", paese="IT",
                          tipi_soggetto=["impresa"])),
             400, "testo_non_conforme"),
            (svc.aggiungi_o_modifica_esterno(
                db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
                EsternoIn(denominazione="Ente esterno", paese="DE", tipi_soggetto=["impresa"]),
                y["id"]),
             404, "not_found"),
        ]
        for coro, status, code in casi:
            with pytest.raises(AppError) as exc:
                await coro
            assert (exc.value.status_code, exc.value.code) == (status, code), code
        # chiusa come annullata: il consorzio non si modifica più
        db.una("partner_calls", id=g.CALL_GUIDA_ID)["stato"] = "chiusa_annullata"
        with pytest.raises(AppError) as exc:
            await svc.aggiorna_membro(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
                                      y["id"], MembroAggiornaIn(ruolo="partner"))
        assert exc.value.code == "call_non_modificabile"
        # … ma chi è membro esce comunque da sé
        await svc.esci(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID, y["id"])
        assert db.membro_di(g.COMPANY["Y"])["stato"] == "uscito"

    async def test_chi_scrive(self, fondo):
        db, sec = await scenario_wp8()
        x, y = await consorzio_xy(db, sec)
        # la controparte non modifica né toglie gli altri, né conferma per loro
        with pytest.raises(NotFoundError):
            await svc.aggiorna_membro(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID,
                                      y["id"], MembroAggiornaIn(ruolo="partner"))
        for azione in (
            lambda: svc.conferma(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID, x["id"],
                                 termini(db, x["id"])),
            lambda: svc.esci(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID, x["id"]),
        ):
            with pytest.raises(AppError) as exc:
                await azione()
            assert (exc.value.status_code, exc.value.code) == (404, "not_found")
        # il membro con visibilità di X legge soltanto
        with pytest.raises(ForbiddenError):
            await svc.conferma(db, sec, attiva("X", editable=False), {"id": MEMBRO_X},
                               g.CALL_GUIDA_ID, x["id"], termini(db, x["id"]))
        # un membro di un'altra call non si tocca da questa
        membro_altra = db.membri(g.CALL_ALTRA_ID)[0]
        with pytest.raises(NotFoundError):
            await svc.esci(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
                           membro_altra["id"])
        with pytest.raises(NotFoundError):
            await svc.esci(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, "non-un-id")
        # l'unica uscita arrivata alla RPC (quella di Y sulla riga di X) è stata
        # respinta lì: nessuna riga è cambiata
        assert len(db.chiamate("fn_partner_membro_esci")) == 1
        assert db.membro_di(g.COMPANY["X"])["stato"] != "uscito"
        assert membro_altra["stato"] != "uscito"

    async def test_esterni_limite_e_riproposta(self, fondo):
        db, sec = await scenario_wp8()
        out = await svc.aggiungi_o_modifica_esterno(
            db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
            EsternoIn(denominazione="Ente esterno", paese="DE", tipi_soggetto=["impresa"],
                      quota_percentuale=Decimal("5")))
        esterno = next(m for m in out.membri if m.esterno)
        assert (esterno.stato, esterno.puo_confermare) == ("proposto", True)
        [chiamata] = db.chiamate("fn_partner_membro_esterno")
        assert set(chiamata["p_payload"]) == {"denominazione", "paese", "tipi_soggetto", "ruolo",
                                              "quota", "posizione_id"}
        # il creatore conferma per l'esterno, poi lo toglie e lo ripropone
        await svc.conferma(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, esterno.id,
                           termini(db, esterno.id))
        await svc.esci(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, esterno.id)
        out = await svc.aggiungi_o_modifica_esterno(
            db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
            EsternoIn(denominazione="Ente esterno", paese="DE", tipi_soggetto=["impresa"]),
            esterno.id)
        assert next(m for m in out.membri if m.id == esterno.id).stato == "proposto"
        for i in range(28):
            db._nuovo_membro(partner_call_id=g.CALL_GUIDA_ID, esterno_denominazione=f"E{i:02d}",
                             esterno_paese="FR", esterno_tipi_soggetto=["impresa"])
        with pytest.raises(AppError) as exc:
            await svc.aggiungi_o_modifica_esterno(
                db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
                EsternoIn(denominazione="Uno di troppo", paese="FR", tipi_soggetto=["impresa"]))
        assert exc.value.code == "limite_membri"

    async def test_documenti_con_stato_e_note_ripulite_verso_gli_altri(self, fondo):
        db, sec = await scenario_wp8()
        await consorzio_xy(db, sec)
        out = await svc.set_documento(
            db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, "mandato_collettivo",
            DocumentoStatoIn(stato="in_corso", note=f"Lo firma {RAGIONE['X']} dal notaio"))
        doc = next(d for d in out.documenti if d.codice == "mandato_collettivo")
        assert (doc.stato, doc.fonte) == ("in_corso", "forma")
        assert RAGIONE["X"] in doc.note
        # checklist della forma (ATS): documenti di base + specifici
        assert {"nda", "lettera_intenti", "mandato_collettivo", "atto_costitutivo"} <= {
            d.codice for d in out.documenti}
        visto = next(d for d in (await leggi(db, sec, "Y")).documenti
                     if d.codice == "mandato_collettivo")
        assert visto.stato == "in_corso" and RAGIONE["X"] not in (visto.note or "")

    async def test_call_scaduta_consorzio_modificabile_annullata_no(self, fondo):
        """Alla scadenza della ricerca di partner il partenariato può andare
        avanti: il consorzio resta modificabile (membri, conferme, esterni,
        documenti), come per una call completata. Annullata o sospesa no."""
        db, sec = await scenario_wp8()
        _, y = await consorzio_xy(db, sec)
        db.una("partner_calls", id=g.CALL_GUIDA_ID)["stato"] = "scaduta"
        out = await leggi(db, sec)
        assert out.modificabile is True and out.budget.modificabile is False
        assert next(m for m in out.membri if str(m.id) == str(y["id"])).puo_modificare
        out = await svc.set_documento(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, "nda",
                                      DocumentoStatoIn(stato="fatto"))
        assert next(d for d in out.documenti if d.codice == "nda").stato == "fatto"
        out = await svc.conferma(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID, y["id"],
                                 termini(db, y["id"]))
        assert next(m for m in out.membri if m.sei_tu).stato == "confermato"
        assert (await leggi(db, sec, "Y")).modificabile is False  # solo il creatore
        for stato in ("chiusa_annullata", "sospesa_moderazione"):
            db.una("partner_calls", id=g.CALL_GUIDA_ID)["stato"] = stato
            if stato == "chiusa_annullata":
                out = await leggi(db, sec)
                assert out.modificabile is False
                assert not any(m.puo_modificare for m in out.membri)
            with pytest.raises(AppError) as exc:
                await svc.set_documento(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
                                        "nda", DocumentoStatoIn(stato="in_corso"))
            assert exc.value.code == "call_non_modificabile"

    async def test_conferma_sui_termini_visti(self, fondo):
        """La pagina di Y mostra il 30%, X lo porta al 20%: la conferma col 30%
        risponde membro_modificato e Y resta da confermare."""
        db, sec = await scenario_wp8()
        _, y = await consorzio_xy(db, sec)
        visti = termini(db, y["id"])
        await svc.aggiorna_membro(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, y["id"],
                                  MembroAggiornaIn(ruolo="partner", posizione_id=g.POS_P1,
                                                   quota_percentuale=Decimal("20")))
        with pytest.raises(AppError) as exc:
            await svc.conferma(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID, y["id"],
                               visti)
        assert (exc.value.status_code, exc.value.code) == (409, "membro_modificato")
        assert db.membro_di(g.COMPANY["Y"])["stato"] == "proposto"
        [chiamata] = db.chiamate("fn_partner_membro_conferma")
        assert (chiamata["p_ruolo"], chiamata["p_posizione"], chiamata["p_quota"]) == (
            "partner", g.POS_P1, "30.00")

    async def test_budget_riservato_e_solo_da_pubblicata(self, fondo):
        db, sec = await scenario_wp8()
        await consorzio_xy(db, sec)
        out = await svc.aggiorna_budget(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
                                        BudgetIn(budget_fascia="2m_5m",
                                                 budget_progetto_eur=Decimal("2500000")))
        assert str(out.budget.esatto) == "2500000.00"
        [chiamata] = db.chiamate("fn_partner_call_aggiorna")
        assert set(chiamata["p_campi"]) == {"budget_fascia", "budget_progetto_eur"}
        with pytest.raises(AppError):
            BudgetIn(budget_fascia="500k_1m", budget_progetto_eur=Decimal("2500000"))
        db.una("partner_calls", id=g.CALL_GUIDA_ID)["stato"] = "chiusa_completata"
        with pytest.raises(AppError) as exc:
            await svc.aggiorna_budget(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
                                      BudgetIn(budget_fascia="2m_5m"))
        assert exc.value.code == "stato_call_non_valido"
        with pytest.raises(ForbiddenError):
            await svc.aggiorna_budget(db, sec, attiva("X", editable=False), {"id": MEMBRO_X},
                                      g.CALL_GUIDA_ID, BudgetIn(budget_fascia="2m_5m"))


# ------------------------------------------------------------ prerequisiti


class TestPrerequisiti:
    async def test_azienda_viva_richiede_il_titolare_attivo(self, fondo):
        """Prerequisito 1: `_azienda_viva` come la 0040 (titolare attivo)."""
        db, sec = await scenario_wp8()
        call, ruolo = await acc.carica_call_autorizzata(db, g.CALL_GUIDA_ID, attiva("O"),
                                                        utente("O"))
        assert ruolo == "pubblico"
        await candidature.invia_candidatura(db, sec, attiva("T"), utente("T"), g.CALL_GUIDA_ID,
                                            candidatura_in())
        _, ruolo_t = await acc.carica_call_autorizzata(db, g.CALL_GUIDA_ID, attiva("T"),
                                                       utente("T"))
        assert ruolo_t == "candidato"
        db.una("profiles", id=g.OWNER["X"])["is_active"] = False
        for nome in ("O", "T"):
            with pytest.raises(NotFoundError):
                await acc.carica_call_autorizzata(db, g.CALL_GUIDA_ID, attiva(nome),
                                                  utente(nome))
        # l'azienda creatrice la vede comunque (non passa da «viva»)
        _, ruolo_x = await acc.carica_call_autorizzata(db, g.CALL_GUIDA_ID, attiva("X"),
                                                       utente("X"))
        assert ruolo_x == "creatore"

    async def test_candidature_ricevute_nelle_card(self, fondo):
        """Prerequisito 2: spontanee in attesa o accettate, per pagina."""
        db, sec = await scenario_wp8()

        async def contatore(nome: str, vista: str) -> int:
            pagina = await pcs.bacheca(db, sec, attiva(nome), utente(nome), vista=vista)
            return next(c.candidature_ricevute for c in pagina.items
                        if str(c.id) == g.CALL_GUIDA_ID)

        assert await contatore("X", "mie") == 0
        cand = await candidature.invia_candidatura(db, sec, attiva("Y"), utente("Y"),
                                                   g.CALL_GUIDA_ID, candidatura_in())
        partenariato_indice.invalida()
        assert await contatore("X", "mie") == 1
        assert await contatore("O", "tutte") == 1
        per_te = await pcs.per_te(db, sec, attiva("Y"), utente("Y"))
        assert next(c.candidature_ricevute for c in per_te.items
                    if str(c.id) == g.CALL_GUIDA_ID) == 1
        # un invito del creatore non è una candidatura ricevuta
        await candidature.invita(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
                                 candidature.InvitoIn(pseudonimo=pseudonimo(
                                     g.CALL_GUIDA_ID, g.CODICE_PUBBLICO["T"])))
        assert await contatore("X", "mie") == 1
        # accettata conta ancora, rifiutata no
        await candidature.decidi(db, sec, attiva("X"), utente("X"), cand.id, "accetta")
        assert await contatore("X", "mie") == 1
        db.una("partner_candidature", id=str(cand.id))["stato"] = "rifiutata"
        assert await contatore("X", "mie") == 0
        # una lettura per pagina, fuori dalla ricarica dell'indice
        db.ops.clear()
        await contatore("X", "mie")
        assert len([o for o in db.letture() if o["tabella"] == "partner_candidature"]) == 1

    async def test_candidature_ricevute_best_effort(self, fondo, caplog):
        db, sec = await scenario_wp8()
        db.guasti[("partner_candidature", "select")] = errore("XX000")
        pagina = await pcs.bacheca(db, sec, attiva("X"), utente("X"), vista="mie")
        assert {c.candidature_ricevute for c in pagina.items} == {0}
        assert "candidature ricevute non contate" in caplog.text

    async def test_membro_uscito_non_impegnato_sul_bando_nell_indice(self, fondo):
        db, sec = await scenario_wp8()
        y = await accetta(db, sec)
        idx = await partenariato_indice.indice(db, sec)
        assert g.BANDO_GUIDA in idx.impegni[g.COMPANY["Y"]]
        await svc.esci(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID, y["id"])
        idx = await partenariato_indice.indice(db, sec)  # invalidato dall'uscita
        assert g.BANDO_GUIDA not in idx.impegni.get(g.COMPANY["Y"], frozenset())

    async def test_creatore_di_call_completata_con_partner_impegnato_nell_indice(self, fondo):
        """Indice del matching come fn_partner_esclusivita_violata (0040): la
        call completata con un'accettata non uscita impegna anche il creatore."""
        db, sec = await scenario_wp8()
        y = await accetta(db, sec)
        db.una("partner_calls", id=g.CALL_GUIDA_ID)["stato"] = "chiusa_completata"
        partenariato_indice.invalida()
        idx = await partenariato_indice.indice(db, sec)
        assert g.BANDO_GUIDA in idx.impegni[g.COMPANY["X"]]
        await svc.esci(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID, y["id"])
        idx = await partenariato_indice.indice(db, sec)
        assert g.BANDO_GUIDA not in idx.impegni.get(g.COMPANY["X"], frozenset())

    async def test_accettazione_scrive_creatore_e_membro(self, fondo):
        db, sec = await scenario_wp8()
        db.tabelle["partner_call_membri"] = []
        y = await accetta(db, sec)
        x = db.membro_di(g.COMPANY["X"])
        assert (x["ruolo"], x["quota_percentuale"], x["stato"]) == ("capofila", "80.00",
                                                                     "proposto")
        assert (y["ruolo"], y["quota_percentuale"], y["posizione_id"]) == (
            "partner", "20.00", g.POS_P1)
