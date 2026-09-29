"""Moderazione DSA dei partenariati (WP9, docs/partenariati.md W2; DSA art.
16-20): servizio vero sul primario finto del WP8 esteso con le RPC della 0041.

Qui vive il primario FINTO del WP9 (`FakePrimaryWP9`): presa in carico,
decisione con effetto sull'oggetto, ricorso, decisione del ricorso,
sospensione e ripristino diretti, metriche e costi (gemelli in Python della
0041 sullo stesso seed del test DB), verifica dell'identità da parte
dell'admin e call da rivalidare, con le stesse guardie e gli stessi detail.
Le guardie vere, i lock, i vincoli e l'atomicità li verifica
`tests/db/test_migration_0041.py`.

Verifica: coerenza tra oggetto e decisione (400 prima della RPC), effetti
sull'oggetto (call sospesa con lo stato precedente, messaggio oscurato,
profilo sospeso) e nessun effetto né notifica se la RPC rifiuta; statement of
reasons dal template «BOZZA — DA RIVEDERE CON IL LEGALE» all'autore in-app e
per email al solo titolare recapitabile; notifiche e viste SENZA l'identità di
chi ha segnalato (e senza l'autore verso chi ha segnalato); ricorso unico
entro 6 mesi, dalle parti giuste, `riformata` che ripristina o applica,
`mantenuto`; sospensione e ripristino diretti (anche `scaduta`); contesto
±10 messaggi e conversazione intera solo con motivazione e audit, audit
fail-closed; uscita dal consorzio di una call sospesa; passo dello
scheduler che ricalcola le validazioni."""

import copy
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import pytest

from app.core.errors import AppError, ForbiddenError, NotFoundError, UpstreamError
from app.schemas.partenariato_moderazione import (
    DecisioneIn,
    RicorsoDecisioneIn,
    RicorsoIn,
    SospendiIn,
    SospensioneIn,
)
from app.schemas.partner_call import SegnalazioneIn
from app.services import partenariati_scheduler, partenariato_collegamenti, partenariato_indice
from app.services import partenariato_consorzio_service as consorzio
from app.services import partenariato_moderazione_service as mod
from app.services import partenariato_moderazione_testi as testi
from app.services import partner_call_service as pcs
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariato_candidature_service import (  # noqa: F401 — fixture
    FM_X,
    MEMBRO_X,
    FakeQueryWP7,
    _confronta,
    _termini,
    _valore,
    attiva,
    errore,
    fixture_fondo,
    utente,
)
from tests.test_partenariato_consorzio_service import (
    FakePrimaryWP8,
    accetta,
    consorzio_xy,
)
from tests.test_partenariato_indice import (  # noqa: F401 — fixture autouse
    EMAIL,
    PIVA,
    RAGIONE,
    ambiente_wp6,
    carica_guida,
    errore_pg,
    secondario_guida,
)

ADMIN_ID = "ad000000-0000-4000-8000-000000000001"
ADMIN = {"id": ADMIN_ID, "role": "admin", "is_active": True}
EMAIL_MEMBRO_X = "membro.x@example.test"
ROMA = ZoneInfo("Europe/Rome")
MOTIVAZIONE = "La call riporta un numero di telefono nel testo pubblico, vietato dai Termini."
MOTIVAZIONE_RICORSO = "Il recapito indicato era quello del bando, non dell'azienda: accolto."
TESTO_RICORSO = "Il numero nel testo è quello dell'ente che gestisce il bando, non il nostro."
DESCRIZIONE = "La call contiene il numero di telefono dell'azienda nel testo pubblico."

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_DECISIONI = ("nessuna_azione", "contenuto_rimosso", "call_sospesa", "profilo_sospeso")
_PER_OGGETTO = {"call": "call_sospesa", "messaggio": "contenuto_rimosso",
                "profilo": "profilo_sospeso"}
_COLONNE_SEGNALAZIONE = (
    "autore_company_profile_id", "decisione", "motivazione", "sor_testo", "deciso_da",
    "deciso_at", "ricorso_testo", "ricorso_da_user_id", "ricorso_at", "ricorso_esito",
    "ricorso_motivazione", "ricorso_deciso_da", "ricorso_deciso_at",
)
_METODI = ("telefonata_sede", "documento_legale_rappresentante", "pec", "altro")
# Servizi del modulo nei costi (0041, 8b).
SERVIZI_COSTO = {
    ("anthropic", "partenariato_estrazione"): "USD", ("anthropic", "partner_profilo_ai"): "USD",
    ("anthropic", "partner_call_posizioni"): "USD", ("anthropic", "partner_call_testi"): "USD",
    ("anthropic", "partner_bozza"): "USD", ("openapi", "IT-advanced"): "EUR",
    ("openapi", "bilancio-ottico"): "EUR", ("openapi", "bilancio-ottico-stato"): "EUR",
    ("openapi", "visure-impresa"): "EUR",
}


def _ora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts(valore) -> datetime:
    istante = datetime.fromisoformat(str(valore).replace("Z", "+00:00"))
    return istante if istante.tzinfo else istante.replace(tzinfo=timezone.utc)


def _meno_mesi(istante: datetime, mesi: int) -> datetime:
    """`now() - interval 'N months'` di Postgres."""
    totale = istante.month - 1 - mesi
    anno, mese = istante.year + totale // 12, totale % 12 + 1
    ultimo = [31, 29 if anno % 4 == 0 and (anno % 100 or anno % 400 == 0) else 28, 31, 30,
              31, 30, 31, 31, 30, 31, 30, 31][mese - 1]
    return istante.replace(year=anno, month=mese, day=min(istante.day, ultimo))


def _arrotonda(valore: Decimal, cifre: int) -> float:
    return float(valore.quantize(Decimal(1).scaleb(-cifre), rounding=ROUND_HALF_UP))


# ------------------------------------------------------------ primario finto


def _vale(riga: dict, termine: str) -> bool:
    colonna, op, valore = termine.split(".", 2)
    if op == "ilike":
        testo = valore.strip("*").lower()
        return testo in str(_valore(riga, colonna) or "").lower()
    return _confronta(op, _valore(riga, colonna), valore)


class FakeQueryWP9(FakeQueryWP7):
    """La query del WP7 con `ilike`, anche nei filtri `or` (ricerca
    dell'admin)."""

    def ilike(self, c, v):
        return self._f("or", "", f"{c}.ilike.{v}")

    def _passa(self, riga: dict) -> bool:
        for op, c, v in self.filtri:
            if op == "or":
                if not any(_vale(riga, t) for t in _termini(v)):
                    return False
            elif not _confronta(op, _valore(riga, c), v):
                return False
        return True


class FakePrimaryWP9(FakePrimaryWP8):
    """Il primario finto del WP8 con le RPC della 0041 (stesse guardie e
    detail)."""

    def __init__(self):
        super().__init__()
        note = dict(self.colonne_note)
        note["partner_segnalazioni"] = (*_COLONNE_SEGNALAZIONE, "stato",
                                        "segnalante_company_id")
        note["partner_calls"] = (*note["partner_calls"], "stato_prima_sospensione",
                                 "sospeso_da")
        note["profiles"] = (*note.get("profiles", ()), "role")
        note["company_identita_stato"] = ("metodo", "verificata_at", "verificata_da",
                                          "richiesta_at", "aggiornato_at")
        note["company_identita_verifiche"] = ("nota",)
        self.colonne_note = note
        self._identita_id = 0

    def table(self, nome):
        return FakeQueryWP9(self, nome)

    def inserisci(self, tabella: str, riga: dict) -> dict:
        if tabella == "partner_segnalazioni":
            riga.setdefault("stato", "ricevuta")
            for colonna in _COLONNE_SEGNALAZIONE:
                riga.setdefault(colonna, None)
            if any(r["oggetto_tipo"] == riga["oggetto_tipo"]
                   and r["oggetto_id"] == riga["oggetto_id"]
                   and r["segnalante_user_id"] == riga["segnalante_user_id"]
                   and r["stato"] in ("ricevuta", "in_esame")
                   for r in self.tabelle.get(tabella, [])):
                raise errore_pg("23505")
        return super().inserisci(tabella, riga)

    # -- aiuti delle guardie
    def _admin(self, admin) -> None:
        profilo = next(iter(self.righe("profiles", id=admin)), None)
        if not profilo or profilo.get("role") != "admin" or not profilo.get("is_active"):
            raise errore("admin_non_autorizzato")

    def _seg(self, sid) -> dict:
        s = next(iter(self.righe("partner_segnalazioni", id=sid)), None)
        if s is None:
            raise errore("segnalazione_non_trovata")
        return s

    def _owner(self, company) -> str | None:
        azienda = next(iter(self.righe("company_profiles", id=company)), None)
        return azienda["parent_id"] if azienda else None

    def _autore_di(self, tipo, oid) -> str | None:
        if tipo == "call" and _UUID.match(oid):
            riga = next(iter(self.righe("partner_calls", id=oid)), None)
            return riga["company_profile_id"] if riga else None
        if tipo == "profilo" and _UUID.match(oid):
            riga = next(iter(self.righe("company_partner_profiles", codice_pubblico=oid)), None)
            return riga["company_profile_id"] if riga else None
        if tipo == "messaggio" and re.fullmatch(r"[0-9]{1,18}", oid):
            riga = next(iter(self.righe("partner_messaggi", id=int(oid))), None)
            return riga["mittente_company_profile_id"] if riga else None
        return None

    def _oggetto(self, tipo, oid) -> dict | None:
        if tipo == "call" and _UUID.match(oid):
            return next(iter(self.righe("partner_calls", id=oid)), None)
        if tipo == "profilo" and _UUID.match(oid):
            return next(iter(self.righe("company_partner_profiles", codice_pubblico=oid)), None)
        if tipo == "messaggio" and re.fullmatch(r"[0-9]{1,18}", oid):
            return next(iter(self.righe("partner_messaggi", id=int(oid))), None)
        if tipo not in ("call", "profilo", "messaggio"):
            raise errore("parametri_non_validi")
        return None

    def _applica(self, tipo, oid, admin, motivazione) -> dict:
        """fn_partner_moderazione_applica."""
        riga = self._oggetto(tipo, oid)
        if riga is None:
            return {"esito": "oggetto_non_trovato"}
        company = riga.get("mittente_company_profile_id") if tipo == "messaggio" \
            else riga["company_profile_id"]
        base = {"autore_company_id": company, "autore_owner_id": self._owner(company)}
        motivo = motivazione.strip()[:500]
        if tipo == "call":
            if riga["stato"] == "sospesa_moderazione":
                return {"esito": "gia_applicato", "stato": riga["stato"], **base}
            if riga["stato"] not in ("bozza", "pubblicata"):
                return {"esito": "non_sospendibile", "stato": riga["stato"], **base}
            prima = riga["stato"]
            riga.update(stato="sospesa_moderazione", stato_prima_sospensione=prima,
                        sospesa_at=_ora(), sospeso_motivo=motivo, sospeso_da=admin)
            return {"esito": "applicato", "stato_precedente": prima,
                    "stato": "sospesa_moderazione", **base}
        if tipo == "profilo":
            if riga.get("sospeso_at"):
                return {"esito": "gia_applicato", **base}
            riga.update(sospeso_at=_ora(), sospeso_motivo=motivo, sospeso_da=admin)
            return {"esito": "applicato", **base}
        if riga.get("nascosto_moderazione_at"):
            return {"esito": "gia_applicato", **base}
        riga.update(nascosto_moderazione_at=_ora(), nascosto_da=admin)
        return {"esito": "applicato", **base}

    def _annulla(self, tipo, oid) -> dict:
        """fn_partner_moderazione_annulla."""
        riga = self._oggetto(tipo, oid)
        if riga is None:
            return {"esito": "oggetto_non_trovato"}
        company = riga.get("mittente_company_profile_id") if tipo == "messaggio" \
            else riga["company_profile_id"]
        base = {"autore_company_id": company, "autore_owner_id": self._owner(company)}
        if tipo == "call":
            if riga["stato"] != "sospesa_moderazione":
                return {"esito": "non_sospeso", "stato": riga["stato"], **base}
            stato = riga["stato_prima_sospensione"]
            oggi = datetime.now(ROMA).date().isoformat()
            if stato == "pubblicata" and str(riga["scadenza_call"])[:10] < oggi:
                stato = "scaduta"
            riga.update(stato=stato, chiusa_at=_ora() if stato == "scaduta" else None,
                        motivo_chiusura="scadenza_call" if stato == "scaduta" else None,
                        stato_prima_sospensione=None, sospesa_at=None, sospeso_motivo=None,
                        sospeso_da=None)
            return {"esito": "annullato", "stato": stato, **base}
        if tipo == "profilo":
            if not riga.get("sospeso_at"):
                return {"esito": "non_sospeso", **base}
            riga.update(sospeso_at=None, sospeso_motivo=None, sospeso_da=None)
            return {"esito": "annullato", **base}
        if not riga.get("nascosto_moderazione_at"):
            return {"esito": "non_sospeso", **base}
        riga.update(nascosto_moderazione_at=None, nascosto_da=None)
        return {"esito": "annullato", **base}

    @staticmethod
    def _motivazione(testo) -> str:
        testo = (testo or "").strip()
        if not 20 <= len(testo) <= 2000:
            raise errore("motivazione_non_valida")
        return testo

    # -- RPC della 0041: moderazione
    def _fn_partner_segnalazione_prendi(self, p):
        self._admin(p["p_admin"])
        s = self._seg(p["p_id"])
        if s["stato"] == "in_esame":
            return {"segnalazione": copy.deepcopy(s), "modificato": False}
        if s["stato"] != "ricevuta":
            raise errore("segnalazione_gia_decisa")
        s.update(stato="in_esame", autore_company_profile_id=s.get("autore_company_profile_id")
                 or self._autore_di(s["oggetto_tipo"], s["oggetto_id"]))
        self._audit(p["p_admin"], "moderazione.presa_in_carico", None, None,
                    {"segnalazione_id": s["id"]})
        return {"segnalazione": copy.deepcopy(s), "modificato": True}

    def _fn_partner_segnalazione_decidi(self, p):
        self._admin(p["p_admin"])
        decisione = p.get("p_decisione")
        if decisione not in _DECISIONI:
            raise errore("decisione_non_valida")
        motivazione = self._motivazione(p.get("p_motivazione"))
        sor = (p.get("p_sor_testo") or "").strip() or None
        if sor is not None and len(sor) > 10000:
            raise errore("parametri_non_validi")
        if decisione != "nessuna_azione" and sor is None:
            raise errore("statement_mancante")
        s = self._seg(p["p_id"])
        if s["stato"] not in ("ricevuta", "in_esame"):
            raise errore("segnalazione_gia_decisa")
        if decisione != "nessuna_azione" and decisione != _PER_OGGETTO[s["oggetto_tipo"]]:
            raise errore("decisione_non_valida")
        effetto = None
        if decisione != "nessuna_azione":
            effetto = self._applica(s["oggetto_tipo"], s["oggetto_id"], p["p_admin"],
                                    motivazione)
            if effetto["esito"] == "oggetto_non_trovato":
                raise errore("oggetto_non_trovato")
            if effetto["esito"] == "non_sospendibile":
                raise errore("oggetto_non_sospendibile")
        autore = (s.get("autore_company_profile_id") or (effetto or {}).get("autore_company_id")
                  or self._autore_di(s["oggetto_tipo"], s["oggetto_id"]))
        owner = self._owner(autore) if autore else None
        s.update(stato="decisa", decisione=decisione, motivazione=motivazione, sor_testo=sor,
                 deciso_da=p["p_admin"], deciso_at=_ora(), autore_company_profile_id=autore)
        self._audit(p["p_admin"], "moderazione.decisione", owner, owner,
                    {"segnalazione_id": s["id"], "decisione": decisione})
        return {"segnalazione": copy.deepcopy(s),
                "effetto": effetto["esito"] if effetto else None,
                "autore_company_id": autore, "autore_owner_id": owner}

    def _fn_partner_segnalazione_ricorso(self, p):
        testo = (p.get("p_testo") or "").strip()
        if not 20 <= len(testo) <= 2000:
            raise errore("ricorso_testo_non_valido")
        s = self._seg(p["p_id"])
        azienda = next(iter(self.righe("company_profiles", id=p.get("p_company"))), None)
        autore = bool(s.get("autore_company_profile_id")
                      and s["autore_company_profile_id"] == p.get("p_company")
                      and azienda and azienda["parent_id"] == p["p_user"])
        segnalante = s["segnalante_user_id"] == p["p_user"]
        if not (autore or segnalante):
            raise errore("segnalazione_non_trovata")
        if (s["stato"] != "decisa" or s.get("ricorso_testo") is not None
                or _ts(s["deciso_at"]) < _meno_mesi(datetime.now(timezone.utc), 6)):
            raise errore("ricorso_non_ammesso")
        if autore and s["decisione"] != "nessuna_azione":
            ruolo = "autore"
        elif segnalante and s["decisione"] == "nessuna_azione":
            ruolo = "segnalante"
        else:
            raise errore("ricorso_non_ammesso")
        s.update(stato="ricorso_presentato", ricorso_testo=testo, ricorso_da_user_id=p["p_user"],
                 ricorso_at=_ora())
        self._audit(p["p_user"], "moderazione.ricorso_presentato", p["p_user"], None,
                    {"segnalazione_id": s["id"], "ruolo": ruolo})
        return {"segnalazione": copy.deepcopy(s), "ruolo": ruolo}

    def _fn_partner_ricorso_decidi(self, p):
        self._admin(p["p_admin"])
        if p.get("p_esito") not in ("confermata", "riformata"):
            raise errore("parametri_non_validi")
        motivazione = self._motivazione(p.get("p_motivazione"))
        s = self._seg(p["p_id"])
        if s["stato"] != "ricorso_presentato":
            raise errore("ricorso_non_in_attesa")
        effetto = None
        if p["p_esito"] == "riformata":
            if s["decisione"] == "nessuna_azione":
                effetto = self._applica(s["oggetto_tipo"], s["oggetto_id"], p["p_admin"],
                                        motivazione)
            elif any(
                a["id"] != s["id"] and a["oggetto_tipo"] == s["oggetto_tipo"]
                and a["oggetto_id"] == s["oggetto_id"]
                and ((a.get("decisione") not in (None, "nessuna_azione")
                      and (a["stato"] in ("decisa", "ricorso_presentato")
                           or (a["stato"] == "ricorso_deciso"
                               and a["ricorso_esito"] == "confermata")))
                     or (a.get("decisione") == "nessuna_azione"
                         and a["stato"] == "ricorso_deciso" and a["ricorso_esito"] == "riformata"))
                for a in self.tabelle.get("partner_segnalazioni", [])
            ):
                effetto = {"esito": "mantenuto"}
            else:
                effetto = self._annulla(s["oggetto_tipo"], s["oggetto_id"])
        s.update(stato="ricorso_deciso", ricorso_esito=p["p_esito"],
                 ricorso_motivazione=motivazione, ricorso_deciso_da=p["p_admin"],
                 ricorso_deciso_at=_ora(),
                 autore_company_profile_id=s.get("autore_company_profile_id")
                 or (effetto or {}).get("autore_company_id"))
        owner = self._owner(s["autore_company_profile_id"])
        self._audit(p["p_admin"], "moderazione.ricorso_deciso", owner, owner,
                    {"segnalazione_id": s["id"], "esito": p["p_esito"]})
        return {"segnalazione": copy.deepcopy(s),
                "effetto": effetto["esito"] if effetto else None,
                "autore_company_id": s["autore_company_profile_id"], "autore_owner_id": owner}

    def _diretta(self, p, applica: bool):
        self._admin(p["p_admin"])
        if p.get("p_oggetto_tipo") not in ("call", "profilo", "messaggio") \
                or p.get("p_oggetto_id") is None:
            raise errore("parametri_non_validi")
        motivazione = self._motivazione(p.get("p_motivazione"))
        tipo, oid = p["p_oggetto_tipo"], p["p_oggetto_id"]
        effetto = self._applica(tipo, oid, p["p_admin"], motivazione) if applica \
            else self._annulla(tipo, oid)
        if effetto["esito"] == "oggetto_non_trovato":
            raise errore("oggetto_non_trovato")
        if effetto["esito"] == "non_sospendibile":
            raise errore("oggetto_non_sospendibile")
        cambiato = effetto["esito"] == ("applicato" if applica else "annullato")
        if cambiato:
            azione = {True: {"call": "admin.partner_call_sospesa",
                             "profilo": "moderazione.profilo_sospeso",
                             "messaggio": "moderazione.messaggio_oscurato"},
                      False: {"call": "admin.partner_call_ripristinata",
                              "profilo": "moderazione.profilo_ripristinato",
                              "messaggio": "moderazione.messaggio_ripristinato"}}[applica][tipo]
            self._audit(p["p_admin"], azione, effetto.get("autore_owner_id"),
                        effetto.get("autore_owner_id"),
                        {"oggetto_tipo": tipo, "oggetto_id": oid, "motivazione": motivazione})
        return {**effetto, "modificato": cambiato}

    def _fn_partner_admin_sospendi(self, p):
        return self._diretta(p, True)

    def _fn_partner_admin_ripristina(self, p):
        return self._diretta(p, False)

    # -- RPC della 0041: metriche e costi (gemelle della SQL)
    @staticmethod
    def _periodo(p) -> tuple[date, date, datetime, datetime]:
        da = date.fromisoformat(p["p_da"]) if p.get("p_da") else None
        a = date.fromisoformat(p["p_a"]) if p.get("p_a") else None
        if da is None or a is None or da > a or (a - da).days > 3660:
            raise errore("periodo_non_valido")
        return (da, a, datetime.combine(da, time(), ROMA),
                datetime.combine(a + timedelta(days=1), time(), ROMA))

    def _fn_admin_metriche_partenariati(self, p):
        da, a, inizio, fine = self._periodo(p)
        adesso = datetime.now(timezone.utc)
        calls = [c for c in self.tabelle.get("partner_calls", [])
                 if c.get("pubblicata_at") and inizio <= _ts(c["pubblicata_at"]) < fine]
        ids = {c["id"] for c in calls}
        cand = [k for k in self.tabelle.get("partner_candidature", [])
                if k["partner_call_id"] in ids]
        osservabili = [c for c in calls if _ts(c["pubblicata_at"]) <= adesso - timedelta(days=30)]
        con_cand = [c for c in osservabili if any(
            k["partner_call_id"] == c["id"] and k["tipo"] == "candidatura"
            and _ts(k["created_at"]) <= _ts(c["pubblicata_at"]) + timedelta(days=30)
            for k in cand)]
        ore = sorted(
            (min(_ts(k["created_at"]) for k in cand
                 if k["partner_call_id"] == c["id"] and k["tipo"] == "candidatura")
             - _ts(c["pubblicata_at"])).total_seconds() / 3600
            for c in calls if any(k["partner_call_id"] == c["id"] and k["tipo"] == "candidatura"
                                  for k in cand))
        mediana = None
        if ore:
            meta = len(ore) // 2
            mediana = ore[meta] if len(ore) % 2 else (ore[meta - 1] + ore[meta]) / 2
        coperture = [Decimal(str(c["copertura_gap_ratio"])) for c in calls
                     if c.get("copertura_gap_ratio") is not None]
        tassi = {}
        for tipo in ("candidatura", "invito"):
            acc = sum(1 for k in cand if k["tipo"] == tipo and k["stato"] == "accettata")
            rif = sum(1 for k in cand if k["tipo"] == tipo and k["stato"] == "rifiutata")
            tassi[tipo] = {"accettate": acc, "rifiutate": rif,
                           "tasso": _arrotonda(Decimal(acc) / (acc + rif), 3)
                           if acc + rif else None}
        n_cand = sum(1 for k in cand if k["tipo"] == "candidatura")
        return {
            "da": da.isoformat(), "a": a.isoformat(),
            "call_pubblicate": len(calls),
            "candidature": n_cand,
            "inviti": sum(1 for k in cand if k["tipo"] == "invito"),
            "candidature_per_call": _arrotonda(Decimal(n_cand) / len(calls), 2)
            if calls else None,
            "call_osservabili_30_giorni": len(osservabili),
            "call_con_candidatura_30_giorni": len(con_cand),
            "percentuale_call_con_candidatura_30_giorni":
                _arrotonda(Decimal(100 * len(con_cand)) / len(osservabili), 1)
                if osservabili else None,
            "accettazione": tassi,
            "ore_mediane_prima_candidatura": _arrotonda(Decimal(str(mediana)), 1)
            if mediana is not None else None,
            "copertura_media_gap": _arrotonda(sum(coperture) / len(coperture), 3)
            if coperture else None,
            "consorzi_validati": sum(1 for c in calls if c.get("validazione_esito")),
            "consorzi_validati_verde": sum(1 for c in calls
                                           if c.get("validazione_esito") == "verde"),
        }

    def _fn_admin_costi_partenariati(self, p):
        da, a, inizio, fine = self._periodo(p)
        voci: dict[tuple, dict] = {}
        for e in self.tabelle.get("api_usage_events", []):
            valuta = SERVIZI_COSTO.get((e["provider"], e["service"]))
            if valuta is None or not inizio <= _ts(e["created_at"]) < fine:
                continue
            chiave = (valuta, e["provider"], e["service"], e["outcome"])
            voce = voci.setdefault(chiave, {"provider": e["provider"], "service": e["service"],
                                            "outcome": e["outcome"], "valuta": valuta,
                                            "eventi": 0, "cost_cents": 0})
            voce["eventi"] += 1
            voce["cost_cents"] += e["cost_cents"]
        ordinate = [voci[k] for k in sorted(voci)]
        totali: dict[str, dict] = {}
        for voce in ordinate:
            t = totali.setdefault(voce["valuta"], {"valuta": voce["valuta"], "eventi": 0,
                                                   "cost_cents": 0})
            t["eventi"] += voce["eventi"]
            t["cost_cents"] += voce["cost_cents"]
        return {"da": da.isoformat(), "a": a.isoformat(), "voci": ordinate,
                "totali": [totali[v] for v in sorted(totali)]}

    # -- RPC della 0041: identità verificata dall'admin
    def _stato_identita(self, company) -> dict | None:
        return next(iter(self.righe("company_identita_stato", company_profile_id=company)), None)

    def _registro(self, company, owner, azione, **campi) -> None:
        self._identita_id += 1
        self.tabelle.setdefault("company_identita_verifiche", []).append({
            "id": self._identita_id, "company_profile_id": company, "family_parent_id": owner,
            "azione": azione, "metodo": None, "nota": None, "attore_user_id": None,
            "origine": "admin", "motivo": None, "created_at": _ora(), **campi})

    def richiedi_identita(self, nome: str, nota: str | None = None) -> None:
        """fn_identita_richiedi (per il seed dei test)."""
        company, owner = g.COMPANY[nome], g.OWNER[nome]
        stato = self._stato_identita(company)
        if stato is None:
            stato = {"company_profile_id": company}
            self.tabelle.setdefault("company_identita_stato", []).append(stato)
        stato.update(stato="richiesta", metodo=None, verificata_at=None, verificata_da=None,
                     richiesta_at=_ora(), aggiornato_at=_ora())
        self._registro(company, owner, "richiesta", nota=nota, attore_user_id=owner,
                       origine="utente")

    def _fn_identita_decidi(self, p):
        self._admin(p["p_admin"])
        nota = (p.get("p_nota") or "").strip() or None
        if p.get("p_esito") not in ("verificata", "rifiutata") or (nota and len(nota) > 500):
            raise errore("parametri_non_validi")
        metodo = None
        if p["p_esito"] == "verificata":
            if p.get("p_metodo") not in _METODI:
                raise errore("metodo_obbligatorio")
            metodo = p["p_metodo"]
        azienda = self._viva(p["p_company"])
        if azienda is None:
            raise errore("company_not_found")
        stato = self._stato_identita(p["p_company"])
        if stato is None or stato["stato"] != "richiesta":
            raise errore("identita_non_richiesta")
        if p["p_esito"] == "verificata" and not self._identita_ok(p["p_company"], False):
            raise errore("identita_non_verificata")
        verificata = p["p_esito"] == "verificata"
        stato.update(stato=p["p_esito"], metodo=metodo,
                     verificata_at=_ora() if verificata else None,
                     verificata_da=p["p_admin"] if verificata else None, aggiornato_at=_ora())
        self._registro(p["p_company"], azienda["parent_id"], p["p_esito"], metodo=metodo,
                       nota=nota, attore_user_id=p["p_admin"])
        self._audit(p["p_admin"], f"identita.{p['p_esito']}", azienda["parent_id"],
                    azienda["parent_id"], {"company_profile_id": p["p_company"]})
        return {"stato": copy.deepcopy(stato), "family_parent_id": azienda["parent_id"],
                "modificato": True}

    def _fn_identita_revoca(self, p):
        self._admin(p["p_admin"])
        motivo = (p.get("p_motivo") or "").strip()
        if not motivo or len(motivo) > 500:
            raise errore("motivo_obbligatorio")
        azienda = next(iter(self.righe("company_profiles", id=p["p_company"])), None)
        if azienda is None:
            raise errore("company_not_found")
        stato = self._stato_identita(p["p_company"])
        if stato is None or stato["stato"] != "verificata":
            return {"stato": copy.deepcopy(stato), "family_parent_id": azienda["parent_id"],
                    "modificato": False}
        stato.update(stato="non_richiesta", metodo=None, verificata_at=None,
                     verificata_da=None, aggiornato_at=_ora())
        self._registro(p["p_company"], azienda["parent_id"], "revocata", motivo=motivo,
                       attore_user_id=p["p_admin"])
        return {"stato": copy.deepcopy(stato), "family_parent_id": azienda["parent_id"],
                "modificato": True}

    # -- RPC della 0041: call da rivalidare
    def _fn_partner_call_validazioni_da_ricalcolare(self, p):
        limite = max(1, min(p.get("p_limite") or 100, 500))
        scelte = []
        for c in self.tabelle.get("partner_calls", []):
            if c["stato"] not in ("pubblicata", "scaduta", "chiusa_completata"):
                continue
            validata = c.get("validazione_at")
            if validata is None or any(_ts(m["updated_at"]) > _ts(validata)
                                       for m in self.membri(c["id"])):
                scelte.append(c)
        scelte.sort(key=lambda c: (c.get("validazione_at") is not None,
                                   str(c.get("validazione_at") or ""), c["id"]))
        return [c["id"] for c in scelte[:limite]]


async def scenario_wp9() -> tuple[FakePrimaryWP9, object]:
    """L'esempio guida sul primario del WP9, come `scenario_wp8` (membro di X
    con visibilità, collegamenti calcolati, backfill dei membri), più un
    admin attivo."""
    db = carica_guida(FakePrimaryWP9())
    db.tabelle["profiles"].append({"id": MEMBRO_X, "email": EMAIL_MEMBRO_X, "is_active": True})
    db.tabelle["profiles"].append({"id": ADMIN_ID, "email": "admin@example.test",
                                   "is_active": True, "role": "admin"})
    db.tabelle.setdefault("family_members", []).append({
        "id": FM_X, "parent_id": g.OWNER["X"], "member_id": MEMBRO_X, "status": "active",
        "denominazione": "Giulia del gruppo X"})
    db.tabelle.setdefault("family_member_company_access", []).append(
        {"family_member_id": FM_X, "company_profile_id": g.COMPANY["X"]})
    assert (await partenariato_collegamenti.backfill(db))["errori"] == 0
    assert db.backfill_membri() == 3
    db.tabelle.setdefault("partner_segnalazioni", [])
    db.ops.clear()
    db.rpcs.clear()
    return db, secondario_guida()


@pytest.fixture(name="posta")
def fixture_posta(monkeypatch, fondo):
    """Le email della moderazione in background raccolte ed eseguite dal
    test (oltre a quelle di candidature e chat del `fondo`)."""
    coda: list = []
    monkeypatch.setattr(mod, "_spawn", coda.append)

    async def rate(*_a, **_k):
        return True

    monkeypatch.setattr(pcs.rate_limit_service, "allow", rate)

    class Posta:
        email = fondo.email

        async def esegui(self):
            await fondo.esegui()
            while coda:
                await coda.pop(0)

        async def azzera(self):
            await self.esegui()
            self.email.clear()

    yield Posta()
    for coro in coda:
        coro.close()


@pytest.fixture(name="indice")
def fixture_indice(monkeypatch):
    """Conta le invalidazioni dell'indice del matching."""
    chiamate: list[int] = []
    monkeypatch.setattr(partenariato_indice, "invalida", lambda: chiamate.append(1))
    return chiamate


def segnala_in(tipo: str, oggetto_id: str, motivo: str = "contatti_nel_testo") -> SegnalazioneIn:
    return SegnalazioneIn(oggetto_tipo=tipo, oggetto_id=oggetto_id, motivo=motivo,
                          descrizione=DESCRIZIONE, buona_fede=True)


async def segnala_call(db, sec, nome: str = "Y") -> str:
    """`nome` segnala la call della guida (di X): → id della segnalazione."""
    out = await pcs.segnala(db, sec, attiva(nome), utente(nome),
                            segnala_in("call", g.CALL_GUIDA_ID))
    return str(out.id)


def segnalazione(db, tipo: str, oggetto_id: str, segnalante: str = "Y", **campi) -> str:
    """Una segnalazione scritta direttamente (come dopo `segnala`)."""
    riga = db.inserisci("partner_segnalazioni", {
        "id": str(uuid.uuid4()), "oggetto_tipo": tipo, "oggetto_id": oggetto_id,
        "segnalante_user_id": g.OWNER[segnalante],
        "segnalante_company_id": g.COMPANY[segnalante], "motivo": "contenuto_illecito",
        "descrizione": DESCRIZIONE, "buona_fede": True,
        "contenuto_snapshot": {"testo": "contenuto segnalato"}, **campi})
    return riga["id"]


def decisione(valore: str, motivazione: str = MOTIVAZIONE) -> DecisioneIn:
    return DecisioneIn(decisione=valore, motivazione=motivazione)


def notifiche(db, user_id: str, tipo: str | None = None) -> list[dict]:
    return [n for n in db.tabelle.get("notifications", [])
            if n["user_id"] == str(user_id) and (tipo is None or n["tipo"] == tipo)]


def canary(testo: str, *nomi: str) -> None:
    """Nessun identificativo delle aziende `nomi` (né dei loro titolari)."""
    for nome in nomi:
        for valore in (g.COMPANY[nome], g.OWNER[nome], PIVA[nome], RAGIONE[nome],
                       RAGIONE[nome].upper(), g.CODICE_PUBBLICO[nome], EMAIL[nome]):
            assert valore not in testo, (nome, valore)


def call(db, call_id: str = g.CALL_GUIDA_ID) -> dict:
    return db.una("partner_calls", id=call_id)


async def decisa_call(db, sec, posta, valore: str = "call_sospesa") -> str:
    """Y segnala la call di X e l'admin decide `valore` (email smaltite)."""
    sid = await segnala_call(db, sec)
    await mod.decidi(db, ADMIN, sid, decisione(valore))
    await posta.esegui()
    return sid


# ------------------------------------------------------------ utente


class TestVisibilita:
    async def test_prima_della_decisione_solo_chi_ha_segnalato(self, posta):
        db, sec = await scenario_wp9()
        sid = await segnala_call(db, sec)
        out = await mod.dettaglio(db, attiva("Y"), utente("Y"), sid)
        assert (out.ruolo, out.stato, out.descrizione) == ("segnalante", "ricevuta", DESCRIZIONE)
        assert out.decisione is None and out.ricorso_possibile is False
        # l'autore (anche con l'azienda attiva giusta) e gli estranei: 404
        for nome in ("X", "O"):
            with pytest.raises(NotFoundError):
                await mod.dettaglio(db, attiva(nome), utente(nome), sid)
        for malformato in ("non-un-uuid", str(uuid.uuid4())):
            with pytest.raises(NotFoundError):
                await mod.dettaglio(db, attiva("Y"), utente("Y"), malformato)

    async def test_nessuna_azione_l_autore_non_sa_nulla(self, posta):
        db, sec = await scenario_wp9()
        sid = await decisa_call(db, sec, posta, "nessuna_azione")
        riga = db.una("partner_segnalazioni", id=sid)
        assert (riga["decisione"], riga["sor_testo"]) == ("nessuna_azione", None)
        assert call(db)["stato"] == "pubblicata"
        assert notifiche(db, g.OWNER["X"], testi.TIPO_DECISIONE) == []
        assert posta.email == []
        with pytest.raises(NotFoundError):
            await mod.dettaglio(db, attiva("X"), utente("X"), sid)
        [esito] = notifiche(db, g.OWNER["Y"], testi.TIPO_DECISIONE)
        assert "Non abbiamo preso provvedimenti" in esito["corpo"]
        out = await mod.dettaglio(db, attiva("Y"), utente("Y"), sid)
        # contro «nessuna azione» ricorre chi ha segnalato
        assert out.ricorso_possibile is True and out.ricorso_entro is not None


class TestDecisione:
    async def test_call_sospesa_statement_e_notifiche_senza_segnalante(self, posta, indice):
        db, sec = await scenario_wp9()
        sid = await segnala_call(db, sec)
        presa = await mod.prendi_in_carico(db, ADMIN, sid)
        assert presa.stato == "in_esame"
        assert presa.autore.company_profile_id == uuid.UUID(g.COMPANY["X"])
        out = await mod.decidi(db, ADMIN, sid, decisione("call_sospesa"))
        assert (out.stato, out.decisione, out.effetto) == ("decisa", "call_sospesa", "applicato")
        c = call(db)
        assert (c["stato"], c["stato_prima_sospensione"]) == ("sospesa_moderazione",
                                                              "pubblicata")
        assert indice == [1]
        # statement of reasons dal template, salvato nella segnalazione
        sor = db.una("partner_segnalazioni", id=sid)["sor_testo"]
        assert sor.startswith(testi.INTESTAZIONE_BOZZA)
        assert "BOZZA — DA RIVEDERE CON IL LEGALE" in sor
        assert MOTIVAZIONE in sor and "categoria: contatti nel testo" in sor
        assert "entro il" in sor and "art. 21" in sor and "Termini d'uso" in sor
        assert "controlli automatici" in sor  # motivo contatti_nel_testo
        assert out.sor_testo == sor
        # autore: titolare e membro in-app, email solo al titolare
        for user in (g.OWNER["X"], MEMBRO_X):
            [n] = notifiche(db, user, testi.TIPO_DECISIONE)
            assert n["url"] == f"/app/partenariati/segnalazioni/{sid}?azienda={g.COMPANY['X']}"
            assert n["company_profile_id"] == g.COMPANY["X"]
        await posta.esegui()
        [email] = posta.email
        assert email["to"] == EMAIL["X"]
        assert email["subject"] == "Decisione di moderazione su un tuo contenuto — BandoFit"
        assert MOTIVAZIONE in email["text"] and testi.INTESTAZIONE_BOZZA in email["text"]
        assert "List-Unsubscribe" not in email["headers"]
        # chi ha segnalato riceve l'esito, non l'autore
        [esito] = notifiche(db, g.OWNER["Y"], testi.TIPO_DECISIONE)
        assert esito["url"] == f"/app/partenariati/segnalazioni/{sid}"
        # canary: nulla di Y verso X, nulla di X verso Y
        vista_x = await mod.dettaglio(db, attiva("X"), utente("X"), sid)
        verso_x = repr(notifiche(db, g.OWNER["X"])) + repr(notifiche(db, MEMBRO_X)) \
            + email["html"] + email["text"] + vista_x.model_dump_json()
        canary(verso_x, "Y")
        assert DESCRIZIONE not in verso_x
        vista_y = await mod.dettaglio(db, attiva("Y"), utente("Y"), sid)
        canary(repr(notifiche(db, g.OWNER["Y"])) + vista_y.model_dump_json(), "X")
        # viste per ruolo
        assert (vista_x.ruolo, vista_x.sor_testo, vista_x.descrizione) == ("autore", sor, None)
        assert vista_x.ricorso_possibile is True and vista_x.editable is True
        assert vista_y.sor_testo is None and vista_y.ricorso_possibile is False
        membro = await mod.dettaglio(db, attiva("X", editable=False),
                                     {"id": MEMBRO_X, "role": "cliente"}, sid)
        assert (membro.ruolo, membro.ricorso_possibile, membro.editable) == (
            "autore", False, False)

    async def test_coerenza_oggetto_decisione_prima_della_rpc(self, posta):
        db, sec = await scenario_wp9()
        sid = await segnala_call(db, sec)
        for valore in ("profilo_sospeso", "contenuto_rimosso"):
            with pytest.raises(AppError) as exc:
                await mod.decidi(db, ADMIN, sid, decisione(valore))
            assert (exc.value.status_code, exc.value.code) == (400, "decisione_non_valida")
        assert db.chiamate("fn_partner_segnalazione_decidi") == []
        assert call(db)["stato"] == "pubblicata"
        assert db.una("partner_segnalazioni", id=sid)["stato"] == "ricevuta"

    async def test_doppia_decisione(self, posta):
        db, sec = await scenario_wp9()
        sid = await decisa_call(db, sec, posta)
        with pytest.raises(AppError) as exc:
            await mod.decidi(db, ADMIN, sid, decisione("nessuna_azione"))
        assert (exc.value.status_code, exc.value.code) == (409, "segnalazione_gia_decisa")
        with pytest.raises(AppError) as exc:
            await mod.prendi_in_carico(db, ADMIN, sid)
        assert exc.value.code == "segnalazione_gia_decisa"

    async def test_rifiuto_della_rpc_nessun_effetto_ne_notifica(self, posta, indice):
        db, sec = await scenario_wp9()
        sid = await segnala_call(db, sec)
        call(db)["stato"] = "chiusa_completata"
        with pytest.raises(AppError) as exc:
            await mod.decidi(db, ADMIN, sid, decisione("call_sospesa"))
        assert (exc.value.status_code, exc.value.code) == (409, "oggetto_non_sospendibile")
        assert db.una("partner_segnalazioni", id=sid)["stato"] == "ricevuta"
        assert call(db)["stato"] == "chiusa_completata" and indice == []
        assert [n for n in db.tabelle.get("notifications", [])
                if n["tipo"].startswith("moderazione.")] == []
        await posta.esegui()
        assert posta.email == []

    async def test_non_admin_403_dalla_rpc(self, posta):
        db, sec = await scenario_wp9()
        sid = await segnala_call(db, sec)
        with pytest.raises(AppError) as exc:
            await mod.decidi(db, utente("O"), sid, decisione("nessuna_azione"))
        assert (exc.value.status_code, exc.value.code) == (403, "forbidden")

    async def test_messaggio_oscurato_e_statement_al_mittente(self, posta):
        db, sec = await scenario_wp9()
        await accetta(db, sec)
        await posta.azzera()
        [conv] = db.tabelle["partner_conversazioni"]
        riga = messaggio(db, conv, "Y", "Chiamami al 333 1234567")
        sid = segnalazione(db, "messaggio", str(riga["id"]), segnalante="X")
        await mod.decidi(db, ADMIN, sid, decisione("contenuto_rimosso"))
        assert riga["nascosto_moderazione_at"] is not None and riga["nascosto_da"] == ADMIN_ID
        [n] = notifiche(db, g.OWNER["Y"], testi.TIPO_DECISIONE)
        assert n["titolo"] == "Un tuo messaggio è stato oscurato"
        await posta.esegui()
        assert [e["to"] for e in posta.email] == [EMAIL["Y"]]
        canary(posta.email[0]["text"] + repr(n), "X")

    async def test_profilo_sospeso(self, posta, indice):
        db, sec = await scenario_wp9()
        sid = segnalazione(db, "profilo", g.CODICE_PUBBLICO["Y"], segnalante="X")
        out = await mod.decidi(db, ADMIN, sid, decisione("profilo_sospeso"))
        profilo = db.una("company_partner_profiles", company_profile_id=g.COMPANY["Y"])
        assert profilo["sospeso_at"] is not None and out.effetto == "applicato"
        assert indice == [1]
        assert out.autore.company_profile_id == uuid.UUID(g.COMPANY["Y"])

    async def test_anteprima_uguale_al_testo_inviato(self, posta):
        db, sec = await scenario_wp9()
        sid = await segnala_call(db, sec)
        db.rpcs.clear()
        scritture = len([o for o in db.ops if o["op"] != "select"])
        anteprima = await mod.anteprima_statement(db, sid, decisione("call_sospesa"))
        assert anteprima.versione == testi.SOR_VERSIONE
        assert (await mod.anteprima_statement(db, sid, decisione("nessuna_azione"))).testo is None
        # l'anteprima non scrive nulla e non chiama RPC
        assert db.rpcs == [] and len([o for o in db.ops if o["op"] != "select"]) == scritture
        with pytest.raises(AppError) as exc:
            await mod.anteprima_statement(db, sid, decisione("profilo_sospeso"))
        assert exc.value.code == "decisione_non_valida"
        out = await mod.decidi(db, ADMIN, sid, decisione("call_sospesa"))
        assert out.sor_testo == anteprima.testo
        assert db.chiamate("fn_partner_segnalazione_decidi")[0]["p_sor_testo"] == anteprima.testo

    async def test_email_solo_a_indirizzi_recapitabili(self, posta):
        db, sec = await scenario_wp9()
        db.email_non_verificate.add(g.OWNER["X"])
        await decisa_call(db, sec, posta)
        assert posta.email == []
        assert len(notifiche(db, g.OWNER["X"], testi.TIPO_DECISIONE)) == 1

    async def test_seconda_segnalazione_sulla_stessa_call(self, posta):
        """La call è già sospesa: la decisione vale (effetto gia_applicato) e
        l'autore riceve comunque la sua motivazione."""
        db, sec = await scenario_wp9()
        await decisa_call(db, sec, posta)
        altra = segnalazione(db, "call", g.CALL_GUIDA_ID, segnalante="Z")
        out = await mod.decidi(db, ADMIN, altra, decisione("call_sospesa"))
        assert out.effetto == "gia_applicato"
        assert len(notifiche(db, g.OWNER["X"], testi.TIPO_DECISIONE)) == 2


def messaggio(db, conv: dict, nome: str, testo: str, **campi) -> dict:
    db._msg += 1
    riga = {"id": db._msg, "conversazione_id": conv["id"],
            "mittente_company_profile_id": g.COMPANY[nome], "mittente_user_id": g.OWNER[nome],
            "testo": testo, "client_msg_id": str(uuid.uuid4()), "nascosto_moderazione_at": None,
            "nascosto_da": None, "created_at": _ora(), **campi}
    db.tabelle.setdefault("partner_messaggi", []).append(riga)
    return riga


# ------------------------------------------------------------ ricorso


class TestRicorso:
    async def test_autore_ricorre_una_volta_riformata_ripristina(self, posta, indice):
        db, sec = await scenario_wp9()
        sid = await decisa_call(db, sec, posta)
        indice.clear()
        # chi ha segnalato non ricorre contro una restrizione
        with pytest.raises(AppError) as exc:
            await mod.ricorso(db, attiva("Y"), utente("Y"), sid, RicorsoIn(testo=TESTO_RICORSO))
        assert (exc.value.status_code, exc.value.code) == (409, "ricorso_non_ammesso")
        # un membro dell'azienda autrice legge ma non ricorre
        with pytest.raises(ForbiddenError):
            await mod.ricorso(db, attiva("X", editable=False), {"id": MEMBRO_X}, sid,
                              RicorsoIn(testo=TESTO_RICORSO))
        out = await mod.ricorso(db, attiva("X"), utente("X"), sid, RicorsoIn(testo=TESTO_RICORSO))
        assert (out.stato, out.ricorso.da, out.ricorso.testo) == (
            "ricorso_presentato", "autore", TESTO_RICORSO)
        assert out.ricorso_possibile is False
        [ricevuto] = notifiche(db, g.OWNER["X"], testi.TIPO_RICORSO_RICEVUTO)
        assert ricevuto["company_profile_id"] == g.COMPANY["X"]
        with pytest.raises(AppError) as exc:
            await mod.ricorso(db, attiva("X"), utente("X"), sid, RicorsoIn(testo=TESTO_RICORSO))
        assert exc.value.code == "ricorso_non_ammesso"
        # chi ha segnalato vede che c'è un ricorso, non il testo
        vista_y = await mod.dettaglio(db, attiva("Y"), utente("Y"), sid)
        assert vista_y.ricorso.da == "autore" and vista_y.ricorso.testo is None
        decisa = await mod.decidi_ricorso(
            db, ADMIN, sid, RicorsoDecisioneIn(esito="riformata", motivazione=MOTIVAZIONE_RICORSO))
        assert (decisa.stato, decisa.effetto, decisa.ricorso.esito) == (
            "ricorso_deciso", "annullato", "riformata")
        c = call(db)
        assert (c["stato"], c["stato_prima_sospensione"], c["sospesa_at"]) == (
            "pubblicata", None, None)
        assert indice == [1]
        [esito] = notifiche(db, g.OWNER["X"], testi.TIPO_RICORSO_DECISO)
        assert esito["titolo"] == "Il tuo ricorso è stato accolto"
        assert "resta comunque sospeso" not in esito["corpo"]
        with pytest.raises(AppError) as exc:
            await mod.decidi_ricorso(db, ADMIN, sid, RicorsoDecisioneIn(
                esito="confermata", motivazione=MOTIVAZIONE_RICORSO))
        assert exc.value.code == "ricorso_non_in_attesa"

    async def test_confermata_nulla_cambia(self, posta, indice):
        db, sec = await scenario_wp9()
        sid = await decisa_call(db, sec, posta)
        await mod.ricorso(db, attiva("X"), utente("X"), sid, RicorsoIn(testo=TESTO_RICORSO))
        indice.clear()
        out = await mod.decidi_ricorso(db, ADMIN, sid, RicorsoDecisioneIn(
            esito="confermata", motivazione=MOTIVAZIONE_RICORSO))
        assert out.effetto is None and call(db)["stato"] == "sospesa_moderazione"
        assert indice == []
        [esito] = notifiche(db, g.OWNER["X"], testi.TIPO_RICORSO_DECISO)
        assert esito["titolo"] == "Il tuo ricorso non è stato accolto"

    async def test_segnalante_contro_nessuna_azione_riformata_applica(self, posta):
        db, sec = await scenario_wp9()
        sid = await decisa_call(db, sec, posta, "nessuna_azione")
        with pytest.raises(NotFoundError):  # l'autore non sa della segnalazione
            await mod.ricorso(db, attiva("X"), utente("X"), sid, RicorsoIn(testo=TESTO_RICORSO))
        out = await mod.ricorso(db, attiva("Y"), utente("Y"), sid, RicorsoIn(testo=TESTO_RICORSO))
        assert out.ricorso.da == "segnalante"
        decisa = await mod.decidi_ricorso(db, ADMIN, sid, RicorsoDecisioneIn(
            esito="riformata", motivazione=MOTIVAZIONE_RICORSO))
        assert decisa.effetto == "applicato" and call(db)["stato"] == "sospesa_moderazione"
        [esito] = notifiche(db, g.OWNER["Y"], testi.TIPO_RICORSO_DECISO)
        assert esito["titolo"] == "Il tuo ricorso è stato accolto"
        # all'autore la restrizione con lo statement «dal ricorso»
        [n] = notifiche(db, g.OWNER["X"], testi.TIPO_DECISIONE)
        await posta.esegui()
        [email] = posta.email
        assert email["to"] == EMAIL["X"] and MOTIVAZIONE_RICORSO in email["text"]
        assert "Puoi chiedere un riesame scrivendo a" in email["text"]
        [audit] = db.righe("audit_log", action=mod.AUDIT_STATEMENT)
        assert audit["payload"]["origine"] == "ricorso"
        vista_x = await mod.dettaglio(db, attiva("X"), utente("X"), sid)
        assert vista_x.ruolo == "autore" and vista_x.sor_testo == audit["payload"]["testo"]
        assert vista_x.sor_testo in email["text"]
        assert vista_x.ricorso.testo is None and vista_x.ricorso_possibile is False
        canary(email["text"] + repr(n) + vista_x.model_dump_json(), "Y")
        assert TESTO_RICORSO not in vista_x.model_dump_json()
        # La decisione EFFETTIVA è la sospensione: l'autore non legge «non
        # abbiamo preso provvedimenti» (decisione resta nessuna_azione, con
        # la sua motivazione, per la storia della segnalazione)…
        assert (vista_x.decisione, vista_x.decisione_effettiva) == (
            "nessuna_azione", "call_sospesa")
        vista_y = await mod.dettaglio(db, attiva("Y"), utente("Y"), sid)
        assert vista_y.decisione_effettiva == "call_sospesa" and vista_y.sor_testo is None
        # …e l'admin ritrova lo statement che l'autore ha ricevuto.
        admin = await mod.dettaglio_admin(db, sid)
        assert admin.decisione_effettiva == "call_sospesa"
        assert admin.sor_testo == audit["payload"]["testo"]

    async def test_ricorso_oltre_sei_mesi(self, posta):
        db, sec = await scenario_wp9()
        sid = await decisa_call(db, sec, posta)
        riga = db.una("partner_segnalazioni", id=sid)
        riga["deciso_at"] = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        vista = await mod.dettaglio(db, attiva("X"), utente("X"), sid)
        assert vista.ricorso_possibile is False
        assert vista.ricorso_entro < datetime.now(timezone.utc)
        with pytest.raises(AppError) as exc:
            await mod.ricorso(db, attiva("X"), utente("X"), sid, RicorsoIn(testo=TESTO_RICORSO))
        assert exc.value.code == "ricorso_non_ammesso"
        riga["deciso_at"] = (datetime.now(timezone.utc) - timedelta(days=170)).isoformat()
        assert (await mod.dettaglio(db, attiva("X"), utente("X"), sid)).ricorso_possibile

    async def test_testo_del_ricorso(self):
        for testo in ("breve", "x" * 2001, "   " + "a" * 19 + "   "):
            with pytest.raises(AppError) as exc:
                RicorsoIn(testo=testo)
            assert (exc.value.status_code, exc.value.code) == (400, "ricorso_testo_non_valido")
        assert RicorsoIn(testo="  " + "a" * 20 + "  ").testo == "a" * 20

    async def test_riformata_mantenuta_da_un_altra_decisione(self, posta):
        db, sec = await scenario_wp9()
        sid = await decisa_call(db, sec, posta)
        altra = segnalazione(db, "call", g.CALL_GUIDA_ID, segnalante="Z")
        await mod.decidi(db, ADMIN, altra, decisione("call_sospesa"))
        await mod.ricorso(db, attiva("X"), utente("X"), sid, RicorsoIn(testo=TESTO_RICORSO))
        out = await mod.decidi_ricorso(db, ADMIN, sid, RicorsoDecisioneIn(
            esito="riformata", motivazione=MOTIVAZIONE_RICORSO))
        assert out.effetto == "mantenuto" and call(db)["stato"] == "sospesa_moderazione"
        assert out.decisione_effettiva == "nessuna_azione"
        # l'autore sa che il ricorso è accolto ma il contenuto resta sospeso
        [esito] = notifiche(db, g.OWNER["X"], testi.TIPO_RICORSO_DECISO)
        assert esito["titolo"] == "Il tuo ricorso è stato accolto"
        assert "resta comunque sospeso" in esito["corpo"]


# ------------------------------------------------ sospensione e ripristino


class TestSospensioneDiretta:
    async def test_sospendi_e_ripristina_la_call(self, posta, indice):
        db, sec = await scenario_wp9()
        dati = SospensioneIn(motivazione=MOTIVAZIONE)
        out = await mod.sospendi(db, ADMIN, "call", g.CALL_GUIDA_ID.upper(), dati)
        assert (out.esito, out.stato, out.modificato, out.oggetto_id) == (
            "applicato", "sospesa_moderazione", True, g.CALL_GUIDA_ID)
        assert call(db)["stato"] == "sospesa_moderazione" and indice == [1]
        [n] = notifiche(db, g.OWNER["X"], testi.TIPO_DECISIONE)
        assert n["url"] == f"/app/partenariati/call/{g.CALL_GUIDA_ID}?azienda={g.COMPANY['X']}"
        # WP9: nessuna pagina della segnalazione per una decisione d'ufficio:
        # lo statement arriva in-app nella forma breve, con la motivazione.
        assert MOTIVAZIONE in n["corpo"] and "d'ufficio" in n["corpo"]
        await posta.esegui()
        [email] = posta.email
        assert "d'ufficio" in email["text"] and "riesame" in email["text"]
        [audit] = db.righe("audit_log", action=mod.AUDIT_STATEMENT)
        assert audit["payload"]["origine"] == "ufficio"
        assert audit["payload"]["testo"] in email["text"]
        # già sospesa: nessuna scrittura né notifica
        di_nuovo = await mod.sospendi(db, ADMIN, "call", g.CALL_GUIDA_ID, dati)
        assert (di_nuovo.esito, di_nuovo.modificato) == ("gia_applicato", False)
        assert len(notifiche(db, g.OWNER["X"], testi.TIPO_DECISIONE)) == 1
        ripristino = await mod.ripristina(db, ADMIN, "call", g.CALL_GUIDA_ID, dati)
        assert (ripristino.esito, ripristino.stato, ripristino.modificato) == (
            "annullato", "pubblicata", True)
        [r] = notifiche(db, g.OWNER["X"], testi.TIPO_RIPRISTINO)
        assert r["titolo"] == "La tua call di partenariato non è più sospesa"
        assert (await mod.ripristina(db, ADMIN, "call", g.CALL_GUIDA_ID, dati)).modificato \
            is False

    async def test_ripristino_di_una_call_scaduta_nel_frattempo(self, posta):
        db, sec = await scenario_wp9()
        dati = SospensioneIn(motivazione=MOTIVAZIONE)
        await mod.sospendi(db, ADMIN, "call", g.CALL_GUIDA_ID, dati)
        call(db)["scadenza_call"] = "2020-01-31"
        out = await mod.ripristina(db, ADMIN, "call", g.CALL_GUIDA_ID, dati)
        assert out.stato == "scaduta"
        assert (call(db)["stato"], call(db)["motivo_chiusura"]) == ("scaduta", "scadenza_call")

    async def test_profilo_e_messaggio(self, posta):
        db, sec = await scenario_wp9()
        dati = SospensioneIn(motivazione=MOTIVAZIONE)
        await accetta(db, sec)
        out = await mod.sospendi(db, ADMIN, "profilo", g.CODICE_PUBBLICO["Y"], dati)
        assert out.modificato
        assert db.una("company_partner_profiles",
                      company_profile_id=g.COMPANY["Y"])["sospeso_at"] is not None
        [n] = notifiche(db, g.OWNER["Y"], testi.TIPO_DECISIONE)
        assert n["url"] == f"/app/azienda?azienda={g.COMPANY['Y']}#partner"
        [conv] = db.tabelle["partner_conversazioni"]
        riga = messaggio(db, conv, "X", "Testo da oscurare")
        await mod.sospendi(db, ADMIN, "messaggio", str(riga["id"]), dati)
        assert riga["nascosto_moderazione_at"] is not None
        [n] = notifiche(db, g.OWNER["X"], testi.TIPO_DECISIONE)
        assert n["url"] == f"/app/partenariati/conversazioni/{conv['id']}?azienda={g.COMPANY['X']}"

    async def test_riferimenti_malformati_o_inesistenti(self, posta):
        db, sec = await scenario_wp9()
        dati = SospensioneIn(motivazione=MOTIVAZIONE)
        for tipo, valore in (("messaggio", "abc"), ("messaggio", "0"), ("call", "x"),
                             ("profilo", "12")):
            with pytest.raises(NotFoundError):
                await mod.sospendi(db, ADMIN, tipo, valore, dati)
        assert db.chiamate("fn_partner_admin_sospendi") == []
        with pytest.raises(AppError) as exc:
            await mod.sospendi(db, ADMIN, "call", str(uuid.uuid4()), dati)
        assert (exc.value.status_code, exc.value.code) == (404, "not_found")

    async def test_call_non_sospendibile(self, posta):
        db, sec = await scenario_wp9()
        call(db)["stato"] = "scaduta"
        with pytest.raises(AppError) as exc:
            await mod.sospendi(db, ADMIN, "call", g.CALL_GUIDA_ID,
                               SospensioneIn(motivazione=MOTIVAZIONE))
        assert exc.value.code == "oggetto_non_sospendibile"

    async def test_motivazione_obbligatoria(self):
        for testo in ("corta", "", None, "x" * 2001):
            with pytest.raises(AppError) as exc:
                SospensioneIn(motivazione=testo)
            assert (exc.value.status_code, exc.value.code) == (400, "motivazione_non_valida")
        # La sospensione d'ufficio: al massimo 500 caratteri (sta per intero
        # nella notifica e sull'oggetto).
        assert len(SospendiIn(motivazione="m" * 500).motivazione) == 500
        for testo in ("corta", "x" * 501):
            with pytest.raises(AppError) as exc:
                SospendiIn(motivazione=testo)
            assert (exc.value.status_code, exc.value.code) == (400, "motivazione_non_valida")

    async def test_statement_d_ufficio_leggibile_in_app(self, posta):
        """Titolare con l'email non recapitabile: lo statement of reasons
        della sospensione d'ufficio gli arriva comunque in-app, completo di
        motivazione (per intero), fondamento, mezzi automatizzati, riesame
        con il termine e vie esterne, entro i 1000 caratteri della notifica."""
        db, sec = await scenario_wp9()
        await accetta(db, sec)
        db.email_non_verificate.add(g.OWNER["Y"])
        motivazione = ("Il profilo riporta recapiti diretti nella descrizione. " * 10)[:499] + "."
        out = await mod.sospendi(db, ADMIN, "profilo", g.CODICE_PUBBLICO["Y"],
                                 SospendiIn(motivazione=motivazione))
        assert out.modificato
        await posta.esegui()
        assert [e for e in posta.email if e["to"] == EMAIL["Y"]] == []
        notifiche_y = notifiche(db, g.OWNER["Y"], testi.TIPO_DECISIONE)
        assert notifiche_y
        for n in notifiche_y:
            corpo = n["corpo"]
            assert len(corpo) <= 1000
            assert motivazione.strip() in corpo
            scadenza = testi.data_it(testi.scadenza_ricorso(testi.adesso()))
            for frammento in ("BOZZA", "d'ufficio", "senza mezzi automatizzati",
                              "Termini d'uso", f"entro il {scadenza}", "art. 21 DSA"):
                assert frammento in corpo, frammento
            canary(corpo, "X")


# ------------------------------------------------------------ contesto


async def conversazione_lunga(db, sec, n: int = 30) -> tuple[dict, list[dict]]:
    await accetta(db, sec)
    [conv] = db.tabelle["partner_conversazioni"]
    righe = [messaggio(db, conv, "Y" if i % 2 else "X", f"Messaggio numero {i}")
             for i in range(1, n + 1)]
    return conv, righe


class TestContesto:
    async def test_finestra_di_dieci_con_audit_prima_dei_dati(self, posta):
        db, sec = await scenario_wp9()
        conv, righe = await conversazione_lunga(db, sec)
        segnalato = righe[14]  # di Y
        sid = segnalazione(db, "messaggio", str(segnalato["id"]), segnalante="X")
        segnalato["nascosto_moderazione_at"] = _ora()
        segnalato["nascosto_da"] = ADMIN_ID
        db.ops.clear()
        out = await mod.contesto(db, ADMIN, sid)
        ids = [m.id for m in out.messaggi]
        assert ids == [r["id"] for r in righe[4:25]]
        assert out.completo is False and out.altri_prima and out.altri_dopo
        centro = next(m for m in out.messaggi if m.segnalato)
        # l'admin legge anche un messaggio oscurato
        assert (centro.id, centro.lato, centro.oscurato, centro.testo) == (
            segnalato["id"], "autore", True, "Messaggio numero 15")
        assert {m.lato for m in out.messaggi} == {"autore", "altra"}
        canary(out.model_dump_json(), "X", "Y")
        [audit] = db.righe("audit_log", action=mod.AUDIT_CONTESTO)
        assert audit["actor_id"] == ADMIN_ID and audit["payload"]["segnalazione_id"] == sid
        primo_audit = next(i for i, o in enumerate(db.ops)
                           if o["tabella"] == "audit_log" and o["op"] == "insert")
        letture = [i for i, o in enumerate(db.ops) if o["tabella"] == "partner_messaggi"
                   and "testo" in o["select"]]
        assert letture and min(letture) > primo_audit

    async def test_inizio_e_fine_della_conversazione(self, posta):
        db, sec = await scenario_wp9()
        conv, righe = await conversazione_lunga(db, sec, n=5)
        sid = segnalazione(db, "messaggio", str(righe[1]["id"]), segnalante="X")
        out = await mod.contesto(db, ADMIN, sid)
        assert [m.id for m in out.messaggi] == [r["id"] for r in righe]
        assert (out.altri_prima, out.altri_dopo) == (False, False)

    async def test_conversazione_intera_solo_con_motivazione(self, posta):
        db, sec = await scenario_wp9()
        conv, righe = await conversazione_lunga(db, sec)
        sid = segnalazione(db, "messaggio", str(righe[14]["id"]), segnalante="X")
        for motivazione in (None, "breve"):
            with pytest.raises(AppError) as exc:
                await mod.contesto(db, ADMIN, sid, completo=True, motivazione=motivazione)
            assert (exc.value.status_code, exc.value.code) == (400, "motivazione_non_valida")
        assert db.righe("audit_log", action=mod.AUDIT_CONTESTO_COMPLETO) == []
        motivo = "Serve l'intera conversazione per valutare il ricorso dell'autore."
        out = await mod.contesto(db, ADMIN, sid, completo=True, motivazione=motivo)
        assert out.completo is True and len(out.messaggi) == 30 and out.troncato is False
        [audit] = db.righe("audit_log", action=mod.AUDIT_CONTESTO_COMPLETO)
        assert audit["payload"]["motivazione"] == motivo

    async def test_conversazione_intera_a_pagine_con_tetto(self, posta, monkeypatch):
        monkeypatch.setattr(mod, "_PAGINA", 4)
        monkeypatch.setattr(mod, "CONTESTO_COMPLETO_MAX", 10)
        db, sec = await scenario_wp9()
        conv, righe = await conversazione_lunga(db, sec, n=12)
        sid = segnalazione(db, "messaggio", str(righe[0]["id"]), segnalante="Y")
        out = await mod.contesto(db, ADMIN, sid, completo=True,
                                 motivazione="Serve per capire il contesto della segnalazione.")
        assert [m.id for m in out.messaggi] == [r["id"] for r in righe[:10]]
        assert out.troncato is True

    async def test_audit_fallito_502_senza_dati(self, posta):
        db, sec = await scenario_wp9()
        conv, righe = await conversazione_lunga(db, sec)
        sid = segnalazione(db, "messaggio", str(righe[3]["id"]), segnalante="Y")
        db.guasti[("audit_log", "insert")] = errore_pg("XX000")
        db.ops.clear()
        for completo in (False, True):
            with pytest.raises(UpstreamError):
                await mod.contesto(db, ADMIN, sid, completo=completo,
                                   motivazione="Serve per capire il contesto della segnalazione.")
        assert not [o for o in db.ops if o["tabella"] == "partner_messaggi"
                    and "testo" in o["select"]]

    async def test_solo_per_i_messaggi(self, posta):
        db, sec = await scenario_wp9()
        sid = await segnala_call(db, sec)
        with pytest.raises(AppError) as exc:
            await mod.contesto(db, ADMIN, sid)
        assert (exc.value.status_code, exc.value.code) == (400, "contesto_non_disponibile")
        sparito = segnalazione(db, "messaggio", "999999", segnalante="X")
        with pytest.raises(NotFoundError):
            await mod.contesto(db, ADMIN, sparito)


# ------------------------------------------------------------ coda


class TestCoda:
    async def test_aperte_dalla_piu_vecchia_senza_id_di_utenti(self, posta):
        db, sec = await scenario_wp9()
        prima = segnalazione(db, "call", g.CALL_GUIDA_ID, segnalante="Y",
                             created_at="2026-09-01T10:00:00+00:00")
        seconda = segnalazione(db, "call", g.CALL_GUIDA_ID, segnalante="Z",
                               created_at="2026-09-02T10:00:00+00:00")
        decisa = segnalazione(db, "profilo", g.CODICE_PUBBLICO["Y"], segnalante="X",
                              created_at="2026-09-03T10:00:00+00:00")
        await mod.decidi(db, ADMIN, decisa, decisione("nessuna_azione"))
        await mod.prendi_in_carico(db, ADMIN, seconda)
        pagina = await mod.coda(db)
        assert [str(s.id) for s in pagina.items] == [prima, seconda] and pagina.total == 2
        assert pagina.items[1].autore.ragione_sociale == RAGIONE["X"]
        testo = pagina.model_dump_json()
        for nome in ("Y", "Z"):
            assert g.OWNER[nome] not in testo
        assert "segnalante_user_id" not in testo and ADMIN_ID not in testo
        decise = await mod.coda(db, stato="decisa")
        assert [str(s.id) for s in decise.items] == [decisa]
        assert (await mod.coda(db, stato="tutte")).total == 3
        dettaglio = await mod.dettaglio_admin(db, prima)
        assert dettaglio.contenuto_snapshot and dettaglio.descrizione == DESCRIZIONE


# ------------------------------------------------ consorzio e scheduler


class TestConsorzioDiUnaCallSospesa:
    async def test_il_membro_esce_dalla_call_sospesa(self, posta):
        db, sec = await scenario_wp9()
        x, y = await consorzio_xy(db, sec)
        await mod.sospendi(db, ADMIN, "call", g.CALL_GUIDA_ID,
                           SospendiIn(motivazione=MOTIVAZIONE))
        # durante la sospensione la controparte legge del consorzio SOLO la
        # propria riga (così trova il bottone per uscire), il titolare con
        # l'uscita possibile e un membro con visibilità in sola lettura…
        solo = await consorzio.get_consorzio(db, sec, attiva("Y"), utente("Y"),
                                             g.CALL_GUIDA_ID)
        assert [str(m.id) for m in solo.membri] == [y["id"]]
        propria = solo.membri[0]
        assert (propria.sei_tu, propria.puo_uscire, propria.stato) == (True, True, "proposto")
        assert solo.validazione.voci == [] and solo.documenti == [] and solo.matrice.righe == []
        assert (solo.budget.fascia, solo.budget.esatto) == (None, None)
        canary(solo.model_dump_json(), "X")
        lettura = await consorzio.get_consorzio(db, sec, attiva("Y", editable=False),
                                                utente("Y"), g.CALL_GUIDA_ID)
        assert lettura.membri[0].puo_uscire is False and lettura.editable is False
        # …un estraneo no, né senza sospensione una call che non vede
        with pytest.raises(NotFoundError):
            await consorzio.get_consorzio(db, sec, attiva("O"), utente("O"), g.CALL_GUIDA_ID)
        # …né tocca le righe altrui, e un estraneo non esce da nulla: sempre
        # il 404 di una call inesistente
        for nome, membro in (("Y", x["id"]), ("O", y["id"]), ("Y", str(uuid.uuid4())),
                             ("Y", "non-un-uuid")):
            with pytest.raises(NotFoundError) as exc:
                await consorzio.esci(db, sec, attiva(nome), utente(nome), g.CALL_GUIDA_ID,
                                     membro)
            assert exc.value.message == "Call di partenariato non trovata"
        assert db.chiamate("fn_partner_membro_esci") == []
        # ma esce dalla propria riga, e la risposta ha solo quella
        out = await consorzio.esci(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID, y["id"])
        assert [str(m.id) for m in out.membri] == [y["id"]]
        assert out.membri[0].stato == "uscito"
        assert db.membro_di(g.COMPANY["Y"])["stato"] == "uscito"
        assert notifiche(db, g.OWNER["X"], consorzio.TIPO_CONSORZIO_AGGIORNATO)
        canary(out.model_dump_json(), "X")
        # uscita: da lì la call sospesa torna un 404 anche per lei
        with pytest.raises(NotFoundError):
            await consorzio.get_consorzio(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID)
        # il creatore non modifica il consorzio di una call sospesa
        with pytest.raises(AppError) as exc:
            await consorzio.esci(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID, y["id"])
        assert exc.value.code == "call_non_modificabile"

    async def test_senza_sospensione_non_cambia_nulla(self, posta):
        db, sec = await scenario_wp9()
        x, y = await consorzio_xy(db, sec)
        with pytest.raises(NotFoundError):
            await consorzio.esci(db, sec, attiva("O"), utente("O"), g.CALL_GUIDA_ID, y["id"])
        out = await consorzio.esci(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID, y["id"])
        assert out.membri[0].stato == "uscito"


class TestRicalcoloValidazioni:
    async def test_passo_dello_scheduler_riallinea_la_validazione(self, posta):
        db, sec = await scenario_wp9()
        await accetta(db, sec)
        c = call(db)
        # la validazione salvata è vecchia: un membro è cambiato dopo
        c.update(validazione_esito="verde", validazione_at="2026-01-01T00:00:00+00:00",
                 copertura_gap_ratio=None)
        esito = await partenariati_scheduler.ricalcolo_validazioni(db, sec)
        assert esito["errori"] == 0 and esito["ricalcolate"] >= 1
        assert _ts(c["validazione_at"]) > _ts("2026-01-01T00:00:00+00:00")
        assert c["validazione_esito"] in ("verde", "rosso", "grigio")
        assert g.CALL_GUIDA_ID not in db._fn_partner_call_validazioni_da_ricalcolare(
            {"p_limite": 100})
        salvate = db.chiamate("fn_partner_call_validazione_salva")
        assert salvate and salvate[-1]["p_call"] == g.CALL_GUIDA_ID

    async def test_call_non_in_uno_stato_del_consorzio(self, posta):
        db, sec = await scenario_wp9()
        call(db)["stato"] = "bozza"
        assert await consorzio.ricalcola_validazione(db, sec, g.CALL_GUIDA_ID) is False
        assert await consorzio.ricalcola_validazione(db, sec, str(uuid.uuid4())) is False
        assert db.chiamate("fn_partner_call_validazione_salva") == []
