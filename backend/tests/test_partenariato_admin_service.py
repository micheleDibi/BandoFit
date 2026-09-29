"""Admin del modulo partenariati (WP9, W3; verifica dell'identità da parte
dell'admin): servizio vero sul primario finto del WP9
(`test_partenariato_moderazione_service.FakePrimaryWP9`).

Verifica: metriche sullo STESSO seed noto del test DB della 0041 con i valori
esatti (gemella in Python della RPC) e la forma della risposta; periodo di
default e periodo non valido (400 prima della RPC); costi per provider e
valuta, mai sommati tra EUR e USD; elenco delle call con filtri, ricerca
ripulita, conteggi e creatore; coda della verifica d'identità con i recapiti
della sede dal registro (mai da un registro di un'altra P.IVA), decisione con
metodo obbligatorio e dati del registro coerenti (anche non di sandbox in
produzione), rifiuto, revoca, notifiche al titolare."""

import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.core.errors import AppError, NotFoundError
from app.schemas.partenariato_admin import IdentitaDecisioneIn, IdentitaRevocaIn
from app.services import partenariato_admin_service as adm
from app.services import partenariato_indice
from app.services import partenariato_moderazione_testi as testi
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariato_candidature_service import fixture_fondo  # noqa: F401
from tests.test_partenariato_indice import (  # noqa: F401 — fixture autouse
    EMAIL,
    PIVA,
    RAGIONE,
    ambiente_wp6,
)
from tests.test_partenariato_moderazione_service import (
    ADMIN,
    FakePrimaryWP9,
    notifiche,
    scenario_wp9,
    segnalazione,
)


ROMA = ZoneInfo("Europe/Rome")


def _fa(**intervallo) -> str:
    return (datetime.now(timezone.utc) - timedelta(**intervallo)).isoformat()


def _dopo(base: str, **intervallo) -> str:
    return (datetime.fromisoformat(base) + timedelta(**intervallo)).isoformat()


def _call(db: FakePrimaryWP9, pubblicata_at: str | None, *, esito=None, copertura=None,
          stato: str = "pubblicata") -> str:
    cid = str(uuid.uuid4())
    db.tabelle.setdefault("partner_calls", []).append({
        "id": cid, "company_profile_id": g.COMPANY["X"], "family_parent_id": g.OWNER["X"],
        "stato": stato, "pubblicata_at": pubblicata_at, "validazione_esito": esito,
        "validazione_at": _fa(minutes=1) if esito else None,
        "copertura_gap_ratio": copertura, "created_at": pubblicata_at or _fa(minutes=1)})
    return cid


def _cand(db: FakePrimaryWP9, call_id: str, tipo: str, stato: str, **dopo) -> None:
    call = next(c for c in db.tabelle["partner_calls"] if c["id"] == call_id)
    db.tabelle.setdefault("partner_candidature", []).append({
        "id": str(uuid.uuid4()), "partner_call_id": call_id, "tipo": tipo, "stato": stato,
        "company_profile_id": g.COMPANY["Y"], "created_at": _dopo(call["pubblicata_at"], **dopo)})


def seed_metriche() -> FakePrimaryWP9:
    """Il seed di `test_migration_0041.TestMetriche.test_seed_noto`."""
    db = FakePrimaryWP9()
    c1 = _call(db, _fa(days=60), esito="verde", copertura="0.5")
    c2 = _call(db, _fa(days=40), esito="rosso", copertura="1")
    c3 = _call(db, _fa(days=5))
    c5 = _call(db, _fa(days=29, hours=12))
    c4 = _call(db, _fa(days=200), esito="verde", copertura="0")  # fuori dal periodo
    _call(db, None, stato="bozza")                                 # mai pubblicata
    _cand(db, c1, "candidatura", "accettata", hours=10)
    _cand(db, c1, "candidatura", "rifiutata", days=40)
    _cand(db, c1, "invito", "rifiutata", days=1)
    _cand(db, c2, "candidatura", "inviata", days=35)
    _cand(db, c2, "invito", "accettata", days=2)
    _cand(db, c3, "candidatura", "rifiutata", hours=30)
    _cand(db, c5, "candidatura", "inviata", days=3)
    _cand(db, c4, "candidatura", "accettata", hours=1)
    return db


class TestMetriche:
    async def test_seed_noto_valori_esatti(self):
        db = seed_metriche()
        oggi = adm.oggi_roma()
        out = await adm.metriche(db, oggi - timedelta(days=90), oggi)
        assert out.model_dump(mode="json") == {
            "da": (oggi - timedelta(days=90)).isoformat(), "a": oggi.isoformat(),
            "call_pubblicate": 4,
            "candidature": 5,
            "inviti": 2,
            "candidature_per_call": 1.25,
            "call_osservabili_30_giorni": 2,
            "call_con_candidatura_30_giorni": 1,
            "percentuale_call_con_candidatura_30_giorni": 50.0,
            "accettazione": {
                "candidatura": {"accettate": 1, "rifiutate": 2, "tasso": 0.333},
                "invito": {"accettate": 1, "rifiutate": 1, "tasso": 0.5},
            },
            "ore_mediane_prima_candidatura": 51.0,
            "copertura_media_gap": 0.75,
            "consorzi_validati": 2,
            "consorzi_validati_verde": 1,
        }
        [chiamata] = db.chiamate("fn_admin_metriche_partenariati")
        assert chiamata == {"p_da": (oggi - timedelta(days=90)).isoformat(),
                            "p_a": oggi.isoformat()}

    async def test_periodo_vuoto_e_default(self):
        db = FakePrimaryWP9()
        out = await adm.metriche(db)
        oggi = adm.oggi_roma()
        assert (out.da, out.a) == (oggi - timedelta(days=29), oggi)
        assert (out.call_pubblicate, out.candidature_per_call, out.copertura_media_gap) == (
            0, None, None)
        assert out.accettazione["candidatura"].tasso is None

    @pytest.mark.parametrize(("da", "a"), [
        (date(2026, 3, 2), date(2026, 3, 1)), (date(2016, 1, 1), date(2026, 3, 1))])
    async def test_periodo_non_valido_prima_della_rpc(self, da, a):
        db = FakePrimaryWP9()
        for funzione in (adm.metriche, adm.costi):
            with pytest.raises(AppError) as exc:
                await funzione(db, da, a)
            assert (exc.value.status_code, exc.value.code) == (400, "periodo_non_valido")
        assert db.rpcs == []


def evento(db, provider, service, outcome, cents, quando: str) -> None:
    db.tabelle.setdefault("api_usage_events", []).append({
        "provider": provider, "service": service, "outcome": outcome, "cost_cents": cents,
        "created_at": datetime.fromisoformat(quando).replace(tzinfo=ROMA).isoformat()})


class TestCosti:
    async def test_per_provider_e_valuta_mai_sommati(self):
        db = FakePrimaryWP9()
        evento(db, "anthropic", "partenariato_estrazione", "success", 10, "2026-03-01T00:00")
        evento(db, "anthropic", "partenariato_estrazione", "success", 20, "2026-03-15T12:00")
        evento(db, "anthropic", "partenariato_estrazione", "success", 30, "2026-03-31T23:59:59")
        evento(db, "anthropic", "partner_call_testi", "timeout_unknown", 7, "2026-03-10T08:00")
        evento(db, "openapi", "IT-advanced", "success", 10, "2026-03-05T09:00")
        evento(db, "openapi", "IT-advanced", "success", 10, "2026-03-06T09:00")
        evento(db, "openapi", "bilancio-ottico", "error", 0, "2026-03-07T09:00")
        evento(db, "openapi", "IT-full", "success", 30, "2026-03-08T09:00")
        evento(db, "anthropic", "ai_check", "success", 50, "2026-03-09T09:00")
        evento(db, "anthropic", "partenariato_estrazione", "success", 99, "2026-04-01T00:00")
        out = await adm.costi(db, date(2026, 3, 1), date(2026, 3, 31))
        assert out.model_dump(mode="json") == {
            "da": "2026-03-01", "a": "2026-03-31",
            "voci": [
                {"provider": "openapi", "service": "IT-advanced", "outcome": "success",
                 "valuta": "EUR", "eventi": 2, "cost_cents": 20},
                {"provider": "openapi", "service": "bilancio-ottico", "outcome": "error",
                 "valuta": "EUR", "eventi": 1, "cost_cents": 0},
                {"provider": "anthropic", "service": "partenariato_estrazione",
                 "outcome": "success", "valuta": "USD", "eventi": 3, "cost_cents": 60},
                {"provider": "anthropic", "service": "partner_call_testi",
                 "outcome": "timeout_unknown", "valuta": "USD", "eventi": 1, "cost_cents": 7},
            ],
            "totali": [
                {"valuta": "EUR", "eventi": 3, "cost_cents": 20},
                {"valuta": "USD", "eventi": 4, "cost_cents": 67},
            ],
        }
        # nessun totale unico tra valute
        assert {t.valuta for t in out.totali} == {"EUR", "USD"}

    async def test_risposta_inattesa_502(self):
        class Rotto(FakePrimaryWP9):
            def _fn_admin_costi_partenariati(self, p):
                return None

        with pytest.raises(AppError) as exc:
            await adm.costi(Rotto())
        assert exc.value.status_code == 502


# ------------------------------------------------------------ call


class TestListaCall:
    async def test_conteggi_creatore_e_filtri(self, fondo):
        db, sec = await scenario_wp9()
        from tests.test_partenariato_consorzio_service import accetta

        await accetta(db, sec)
        segnalazione(db, "call", g.CALL_GUIDA_ID, segnalante="Z")
        pagina = await adm.lista_call(db, stato="pubblicata")
        guida = next(c for c in pagina.items if str(c.id) == g.CALL_GUIDA_ID)
        assert (guida.candidature, guida.inviti, guida.membri, guida.segnalazioni_aperte) == (
            1, 0, 2, 1)
        assert guida.creatore.ragione_sociale == RAGIONE["X"]
        assert guida.bando.titolo == g.CALL_GUIDA["bando_titolo"]
        assert {c.stato for c in pagina.items} == {"pubblicata"}
        # ricerca per testo (titolo o bando) e per id esatto
        per_testo = await adm.lista_call(db, q="prototipazione")
        assert [str(c.id) for c in per_testo.items] == [g.CALL_GUIDA_ID]
        per_id = await adm.lista_call(db, q=g.CALL_ALTRA_ID)
        assert [str(c.id) for c in per_id.items] == [g.CALL_ALTRA_ID]
        assert (await adm.lista_call(db, q="nessuna call così")).total == 0
        vuota = await adm.lista_call(db, stato="sospesa_moderazione")
        assert vuota.items == [] and vuota.total == 0
        # un membro uscito non conta più
        db.membro_di(g.COMPANY["Y"])["stato"] = "uscito"
        guida = next(c for c in (await adm.lista_call(db)).items
                     if str(c.id) == g.CALL_GUIDA_ID)
        assert guida.membri == 1

    async def test_paginazione(self, fondo):
        db, sec = await scenario_wp9()
        pagina = await adm.lista_call(db, page=2, page_size=2)
        assert (pagina.total, pagina.page, len(pagina.items)) == (3, 2, 1)

    async def test_ricerca_per_denominazione_del_registro(self, fondo):
        """Completamento WP9: `q` cerca anche nella denominazione del Registro
        Imprese dell'azienda creatrice (senza maiuscole), oltre a titolo e
        bando; le due ricerche si uniscono, con totale e pagine giusti."""
        db, sec = await scenario_wp9()
        # O ha creato la call «altra» e quella riservata; nessun titolo né
        # bando contiene «sintetica o».
        per_nome = await adm.lista_call(db, q="impresa SINTETICA o")
        assert sorted(str(c.id) for c in per_nome.items) == sorted(
            [g.CALL_ALTRA_ID, g.CALL_RISERVATA_ID])
        assert per_nome.total == 2
        assert {c.creatore.ragione_sociale for c in per_nome.items} == {RAGIONE["O"]}
        # un'azienda che non ha creato call non porta nulla
        assert (await adm.lista_call(db, q="sintetica y")).total == 0
        # filtro di stato applicato anche alla ricerca per nome
        db.una("partner_calls", id=g.CALL_RISERVATA_ID)["stato"] = "chiusa_annullata"
        pubblicate = await adm.lista_call(db, q="sintetica o", stato="pubblicata")
        assert [str(c.id) for c in pubblicate.items] == [g.CALL_ALTRA_ID]
        # unione: X per denominazione, la call di O per titolo
        db.una("company_data", company_profile_id=g.COMPANY["X"])["denominazione"] = (
            "INNOVAZIONE DIGITALE SRL")
        unione = await adm.lista_call(db, q="innovazione digitale", page_size=1)
        assert unione.total == 2 and len(unione.items) == 1
        seconda = await adm.lista_call(db, q="innovazione digitale", page=2, page_size=1)
        assert {str(unione.items[0].id), str(seconda.items[0].id)} == {
            g.CALL_GUIDA_ID, g.CALL_ALTRA_ID}
        # denominazioni lette a blocchi (in_ con al più 100 aziende)
        letture = [o for o in db.ops if o["tabella"] == "company_data" and o["op"] == "select"]
        assert letture and all(
            len(v) <= 100 for o in letture for op, c, v in o["filtri"] if op == "in")

    async def test_ricerca_per_denominazione_a_blocchi_e_keyset(self, fondo, monkeypatch):
        """Correzione WP9: nessuna lettura della ricerca per denominazione
        supera il max-rows. Con blocchi e pagine di una riga il risultato non
        cambia, le denominazioni si leggono un'azienda alla volta e i
        creatori a keyset (una lettura per call, più quella vuota finale)."""
        db, sec = await scenario_wp9()
        atteso = await adm.lista_call(db, q="sintetica o")
        monkeypatch.setattr(adm, "_BLOCCO", 1)
        monkeypatch.setattr(adm, "_PAGINA", 1)
        db.ops.clear()
        ridotta = await adm.lista_call(db, q="sintetica o")
        assert ridotta.total == atteso.total == 2
        assert sorted(str(c.id) for c in ridotta.items) == sorted(
            str(c.id) for c in atteso.items)
        call = db.tabelle["partner_calls"]
        creatori = {str(r["company_profile_id"]) for r in call if r.get("company_profile_id")}
        assert len(creatori) >= 2
        per_nome = [o for o in db.ops if o["tabella"] == "company_data" and o["op"] == "select"
                    and any(op == "or" and "denominazione.ilike" in str(v)
                            for op, _c, v in o["filtri"])]
        assert len(per_nome) == len(creatori)
        assert all(len(v) == 1 for o in per_nome for op, _c, v in o["filtri"] if op == "in")
        chiavi = [o for o in db.ops if o["tabella"] == "partner_calls" and o["op"] == "select"
                  and o["select"] == "id,company_profile_id"]
        assert len(chiavi) == len(call) + 1

    @pytest.mark.parametrize(("q", "atteso"), [
        (None, None), ("", None), ("  ", None), ("a,b(c)*d", "a b c d"),
        ("x" * 300, "x" * 100), ('nome"\\:%', "nome"),
    ])
    def test_ricerca_ripulita(self, q, atteso):
        assert adm.pulisci_ricerca(q) == atteso


# ---------------------------------------------------- verifica dell'identità


def con_recapiti(db, nome: str) -> None:
    dati = db.una("company_data", company_profile_id=g.COMPANY[nome])
    dati["raw"] = {**dati["raw"], "pec": f"{nome.lower()}@pec.example.test",
                   "contacts": {"telephoneNumber": "0961 000000"},
                   "address": {"town": "Catanzaro", "province": {"code": "CZ"}}}


@pytest.fixture
def produzione(monkeypatch):
    """Ambiente openapi di produzione: i dati di prova del registro non
    valgono (fail-closed, come `richiedi_non_sandbox`)."""
    from app.core.config import get_settings

    monkeypatch.setenv("OPENAPI_ENV", "production")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestCodaIdentita:
    async def test_coda_con_i_dati_del_registro(self, fondo):
        db, _ = await scenario_wp9()
        con_recapiti(db, "X")
        con_recapiti(db, "Y")
        db.una("company_data", company_profile_id=g.COMPANY["Y"])["piva_fetched"] = "99999999999"
        db.richiedi_identita("X", nota="Chiamate la sede al mattino")
        db.richiedi_identita("Y")
        db.richiedi_identita("Z")
        db.una("company_identita_stato", company_profile_id=g.COMPANY["Z"])["stato"] = \
            "verificata"
        pagina = await adm.coda_identita(db)
        assert [str(r.company_profile_id) for r in pagina.items] == [g.COMPANY["X"],
                                                                     g.COMPANY["Y"]]
        x, y = pagina.items
        assert (x.registro_ok, x.registro_motivo, x.nota) == (
            True, None, "Chiamate la sede al mattino")
        assert (x.registro.pec, x.registro.telefono, x.registro.comune, x.registro.provincia) == (
            "x@pec.example.test", "0961 000000", "Catanzaro", "CZ")
        assert x.denominazione_registro == RAGIONE["X"].upper()
        assert (x.titolare.email, x.ragione_sociale, x.partita_iva) == (
            EMAIL["X"], RAGIONE["X"], PIVA["X"])
        # registro di un'altra P.IVA: nessun recapito, non verificabile
        assert (y.registro_ok, y.registro_motivo) == (False, "piva_diversa")
        assert (y.registro.pec, y.registro.telefono, y.denominazione_registro) == (
            None, None, None)
        verificate = await adm.coda_identita(db, stato="verificata")
        assert [str(r.company_profile_id) for r in verificate.items] == [g.COMPANY["Z"]]
        # mai il raw intero nelle letture
        letture = [o["select"] for o in db.ops if o["tabella"] == "company_data"]
        assert letture and all("raw," not in s and not s.endswith(",raw") for s in letture)

    def test_regola_del_registro(self, produzione, monkeypatch):
        azienda = {"partita_iva": "12345678901"}
        dati = {"piva_fetched": "12345678901", "stato_impresa": "Attiva", "sandbox": False}
        assert adm.registro_motivo(azienda, dati) is None
        assert adm.registro_motivo(azienda, None) == "dati_non_importati"
        assert adm.registro_motivo(None, dati) == "dati_non_importati"
        assert adm.registro_motivo(azienda, {**dati, "piva_fetched": "1"}) == "piva_diversa"
        assert adm.registro_motivo(azienda, {**dati, "stato_impresa": "Cessata"}) == \
            "impresa_non_attiva"
        assert adm.registro_motivo(azienda, {**dati, "sandbox": True}) == "dati_sandbox"
        monkeypatch.setenv("OPENAPI_ENV", "sandbox")
        from app.core.config import get_settings

        get_settings.cache_clear()
        assert adm.registro_motivo(azienda, {**dati, "sandbox": True}) is None


class TestDecisioneIdentita:
    @pytest.fixture
    def indice(self, monkeypatch):
        chiamate: list[int] = []
        monkeypatch.setattr(partenariato_indice, "invalida", lambda: chiamate.append(1))
        return chiamate

    async def test_verifica_con_metodo_e_notifica_al_titolare(self, fondo, indice):
        db, _ = await scenario_wp9()
        db.richiedi_identita("X")
        out = await adm.decidi_identita(db, ADMIN, g.COMPANY["X"], IdentitaDecisioneIn(
            esito="verificata", metodo="telefonata_sede", nota="Confermato dal centralino"))
        assert (str(out.company_profile_id), out.stato, out.metodo, out.modificato) == (
            g.COMPANY["X"], "verificata", "telefonata_sede", True)
        assert out.verificata_at is not None and indice == [1]
        [n] = notifiche(db, g.OWNER["X"], testi.TIPO_IDENTITA)
        assert n["titolo"] == "L'identità della tua azienda è verificata"
        assert n["url"] == f"/app/azienda?azienda={g.COMPANY['X']}#partner"
        assert n["company_profile_id"] == g.COMPANY["X"]
        # solo al titolare, non ai membri
        assert [x["user_id"] for x in db.tabelle["notifications"]
                if x["tipo"] == testi.TIPO_IDENTITA] == [g.OWNER["X"]]
        [chiamata] = db.chiamate("fn_identita_decidi")
        assert chiamata["p_nota"] == "Confermato dal centralino"
        # una seconda decisione: nessuna richiesta in attesa
        with pytest.raises(AppError) as exc:
            await adm.decidi_identita(db, ADMIN, g.COMPANY["X"], IdentitaDecisioneIn(
                esito="rifiutata"))
        assert (exc.value.status_code, exc.value.code) == (409, "identita_non_richiesta")

    async def test_metodo_obbligatorio_e_registro_coerente_prima_della_rpc(self, fondo,
                                                                           produzione):
        db, _ = await scenario_wp9()
        db.richiedi_identita("X")
        with pytest.raises(AppError) as exc:
            await adm.decidi_identita(db, ADMIN, g.COMPANY["X"],
                                      IdentitaDecisioneIn(esito="verificata"))
        assert (exc.value.status_code, exc.value.code) == (400, "metodo_obbligatorio")
        db.una("company_data", company_profile_id=g.COMPANY["X"])["sandbox"] = True
        with pytest.raises(AppError) as exc:
            await adm.decidi_identita(db, ADMIN, g.COMPANY["X"], IdentitaDecisioneIn(
                esito="verificata", metodo="pec"))
        assert (exc.value.status_code, exc.value.code) == (409, "identita_non_verificata")
        assert "Registro Imprese" in exc.value.message
        assert db.chiamate("fn_identita_decidi") == []
        # il rifiuto non chiede né metodo né registro coerente
        out = await adm.decidi_identita(db, ADMIN, g.COMPANY["X"], IdentitaDecisioneIn(
            esito="rifiutata", metodo="pec"))
        assert (out.stato, out.metodo) == ("rifiutata", None)
        assert db.chiamate("fn_identita_decidi")[0]["p_metodo"] is None
        [n] = notifiche(db, g.OWNER["X"], testi.TIPO_IDENTITA)
        assert n["titolo"] == "Non abbiamo potuto verificare l'identità della tua azienda"

    async def test_azienda_inesistente_o_malformata(self, fondo):
        db, _ = await scenario_wp9()
        for valore in ("non-un-uuid", str(uuid.uuid4())):
            with pytest.raises(NotFoundError):
                await adm.decidi_identita(db, ADMIN, valore, IdentitaDecisioneIn(
                    esito="verificata", metodo="pec"))
        with pytest.raises(AppError) as exc:
            await adm.decidi_identita(db, ADMIN, str(uuid.uuid4()),
                                      IdentitaDecisioneIn(esito="rifiutata"))
        assert (exc.value.status_code, exc.value.code) == (404, "not_found")

    async def test_revoca(self, fondo, indice):
        db, _ = await scenario_wp9()
        # non verificata: nessuna scrittura né notifica
        out = await adm.revoca_identita(db, ADMIN, g.COMPANY["X"],
                                        IdentitaRevocaIn(motivo="Controllo periodico"))
        assert (out.stato, out.modificato) == ("non_richiesta", False)
        assert notifiche(db, g.OWNER["X"], testi.TIPO_IDENTITA) == [] and indice == []
        db.richiedi_identita("X")
        await adm.decidi_identita(db, ADMIN, g.COMPANY["X"], IdentitaDecisioneIn(
            esito="verificata", metodo="documento_legale_rappresentante"))
        out = await adm.revoca_identita(db, ADMIN, g.COMPANY["X"],
                                        IdentitaRevocaIn(motivo="Documento non più valido"))
        assert (out.stato, out.modificato, out.metodo) == ("non_richiesta", True, None)
        titoli = [n["titolo"] for n in notifiche(db, g.OWNER["X"], testi.TIPO_IDENTITA)]
        assert titoli[-1] == "La verifica dell'identità della tua azienda è stata revocata"
        assert len(indice) == 2

    def test_input(self):
        for motivo in ("", "   ", None, "x" * 501):
            with pytest.raises(AppError) as exc:
                IdentitaRevocaIn(motivo=motivo)
            assert (exc.value.status_code, exc.value.code) == (400, "motivo_obbligatorio")
        with pytest.raises(AppError) as exc:
            IdentitaDecisioneIn(esito="verificata", metodo="pec", nota="x" * 501)
        assert exc.value.status_code == 400
        assert IdentitaDecisioneIn(esito="rifiutata", nota="   ").nota is None


def test_la_notifica_di_verifica_dice_cosa_sblocca():
    """Completamento WP9: le call con il nome hanno la loro proiezione verso
    terzi, quindi l'esito della verifica le nomina insieme al profilo."""
    corpo = testi.corpo_identita("verificata")
    assert "profilo partner" in corpo and "tra aziende verificate" in corpo
    assert "nelle call di partenariato" in corpo
