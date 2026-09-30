"""Notifiche proattive, call seguite e digest settimanale (WP6, M5, M6, Q6).

Fan-out alla pubblicazione (claim una volta, top-K, soglia, tetto
settimanale, dedup, ricontrollo live, niente call solo su invito, nessun dato
di chi ha creato la call, completamento solo senza errori, ripresa dallo
scheduler); destinatari (titolare e membri con visibilità); notifiche a chi
segue una call; digest (solo aziende con opt-in, `filtra_recapitabili`,
opt-out, ledger at-most-once, header RFC 8058, nessun contenuto → nessuna
email, invii interrotti → incerti); impostazioni e disiscrizione per token;
passi dello scheduler. Primario finto di `test_partenariato_indice`."""

import copy
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services import (
    bando_alert_service,
    email_service,
    partenariato_candidature_service,
    partenariato_indice,
)
from app.services import partenariati_scheduler as sched
from app.services import partenariato_notifiche as pn
from app.services.partenariato_accesso import pseudonimo
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariato_indice import (  # noqa: F401 — fixture autouse
    EMAIL,
    PIVA,
    RAGIONE,
    T0,
    FakePrimary,
    ambiente_wp6,
    errore_pg,
    scenario_guida,
)

MEMBRO_Y = "b0000000-0000-4000-8000-000000000001"
MEMBRO_Y_SENZA = "b0000000-0000-4000-8000-000000000002"
MEMBRO_Y_INATTIVO = "b0000000-0000-4000-8000-000000000003"
BANDO_GUIDA_TITOLO = g.CALL_GUIDA["bando_titolo"]
ROMA = ZoneInfo("Europe/Rome")


def imposta(monkeypatch, **valori):
    from app.core.config import get_settings

    for chiave, valore in valori.items():
        monkeypatch.setenv(chiave, str(valore))
    get_settings.cache_clear()


def con_membri(db: FakePrimary) -> FakePrimary:
    """Y ha un membro con visibilità, uno senza e uno con il profilo non
    attivo."""
    for member, email, attivo in ((MEMBRO_Y, "membro.y@example.test", True),
                                  (MEMBRO_Y_SENZA, "senza.y@example.test", True),
                                  (MEMBRO_Y_INATTIVO, "inattivo.y@example.test", False)):
        db.tabelle["profiles"].append({"id": member, "email": email, "is_active": attivo})
        db.tabelle.setdefault("family_members", []).append({
            "id": f"fm-{member}", "parent_id": g.OWNER["Y"], "member_id": member,
            "status": "active"})
    for member in (MEMBRO_Y, MEMBRO_Y_INATTIVO):
        db.tabelle.setdefault("family_member_company_access", []).append(
            {"family_member_id": f"fm-{member}", "company_profile_id": g.COMPANY["Y"]})
    return db


def notifiche(db, tipo=pn.TIPO_PER_TE) -> list[dict]:
    return [n for n in db.tabelle.get("notifications", []) if n["tipo"] == tipo]


# ------------------------------------------------------------ destinatari


async def test_destinatari_titolare_e_membri_con_visibilita():
    db, _ = await scenario_guida()
    con_membri(db)
    destinatari = await pn.destinatari_azienda(db, g.COMPANY["Y"])
    assert {d["id"] for d in destinatari} == {g.OWNER["Y"], MEMBRO_Y}
    per_azienda = await pn.destinatari_per_azienda(db, [g.COMPANY["Y"], g.COMPANY["T"]])
    assert {d["id"] for d in per_azienda[g.COMPANY["T"]]} == {g.OWNER["T"]}


# ---------------------------------------------------------------- fan-out


async def test_fan_out_della_guida():
    db, sec = await scenario_guida()
    con_membri(db)
    esito = await pn.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    assert esito == {"claim": True, "candidati": 2, "notificate": 2, "riconsegnate": 0,
                     "saltate": 0, "errori": 0, "rinviato": False}
    ricevute = notifiche(db)
    assert {(n["user_id"], n["company_profile_id"]) for n in ricevute} == {
        (g.OWNER["Y"], g.COMPANY["Y"]), (MEMBRO_Y, g.COMPANY["Y"]),
        (g.OWNER["T"], g.COMPANY["T"])}
    y = next(n for n in ricevute if n["user_id"] == g.OWNER["Y"])
    assert y["titolo"] == "Nuova call di partenariato per te"
    assert y["corpo"] == f"Copri 2 requisiti mancanti per il bando «{BANDO_GUIDA_TITOLO}»."
    assert y["url"] == f"/app/partenariati/call/{g.CALL_GUIDA_ID}?azienda={g.COMPANY['Y']}"
    assert y["dedup_key"] == f"partner-per-te:{g.CALL_GUIDA_ID}:{g.COMPANY['Y']}"
    # nessun dato di chi ha creato la call
    testo = repr(ricevute)
    for canary in (RAGIONE["X"], RAGIONE["X"].upper(), PIVA["X"], g.COMPANY["X"],
                   g.OWNER["X"], g.CODICE_PUBBLICO["X"], g.CALL_GUIDA["titolo"]):
        assert canary not in testo
    righe = db.righe("partner_notifiche_proattive", partner_call_id=g.CALL_GUIDA_ID)
    assert {(r["company_profile_id"], r["copertura"], r["punteggio"]) for r in righe} == {
        (g.COMPANY["Y"], 2, g.PUNTEGGIO_Y), (g.COMPANY["T"], 2, g.PUNTEGGIO_T)}
    assert all(date.fromisoformat(r["settimana"]).isoweekday() == 1 for r in righe)
    assert db.una("partner_calls", id=g.CALL_GUIDA_ID)["fanout_completato_at"]


async def test_fan_out_una_volta_sola():
    db, sec = await scenario_guida()
    await pn.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    prima = len(db.tabelle["notifications"])
    esito = await pn.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    assert esito["claim"] is False and len(db.tabelle["notifications"]) == prima


async def test_top_k(monkeypatch):
    imposta(monkeypatch, PARTENARIATO_NOTIFICHE_TOP_K=1)
    db, sec = await scenario_guida()
    esito = await pn.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    assert esito["notificate"] == 1
    assert {n["company_profile_id"] for n in notifiche(db)} == {g.COMPANY["Y"]}


async def test_soglia_del_punteggio(monkeypatch):
    imposta(monkeypatch, PARTENARIATO_NOTIFICHE_SOGLIA=g.PUNTEGGIO_T + 1)
    db, sec = await scenario_guida()
    await pn.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    assert {n["company_profile_id"] for n in notifiche(db)} == {g.COMPANY["Y"]}


async def test_nessun_limite_per_owner_sulle_notifiche(monkeypatch):
    """Il limite di 2 aziende dello stesso owner vale per la lista mostrata
    al creatore, non per chi riceve la notifica (WP7): con tre aziende dello
    stesso owner la notifica arriva alla terza, l'unica sopra la soglia,
    anche se le prime due nell'ordine non la superano."""
    from app.services import partenariato_matching as pm
    from tests.test_partenariato_matching import _m

    imposta(monkeypatch, PARTENARIATO_NOTIFICHE_SOGLIA=50)
    match = {
        g.COMPANY["Y"]: _m(g.COMPANY["Y"], "advisor", 3, 40, call_id=g.CALL_GUIDA_ID),
        g.COMPANY["T"]: _m(g.COMPANY["T"], "advisor", 3, 45, call_id=g.CALL_GUIDA_ID),
        g.COMPANY["U"]: _m(g.COMPANY["U"], "advisor", 2, 80, call_id=g.CALL_GUIDA_ID),
    }
    monkeypatch.setattr(pm, "valuta_coppia", lambda call, cand, **_: match.get(cand.company_id))
    db, sec = await scenario_guida()
    esito = await pn.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    assert esito["notificate"] == 1
    assert {n["company_profile_id"] for n in notifiche(db)} == {g.COMPANY["U"]}


async def test_tetto_settimanale_per_azienda(monkeypatch):
    imposta(monkeypatch, PARTENARIATO_NOTIFICHE_TETTO_SETTIMANA=2)
    db, sec = await scenario_guida()
    for n in range(2):
        db.inserisci("partner_notifiche_proattive", {
            "company_profile_id": g.COMPANY["Y"], "partner_call_id": f"altra-{n}",
            "settimana": pn.lunedi().isoformat(), "copertura": 1, "punteggio": 60})
    esito = await pn.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    assert (esito["notificate"], esito["saltate"]) == (1, 1)
    assert {n["company_profile_id"] for n in notifiche(db)} == {g.COMPANY["T"]}
    [claim_y] = [p for p in db.chiamate("fn_partner_claim_notifica")
                 if p["p_company"] == g.COMPANY["Y"]]
    assert claim_y["p_tetto"] == 2 and claim_y["p_copertura"] == 2


async def test_dedup_per_azienda_e_call_anche_su_settimane_diverse():
    # Y era già stata reclamata e avvisata da un giro precedente (un'altra
    # settimana): nessuna riga nuova, nessun posto del tetto, nessun doppione.
    db, sec = await scenario_guida()
    db.inserisci("partner_notifiche_proattive", {
        "company_profile_id": g.COMPANY["Y"], "partner_call_id": g.CALL_GUIDA_ID,
        "settimana": "2026-09-21", "copertura": 2, "punteggio": 90})
    db.inserisci("notifications", {
        "user_id": g.OWNER["Y"], "tipo": pn.TIPO_PER_TE, "titolo": "t", "corpo": "c",
        "url": "u", "company_profile_id": g.COMPANY["Y"],
        "dedup_key": f"partner-per-te:{g.CALL_GUIDA_ID}:{g.COMPANY['Y']}"})
    esito = await pn.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    assert (esito["notificate"], esito["riconsegnate"]) == (1, 1)
    assert len(db.righe("partner_notifiche_proattive", company_profile_id=g.COMPANY["Y"])) == 1
    assert sorted(n["company_profile_id"] for n in notifiche(db)) == sorted(
        [g.COMPANY["Y"], g.COMPANY["T"]])


async def test_la_ripresa_riconsegna_a_chi_era_stata_reclamata(monkeypatch):
    # Primo giro: il claim di Y riesce, poi la lettura dei destinatari cade.
    # La riga di Y resta (occupa un posto del tetto): alla ripresa il claim
    # risponde false per dedup e Y deve comunque ricevere la notifica.
    db, sec = await scenario_guida()
    vera = pn.destinatari_azienda

    async def cade_su_y(primary, company_id):
        if company_id == g.COMPANY["Y"]:
            raise errore_pg("08006")
        return await vera(primary, company_id)

    monkeypatch.setattr(pn, "destinatari_azienda", cade_su_y)
    primo = await pn.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    assert (primo["notificate"], primo["errori"]) == (1, 1)
    assert db.righe("partner_notifiche_proattive", company_profile_id=g.COMPANY["Y"])
    assert g.COMPANY["Y"] not in {n["company_profile_id"] for n in notifiche(db)}
    monkeypatch.setattr(pn, "destinatari_azienda", vera)
    call = db.una("partner_calls", id=g.CALL_GUIDA_ID)
    assert call["fanout_completato_at"] is None
    call["fanout_claim_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    ripresa = await pn.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    assert (ripresa["riconsegnate"], ripresa["saltate"], ripresa["errori"]) == (2, 0, 0)
    assert {n["company_profile_id"] for n in notifiche(db)} == {g.COMPANY["Y"], g.COMPANY["T"]}
    assert len([n for n in notifiche(db) if n["company_profile_id"] == g.COMPANY["T"]]) == 1
    assert call["fanout_completato_at"]


async def test_creatore_senza_collegamenti_il_fan_out_resta_pendente():
    # Il ricalcolo dei collegamenti del creatore dopo la pubblicazione
    # fallisce: senza marker il matching esclude tutti (fail-closed). Il
    # fan-out non si chiude: lo scheduler prima ricalcola, poi notifica.
    db, sec = await scenario_guida()
    for altra in (g.CALL_ALTRA_ID, g.CALL_RISERVATA_ID):  # già completate
        db.una("partner_calls", id=altra).update(fanout_claim_at=T0, fanout_completato_at=T0)
    db.guasti[("company_collegamenti", "insert")] = errore_pg("57014")
    await pn.dopo_pubblicazione(db, sec, g.CALL_GUIDA_ID, g.COMPANY["X"])
    assert db.righe("company_collegamenti_stato", company_profile_id=g.COMPANY["X"]) == []
    call = db.una("partner_calls", id=g.CALL_GUIDA_ID)
    assert call["fanout_completato_at"] is None and notifiche(db) == []
    del db.guasti[("company_collegamenti", "insert")]
    call["fanout_claim_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert (await sched.backfill_collegamenti(db))["ricalcolate"] == 1
    esito = await sched.fanout_pendenti(db, sec)
    assert (esito["completate"], esito["notificate"]) == (1, 2)
    assert {n["company_profile_id"] for n in notifiche(db)} == {g.COMPANY["Y"], g.COMPANY["T"]}
    assert call["fanout_completato_at"]


async def test_call_fuori_dall_indice_il_fan_out_resta_pendente():
    # Bando momentaneamente assente dal catalogo: la call resta pubblicata
    # (7 giorni di tolleranza) ma non è nell'indice. Non si chiude a vuoto.
    db, sec = await scenario_guida()
    sec.stati.pop(g.BANDO_GUIDA, None)
    esito = await pn.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    assert esito["claim"] is True and esito["rinviato"] is True
    assert db.una("partner_calls", id=g.CALL_GUIDA_ID)["fanout_completato_at"] is None


async def test_revoca_con_indice_fresco_non_riceve_la_notifica():
    db, sec = await scenario_guida()
    await partenariato_indice.indice(db, sec)  # indice fresco con Y
    db.una("company_partner_profiles", company_profile_id=g.COMPANY["Y"])[
        "visibile_come_partner"] = False
    esito = await pn.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    assert esito["candidati"] == 1
    assert {n["company_profile_id"] for n in notifiche(db)} == {g.COMPANY["T"]}
    assert all(p["p_company"] != g.COMPANY["Y"] for p in db.chiamate("fn_partner_claim_notifica"))


async def test_call_solo_su_invito_nessuna_notifica_proattiva():
    db, sec = await scenario_guida()
    esito = await pn.fan_out_pubblicazione(db, sec, g.CALL_RISERVATA_ID)
    assert esito["claim"] is True and esito["candidati"] == 0
    assert notifiche(db) == []
    assert db.una("partner_calls", id=g.CALL_RISERVATA_ID)["fanout_completato_at"]


async def test_errore_su_un_azienda_non_completa_e_lo_scheduler_riprende():
    db, sec = await scenario_guida()
    for altra in (g.CALL_ALTRA_ID, g.CALL_RISERVATA_ID):  # già completate
        db.una("partner_calls", id=altra).update(fanout_claim_at=T0, fanout_completato_at=T0)
    db.rpc_guasti["fn_partner_claim_notifica"] = errore_pg("40001")
    esito = await pn.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    assert esito["errori"] == 2 and esito["notificate"] == 0
    call = db.una("partner_calls", id=g.CALL_GUIDA_ID)
    assert call["fanout_completato_at"] is None and call["fanout_claim_at"]
    del db.rpc_guasti["fn_partner_claim_notifica"]
    # claim ancora fresco: lo scheduler non lo ruba
    assert (await pn.fanout_pendenti(db, sec))["notificate"] == 0
    # claim scaduto (processo morto): lo scheduler riprende e completa
    call["fanout_claim_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    esito = await pn.fanout_pendenti(db, sec)
    assert esito == {"call": 1, "completate": 1, "notificate": 2, "errori": 0}
    assert call["fanout_completato_at"]


async def test_fan_out_non_parte_su_una_call_non_pubblicata():
    db, sec = await scenario_guida()
    db.una("partner_calls", id=g.CALL_GUIDA_ID)["stato"] = "chiusa_completata"
    esito = await pn.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    assert esito["claim"] is False and notifiche(db) == []


async def test_dopo_pubblicazione_ricalcola_i_collegamenti_e_non_solleva(caplog):
    db, sec = await scenario_guida()
    db.tabelle["company_collegamenti_stato"] = [
        r for r in db.tabelle["company_collegamenti_stato"]
        if r["company_profile_id"] != g.COMPANY["X"]]
    await pn.dopo_pubblicazione(db, sec, g.CALL_GUIDA_ID, g.COMPANY["X"])
    assert db.righe("company_collegamenti_stato", company_profile_id=g.COMPANY["X"])
    assert {n["company_profile_id"] for n in notifiche(db)} == {g.COMPANY["Y"], g.COMPANY["T"]}
    # un guasto non solleva mai (il fan-out resta allo scheduler)
    db.rpc_guasti["fn_partner_fanout_claim"] = errore_pg("XX000")
    await pn.dopo_pubblicazione(db, sec, g.CALL_ALTRA_ID, g.COMPANY["O"])
    assert "rinviato allo scheduler" in caplog.text


# ---------------------------------------------------------- call seguite


async def test_notifica_a_chi_segue_la_call():
    db, _ = await scenario_guida()
    con_membri(db)
    for nome in ("Y", "T", "X"):  # X è il creatore: una sua riga non conta
        db.inserisci("partner_call_salvate", {"company_profile_id": g.COMPANY[nome],
                                              "partner_call_id": g.CALL_GUIDA_ID,
                                              "user_id": g.OWNER[nome]})
    call = db.una("partner_calls", id=g.CALL_GUIDA_ID)
    assert await pn.notifica_salvate(db, call, "modificata") == 2
    assert await pn.notifica_salvate(db, call, "modificata") == 2  # dedup per versione
    modificate = notifiche(db, pn.TIPO_SEGUITA_MODIFICATA)
    assert {(n["user_id"], n["company_profile_id"]) for n in modificate} == {
        (g.OWNER["Y"], g.COMPANY["Y"]), (MEMBRO_Y, g.COMPANY["Y"]),
        (g.OWNER["T"], g.COMPANY["T"])}
    assert modificate[0]["dedup_key"].startswith(f"partner-seguita:{g.CALL_GUIDA_ID}:v1:")
    call["versione"] = 2
    await pn.notifica_salvate(db, call, "modificata")
    assert len(notifiche(db, pn.TIPO_SEGUITA_MODIFICATA)) == 6
    await pn.notifica_salvate(db, call, "chiusa")
    chiuse = notifiche(db, pn.TIPO_SEGUITA_CHIUSA)
    assert len(chiuse) == 3
    assert chiuse[0]["url"].startswith("/app/partenariati?vista=salvate&azienda=")
    assert RAGIONE["X"] not in repr(chiuse)


async def test_notifica_salvate_non_solleva():
    db, _ = await scenario_guida()
    db.guasti[("partner_call_salvate", "select")] = errore_pg("XX000")
    assert await pn.notifica_salvate(db, {"id": g.CALL_GUIDA_ID}, "chiusa") == 0


# ------------------------------------------------------------------ digest


class _Posta(list):
    """Email intercettate da `_dispatch` (nessun invio reale)."""

    def __init__(self):
        super().__init__()
        self.esito = {"ok": True}


@pytest.fixture
def posta(monkeypatch):
    inviate = _Posta()

    async def dispatch(to_email, subject, html_body, text_body, headers=None):
        inviate.append({"to": to_email, "subject": subject, "html": html_body,
                        "text": text_body, "headers": headers})
        return inviate.esito["ok"]

    monkeypatch.setattr(email_service, "_dispatch", dispatch)
    return inviate


async def _con_notifiche():
    db, sec = await scenario_guida()
    await pn.fan_out_pubblicazione(db, sec, g.CALL_GUIDA_ID)
    return db, sec


async def test_digest_della_settimana(posta):
    db, _ = await _con_notifiche()
    settimana = pn.lunedi()
    riepilogo = await pn.esegui_digest(db, settimana)
    assert riepilogo["esito"] == "ok" and riepilogo["inviate"] == 2
    assert {m["to"] for m in posta} == {EMAIL["Y"], EMAIL["T"]}
    y = next(m for m in posta if m["to"] == EMAIL["Y"])
    token = db.una("partner_email_settings", user_id=g.OWNER["Y"])["unsubscribe_token"]
    url = (f"https://api.bandofit.test/api/v1/partenariati/email/unsubscribe?token={token}"
           "&tipo=digest")
    assert y["headers"] == {"List-Unsubscribe": f"<{url}>",
                            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    assert y["subject"] == "Una nuova call di partenariato per te — BandoFit"
    assert BANDO_GUIDA_TITOLO in y["text"] and "copri 2 requisiti mancanti" in y["text"]
    assert f"/app/partenariati/call/{g.CALL_GUIDA_ID}?azienda={g.COMPANY['Y']}" in y["text"]
    for canary in (RAGIONE["X"], PIVA["X"], g.COMPANY["X"], g.CALL_GUIDA["titolo"]):
        assert canary not in y["html"] and canary not in y["text"]
    # ledger e notifiche incluse
    assert {r["stato"] for r in db.tabelle["partner_digest_invii"]} == {"inviata"}
    assert all(r["digest_incluso_at"] for r in db.tabelle["partner_notifiche_proattive"])
    run = db.una("partner_digest_runs", settimana=settimana.isoformat())
    assert run["riepilogo"]["inviate"] == 2
    # una sola volta per settimana
    assert await pn.esegui_digest(db, settimana) == {"esito": "gia_eseguito"}
    # la settimana dopo non c'è nulla di nuovo: nessuna email
    dopo = await pn.esegui_digest(db, settimana + timedelta(days=7))
    assert dopo["aziende"] == 0 and len(posta) == 2


async def test_digest_solo_email_verificate_e_non_soppresse(posta):
    db, _ = await _con_notifiche()
    db.email_non_verificate.add(g.OWNER["T"])
    db.tabelle["email_suppressions"] = [{"email": EMAIL["Y"].upper()}]
    riepilogo = await pn.esegui_digest(db, pn.lunedi())
    assert posta == [] and riepilogo["inviate"] == 0
    # nessuno le ha ricevute: restano da includere
    assert not any(r["digest_incluso_at"] for r in db.tabelle["partner_notifiche_proattive"])


def _soppressi_oltre_le_1000_righe(email: str) -> list[dict]:
    """1500 indirizzi soppressi e poi `email`, oltre il max-rows di PostgREST."""
    return [{"id": i, "email": f"altro{i}@example.test"} for i in range(1, 1501)] + [
        {"id": 1501, "email": email.upper()}]


async def test_digest_nessuna_email_a_un_soppresso_oltre_le_1000_righe(posta):
    db, _ = await _con_notifiche()
    db.max_righe = 1000
    db.tabelle["email_suppressions"] = _soppressi_oltre_le_1000_righe(EMAIL["Y"])
    riepilogo = await pn.esegui_digest(db, pn.lunedi())
    assert [m["to"] for m in posta] == [EMAIL["T"]] and riepilogo["inviate"] == 1


async def test_email_di_evento_nessuna_email_a_un_soppresso_oltre_le_1000_righe():
    db, _ = await scenario_guida()
    db.max_righe = 1000
    db.tabelle["email_suppressions"] = _soppressi_oltre_le_1000_righe(EMAIL["Y"])
    inviate: list[str] = []

    async def invia(to_email, token, azienda):
        inviate.append(to_email)
        return True

    destinatari = [{"id": g.OWNER["Y"], "email": EMAIL["Y"]},
                   {"id": g.OWNER["T"], "email": EMAIL["T"]}]
    assert await partenariato_candidature_service.email_evento(
        db, g.COMPANY["Y"], destinatari, invia) == 1
    assert inviate == [EMAIL["T"]]


async def test_recapitabili_oltre_le_1000_email_verificate():
    db = FakePrimary()
    db.max_righe = 1000
    destinatari = [{"id": f"u{i}", "email": f"u{i}@example.test"} for i in range(1_200)]
    assert len(await bando_alert_service.filtra_recapitabili(db, destinatari)) == 1_200


async def test_digest_rispetta_l_opt_out_e_l_opt_in(posta):
    db, _ = await _con_notifiche()
    db.inserisci("partner_email_settings", {"user_id": g.OWNER["T"],
                                            "digest_abilitato": False})
    db.una("company_partner_profiles", company_profile_id=g.COMPANY["Y"])[
        "visibile_come_partner"] = False
    riepilogo = await pn.esegui_digest(db, pn.lunedi())
    assert posta == [] and riepilogo["saltate"] == 1 and riepilogo["aziende"] == 1


async def test_digest_nessun_contenuto_nessuna_email(posta):
    db, _ = await scenario_guida()
    riepilogo = await pn.esegui_digest(db, pn.lunedi())
    assert posta == [] and riepilogo["aziende"] == 0 and riepilogo["esito"] == "ok"


async def test_digest_membro_con_visibilita_e_advisor_con_sezioni(posta):
    db, _ = await _con_notifiche()
    con_membri(db)
    await pn.esegui_digest(db, pn.lunedi())
    assert {m["to"] for m in posta} == {EMAIL["Y"], EMAIL["T"], "membro.y@example.test"}
    # una sola azienda per destinatario: nessuna intestazione di sezione
    assert "[" not in posta[0]["text"].split("\n\n")[1]


COMPANY_Y2 = "c0000000-0000-4000-8000-000000000022"
RAGIONE_Y2 = "Seconda Sintetica Srl"


async def test_digest_advisor_con_due_aziende_una_sezione_per_azienda(posta):
    # Il titolare di Y ha anche Y2 (visibile), con una notifica per la call
    # di O: UNA email con due sezioni, ognuna con le sue call e il suo
    # `?azienda=`; il membro che vede solo Y non vede nulla di Y2.
    db, _ = await _con_notifiche()
    con_membri(db)
    riga = copy.deepcopy(db.una("company_profiles", id=g.COMPANY["Y"]))
    riga.update(id=COMPANY_Y2, ragione_sociale=RAGIONE_Y2, partita_iva="30000000001")
    db.tabelle["company_profiles"].append(riga)
    profilo = copy.deepcopy(db.una("company_partner_profiles",
                                   company_profile_id=g.COMPANY["Y"]))
    profilo.update(company_profile_id=COMPANY_Y2,
                   codice_pubblico="d0000000-0000-4000-8000-000000000022")
    db.tabelle["company_partner_profiles"].append(profilo)
    db.inserisci("partner_notifiche_proattive", {
        "company_profile_id": COMPANY_Y2, "partner_call_id": g.CALL_ALTRA_ID,
        "settimana": pn.lunedi().isoformat(), "copertura": 1, "punteggio": 60})
    riepilogo = await pn.esegui_digest(db, pn.lunedi())
    assert riepilogo["aziende"] == 3
    [titolare] = [m for m in posta if m["to"] == EMAIL["Y"]]
    assert titolare["subject"] == "2 nuove call di partenariato per te — BandoFit"
    testo = titolare["text"]
    assert f"[{RAGIONE['Y']}]" in testo and f"[{RAGIONE_Y2}]" in testo
    sezione_y, sezione_y2 = testo.split(f"[{RAGIONE_Y2}]")  # sezioni in ordine di id
    assert f"/app/partenariati/call/{g.CALL_GUIDA_ID}?azienda={g.COMPANY['Y']}" in sezione_y
    assert g.CALL_ALTRA_ID not in sezione_y
    assert f"/app/partenariati/call/{g.CALL_ALTRA_ID}?azienda={COMPANY_Y2}" in sezione_y2
    assert g.CALL_GUIDA_ID not in sezione_y2
    assert RAGIONE_Y2 in titolare["html"] and RAGIONE["Y"] in titolare["html"]
    [membro] = [m for m in posta if m["to"] == "membro.y@example.test"]
    assert RAGIONE_Y2 not in membro["text"] + membro["html"]
    assert COMPANY_Y2 not in membro["text"] and g.CALL_ALTRA_ID not in membro["text"]
    # una riga del ledger per utente, anche con due aziende
    assert len(db.righe("partner_digest_invii", user_id=g.OWNER["Y"])) == 1


async def test_digest_un_errore_su_un_destinatario_non_ferma_gli_altri(posta, monkeypatch):
    db, _ = await _con_notifiche()
    vero = pn._claim_invio

    async def cade_su_y(primary, user_id, settimana):
        if user_id == g.OWNER["Y"]:
            raise errore_pg("57014")
        return await vero(primary, user_id, settimana)

    monkeypatch.setattr(pn, "_claim_invio", cade_su_y)
    riepilogo = await pn.esegui_digest(db, pn.lunedi())
    assert (riepilogo["esito"], riepilogo["errori"], riepilogo["inviate"]) == ("ok", 1, 1)
    assert {m["to"] for m in posta} == {EMAIL["T"]}
    incluse = {r["company_profile_id"]: r["digest_incluso_at"]
               for r in db.tabelle["partner_notifiche_proattive"]}
    assert incluse[g.COMPANY["T"]] and incluse[g.COMPANY["Y"]] is None  # Y la settimana dopo


async def test_digest_errore_dopo_l_invio_le_notifiche_restano_incluse(posta, monkeypatch):
    # Il ledger non si aggiorna dopo invii riusciti: gli altri destinatari
    # ricevono comunque e nessuna notifica già inviata si ripropone.
    db, _ = await _con_notifiche()

    async def nessuno(primary):
        return 0

    monkeypatch.setattr(pn, "_marca_incerti", nessuno)
    db.guasti[("partner_digest_invii", "update")] = errore_pg("57014")
    riepilogo = await pn.esegui_digest(db, pn.lunedi())
    assert (riepilogo["inviate"], riepilogo["errori"]) == (2, 2)
    assert {m["to"] for m in posta} == {EMAIL["Y"], EMAIL["T"]}
    assert all(r["digest_incluso_at"] for r in db.tabelle["partner_notifiche_proattive"])
    # il ledger resta «in_invio»: la run successiva lo marca incerto, mai ritentato
    assert {r["stato"] for r in db.tabelle["partner_digest_invii"]} == {"in_invio"}


async def test_digest_invio_fallito_resta_da_includere(posta):
    db, _ = await _con_notifiche()
    posta.esito["ok"] = False
    riepilogo = await pn.esegui_digest(db, pn.lunedi())
    assert riepilogo["fallite"] == 2
    assert {r["stato"] for r in db.tabelle["partner_digest_invii"]} == {"fallita"}
    assert not any(r["digest_incluso_at"] for r in db.tabelle["partner_notifiche_proattive"])


async def test_digest_invii_interrotti_diventano_incerti_e_non_si_ritentano(posta):
    db, _ = await _con_notifiche()
    settimana = pn.lunedi()
    db.inserisci("partner_digest_invii", {"user_id": g.OWNER["Y"],
                                          "settimana": settimana.isoformat()})
    riepilogo = await pn.esegui_digest(db, settimana)
    assert riepilogo["incerte"] == 1
    assert db.una("partner_digest_invii", user_id=g.OWNER["Y"])["stato"] == "incerta"
    assert {m["to"] for m in posta} == {EMAIL["T"]}  # Y: at-most-once


async def test_digest_errore_nel_riepilogo_senza_sollevare(posta):
    db, _ = await _con_notifiche()
    db.guasti[("partner_notifiche_proattive", "select")] = errore_pg("57014")
    riepilogo = await pn.esegui_digest(db, pn.lunedi())
    assert riepilogo["esito"] == "errore" and riepilogo["errore"] == "57014"


@pytest.mark.parametrize(
    ("locale", "atteso"),
    [
        (datetime(2026, 10, 5, 8, 29, tzinfo=ROMA), None),  # lunedì, prima dell'ora
        (datetime(2026, 10, 6, 9, 0, tzinfo=ROMA), None),  # martedì
        (datetime(2026, 10, 5, 8, 30, tzinfo=ROMA), date(2026, 10, 5)),
        (datetime(2026, 10, 5, 23, 0, tzinfo=ROMA), date(2026, 10, 5)),
    ],
)
async def test_digest_se_dovuto(monkeypatch, locale, atteso):
    chiamate = []

    async def esegui(primary, settimana):
        chiamate.append(settimana)
        return {"esito": "ok"}

    monkeypatch.setattr(pn, "esegui_digest", esegui)
    esito = await pn.digest_se_dovuto(FakePrimary(), locale.astimezone(timezone.utc))
    assert chiamate == ([atteso] if atteso else [])
    assert (esito is None) is (atteso is None)


def test_prossimo_digest():
    assert pn.prossimo_digest(datetime(2026, 10, 1, 12, 0, tzinfo=ROMA)) == datetime(
        2026, 10, 5, 8, 30, tzinfo=ROMA)
    assert pn.prossimo_digest(datetime(2026, 10, 5, 8, 0, tzinfo=ROMA)) == datetime(
        2026, 10, 5, 8, 30, tzinfo=ROMA)
    assert pn.prossimo_digest(datetime(2026, 10, 5, 8, 30, tzinfo=ROMA)) == datetime(
        2026, 10, 12, 8, 30, tzinfo=ROMA)


def test_lunedi_in_europa_roma():
    # domenica 23:30 UTC = lunedì 01:30 a Roma (ora legale)
    assert pn.lunedi(datetime(2026, 10, 4, 23, 30, tzinfo=timezone.utc)) == date(2026, 10, 5)
    assert pn.lunedi(datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)) == date(2026, 9, 28)


# ------------------------------------------------ impostazioni e disiscrizione


async def test_impostazioni_email():
    db = FakePrimary()
    utente = g.OWNER["Y"]
    assert (await pn.settings_per_utente(db, utente)).model_dump() == {
        "digest_abilitato": True, "eventi_abilitati": True}
    out = await pn.aggiorna_settings(db, utente, pn.EmailSettingsPartenariatiIn(
        digest_abilitato=False, eventi_abilitati=True))
    assert out.model_dump() == {"digest_abilitato": False, "eventi_abilitati": True}
    token = db.una("partner_email_settings", user_id=utente)["unsubscribe_token"]
    await pn.aggiorna_settings(db, utente, pn.EmailSettingsPartenariatiIn(
        digest_abilitato=True, eventi_abilitati=True))
    assert db.una("partner_email_settings", user_id=utente)["unsubscribe_token"] == token


@pytest.mark.parametrize("tipo, campo", [("digest", "digest_abilitato"),
                                         ("eventi", "eventi_abilitati")])
async def test_disiscrizione_per_token(tipo, campo):
    db = FakePrimary()
    riga = db.inserisci("partner_email_settings", {"user_id": g.OWNER["Y"]})
    await pn.unsubscribe_by_token(db, riga["unsubscribe_token"], tipo)
    aggiornata = db.una("partner_email_settings", user_id=g.OWNER["Y"])
    assert aggiornata[campo] is False
    altro = "eventi_abilitati" if campo == "digest_abilitato" else "digest_abilitato"
    assert aggiornata[altro] is True
    [audit] = db.tabelle["audit_log"]
    assert audit["action"] == "partenariati.email_disiscrizione"
    assert riga["unsubscribe_token"] not in repr(audit)
    # token ignoto: nulla cambia, nessun audit
    await pn.unsubscribe_by_token(db, "00000000-0000-4000-8000-000000000000", tipo)
    assert len(db.tabelle["audit_log"]) == 1


# ------------------------------------------------------------ scheduler


async def test_passo_backfill_invalida_l_indice_solo_se_cambia_qualcosa():
    db, sec = await scenario_guida()
    primo = await partenariato_indice.indice(db, sec)
    assert await sched.backfill_collegamenti(db) == {"ricalcolate": 0, "rimosse": 0,
                                                     "errori": 0}
    assert await partenariato_indice.indice(db, sec) is primo
    db.tabelle["company_collegamenti_stato"] = [
        r for r in db.tabelle["company_collegamenti_stato"]
        if r["company_profile_id"] != g.COMPANY["Y"]]
    assert (await sched.backfill_collegamenti(db))["ricalcolate"] == 1
    assert await partenariato_indice.indice(db, sec) is not primo


async def test_passo_backfill_a_flag_spento_non_fa_nulla(monkeypatch):
    imposta(monkeypatch, PARTENARIATI_ATTIVO="false")
    db, _ = await scenario_guida()
    db.ops.clear()
    assert await sched.backfill_collegamenti(db) == {"ricalcolate": 0, "rimosse": 0,
                                                     "errori": 0}
    assert db.ops == []


async def test_passi_fanout_e_digest_dello_scheduler(posta):
    db, sec = await scenario_guida()
    esito = await sched.fanout_pendenti(db, sec)
    assert (esito["call"], esito["completate"], esito["errori"]) == (3, 3, 0)
    guida = db.righe("partner_notifiche_proattive", partner_call_id=g.CALL_GUIDA_ID)
    assert {r["company_profile_id"] for r in guida} == {g.COMPANY["Y"], g.COMPANY["T"]}
    assert esito["notificate"] == len(db.tabelle["partner_notifiche_proattive"])
    assert await sched.digest_settimanale(
        db, datetime(2026, 10, 6, 9, 0, tzinfo=ROMA)) == {"esito": "non_dovuto"}
    esito = await sched.digest_settimanale(db, datetime(2026, 10, 5, 9, 0, tzinfo=ROMA))
    assert esito["inviate"] == len(posta) and esito["aziende"] >= 2


async def test_la_run_giornaliera_comprende_i_passi_del_wp6(posta, monkeypatch):
    db, sec = await scenario_guida()
    for nome in ("failsafe_estrazioni", "failsafe_bozze_profilo", "failsafe_ai_call"):
        async def zero(primary):
            return 0
        monkeypatch.setattr(sched, nome, zero)

    async def nessuna(primary, secondary, oggi):
        return {}

    async def batch(primary, secondary, ai, oggi):
        return {}

    monkeypatch.setattr(sched, "chiusura_call", nessuna)
    monkeypatch.setattr(sched, "batch_estrazioni", batch)
    db.tabelle.setdefault("partenariati_runs", [])
    esiti = await sched.esegui_run(db, sec, None, g.OGGI,
                                   datetime(2026, 10, 5, 9, 0, tzinfo=ROMA))
    assert esiti["backfill_collegamenti"]["errori"] == 0
    assert esiti["fanout_pendenti"]["completate"] == 3
    assert esiti["digest_settimanale"]["inviate"] == len(posta) >= 2


async def test_pseudonimo_e_handle_mai_nelle_notifiche(posta):
    # Lo pseudonimo è per il creatore: mai nelle notifiche né nel digest dei
    # candidati, e nemmeno il codice pubblico di nessuno.
    db, _ = await _con_notifiche()
    await pn.esegui_digest(db, pn.lunedi())
    testo = repr(db.tabelle["notifications"]) + repr(list(posta))
    assert notifiche(db) and posta
    for nome in ("Y", "T", "X"):
        assert pseudonimo(g.CALL_GUIDA_ID, g.CODICE_PUBBLICO[nome]) not in testo
        assert g.CODICE_PUBBLICO[nome] not in testo


async def test_destinatari_a_blocchi_di_owner(monkeypatch):
    # Centinaia di owner nel digest: le letture degli alert (senza
    # paginazione) si fanno a blocchi, mai con tutti gli id insieme.
    db = FakePrimary()
    aziende = [f"c{i:07d}-0000-4000-8000-000000000000" for i in range(250)]
    for i, cid in enumerate(aziende):
        db.tabelle.setdefault("company_profiles", []).append(
            {"id": cid, "parent_id": f"a{i:07d}-0000-4000-8000-000000000000"})
    blocchi: list[int] = []

    async def carica(primary, owners):
        blocchi.append(len(owners))
        return {o: [{"id": o, "email": f"{o}@example.test"}] for o in owners}

    async def visibilita(primary, owners):
        return {}

    monkeypatch.setattr(pn.bando_alert_service, "carica_destinatari", carica)
    monkeypatch.setattr(pn.bando_alert_service, "carica_visibilita_membri", visibilita)
    uscita = await pn.destinatari_per_azienda(db, aziende)
    assert blocchi == [100, 100, 50]
    assert all(len(uscita[cid]) == 1 for cid in aziende)
