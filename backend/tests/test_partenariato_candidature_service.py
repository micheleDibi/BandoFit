"""Candidature spontanee e inviti (WP7, docs/partenariati.md K1-K2, Q13, Q14,
Q25): servizio vero sul primario finto del WP6 caricato con l'esempio guida.

Qui vive anche il primario FINTO del WP7 (`FakePrimaryWP7`): le RPC della
0039 con le stesse guardie e gli stessi detail (candidatura, invito,
decisione, ritiro, scadenze, messaggi, letture, claim delle email,
riepilogo, chiusura), i filtri PostgREST `or`, `lt` e i percorsi JSON,
usato dagli altri test del WP7. Le guardie vere, i lock e i trigger li
verifica `tests/db/test_migration_0039.py`.

Verifica: candidatura con e senza opt-in, piano Gratuito
(`funzione_non_inclusa`), limite del mese, contatti e identificativi nel
messaggio, requisiti non visibili, valutazione in vista «terzi» senza numeri
né punteggio; invito con pseudonimo valido, di un'altra call, non più
suggeribile, malformato; decisioni dal lato giusto (l'altro è 404), doppia
decisione, rifiuto con motivo; rivelazione SIMMETRICA (WP9: identità e audit
solo con entrambe le aziende verificate dalla piattaforma, canary con una
sola verificata, revoca che spegne l'identità nelle viste successive,
interruttore spento); ritiro;
liste per lato con la scadenza pigra; notifiche con deep link e email solo
con `eventi_abilitati`, mai testi; riepilogo; scheduler."""

import copy
import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from postgrest.exceptions import APIError
from pydantic import ValidationError

from app.api.deps import ActiveCompany
from app.core.errors import AppError, BadRequestError, ForbiddenError, NotFoundError
from app.services import (
    bandi_service,
    email_service,
    partenariati_scheduler,
    partenariato_collegamenti,
    partenariato_indice,
)
from app.services import partenariato_candidature_service as svc
from app.services import partenariato_chat_service as chat
from app.services import partner_call_service as pcs
from app.services import partner_profile_service as pps
from app.services.partenariato_accesso import pseudonimo
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariato_indice import (  # noqa: F401 — fixture autouse
    EMAIL,
    PIVA,
    RAGIONE,
    FakePrimary,
    FakeQuery,
    ambiente_wp6,
    carica_guida,
    secondario_guida,
)

MEMBRO_X = "b0000000-0000-4000-8000-0000000000aa"
FM_X = "b1000000-0000-4000-8000-0000000000aa"
EMAIL_MEMBRO_X = "membro.x@example.test"
COMPANY_Y2 = "c0000000-0000-4000-8000-000000000022"
MESSAGGIO_Y = (
    "Siamo un organismo di ricerca con un laboratorio di prototipazione e prove: possiamo "
    "coprire la posizione con il nostro gruppo di lavoro."
)
# Valori esatti di bilancio (mai verso terzi).
NUMERI_ESATTI = ("2400000", "2600000", "812345", "3100000", "3300000")


# ------------------------------------------------------------ primario finto


def errore(detail: str) -> APIError:
    return APIError({"message": "errore sql", "code": "P0001", "details": detail, "hint": None})


def _adesso() -> datetime:
    return datetime.now(timezone.utc)


def _valore(riga: dict, colonna: str):
    if "->" not in colonna:
        return riga.get(colonna)
    parti = re.split(r"->>|->", colonna)
    valore = riga.get(parti[0])
    for passo in parti[1:]:
        valore = valore.get(passo) if isinstance(valore, dict) else None
    return valore


def _confronta(op: str, a, v) -> bool:
    if op == "eq":
        return a is not None and str(a) == str(v)
    if op == "in":
        return str(a) in v
    if op == "is":
        return not (v == "null" and a is not None)
    if a is None:
        return False
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        a, v = float(a), float(v)
    else:
        a, v = str(a), str(v)
    return {"gt": a > v, "gte": a >= v, "lt": a < v, "lte": a <= v}[op]


def _termini(testo: str) -> list[str]:
    parti, livello, corrente = [], 0, ""
    for ch in testo:
        livello += (ch == "(") - (ch == ")")
        if ch == "," and livello == 0:
            parti.append(corrente)
            corrente = ""
        else:
            corrente += ch
    return [p for p in [*parti, corrente] if p]


def _vale(riga: dict, termine: str) -> bool:
    if termine.startswith("and(") and termine.endswith(")"):
        return all(_vale(riga, t) for t in _termini(termine[4:-1]))
    colonna, op, valore = termine.split(".", 2)
    return _confronta(op, _valore(riga, colonna), valore)


class FakeQueryWP7(FakeQuery):
    """La query finta del WP6 con `or` (anche `and(...)` annidati), `lt`,
    `lte` e i percorsi JSON (`payload->>chiave`) nei filtri."""

    def or_(self, filtro: str):
        return self._f("or", "", filtro)

    def lt(self, c, v):
        return self._f("lt", c, v)

    def lte(self, c, v):
        return self._f("lte", c, v)

    def _passa(self, riga: dict) -> bool:
        for op, c, v in self.filtri:
            if op == "or":
                if not any(_vale(riga, t) for t in _termini(v)):
                    return False
            elif not _confronta(op, _valore(riga, c), v):
                return False
        return True


class FakePrimaryWP7(FakePrimary):
    """Il primario finto del WP6 con le RPC della 0039 (stesse guardie e
    detail) e il rate limit. `limiti` = candidature al mese per owner
    (default 5, come Smart; None = illimitate, 0 = Gratuito)."""

    def __init__(self):
        super().__init__()
        self.limiti: dict[str, int | None] = {}
        self.rate: dict[str, int] = {}
        self._msg = 0
        note = dict(self.colonne_note)
        note["company_partner_profiles"] = (*note["company_partner_profiles"],
                                            "referente_user_id")
        note["profiles"] = ("nome", "cognome")
        note["family_members"] = ("denominazione",)
        self.colonne_note = note

    def table(self, nome):
        return FakeQueryWP7(self, nome)

    # -- aiuti delle guardie
    def _viva(self, company_id) -> dict | None:
        return next((r for r in self.righe("company_profiles", id=company_id)
                     if not r.get("deleted_at") and not r.get("archived_at")), None)

    def _controparte_viva(self, company_id) -> bool:
        """Come la 0039 per la controparte: viva e con il profilo dell'owner attivo."""
        azienda = self._viva(company_id)
        owner = next(iter(self.righe("profiles", id=(azienda or {}).get("parent_id"))), None)
        return bool(azienda and owner and owner.get("is_active") is not False)

    def _blocca(self, owner, company) -> None:
        if not self.righe("profiles", id=owner):
            raise errore("owner_not_found")
        azienda = self._viva(company)
        if azienda is None or str(azienda["parent_id"]) != str(owner):
            raise errore("azienda_non_disponibile")

    def _identita_ok(self, company, richiedi) -> bool:
        cp = next(iter(self.righe("company_profiles", id=company)), None)
        cd = next(iter(self.righe("company_data", company_profile_id=company)), None)
        return bool(
            cp and cd and cd.get("piva_fetched") == cp.get("partita_iva")
            and str(cd.get("stato_impresa") or "").strip().lower() == "attiva"
            and (richiedi is False or cd.get("sandbox") is False)
        )

    # -- 0041: identità verificata dall'admin
    def verifica_identita(self, company) -> None:
        """Riga `verificata` di company_identita_stato (fn_identita_decidi)."""
        self.tabelle.setdefault("company_identita_stato", [])[:] = [
            r for r in self.tabelle.get("company_identita_stato", [])
            if r["company_profile_id"] != company
        ] + [{"company_profile_id": company, "stato": "verificata",
              "metodo": "pec", "verificata_at": _adesso().isoformat(),
              "verificata_da": "d0000000-0000-4000-8000-0000000000ad",
              "richiesta_at": _adesso().isoformat(), "aggiornato_at": _adesso().isoformat()}]

    def revoca_identita(self, company) -> None:
        """fn_identita_revoca / revoca automatica: stato non_richiesta."""
        for r in self.righe("company_identita_stato", company_profile_id=company):
            r.update(stato="non_richiesta", metodo=None, verificata_at=None, verificata_da=None)

    def _fn_partenariato_identita_forte(self, p):
        """fn_partenariato_identita_forte (0041): verificata + T5 senza sandbox."""
        stato = next(iter(self.righe("company_identita_stato",
                                     company_profile_id=p["p_company"])), None)
        return bool(stato and stato.get("stato") == "verificata"
                    and self._identita_ok(p["p_company"], False))

    def _call_aperta(self, call) -> bool:
        oggi = bandi_service.today_italy().isoformat()
        return bool(
            call and call["stato"] == "pubblicata" and str(call["scadenza_call"])[:10] >= oggi
            and (not call.get("bando_scadenza") or str(call["bando_scadenza"])[:10] >= oggi)
            and self._viva(call["company_profile_id"]) is not None
        )

    def _utente_di(self, user, company) -> bool:
        azienda = next(iter(self.righe("company_profiles", id=company)), None)
        profilo = next(iter(self.righe("profiles", id=user)), None)
        if not azienda or not profilo or not profilo.get("is_active"):
            return False
        if str(azienda["parent_id"]) == str(user):
            return True
        membri = [m for m in self.righe("family_members", parent_id=azienda["parent_id"],
                                         member_id=user) if m.get("status") == "active"]
        return any(self.righe("family_member_company_access", family_member_id=m["id"],
                              company_profile_id=company) for m in membri)

    def usate(self, owner) -> int:
        fuso = ZoneInfo("Europe/Rome")
        inizio = _adesso().astimezone(fuso).replace(day=1, hour=0, minute=0, second=0,
                                                    microsecond=0)
        return sum(1 for r in self.righe("partner_candidature", family_parent_id=owner)
                   if r["tipo"] == "candidatura"
                   and datetime.fromisoformat(r["created_at"]) >= inizio)

    def _scadi(self, righe) -> int:
        n = 0
        for r in righe:
            if (r["tipo"] == "invito" and r["stato"] == "inviata"
                    and datetime.fromisoformat(r["scade_at"]) <= _adesso()):
                r.update(stato="scaduta", motivo_chiusura="ttl", chiusa_at=_adesso().isoformat())
                n += 1
        return n

    def _attiva(self, call, company) -> dict | None:
        return next((r for r in self.righe("partner_candidature", partner_call_id=call,
                                            company_profile_id=company)
                     if r["stato"] in ("inviata", "accettata")), None)

    def _audit(self, attore, azione, target, famiglia, payload) -> None:
        self.inserisci("audit_log", {"actor_id": attore, "action": azione,
                                     "target_user_id": target, "family_parent_id": famiglia,
                                     "payload": payload})

    def _riga_candidatura(self, **campi) -> dict:
        adesso = _adesso().isoformat()
        riga = {
            "id": str(uuid.uuid4()), "posizione_id": None, "messaggio": None,
            "requisiti_dichiarati": [], "valutazione": {}, "stato": "inviata",
            "motivo_chiusura": None, "motivo_rifiuto": None, "decisa_da_user_id": None,
            "decisa_at": None, "chiusa_at": None, "scade_at": None, "conversazione_id": None,
            "created_at": adesso, "updated_at": adesso, **campi,
        }
        self.tabelle.setdefault("partner_candidature", []).append(riga)
        return riga

    # -- RPC della 0039
    def _fn_consume_auth_rate_limit(self, p):
        self.rate[p["p_bucket"]] = self.rate.get(p["p_bucket"], 0) + 1
        return self.rate[p["p_bucket"]] <= p["p_limit"]

    def _fn_partner_invia_candidatura(self, p):
        pl = p["p_payload"]
        ammesse = {"owner_id", "company_id", "attore_id", "call_id", "posizione_id", "messaggio",
                   "requisiti_dichiarati", "valutazione", "pseudonimo", "richiedi_non_sandbox"}
        messaggio = (pl.get("messaggio") or "").strip(" ")
        requisiti = pl.get("requisiti_dichiarati") or []
        if (not isinstance(pl, dict) or set(pl) - ammesse
                or not all(pl.get(k) for k in ("owner_id", "company_id", "call_id",
                                                "posizione_id"))
                or not 50 <= len(messaggio) <= 2000 or not isinstance(pl.get("valutazione"), dict)
                or not re.fullmatch(r"[A-Z2-7]{16}", pl.get("pseudonimo") or "")):
            raise errore("parametri_non_validi")
        if len(set(requisiti)) != len(requisiti):
            raise errore("requisiti_non_validi")
        owner, company, call_id = pl["owner_id"], pl["company_id"], pl["call_id"]
        if pl.get("attore_id") != owner:
            raise errore("attore_non_titolare")
        self._blocca(owner, company)
        limite = self.limiti.get(owner, 5)
        if limite is not None and limite <= 0:
            raise errore("funzione_non_inclusa")
        profilo = next(iter(self.righe("company_partner_profiles",
                                       company_profile_id=company)), None)
        if not profilo or not profilo.get("visibile_come_partner") or profilo.get("sospeso_at"):
            raise errore("profilo_partner_non_attivo")
        if not self._identita_ok(company, pl.get("richiedi_non_sandbox")):
            raise errore("identita_non_verificata")
        call = next(iter(self.righe("partner_calls", id=call_id)), None)
        if not self._call_aperta(call):
            raise errore("call_non_attiva")
        creatore = self._viva(call["company_profile_id"]) or {}
        if (call["company_profile_id"] == company or call["family_parent_id"] == owner
                or creatore.get("parent_id") == owner):
            raise errore("stesso_gruppo")
        if call["visibilita"] != "pubblica":
            raise errore("call_solo_invitati")
        if not self.righe("partner_call_posizioni", id=pl["posizione_id"], call_id=call_id):
            raise errore("posizione_non_valida")
        if not set(requisiti) <= {r["id"] for r in self.righe("partner_call_requisiti",
                                                               call_id=call_id)}:
            raise errore("requisiti_non_validi")
        self._scadi(self.righe("partner_candidature", partner_call_id=call_id,
                               company_profile_id=company))
        esistente = self._attiva(call_id, company)
        if esistente:
            raise errore("invito_gia_attivo" if esistente["tipo"] == "invito"
                         else "candidatura_gia_attiva")
        if limite is not None and self.usate(owner) >= limite:
            raise errore("candidature_esaurite")
        riga = self._riga_candidatura(
            partner_call_id=call_id, tipo="candidatura", company_profile_id=company,
            family_parent_id=owner, creatore_company_profile_id=call["company_profile_id"],
            posizione_id=pl["posizione_id"], messaggio=messaggio, requisiti_dichiarati=requisiti,
            valutazione=pl["valutazione"], pseudonimo=pl["pseudonimo"],
            inviata_da_user_id=pl["attore_id"])
        self._audit(pl["attore_id"], "partenariato.candidatura_inviata", call["family_parent_id"],
                    owner, {"candidatura_id": riga["id"], "call_id": call_id,
                            "company_profile_id": company,
                            "creatore_company_profile_id": call["company_profile_id"]})
        return {"candidatura": copy.deepcopy(riga), "usate": self.usate(owner), "limite": limite}

    def _fn_partner_invita(self, p):
        pl = p["p_payload"]
        ammesse = {"owner_id", "company_id", "attore_id", "call_id", "invitato_company_id",
                   "posizione_id", "messaggio", "valutazione", "pseudonimo", "max_inviti",
                   "ttl_giorni"}
        messaggio = ((pl.get("messaggio") or "").strip(" ")) or None
        if (set(pl) - ammesse or not all(pl.get(k) for k in ("owner_id", "company_id",
                                                              "call_id", "invitato_company_id"))
                or not 0 <= int(pl.get("max_inviti", -1)) <= 1000
                or not 1 <= int(pl.get("ttl_giorni", 0)) <= 90
                or (messaggio and len(messaggio) > 1000)
                or not isinstance(pl.get("valutazione"), dict)
                or not re.fullmatch(r"[A-Z2-7]{16}", pl.get("pseudonimo") or "")):
            raise errore("parametri_non_validi")
        owner, company, call_id, y = (pl["owner_id"], pl["company_id"], pl["call_id"],
                                      pl["invitato_company_id"])
        if pl.get("attore_id") != owner:
            raise errore("attore_non_titolare")
        self._blocca(owner, company)
        azienda_y = self._viva(y)
        if azienda_y is None or y == company or azienda_y["parent_id"] == owner:
            raise errore("partner_non_disponibile")
        profilo = next(iter(self.righe("company_partner_profiles", company_profile_id=y)), None)
        if (not profilo or not profilo.get("visibile_come_partner") or profilo.get("sospeso_at")
                or profilo.get("accetta_inviti") is False):
            raise errore("partner_non_disponibile")
        call = next((c for c in self.righe("partner_calls", id=call_id)
                     if c["company_profile_id"] == company and c["family_parent_id"] == owner),
                    None)
        if call is None:
            raise errore("call_not_found")
        if not self._call_aperta(call):
            raise errore("call_non_attiva")
        if pl.get("posizione_id") and not self.righe("partner_call_posizioni",
                                                     id=pl["posizione_id"], call_id=call_id):
            raise errore("posizione_non_valida")
        self._scadi(self.righe("partner_candidature", partner_call_id=call_id))
        esistente = self._attiva(call_id, y)
        if esistente:
            raise errore("invito_gia_attivo" if esistente["tipo"] == "invito"
                         else "candidatura_gia_attiva")
        if any(r["tipo"] == "invito" and r["stato"] == "rifiutata"
               for r in self.righe("partner_candidature", partner_call_id=call_id,
                                   company_profile_id=y)):
            raise errore("partner_non_disponibile")
        attivi = sum(1 for r in self.righe("partner_candidature", partner_call_id=call_id)
                     if r["tipo"] == "invito" and r["stato"] == "inviata")
        if attivi >= int(pl["max_inviti"]):
            raise errore("inviti_esauriti_call")
        riga = self._riga_candidatura(
            partner_call_id=call_id, tipo="invito", company_profile_id=y,
            family_parent_id=azienda_y["parent_id"], creatore_company_profile_id=company,
            posizione_id=pl.get("posizione_id"), messaggio=messaggio,
            valutazione=pl["valutazione"], pseudonimo=pl["pseudonimo"],
            inviata_da_user_id=pl["attore_id"],
            scade_at=(_adesso() + timedelta(days=int(pl["ttl_giorni"]))).isoformat())
        self._audit(pl["attore_id"], "partenariato.invito_inviato", azienda_y["parent_id"], owner,
                    {"candidatura_id": riga["id"], "call_id": call_id, "company_profile_id": y,
                     "creatore_company_profile_id": company})
        return {"candidatura": copy.deepcopy(riga), "inviti_attivi": attivi + 1,
                "max_inviti": int(pl["max_inviti"])}

    def _fn_partner_decidi(self, p):
        if p.get("p_decisione") not in ("accetta", "rifiuta"):
            raise errore("parametri_non_validi")
        motivo = ((p.get("p_motivo") or "").strip(" ") or None) \
            if p["p_decisione"] == "rifiuta" else None
        if motivo and len(motivo) > 500:
            raise errore("parametri_non_validi")
        if p["p_attore"] != p["p_owner"]:
            raise errore("attore_non_titolare")
        riga = next(iter(self.righe("partner_candidature", id=p["p_candidatura"])), None)
        if riga is None:
            raise errore("candidatura_non_trovata")
        decide = riga["creatore_company_profile_id"] if riga["tipo"] == "candidatura" \
            else riga["company_profile_id"]
        controparte = riga["company_profile_id"] if riga["tipo"] == "candidatura" \
            else riga["creatore_company_profile_id"]
        if p["p_company"] != decide:
            raise errore("candidatura_non_trovata")
        self._blocca(p["p_owner"], p["p_company"])
        if riga["stato"] != "inviata":
            raise errore("candidatura_gia_decisa")
        if riga["tipo"] == "invito" and datetime.fromisoformat(riga["scade_at"]) <= _adesso():
            raise errore("invito_scaduto")
        adesso = _adesso().isoformat()
        x_owner = self.una("company_profiles", id=riga["creatore_company_profile_id"])["parent_id"]
        y_owner = self.una("company_profiles", id=riga["company_profile_id"])["parent_id"]
        target = y_owner if x_owner == p["p_owner"] else x_owner
        payload = {"candidatura_id": riga["id"], "call_id": riga["partner_call_id"],
                   "tipo": riga["tipo"], "company_profile_id": riga["company_profile_id"],
                   "creatore_company_profile_id": riga["creatore_company_profile_id"]}
        if p["p_decisione"] == "rifiuta":
            riga.update(stato="rifiutata", motivo_rifiuto=motivo, decisa_da_user_id=p["p_attore"],
                        decisa_at=adesso)
            self._audit(p["p_attore"], "partenariato.candidatura_rifiutata", target,
                        p["p_owner"], payload)
            return {"candidatura": copy.deepcopy(riga), "conversazione_id": None}
        call = self.una("partner_calls", id=riga["partner_call_id"])
        if not self._call_aperta(call):
            raise errore("call_non_attiva")
        if not self._identita_ok(p["p_company"], p.get("p_richiedi_non_sandbox")):
            raise errore("identita_non_verificata")
        profilo = next(iter(self.righe("company_partner_profiles",
                                       company_profile_id=riga["company_profile_id"])), None)
        if not profilo or not profilo.get("visibile_come_partner") or profilo.get("sospeso_at"):
            raise errore("profilo_partner_non_attivo" if riga["company_profile_id"] ==
                         p["p_company"] else "controparte_non_disponibile")
        if not self._controparte_viva(controparte) or not self._identita_ok(
                controparte, p.get("p_richiedi_non_sandbox")):
            raise errore("controparte_non_disponibile")
        if x_owner == y_owner:
            raise errore("stesso_gruppo")
        if self._esclusivita_violata(riga["company_profile_id"], call):
            raise errore("esclusivita_violata")
        conversazione = {
            "id": str(uuid.uuid4()), "partner_call_id": riga["partner_call_id"],
            "candidatura_id": riga["id"],
            "company_creatore_id": riga["creatore_company_profile_id"],
            "company_partner_id": riga["company_profile_id"], "stato": "aperta",
            "chiusa_da_user_id": None, "chiusa_at": None, "ultimo_messaggio_id": None,
            "ultimo_messaggio_at": None, "created_at": adesso, "updated_at": adesso,
        }
        self.tabelle.setdefault("partner_conversazioni", []).append(conversazione)
        riga.update(stato="accettata", decisa_da_user_id=p["p_attore"], decisa_at=adesso,
                    conversazione_id=conversazione["id"])
        payload["conversazione_id"] = conversazione["id"]
        self._audit(p["p_attore"], "partenariato.candidatura_accettata", target, p["p_owner"],
                    payload)
        # 0041: rivelazione SIMMETRICA, ricontrollata sotto i lock.
        if (p.get("p_rivela")
                and self._fn_partenariato_identita_forte({"p_company": riga["company_profile_id"]})
                and self._fn_partenariato_identita_forte(
                    {"p_company": riga["creatore_company_profile_id"]})):
            for azione in ("partenariato.identita_rivelata", "partenariato.contatti_rivelati"):
                self._audit(p["p_attore"], azione, target, p["p_owner"], payload)
        return {"candidatura": copy.deepcopy(riga), "conversazione_id": conversazione["id"]}

    def _esclusivita_violata(self, company, call) -> bool:
        altre = [c for c in self.righe("partner_calls", company_profile_id=company)
                 if c["bando_id"] == call["bando_id"] and c["id"] != call["id"]
                 and c["stato"] == "pubblicata"]
        for k in self.righe("partner_candidature", company_profile_id=company, stato="accettata"):
            altra = self.una("partner_calls", id=k["partner_call_id"])
            if (altra["id"] != call["id"] and altra["bando_id"] == call["bando_id"]
                    and altra["stato"] != "chiusa_annullata"):
                altre.append(altra)
        return any(call.get("esclusivita") or c.get("esclusivita") for c in altre)

    def _fn_partner_ritira(self, p):
        if p["p_attore"] != p["p_owner"]:
            raise errore("attore_non_titolare")
        riga = next(iter(self.righe("partner_candidature", id=p["p_candidatura"])), None)
        if riga is None:
            raise errore("candidatura_non_trovata")
        ritira = riga["company_profile_id"] if riga["tipo"] == "candidatura" \
            else riga["creatore_company_profile_id"]
        if p["p_company"] != ritira:
            raise errore("candidatura_non_trovata")
        self._blocca(p["p_owner"], p["p_company"])
        if riga["stato"] != "inviata":
            raise errore("candidatura_gia_decisa")
        if riga["tipo"] == "invito" and datetime.fromisoformat(riga["scade_at"]) <= _adesso():
            raise errore("invito_scaduto")
        riga.update(stato="ritirata", chiusa_at=_adesso().isoformat())
        self._audit(p["p_attore"], "partenariato.candidatura_ritirata", p["p_owner"],
                    p["p_owner"], {"candidatura_id": riga["id"]})
        return {"candidatura": copy.deepcopy(riga)}

    def _fn_partner_scadi_inviti(self, p):
        return self._scadi(sorted(self.tabelle.get("partner_candidature", []),
                                  key=lambda r: str(r.get("scade_at")))[: p.get("p_limite") or 500])

    def _fn_partner_invia_messaggio(self, p):
        testo = p.get("p_testo")
        if (not p.get("p_conversazione") or not p.get("p_client_msg_id") or not testo
                or not 1 <= len(testo) <= 5000 or not testo.strip()):
            raise errore("parametri_non_validi")
        if p["p_attore"] != p["p_owner"]:
            raise errore("attore_non_titolare")
        if not self.righe("profiles", id=p["p_owner"]):
            raise errore("owner_not_found")
        azienda = self._viva(p["p_company"])
        if azienda is None or azienda["parent_id"] != p["p_owner"]:
            raise errore("azienda_non_disponibile")
        conv = next((c for c in self.righe("partner_conversazioni", id=p["p_conversazione"])
                     if p["p_company"] in (c["company_creatore_id"], c["company_partner_id"])),
                    None)
        if conv is None:
            raise errore("conversazione_non_trovata")
        controparte = conv["company_partner_id"] if conv["company_creatore_id"] == p["p_company"] \
            else conv["company_creatore_id"]
        doppio = next(iter(self.righe("partner_messaggi", conversazione_id=conv["id"],
                                      client_msg_id=p["p_client_msg_id"])), None)
        if doppio:
            if doppio["mittente_company_profile_id"] != p["p_company"]:
                raise errore("parametri_non_validi")
            return {"messaggio": copy.deepcopy(doppio), "company_destinataria_id": controparte,
                    "duplicato": True}
        if conv["stato"] != "aperta":
            raise errore("conversazione_chiusa")
        if not self._controparte_viva(controparte):
            raise errore("controparte_non_disponibile")
        self._msg += 1
        messaggio = {
            "id": self._msg, "conversazione_id": conv["id"],
            "mittente_company_profile_id": p["p_company"], "mittente_user_id": p["p_attore"],
            "testo": testo, "client_msg_id": p["p_client_msg_id"],
            "nascosto_moderazione_at": None, "nascosto_da": None,
            "created_at": _adesso().isoformat(),
        }
        self.tabelle.setdefault("partner_messaggi", []).append(messaggio)
        conv.update(ultimo_messaggio_id=messaggio["id"],
                    ultimo_messaggio_at=messaggio["created_at"])
        self._lettura(conv["id"], p["p_attore"], p["p_company"], messaggio["id"])
        return {"messaggio": copy.deepcopy(messaggio), "company_destinataria_id": controparte,
                "duplicato": False}

    def _lettura(self, conv, user, company, fino_a=None) -> dict:
        riga = next(iter(self.righe("partner_conversazione_letture", conversazione_id=conv,
                                    user_id=user)), None)
        if riga is None:
            riga = {"conversazione_id": conv, "user_id": user, "company_profile_id": company,
                    "letto_fino_a_id": 0, "email_fino_a_id": None}
            self.tabelle.setdefault("partner_conversazione_letture", []).append(riga)
        if fino_a is not None:
            riga["letto_fino_a_id"] = max(riga["letto_fino_a_id"], fino_a)
        return riga

    def _fn_partner_segna_letto(self, p):
        conv = next((c for c in self.righe("partner_conversazioni", id=p["p_conversazione"])
                     if p["p_company"] in (c["company_creatore_id"], c["company_partner_id"])),
                    None)
        if conv is None or not self._utente_di(p["p_user"], p["p_company"]):
            raise errore("conversazione_non_trovata")
        ultimo = conv.get("ultimo_messaggio_id") or 0
        fino_a = p.get("p_fino_a")
        return self._lettura(conv["id"], p["p_user"], p["p_company"],
                             min(ultimo if fino_a is None else fino_a, ultimo))["letto_fino_a_id"]

    def _fn_partner_claim_email_chat(self, p):
        conv = next((c for c in self.righe("partner_conversazioni", id=p["p_conversazione"])
                     if p["p_company"] in (c["company_creatore_id"], c["company_partner_id"])),
                    None)
        if conv is None:
            raise errore("conversazione_non_trovata")
        ultimi = [m["id"] for m in self.righe("partner_messaggi", conversazione_id=conv["id"])
                  if m["id"] <= p["p_ultimo_id"]
                  and m["mittente_company_profile_id"] != p["p_company"]]
        if not ultimi:
            return []
        rivendicati = []
        for user in sorted({u for u in p["p_user_ids"] or [] if u}):
            if not self._utente_di(user, p["p_company"]):
                continue
            riga = self._lettura(conv["id"], user, p["p_company"])
            email = riga.get("email_fino_a_id")
            if riga["letto_fino_a_id"] < max(ultimi) and (
                    email is None or email <= riga["letto_fino_a_id"]):
                riga["email_fino_a_id"] = max(ultimi)
                rivendicati.append(user)
        return rivendicati

    def _fn_partner_conversazioni_riepilogo(self, p):
        if not self._utente_di(p["p_user"], p["p_company"]):
            return []
        uscita = []
        for conv in self.tabelle.get("partner_conversazioni", []):
            if p["p_company"] not in (conv["company_creatore_id"], conv["company_partner_id"]):
                continue
            letto = next((r["letto_fino_a_id"] for r in self.righe(
                "partner_conversazione_letture", conversazione_id=conv["id"],
                user_id=p["p_user"])), 0)
            non_letti = sum(1 for m in self.righe("partner_messaggi", conversazione_id=conv["id"])
                            if m["id"] > letto and m["mittente_company_profile_id"] !=
                            p["p_company"] and m.get("nascosto_moderazione_at") is None)
            uscita.append({"conversazione_id": conv["id"], "non_letti": non_letti,
                           "ultimo_messaggio_at": conv.get("ultimo_messaggio_at"),
                           "_creata": conv["created_at"]})
        uscita.sort(key=lambda r: (r["ultimo_messaggio_at"] is None,
                                   str(r["ultimo_messaggio_at"] or ""), r["_creata"]),
                    reverse=False)
        uscita.sort(key=lambda r: str(r["ultimo_messaggio_at"] or ""), reverse=True)
        return [{k: v for k, v in r.items() if k != "_creata"} for r in uscita]

    def _fn_partner_chiudi_conversazione(self, p):
        if p["p_attore"] != p["p_owner"]:
            raise errore("attore_non_titolare")
        azienda = self._viva(p["p_company"])
        if azienda is None or azienda["parent_id"] != p["p_owner"]:
            raise errore("azienda_non_disponibile")
        conv = next((c for c in self.righe("partner_conversazioni", id=p["p_conversazione"])
                     if c["company_creatore_id"] == p["p_company"]), None)
        if conv is None:
            raise errore("conversazione_non_trovata")
        if conv["stato"] != "aperta":
            raise errore("conversazione_chiusa")
        conv.update(stato="chiusa", chiusa_at=_adesso().isoformat(),
                    chiusa_da_user_id=p["p_attore"])
        self._audit(p["p_attore"], "partenariato.conversazione_chiusa", p["p_owner"],
                    p["p_owner"], {"conversazione_id": conv["id"]})
        return copy.deepcopy(conv)


async def scenario_wp7(**limiti) -> tuple[FakePrimaryWP7, object]:
    """L'esempio guida (X crea la call, Y la copre) con un membro di X con
    visibilità, le chiavi dei collegamenti calcolate e i limiti del piano per
    owner (`scenario_wp7(Y=0)` = Y sul Gratuito)."""
    db = carica_guida(FakePrimaryWP7())
    db.tabelle["profiles"].append({"id": MEMBRO_X, "email": EMAIL_MEMBRO_X, "is_active": True})
    db.tabelle.setdefault("family_members", []).append({
        "id": FM_X, "parent_id": g.OWNER["X"], "member_id": MEMBRO_X, "status": "active",
        "denominazione": "Giulia del gruppo X"})
    db.tabelle.setdefault("family_member_company_access", []).append(
        {"family_member_id": FM_X, "company_profile_id": g.COMPANY["X"]})
    assert (await partenariato_collegamenti.backfill(db))["errori"] == 0
    db.limiti.update({g.OWNER[nome]: valore for nome, valore in limiti.items()})
    db.ops.clear()
    db.rpcs.clear()
    return db, secondario_guida()


def attiva(nome: str, *, editable: bool = True, company: str | None = None) -> ActiveCompany:
    return ActiveCompany(company_id=company or g.COMPANY[nome], owner_id=g.OWNER[nome],
                         editable=editable)


def utente(nome: str) -> dict:
    return {"id": g.OWNER[nome], "role": "cliente", "is_active": True}


def pseudo(nome: str, call: str = g.CALL_GUIDA_ID) -> str:
    return pseudonimo(call, g.CODICE_PUBBLICO[nome])


def candidatura_in(**modifiche) -> svc.CandidaturaIn:
    return svc.CandidaturaIn(**{"posizione_id": g.POS_P1, "messaggio": MESSAGGIO_Y,
                                "requisiti_dichiarati": [g.REQ["A"], g.REQ["C"]], **modifiche})


@pytest.fixture(name="fondo")
def fixture_fondo(monkeypatch):
    """I task in background (email, avvisi della chat) raccolti e poi eseguiti
    dal test; le email catturate prima del provider (nessuna rete)."""
    coda: list = []
    monkeypatch.setattr(svc, "_spawn", coda.append)
    monkeypatch.setattr(chat, "_spawn", coda.append)
    inviate: list[dict] = []

    async def dispatch(to_email, subject, html_body, text_body, headers=None):
        inviate.append({"to": to_email, "subject": subject, "html": html_body,
                        "text": text_body, "headers": headers or {}})
        return True

    monkeypatch.setattr(email_service, "_dispatch", dispatch)

    class Fondo:
        email = inviate

        async def esegui(self):
            while coda:
                await coda.pop(0)

        async def azzera(self):
            await self.esegui()
            inviate.clear()

    yield Fondo()
    for coro in coda:
        coro.close()


async def candida(db, sec, nome="Y", call=g.CALL_GUIDA_ID, **modifiche):
    return await svc.invia_candidatura(db, sec, attiva(nome), utente(nome), call,
                                       candidatura_in(**modifiche))


async def invita_y(db, sec, **k):
    return await svc.invita(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
                            svc.InvitoIn(pseudonimo=pseudo("Y"), **k))


def _canary(testo: str, *nomi: str) -> None:
    for nome in nomi:
        for valore in (g.COMPANY[nome], g.OWNER[nome], PIVA[nome], RAGIONE[nome],
                       RAGIONE[nome].upper(), g.CODICE_PUBBLICO[nome], EMAIL[nome]):
            assert valore not in testo, (nome, valore)
    for numero in NUMERI_ESATTI:
        assert numero not in testo, numero


def _canary_identita(testo: str, nome: str) -> None:
    """Nessun dato che identifica l'azienda `nome` (la controparte vede il
    budget esatto della call: qui contano solo i dati d'identità)."""
    for valore in (g.COMPANY[nome], g.OWNER[nome], PIVA[nome], RAGIONE[nome],
                   RAGIONE[nome].upper(), g.CODICE_PUBBLICO[nome], EMAIL[nome],
                   "impresa.x@pec.example.test"):
        assert valore not in testo, (nome, valore)


# ------------------------------------------------------------ candidatura


class TestCandidatura:
    async def test_candidatura_con_opt_in(self, fondo):
        db, sec = await scenario_wp7()
        out = await candida(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        assert (riga["tipo"], riga["stato"], riga["company_profile_id"]) == (
            "candidatura", "inviata", g.COMPANY["Y"])
        assert riga["pseudonimo"] == pseudo("Y")
        # valutazione salvata: vista «terzi», solo esiti e fasce (Y anonima:
        # la sola fascia di fatturato), niente punteggio né valori
        valutazione = riga["valutazione"]
        assert valutazione["copertura"] == {"coperti": 2, "cercati": 2}
        assert valutazione["spiegazione"] == g.SPIEGAZIONE_GUIDA
        assert valutazione["fasce"] == {"fatturato": "2m_10m", "patrimonio_netto": None,
                                        "dipendenti": None, "trend": None}
        assert "punteggio" not in valutazione and valutazione["dettaglio"] is None
        _canary(str(valutazione), "Y")
        # risposta per Y: il proprio lato, la quota del mese, niente del creatore
        assert (out.lato, out.tipo, out.stato, out.puo_ritirare, out.puo_decidere) == (
            "partner", "candidatura", "inviata", True, False)
        assert out.quota.model_dump() == {"usate": 1, "limite": 5}
        assert out.candidato is None and out.valutazione is None
        assert [r.etichetta for r in out.requisiti_dichiarati] == ["A", "C"]
        _canary(out.model_dump_json(), "X")
        # RPC con lo pseudonimo e mai l'identità del creatore
        [chiamata] = db.chiamate("fn_partner_invia_candidatura")
        assert set(chiamata["p_payload"]) == {
            "owner_id", "company_id", "attore_id", "call_id", "posizione_id", "messaggio",
            "requisiti_dichiarati", "valutazione", "pseudonimo", "richiedi_non_sandbox"}
        # notifica in-app al titolare e al membro di X, con il deep link
        notifiche = db.righe("notifications", tipo=svc.TIPO_CANDIDATURA_RICEVUTA)
        assert {n["user_id"] for n in notifiche} == {g.OWNER["X"], MEMBRO_X}
        assert all(n["url"] == f"/app/partenariati/call/{g.CALL_GUIDA_ID}?tab=candidature"
                              f"&azienda={g.COMPANY['X']}" for n in notifiche)
        assert all(n["company_profile_id"] == g.COMPANY["X"] for n in notifiche)
        _canary(str(notifiche), "Y")
        # email in background: con lo pseudonimo, mai il testo della candidatura
        await fondo.esegui()
        assert {e["to"] for e in fondo.email} == {EMAIL["X"], EMAIL_MEMBRO_X}
        for email in fondo.email:
            assert "Laboratorio" not in email["text"] and "prototipazione" not in email["text"]
            assert pseudo("Y") in email["text"]
            assert "tipo=eventi" in email["headers"]["List-Unsubscribe"]
            _canary(email["text"] + email["html"], "Y")

    async def test_email_solo_con_eventi_abilitati_e_recapitabili(self, fondo):
        db, sec = await scenario_wp7()
        db.inserisci("partner_email_settings", {"user_id": g.OWNER["X"],
                                                "eventi_abilitati": False})
        db.email_non_verificate.add(MEMBRO_X)
        await candida(db, sec)
        await fondo.esegui()
        assert fondo.email == []
        # la notifica in-app arriva comunque (canale affidabile)
        assert len(db.righe("notifications", tipo=svc.TIPO_CANDIDATURA_RICEVUTA)) == 2

    async def test_senza_opt_in(self, fondo):
        db, sec = await scenario_wp7()
        with pytest.raises(AppError) as exc:
            await candida(db, sec, nome="V")
        assert (exc.value.status_code, exc.value.code) == (409, "profilo_partner_non_attivo")
        assert db.tabelle.get("partner_candidature", []) == []

    async def test_gratuito_funzione_non_inclusa(self, fondo):
        db, sec = await scenario_wp7(Y=0)
        with pytest.raises(AppError) as exc:
            await candida(db, sec)
        assert (exc.value.status_code, exc.value.code) == (409, "funzione_non_inclusa")
        assert "inviti" in exc.value.message

    async def test_limite_del_mese_sul_pool_dell_owner(self, fondo):
        db, sec = await scenario_wp7(Y=1)
        await candida(db, sec)
        with pytest.raises(AppError) as exc:
            await candida(db, sec, call=g.CALL_ALTRA_ID, posizione_id=g.POS_ALTRA,
                          requisiti_dichiarati=[])
        assert exc.value.code == "candidature_esaurite"

    async def test_illimitate(self, fondo):
        db, sec = await scenario_wp7(Y=None)
        out = await candida(db, sec)
        assert out.quota.model_dump() == {"usate": 1, "limite": None}

    @pytest.mark.parametrize("testo", [
        "Scriveteci a laboratorio@esempio.it per ogni dettaglio sul progetto e sui prototipi.",
        "Chiamate lo 06 1234 5678 per ogni dettaglio sul progetto e sui prototipi del gruppo.",
        "Siamo la Impresa Sintetica Y e abbiamo un laboratorio di prototipazione e prove dal 2010.",
        "Trovate tutto su www.laboratorio-esempio.it con i dettagli sui prototipi e le prove.",
    ])
    async def test_contatti_o_identita_nel_messaggio(self, fondo, testo):
        db, sec = await scenario_wp7()
        with pytest.raises(AppError) as exc:
            await candida(db, sec, messaggio=testo)
        assert (exc.value.status_code, exc.value.code) == (400, "testo_non_conforme")
        assert "@" not in exc.value.message and "1234" not in exc.value.message
        assert db.chiamate("fn_partner_invia_candidatura") == []

    def test_messaggio_troppo_corto_o_lungo(self):
        with pytest.raises(BadRequestError):
            candidatura_in(messaggio="Troppo breve")
        with pytest.raises(BadRequestError):
            candidatura_in(messaggio="x" * 2001)
        # gli spazi e i caratteri invisibili non contano
        with pytest.raises(BadRequestError):
            candidatura_in(messaggio="  " + "​" * 60 + "breve  ")

    async def test_requisiti_non_visibili_o_di_un_altra_call(self, fondo):
        db, sec = await scenario_wp7()
        for requisiti in ([g.REQ["B"]], [g.REQ_ALTRA["A"]]):
            with pytest.raises(BadRequestError):
                await candida(db, sec, requisiti_dichiarati=requisiti)
        assert db.chiamate("fn_partner_invia_candidatura") == []

    async def test_propria_call_membro_e_solo_invitati(self, fondo):
        db, sec = await scenario_wp7()
        with pytest.raises(AppError) as exc:
            await candida(db, sec, nome="X")
        assert exc.value.code == "stesso_gruppo"
        with pytest.raises(ForbiddenError):
            await svc.invia_candidatura(db, sec, attiva("Y", editable=False), utente("Y"),
                                        g.CALL_GUIDA_ID, candidatura_in())
        with pytest.raises(NotFoundError):  # la call su invito non si rivela
            await candida(db, sec, call=g.CALL_RISERVATA_ID, posizione_id=g.POS_RISERVATA,
                          requisiti_dichiarati=[])

    async def test_seconda_candidatura_attiva(self, fondo):
        db, sec = await scenario_wp7()
        await candida(db, sec)
        with pytest.raises(AppError) as exc:
            await candida(db, sec)
        assert exc.value.code == "candidatura_gia_attiva"

    async def test_rate_limit_anti_abuso(self, fondo, monkeypatch):
        monkeypatch.setenv("PARTNER_CANDIDATURE_LIMITE_GIORNO", "0")
        from app.core.config import get_settings

        get_settings.cache_clear()
        db, sec = await scenario_wp7()
        with pytest.raises(AppError) as exc:
            await candida(db, sec)
        assert (exc.value.status_code, exc.value.code) == (429, "limite_candidature")

    async def test_requisiti_dichiarabili_anche_senza_match(self, fondo):
        """U non passa un filtro rigido (niente match), ma può candidarsi: il
        dettaglio porta comunque gli id dei requisiti visibili da dichiarare,
        con le stesse etichette della proiezione pubblica."""
        db, sec = await scenario_wp7()
        vista = await pcs.dettaglio(db, sec, attiva("U"), utente("U"), g.CALL_GUIDA_ID)
        assert vista.match is None
        dichiarabili = {r.etichetta: str(r.id) for r in vista.requisiti_dichiarabili}
        assert list(dichiarabili) == [r.etichetta for r in vista.requisiti]
        assert dichiarabili["A"] == g.REQ["A"] and g.REQ["B"] not in dichiarabili.values()
        out = await candida(db, sec, nome="U", requisiti_dichiarati=list(dichiarabili.values()))
        assert {r.etichetta for r in out.requisiti_dichiarati} == set(dichiarabili)

    async def test_non_compatibile_senza_motivo(self, fondo):
        """U non passa la regola F (fascia): la candidatura si può mandare, il
        creatore vede solo «non compatibile», mai il perché né la fascia."""
        db, sec = await scenario_wp7()
        await candida(db, sec, nome="U")
        [riga] = db.tabelle["partner_candidature"]
        assert riga["valutazione"] == {"compatibile": False}
        [vista] = (await svc.lista(db, sec, attiva("X"), utente("X"),
                                   direzione="ricevute")).items
        assert (vista.compatibile, vista.valutazione) == (False, None)
        assert "finanziaria" not in vista.model_dump_json()


# ------------------------------------------------------------------ invito


class TestInvito:
    async def test_invito_con_pseudonimo(self, fondo):
        db, sec = await scenario_wp7()
        out = await invita_y(db, sec, messaggio="Ci servirebbe il vostro laboratorio.")
        [riga] = db.tabelle["partner_candidature"]
        assert (riga["tipo"], riga["company_profile_id"], riga["pseudonimo"]) == (
            "invito", g.COMPANY["Y"], pseudo("Y"))
        assert riga["valutazione"]["spiegazione"] == g.SPIEGAZIONE_GUIDA
        [payload] = [c["p_payload"] for c in db.chiamate("fn_partner_invita")]
        assert (payload["max_inviti"], payload["ttl_giorni"]) == (30, 14)
        assert payload["invitato_company_id"] == g.COMPANY["Y"]
        # risposta per X: lo pseudonimo, mai id, codice o nome di Y
        assert (out.lato, out.candidato.pseudonimo, out.candidato.disponibile) == (
            "creatore", pseudo("Y"), True)
        assert out.inviti.model_dump() == {"attivi": 1, "massimo": 30}
        assert out.puo_ritirare is True and out.puo_decidere is False
        _canary(out.model_dump_json(), "Y")
        # Y: notifica con la scadenza e il deep link; email senza il messaggio
        [notifica] = db.righe("notifications", tipo=svc.TIPO_INVITO_RICEVUTO)
        assert notifica["user_id"] == g.OWNER["Y"]
        assert notifica["url"] == f"/app/partenariati/call/{g.CALL_GUIDA_ID}?azienda=" \
                                  f"{g.COMPANY['Y']}"
        assert "scade il" in notifica["corpo"]
        await fondo.esegui()
        [email] = fondo.email
        assert email["to"] == EMAIL["Y"] and "laboratorio" not in email["text"]
        assert "L'invito scade il" in email["text"]
        _canary(email["text"] + email["html"], "X")

    async def test_body_con_company_profile_id_rifiutato(self):
        with pytest.raises(ValidationError):
            svc.InvitoIn(pseudonimo=pseudo("Y"), company_profile_id=g.COMPANY["Y"])

    @pytest.mark.parametrize("valore", [
        "AAAAAAAAAAAAAAAA",  # nessuno
        "non-uno-pseudonimo",
        ("Y", g.CALL_ALTRA_ID),  # di un'altra call
        ("Z", g.CALL_GUIDA_ID),  # esclusa (territorio)
    ])
    async def test_pseudonimo_non_valido_codice_neutro(self, fondo, valore):
        if isinstance(valore, tuple):
            valore = pseudo(*valore)
        db, sec = await scenario_wp7()
        with pytest.raises(AppError) as exc:
            await svc.invita(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
                             svc.InvitoIn(pseudonimo=valore))
        assert (exc.value.status_code, exc.value.code) == (409, "partner_non_disponibile")
        assert db.chiamate("fn_partner_invita") == []

    @pytest.mark.parametrize("modifica", [
        {"visibile_come_partner": False},
        {"accetta_inviti": False},
        {"sospeso_at": "2026-09-30T10:00:00+00:00"},
    ])
    async def test_non_piu_suggeribile_anche_con_indice_fresco(self, fondo, modifica):
        db, sec = await scenario_wp7()
        await partenariato_indice.indice(db, sec)  # indice fresco con Y dentro
        db.una("company_partner_profiles", company_profile_id=g.COMPANY["Y"]).update(modifica)
        with pytest.raises(AppError) as exc:
            await invita_y(db, sec)
        assert exc.value.code == "partner_non_disponibile"
        # lo ferma il ricontrollo live del servizio, prima della RPC
        assert db.chiamate("fn_partner_invita") == []

    async def test_contatti_nel_messaggio_dell_invito(self, fondo):
        db, sec = await scenario_wp7()
        with pytest.raises(AppError) as exc:
            await invita_y(db, sec, messaggio="Chiamaci allo 0961 123456, grazie.")
        assert exc.value.code == "testo_non_conforme"
        with pytest.raises(AppError) as exc:
            await invita_y(db, sec, messaggio="Siamo la Impresa Sintetica X di Catanzaro.")
        assert exc.value.code == "testo_non_conforme"

    async def test_solo_il_creatore_titolare(self, fondo):
        db, sec = await scenario_wp7()
        with pytest.raises(ForbiddenError):
            await svc.invita(db, sec, attiva("X", editable=False), utente("X"), g.CALL_GUIDA_ID,
                             svc.InvitoIn(pseudonimo=pseudo("Y")))
        with pytest.raises(NotFoundError):
            await svc.invita(db, sec, attiva("O"), utente("O"), g.CALL_GUIDA_ID,
                             svc.InvitoIn(pseudonimo=pseudo("Y")))

    async def test_doppio_invito_e_tetto(self, fondo, monkeypatch):
        db, sec = await scenario_wp7()
        await invita_y(db, sec)
        with pytest.raises(AppError) as exc:
            await invita_y(db, sec)
        assert exc.value.code == "invito_gia_attivo"
        monkeypatch.setenv("PARTNER_INVITI_MAX_PER_CALL", "1")
        from app.core.config import get_settings

        get_settings.cache_clear()
        with pytest.raises(AppError) as exc:
            await svc.invita(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
                             svc.InvitoIn(pseudonimo=pseudo("T")))
        assert exc.value.code == "inviti_esauriti_call"

    async def test_suggeriti_con_lo_stato_del_contatto(self, fondo):
        """I suggeriti dicono al creatore se con l'azienda c'è già un contatto
        sulla call (anche dopo aver ricaricato la pagina): in attesa, accettato,
        invito rifiutato; nulla dopo un ritiro o una scadenza."""
        db, sec = await scenario_wp7()

        async def stati():
            out = await pcs.suggeriti(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID)
            return {i.pseudonimo: i.stato_contatto for i in out.items}

        assert await stati() == {pseudo("Y"): None, pseudo("T"): None}
        await invita_y(db, sec)
        await candida(db, sec, nome="T")
        assert await stati() == {pseudo("Y"): "inviata", pseudo("T"): "inviata"}
        invito = db.righe("partner_candidature", tipo="invito")[0]
        candidatura_t = db.righe("partner_candidature", tipo="candidatura")[0]
        await svc.decidi(db, sec, attiva("Y"), utente("Y"), invito["id"], "rifiuta")
        await svc.decidi(db, sec, attiva("X"), utente("X"), candidatura_t["id"], "accetta")
        assert await stati() == {pseudo("Y"): "rifiutata", pseudo("T"): "accettata"}
        _canary(str(await stati()), "Y", "T")

    async def test_suggeriti_invito_ritirato_o_scaduto_si_rinvita(self, fondo):
        db, sec = await scenario_wp7()
        await invita_y(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        riga["scade_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        out = await pcs.suggeriti(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID)
        assert {i.pseudonimo: i.stato_contatto for i in out.items}[pseudo("Y")] is None

    async def test_nessun_reinvito_dopo_il_rifiuto(self, fondo):
        """Il rifiuto di Y vale per la call: un nuovo invito dà il codice neutro e
        non arriva nessuna notifica."""
        db, sec = await scenario_wp7()
        await invita_y(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        await svc.decidi(db, sec, attiva("Y"), utente("Y"), riga["id"], "rifiuta")
        await fondo.azzera()
        with pytest.raises(AppError) as exc:
            await invita_y(db, sec)
        assert (exc.value.status_code, exc.value.code) == (409, "partner_non_disponibile")
        assert len(db.tabelle["partner_candidature"]) == 1
        assert len(db.righe("notifications", tipo=svc.TIPO_INVITO_RICEVUTO)) == 1
        await fondo.esegui()
        assert fondo.email == []

    async def test_invito_nell_indice_apre_la_call_solo_su_invito(self, fondo):
        """Un invito in attesa rende la call solo su invito visibile a Y
        («Per te») e conta nelle sue esposizioni."""
        db, sec = await scenario_wp7()
        call = db.una("partner_calls", id=g.CALL_GUIDA_ID)
        call["visibilita"] = "solo_invitati"
        partenariato_indice.invalida()
        prima = await pcs.per_te(db, sec, attiva("Y"), utente("Y"))
        assert g.CALL_GUIDA_ID not in [str(i.id) for i in prima.items]
        await invita_y(db, sec)
        idx = await partenariato_indice.indice(db, sec)
        assert idx.matching.inviti[g.CALL_GUIDA_ID] == {g.COMPANY["Y"]}
        assert idx.esposizioni.get(g.COMPANY["Y"]) == 1
        dopo = await pcs.per_te(db, sec, attiva("Y"), utente("Y"))
        assert g.CALL_GUIDA_ID in [str(i.id) for i in dopo.items]
        # e il dettaglio: vista pubblica con il proprio invito da decidere
        vista = await pcs.dettaglio(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID)
        assert vista.candidatura.tipo == "invito" and vista.candidatura.puo_decidere is True
        assert "dettagli_riservati" not in vista.model_dump()


# ------------------------------------------------------------- decisioni


class TestDecisioni:
    async def test_accettazione_crea_conversazione_e_audit_senza_rivelazione(self, fondo):
        db, sec = await scenario_wp7()
        await candida(db, sec)
        await fondo.azzera()
        [riga] = db.tabelle["partner_candidature"]
        out = await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "accetta")
        assert out.stato == "accettata" and out.conversazione_id is not None
        [conv] = db.tabelle["partner_conversazioni"]
        assert (conv["company_creatore_id"], conv["company_partner_id"]) == (
            g.COMPANY["X"], g.COMPANY["Y"])
        [chiamata] = db.chiamate("fn_partner_decidi")
        assert chiamata["p_rivela"] is False
        azioni = [a["action"] for a in db.tabelle["audit_log"]]
        assert "partenariato.candidatura_accettata" in azioni
        assert "partenariato.identita_rivelata" not in azioni
        assert "partenariato.contatti_rivelati" not in azioni
        # Y avvisata con il link alla conversazione
        [notifica] = db.righe("notifications", tipo=svc.TIPO_ACCETTATA)
        assert notifica["user_id"] == g.OWNER["Y"]
        assert notifica["url"] == f"/app/partenariati/conversazioni/{conv['id']}?azienda=" \
                                  f"{g.COMPANY['Y']}"
        await fondo.esegui()
        [email] = fondo.email
        assert email["subject"].startswith("La tua candidatura è stata accettata")
        _canary(email["text"] + email["html"], "X")
        # impegno sul bando nell'indice (esclusività)
        idx = await partenariato_indice.indice(db, sec)
        assert g.BANDO_GUIDA in idx.impegni[g.COMPANY["Y"]]

    async def test_vista_controparte_senza_identita(self, fondo):
        db, sec = await scenario_wp7()
        db.una("partner_calls", id=g.CALL_GUIDA_ID)["dettagli_riservati"] = (
            "Il progetto della Impresa Sintetica X riguarda la linea pilota di Catanzaro.")
        await candida(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "accetta")
        vista = await pcs.dettaglio(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID)
        corpo = vista.model_dump(mode="json")
        assert corpo["vista"] == "controparte" and corpo["identita_rivelata"] is False
        assert corpo["identita"] is None
        assert corpo["budget_progetto_eur"] == "3100000.00"
        assert "linea pilota" in corpo["dettagli_riservati"]
        assert "Sintetica X" not in corpo["dettagli_riservati"]  # nessuna identità
        assert corpo["creatore"]["denominazione"] == "Azienda anonima"
        assert corpo["candidatura"]["conversazione_id"] == str(riga["conversazione_id"])
        testo = vista.model_dump_json()
        for valore in (g.COMPANY["X"], g.OWNER["X"], PIVA["X"], EMAIL["X"]):
            assert valore not in testo

    async def test_rivelazione_con_entrambe_verificate_identita_e_audit(self, fondo):
        db, sec = await scenario_wp7()
        db.verifica_identita(g.COMPANY["X"])
        db.verifica_identita(g.COMPANY["Y"])
        db.una("profiles", id=g.OWNER["X"]).update(nome="Carla", cognome="Neri")
        db.una("partner_calls", id=g.CALL_GUIDA_ID)["dettagli_riservati"] = (
            "Il progetto della Impresa Sintetica X riguarda la linea pilota di Catanzaro.")
        db.una("company_data", company_profile_id=g.COMPANY["X"])["raw"].update(
            pec="impresa.x@pec.example.test")
        await candida(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "accetta")
        assert db.chiamate("fn_partner_decidi")[0]["p_rivela"] is True
        azioni = [a["action"] for a in db.tabelle["audit_log"]]
        assert azioni.count("partenariato.identita_rivelata") == 1
        assert azioni.count("partenariato.contatti_rivelati") == 1
        vista = await pcs.dettaglio(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID)
        assert vista.identita_rivelata is True
        assert vista.identita.ragione_sociale == RAGIONE["X"].upper()
        assert vista.identita.pec == "impresa.x@pec.example.test"
        assert (vista.identita.referente_nome, vista.identita.referente_ruolo) == (
            "Carla Neri", "titolare")
        assert "Sintetica X" in vista.dettagli_riservati
        assert EMAIL["X"] not in vista.model_dump_json()  # mai l'email del referente

    async def test_verifiche_successive_non_toccano_le_accettazioni_precedenti(self, fondo):
        """Accettazione senza verifiche (nessun audit di rivelazione): verificare
        dopo le due aziende non rivela nulla su quella candidatura."""
        db, sec = await scenario_wp7()
        await candida(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "accetta")
        db.verifica_identita(g.COMPANY["X"])
        db.verifica_identita(g.COMPANY["Y"])
        vista = await pcs.dettaglio(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID)
        assert vista.identita is None and vista.identita_rivelata is False

    @pytest.mark.parametrize("verificata", ["X", "Y"])
    async def test_una_sola_verificata_niente_rivelazione_canary_identita(self, fondo, verificata):
        """Rivelazione simmetrica: con una sola azienda verificata nessuna delle
        due vede l'identità dell'altra (vista controparte, chat), la RPC non
        riceve p_rivela e non c'è audit di rivelazione."""
        db, sec = await scenario_wp7()
        db.verifica_identita(g.COMPANY[verificata])
        db.una("partner_calls", id=g.CALL_GUIDA_ID)["dettagli_riservati"] = (
            "Il progetto della Impresa Sintetica X riguarda la linea pilota di Catanzaro.")
        await candida(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "accetta")
        assert db.chiamate("fn_partner_decidi")[0]["p_rivela"] is False
        azioni = [a["action"] for a in db.tabelle["audit_log"]]
        assert "partenariato.identita_rivelata" not in azioni
        assert "partenariato.contatti_rivelati" not in azioni
        vista_y = await pcs.dettaglio(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID)
        assert vista_y.identita is None and vista_y.identita_rivelata is False
        _canary_identita(vista_y.model_dump_json(), "X")
        [conv] = db.tabelle["partner_conversazioni"]
        chat_x = await chat.dettaglio(db, sec, attiva("X"), utente("X"), conv["id"])
        chat_y = await chat.dettaglio(db, sec, attiva("Y"), utente("Y"), conv["id"])
        assert chat_x.identita is None and chat_y.identita is None
        _canary_identita(chat_x.model_dump_json(), "Y")
        _canary_identita(chat_y.model_dump_json(), "X")

    @pytest.mark.parametrize("revocata", ["X", "Y"])
    async def test_revoca_spegne_la_rivelazione_nelle_viste_future(self, fondo, revocata):
        """Rivelata con entrambe verificate; revocata dopo la verifica di una
        delle due, le viste successive non mostrano più l'identità di nessuna
        (gli audit restano). Con una nuova verifica tornano a mostrarla."""
        db, sec = await scenario_wp7()
        db.verifica_identita(g.COMPANY["X"])
        db.verifica_identita(g.COMPANY["Y"])
        await candida(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "accetta")
        [conv] = db.tabelle["partner_conversazioni"]
        assert (await chat.dettaglio(db, sec, attiva("X"), utente("X"), conv["id"])).identita
        vista = await pcs.dettaglio(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID)
        assert vista.identita.ragione_sociale == RAGIONE["X"].upper()
        db.revoca_identita(g.COMPANY[revocata])
        vista = await pcs.dettaglio(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID)
        assert vista.identita is None and vista.identita_rivelata is False
        _canary_identita(vista.model_dump_json(), "X")
        for nome, altra in (("X", "Y"), ("Y", "X")):
            dettaglio = await chat.dettaglio(db, sec, attiva(nome), utente(nome), conv["id"])
            assert dettaglio.identita is None and dettaglio.identita_rivelata is False
            _canary_identita(dettaglio.model_dump_json(), altra)
        azioni = [a["action"] for a in db.tabelle["audit_log"]]
        assert azioni.count("partenariato.identita_rivelata") == 1  # l'audit resta
        db.verifica_identita(g.COMPANY[revocata])
        vista = await pcs.dettaglio(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID)
        assert vista.identita_rivelata is True

    async def test_interruttore_spento_nessuna_rivelazione(self, fondo, monkeypatch):
        monkeypatch.setattr(pps, "RIVELAZIONE_IDENTITA_DISPONIBILE", False)
        db, sec = await scenario_wp7()
        db.verifica_identita(g.COMPANY["X"])
        db.verifica_identita(g.COMPANY["Y"])
        await candida(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "accetta")
        assert db.chiamate("fn_partner_decidi")[0]["p_rivela"] is False
        assert db.chiamate("fn_partenariato_identita_forte") == []
        vista = await pcs.dettaglio(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID)
        assert vista.identita is None

    async def test_verifica_non_leggibile_alla_decisione_502_senza_decidere(self, fondo):
        db, sec = await scenario_wp7()
        db.rpc_guasti["fn_partenariato_identita_forte"] = errore("errore_interno")
        await candida(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        with pytest.raises(AppError) as exc:
            await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "accetta")
        assert (exc.value.status_code, exc.value.code) == (502, "upstream_error")
        assert db.chiamate("fn_partner_decidi") == [] and riga["stato"] == "inviata"
        # il rifiuto non legge le verifiche
        await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "rifiuta")
        assert riga["stato"] == "rifiutata"

    async def test_verifica_non_leggibile_nelle_viste_niente_identita(self, fondo):
        db, sec = await scenario_wp7()
        db.verifica_identita(g.COMPANY["X"])
        db.verifica_identita(g.COMPANY["Y"])
        await candida(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "accetta")
        db.rpc_guasti["fn_partenariato_identita_forte"] = errore("errore_interno")
        vista = await pcs.dettaglio(db, sec, attiva("Y"), utente("Y"), g.CALL_GUIDA_ID)
        assert vista.identita is None
        _canary_identita(vista.model_dump_json(), "X")

    async def test_lato_sbagliato_404_e_doppia_decisione(self, fondo):
        db, sec = await scenario_wp7()
        await candida(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        with pytest.raises(NotFoundError):  # Y non decide la propria candidatura
            await svc.decidi(db, sec, attiva("Y"), utente("Y"), riga["id"], "accetta")
        with pytest.raises(NotFoundError):  # un terzo
            await svc.decidi(db, sec, attiva("O"), utente("O"), riga["id"], "accetta")
        with pytest.raises(NotFoundError):  # id malformato
            await svc.decidi(db, sec, attiva("X"), utente("X"), "non-un-id", "accetta")
        with pytest.raises(ForbiddenError):  # il membro legge soltanto
            await svc.decidi(db, sec, attiva("X", editable=False), utente("X"), riga["id"],
                             "accetta")
        await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "rifiuta")
        with pytest.raises(AppError) as exc:
            await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "accetta")
        assert exc.value.code == "candidatura_gia_decisa"

    async def test_rifiuto_con_motivo(self, fondo):
        db, sec = await scenario_wp7()
        await candida(db, sec)
        await fondo.azzera()
        [riga] = db.tabelle["partner_candidature"]
        with pytest.raises(AppError) as exc:
            await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "rifiuta",
                             svc.RifiutoIn(motivo="Scrivici a info@esempio.it"))
        assert exc.value.code == "testo_non_conforme"
        out = await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "rifiuta",
                               svc.RifiutoIn(motivo="Abbiamo già coperto la posizione."))
        assert out.stato == "rifiutata" and out.conversazione_id is None
        [notifica] = db.righe("notifications", tipo=svc.TIPO_RIFIUTATA)
        assert "già coperto" not in (notifica["corpo"] or "")  # il motivo non esce
        # deep link alla sotto-lista in cui Y trova la propria candidatura
        percorso = (f"/app/partenariati?vista=candidature&direzione=inviate"
                    f"&azienda={g.COMPANY['Y']}")
        assert notifica["url"] == percorso
        await fondo.esegui()
        [email] = fondo.email
        assert "già coperto" not in email["text"]
        assert percorso.replace("&", "&amp;") in email["html"] or percorso in email["text"]
        [vista_y] = (await svc.lista(db, sec, attiva("Y"), utente("Y"),
                                     direzione="inviate")).items
        assert vista_y.motivo_rifiuto == "Abbiamo già coperto la posizione."

    async def test_invito_accettato_da_y_avvisa_x_con_lo_pseudonimo(self, fondo):
        db, sec = await scenario_wp7()
        await invita_y(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        with pytest.raises(NotFoundError):  # X non decide il proprio invito
            await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "accetta")
        await fondo.azzera()
        out = await svc.decidi(db, sec, attiva("Y"), utente("Y"), riga["id"], "accetta")
        assert out.stato == "accettata" and out.lato == "partner"
        await fondo.esegui()
        assert {e["to"] for e in fondo.email} == {EMAIL["X"], EMAIL_MEMBRO_X}
        assert all(pseudo("Y") in e["text"] for e in fondo.email)

    async def test_invito_rifiutato_deep_link_agli_inviti_mandati(self, fondo):
        db, sec = await scenario_wp7()
        await invita_y(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        await fondo.azzera()
        await svc.decidi(db, sec, attiva("Y"), utente("Y"), riga["id"], "rifiuta")
        notifiche = db.righe("notifications", tipo=svc.TIPO_RIFIUTATA)
        assert {n["user_id"] for n in notifiche} == {g.OWNER["X"], MEMBRO_X}
        percorso = (f"/app/partenariati/call/{g.CALL_GUIDA_ID}?tab=candidature"
                    f"&direzione=inviate&azienda={g.COMPANY['X']}")
        assert all(n["url"] == percorso for n in notifiche)
        await fondo.esegui()
        assert fondo.email and all(percorso in e["text"] for e in fondo.email)

    async def test_invito_scaduto_non_ancora_marcato(self, fondo):
        """Decisione su un invito oltre la scadenza che nessuno ha ancora
        marcato (niente lista prima): la RPC risponde `invito_scaduto`."""
        db, sec = await scenario_wp7()
        await invita_y(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        riga["scade_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        for decisione in ("accetta", "rifiuta"):
            with pytest.raises(AppError) as exc:
                await svc.decidi(db, sec, attiva("Y"), utente("Y"), riga["id"], decisione)
            assert (exc.value.status_code, exc.value.code, exc.value.message) == (
                409, "invito_scaduto", "L'invito è scaduto")
        assert riga["stato"] == "inviata"
        with pytest.raises(AppError) as exc:  # nemmeno X lo ritira più
            await svc.ritira(db, sec, attiva("X"), utente("X"), riga["id"])
        assert (exc.value.status_code, exc.value.code) == (409, "invito_scaduto")

    async def test_invito_scaduto_marcato_dalla_lista(self, fondo):
        """La lista marca gli inviti scaduti (scadenza pigra): dopo, la
        decisione trova la riga già chiusa."""
        db, sec = await scenario_wp7()
        await invita_y(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        riga["scade_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        [vista] = (await svc.lista(db, sec, attiva("Y"), utente("Y"),
                                   direzione="ricevute")).items
        assert vista.stato == "scaduta" and vista.puo_decidere is False
        assert (riga["stato"], riga["motivo_chiusura"]) == ("scaduta", "ttl")
        with pytest.raises(AppError) as exc:
            await svc.decidi(db, sec, attiva("Y"), utente("Y"), riga["id"], "accetta")
        assert (exc.value.status_code, exc.value.code) == (409, "candidatura_gia_decisa")

    async def test_accettata_su_call_annullata_non_impegna(self, fondo):
        """Y accettata sulla call esclusiva di X; X annulla la call: sul bando Y
        torna libera, per la RPC e per l'indice (niente impegno)."""
        db, sec = await scenario_wp7()
        db.una("partner_calls", id=g.CALL_GUIDA_ID)["esclusivita"] = True
        await candida(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "accetta")
        idx = await partenariato_indice.indice(db, sec)
        assert g.BANDO_GUIDA in idx.impegni[g.COMPANY["Y"]]
        assert g.BANDO_GUIDA in idx.impegni_esclusivi[g.COMPANY["Y"]]
        db.una("partner_calls", id=g.CALL_GUIDA_ID).update(
            stato="chiusa_annullata", chiusa_at=datetime.now(timezone.utc).isoformat(),
            motivo_chiusura="creatore_annullata")
        partenariato_indice.invalida()
        idx = await partenariato_indice.indice(db, sec)
        assert g.BANDO_GUIDA not in idx.impegni.get(g.COMPANY["Y"], frozenset())
        assert g.BANDO_GUIDA not in idx.impegni_esclusivi.get(g.COMPANY["Y"], frozenset())

    async def test_esclusivita_violata(self, fondo):
        db, sec = await scenario_wp7()
        db.una("partner_calls", id=g.CALL_GUIDA_ID)["esclusivita"] = True
        # Y ha già una call pubblicata sullo stesso bando
        seconda = copy.deepcopy(db.una("partner_calls", id=g.CALL_ALTRA_ID))
        seconda.update(id=str(uuid.uuid4()), company_profile_id=g.COMPANY["Y"],
                       family_parent_id=g.OWNER["Y"], bando_id=g.BANDO_GUIDA)
        db.tabelle["partner_calls"].append(seconda)
        await candida(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        with pytest.raises(AppError) as exc:
            await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "accetta")
        assert exc.value.code == "esclusivita_violata"


# ------------------------------------------------------------ ritiro e liste


class TestRitiroEListe:
    async def test_ritiro_dal_mittente(self, fondo):
        db, sec = await scenario_wp7()
        await candida(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        with pytest.raises(NotFoundError):  # il creatore non ritira la candidatura di Y
            await svc.ritira(db, sec, attiva("X"), utente("X"), riga["id"])
        out = await svc.ritira(db, sec, attiva("Y"), utente("Y"), riga["id"])
        assert out.stato == "ritirata" and out.puo_ritirare is False
        await invita_y(db, sec)
        invito = db.righe("partner_candidature", tipo="invito")[0]
        with pytest.raises(NotFoundError):  # l'invitata rifiuta, non ritira
            await svc.ritira(db, sec, attiva("Y"), utente("Y"), invito["id"])
        assert (await svc.ritira(db, sec, attiva("X"), utente("X"), invito["id"])).stato == \
            "ritirata"

    async def test_liste_per_lato_advisor_e_membro(self, fondo):
        db, sec = await scenario_wp7()
        await candida(db, sec)
        await svc.invita(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
                         svc.InvitoIn(pseudonimo=pseudo("T")))
        ricevute_x = await svc.lista(db, sec, attiva("X"), utente("X"), direzione="ricevute")
        inviate_x = await svc.lista(db, sec, attiva("X"), utente("X"), direzione="inviate")
        assert [(i.tipo, i.lato) for i in ricevute_x.items] == [("candidatura", "creatore")]
        assert [(i.tipo, i.lato) for i in inviate_x.items] == [("invito", "creatore")]
        y = ricevute_x.items[0]
        assert y.candidato.pseudonimo == pseudo("Y") and y.messaggio == MESSAGGIO_Y
        assert y.valutazione.copertura.coperti == 2 and y.puo_decidere is True
        _canary(ricevute_x.model_dump_json() + inviate_x.model_dump_json(), "Y", "T")
        inviate_y = await svc.lista(db, sec, attiva("Y"), utente("Y"), direzione="inviate")
        assert [i.lato for i in inviate_y.items] == ["partner"]
        _canary(inviate_y.model_dump_json(), "X")
        # membro di X: legge, ma non può decidere
        membro = await svc.lista(db, sec, attiva("X", editable=False), {"id": MEMBRO_X},
                                 direzione="ricevute")
        assert membro.items[0].puo_decidere is False
        # Advisor: la seconda azienda dell'owner di Y non vede le righe di Y
        riga = copy.deepcopy(db.una("company_profiles", id=g.COMPANY["Y"]))
        riga.update(id=COMPANY_Y2, partita_iva="30000000001")
        db.tabelle["company_profiles"].append(riga)
        y2 = attiva("Y", company=COMPANY_Y2)
        assert (await svc.lista(db, sec, y2, utente("Y"), direzione="inviate")).items == []
        with pytest.raises(NotFoundError):
            await svc.dettaglio(db, sec, y2, utente("Y"), y.id)
        # filtri
        assert (await svc.lista(db, sec, attiva("X"), utente("X"), direzione="ricevute",
                                stato="accettata")).total == 0

    async def test_testi_del_creatore_senza_i_suoi_identificativi(self, fondo):
        """Difesa in profondità: se titolo della call, titolo della posizione ed
        etichetta di un requisito contengono (per esempio dopo un cambio della
        ragione sociale) gli identificativi del creatore, verso il partner
        escono ripuliti, nelle candidature e nelle conversazioni; il creatore
        li vede com'erano."""
        db, sec = await scenario_wp7()
        await candida(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        await svc.decidi(db, sec, attiva("X"), utente("X"), riga["id"], "accetta")
        nome = "Impresa Sintetica X"
        db.una("partner_calls", id=g.CALL_GUIDA_ID)["titolo"] = f"Cerchiamo partner per {nome}"
        db.una("partner_call_posizioni", id=g.POS_P1)["titolo"] = f"Laboratorio per {nome}"
        db.una("partner_call_requisiti", id=g.REQ["A"])["etichetta"] = f"A {nome}"
        [vista_y] = (await svc.lista(db, sec, attiva("Y"), utente("Y"),
                                     direzione="inviate")).items
        testo = vista_y.model_dump_json()
        assert nome.lower() not in testo.lower() and PIVA["X"] not in testo
        assert vista_y.call.titolo.startswith("Cerchiamo partner per")
        conversazioni = await chat.lista_conversazioni(db, sec, attiva("Y"), utente("Y"))
        assert nome.lower() not in conversazioni.model_dump_json().lower()
        dettaglio = await chat.dettaglio(db, sec, attiva("Y"), utente("Y"),
                                         riga["conversazione_id"])
        assert nome.lower() not in (dettaglio.call.titolo or "").lower()
        # il creatore vede i propri testi com'erano
        [vista_x] = (await svc.lista(db, sec, attiva("X"), utente("X"),
                                     direzione="ricevute")).items
        assert vista_x.call.titolo == f"Cerchiamo partner per {nome}"

    async def test_dettaglio_del_creatore_con_profilo_pubblico(self, fondo):
        db, sec = await scenario_wp7()
        out = await candida(db, sec)
        dettaglio = await svc.dettaglio(db, sec, attiva("X"), utente("X"), out.id)
        profilo = dettaglio.candidato.profilo
        assert profilo is not None and profilo.anonimo is True and profilo.denominazione is None
        assert "codice_pubblico" not in profilo.model_dump()
        assert profilo.fasce.fatturato == "2m_10m"
        _canary(dettaglio.model_dump_json(), "Y")

    async def test_revoca_dell_opt_in_candidata_non_piu_disponibile(self, fondo):
        db, sec = await scenario_wp7()
        out = await candida(db, sec)
        # la revoca (il trigger della 0039 chiude i pendenti; qui conta la proiezione)
        db.una("company_partner_profiles", company_profile_id=g.COMPANY["Y"])[
            "visibile_come_partner"] = False
        vista = await svc.dettaglio(db, sec, attiva("X"), utente("X"), out.id)
        assert vista.candidato.disponibile is False and vista.candidato.profilo is None
        assert (vista.valutazione, vista.compatibile, vista.messaggio) == (None, None, None)
        assert vista.requisiti_dichiarati == [] and "2m_10m" not in vista.model_dump_json()


# ------------------------------------------------------------ riepilogo e scheduler


class TestRiepilogoEScheduler:
    async def test_riepilogo(self, fondo):
        db, sec = await scenario_wp7()
        await candida(db, sec)
        await svc.invita(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID,
                         svc.InvitoIn(pseudonimo=pseudo("T")))
        x = await pcs.riepilogo_partenariati(db, sec, attiva("X"), utente("X"))
        t = await pcs.riepilogo_partenariati(db, sec, attiva("T"), utente("T"))
        assert (x.candidature_da_decidere, x.inviti_ricevuti, x.messaggi_non_letti) == (1, 0, 0)
        assert (t.candidature_da_decidere, t.inviti_ricevuti) == (0, 1)

    async def test_scheduler_scadenza_inviti(self, fondo):
        db, sec = await scenario_wp7()
        await invita_y(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        riga["scade_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        primo = await partenariato_indice.indice(db, sec)
        assert await partenariati_scheduler.scadenza_inviti(db) == 1
        assert riga["stato"] == "scaduta" and riga["motivo_chiusura"] == "ttl"
        assert await partenariato_indice.indice(db, sec) is not primo  # invalidato
        assert await partenariati_scheduler.scadenza_inviti(db) == 0


class TestNominativoVersoTerzi:
    """WP9: un profilo salvato come nominativo mostra il nome ai terzi
    (suggeriti del creatore, dettaglio della candidatura) solo se OGGI
    l'azienda ha l'identità verificata dalla piattaforma; una verifica
    revocata lo spegne nelle viste successive."""

    async def _viste(self, db, sec):
        sugg = await pcs.suggeriti(db, sec, attiva("X"), utente("X"), g.CALL_GUIDA_ID)
        [cand] = db.righe("partner_candidature", company_profile_id=g.COMPANY["Y"])
        dettaglio = await svc.dettaglio(db, sec, attiva("X"), utente("X"), cand["id"])
        voce = next(i for i in sugg.items if i.pseudonimo == pseudo("Y"))
        self.fasce_match = voce.match.fasce
        return voce.profilo, dettaglio.candidato.profilo

    async def test_nome_solo_con_la_verifica_di_oggi(self, fondo):
        db, sec = await scenario_wp7()
        db.una("company_partner_profiles", company_profile_id=g.COMPANY["Y"])["anonimo"] = False
        await candida(db, sec)
        def solo_fatturato(fasce) -> bool:
            return fasce is None or all(
                v is None for k, v in fasce.model_dump().items() if k != "fatturato")

        for profilo in await self._viste(db, sec):
            assert profilo.anonimo is True and profilo.denominazione is None
            _canary_identita(profilo.model_dump_json(), "Y")
        # anche il match verso il creatore resta quello di un anonimo (Q12)
        assert solo_fatturato(self.fasce_match)
        db.verifica_identita(g.COMPANY["Y"])
        for profilo in await self._viste(db, sec):
            assert profilo.anonimo is False
            assert profilo.denominazione == RAGIONE["Y"].upper()
        assert not solo_fatturato(self.fasce_match)
        db.revoca_identita(g.COMPANY["Y"])
        for profilo in await self._viste(db, sec):
            assert profilo.anonimo is True and profilo.denominazione is None
            _canary_identita(profilo.model_dump_json(), "Y")
        assert solo_fatturato(self.fasce_match)

    @staticmethod
    def _solo_fatturato(fasce) -> bool:
        return fasce is None or all(
            v is None for k, v in fasce.model_dump().items() if k != "fatturato")

    async def test_valutazione_salvata_di_un_nominativo_non_verificato(self, fondo):
        """Profilo salvato come nominativo, identità non verificata oggi: la
        valutazione salvata con la candidatura e con l'invito è quella di un
        anonimo (Q12: la sola fascia di fatturato)."""
        db, sec = await scenario_wp7()
        db.una("company_partner_profiles", company_profile_id=g.COMPANY["Y"])["anonimo"] = False
        await candida(db, sec)
        await svc.ritira(db, sec, attiva("Y"), utente("Y"),
                         db.tabelle["partner_candidature"][0]["id"])
        await invita_y(db, sec)
        righe = db.tabelle["partner_candidature"]
        assert {r["tipo"] for r in righe} == {"candidatura", "invito"}
        for riga in righe:
            assert riga["valutazione"]["fasce"] == {
                "fatturato": "2m_10m", "patrimonio_netto": None, "dipendenti": None,
                "trend": None}, riga["tipo"]
        [payload] = [c["p_payload"] for c in db.chiamate("fn_partner_invita")]
        assert self._solo_fatturato(svc.MatchOut.model_validate(payload["valutazione"]).fasce)

    async def test_revoca_riduce_la_valutazione_gia_salvata(self, fondo):
        """Candidatura mandata da verificata (tutte le fasce salvate): dopo la
        revoca della verifica il creatore vede, nelle viste successive, la
        sola fascia di fatturato; con la verifica di nuovo, di nuovo tutte."""
        db, sec = await scenario_wp7()
        db.una("company_partner_profiles", company_profile_id=g.COMPANY["Y"])["anonimo"] = False
        db.verifica_identita(g.COMPANY["Y"])
        await candida(db, sec)
        [riga] = db.tabelle["partner_candidature"]
        assert not self._solo_fatturato(svc.MatchOut.model_validate(riga["valutazione"]).fasce)

        async def viste():
            dettaglio = await svc.dettaglio(db, sec, attiva("X"), utente("X"), riga["id"])
            pagina = await svc.lista(db, sec, attiva("X"), utente("X"), direzione="ricevute")
            [voce] = pagina.items
            return dettaglio.valutazione, voce.valutazione

        for valutazione in await viste():
            assert not self._solo_fatturato(valutazione.fasce)
        db.revoca_identita(g.COMPANY["Y"])
        for valutazione in await viste():
            assert self._solo_fatturato(valutazione.fasce)
            assert valutazione.fasce.fatturato == "2m_10m"
            assert valutazione.copertura.coperti == 2  # il resto resta
        # Y che torna anonima da sé: stesse fasce ridotte
        db.verifica_identita(g.COMPANY["Y"])
        db.una("company_partner_profiles", company_profile_id=g.COMPANY["Y"])["anonimo"] = True
        for valutazione in await viste():
            assert self._solo_fatturato(valutazione.fasce)

    async def test_profilo_anonimo_non_legge_le_verifiche(self, fondo):
        db, sec = await scenario_wp7()
        db.verifica_identita(g.COMPANY["Y"])
        await candida(db, sec)
        db.rpcs.clear()
        for profilo in await self._viste(db, sec):
            assert profilo.anonimo is True
        assert db.chiamate("fn_partenariato_identita_forte") == []
