"""POST /auth/register non deve rivelare se un indirizzo è già registrato (CWE-204).

È il test che dimostra la chiusura dell'oracolo, quindi asserisce sul contratto
osservabile dall'esterno — status e byte del corpo — e non sui dettagli interni.

Tre proprietà, che vanno tenute insieme perché una da sola non basta:
  1. i due rami producono la STESSA risposta (anche quando scatta il cooldown);
  2. il ramo «indirizzo libero» non manda la password a GoTrue — altrimenti chi
     registra può rileggere l'esistenza dal token endpoint di Supabase, che il
     browser raggiunge con la anon key;
  3. il ramo «già in attesa di conferma» non emette un token — emetterlo
     invaliderebbe il link che la vittima ha già in casella.
"""

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_primary
from app.api.routers import auth as auth_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.services import auth_service, rate_limit_service

# Riferimento alla funzione vera, catturato prima che la fixture autouse la
# sostituisca: serve a TestPavimentoLatenza.
_LIVELLA_LATENZA = auth_service._livella_latenza

PROFILO = {"id": "11111111-1111-1111-1111-111111111111", "email": "mario@test.it"}
PIANO_OK = [{"tipo_prezzo": "importo"}]
POSIZIONE_CTO = {"id": 3, "nome": "CTO", "slug": "cto"}

BODY = {
    "email": "mario@test.it",
    "nome": "Mario",
    "cognome": "Rossi",
    "telefono": "+393471234567",
    "job_position_slug": "cto",
    "plan_slug": "pro",
}


class FakeQuery:
    """Query PostgREST minimale: registra i filtri e restituisce una risposta
    preconfezionata per tabella."""

    def __init__(self, table: str, rows: dict):
        self._table = table
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def ilike(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def insert(self, *_a, **_k):
        return self

    def update(self, *_a, **_k):
        return self

    def is_(self, *_a, **_k):
        return self

    async def execute(self):
        return SimpleNamespace(data=self._rows.get(self._table, []))


class FakePrimary:
    def __init__(
        self,
        *,
        profilo: dict | None,
        confermato: bool = True,
        create_raises=None,
        verdetti: dict[str, bool] | None = None,
    ):
        self.rows = {
            "subscription_plans": PIANO_OK,
            "job_positions": [POSIZIONE_CTO],
            "profiles": [profilo] if profilo else [],
            "auth_tokens": [],
        }
        self.create_calls: list[dict] = []
        self.rpc_calls: list[tuple] = []
        self.verdetti = verdetti or {}
        self._create_raises = create_raises

        async def create_user(payload):
            self.create_calls.append(payload)
            if self._create_raises:
                raise self._create_raises
            return SimpleNamespace(user=SimpleNamespace(id=PROFILO["id"]))

        async def get_user_by_id(_uid):
            return SimpleNamespace(
                user=SimpleNamespace(email_confirmed_at="2026-01-01" if confermato else None)
            )

        self.auth = SimpleNamespace(
            admin=SimpleNamespace(create_user=create_user, get_user_by_id=get_user_by_id)
        )

    def table(self, name: str):
        return FakeQuery(name, self.rows)

    def rpc(self, name: str, params: dict):
        self.rpc_calls.append((name, params))
        # Consentito salvo che il test dichiari il contrario per quel bucket.
        return FakeQuery("_rpc", {"_rpc": self.verdetti.get(params["p_bucket"], True)})


def make_client(primary: FakePrimary) -> TestClient:
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/v1")
    # Senza gli handler, le AppError non diventano il body {"error": {...}} e il
    # confronto byte-a-byte non direbbe nulla di vero.
    register_exception_handlers(app)
    app.dependency_overrides[get_primary] = lambda: primary
    return TestClient(app)


def post_register(primary: FakePrimary, headers: dict | None = None, **overrides):
    with make_client(primary) as client:
        return client.post("/api/v1/auth/register", json={**BODY, **overrides}, headers=headers)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    """Settings finte: il router chiama client_ip() → get_settings(), quindi
    senza queste il file non gira su un checkout pulito (il .env del repo non
    ha le chiavi Supabase)."""
    for key, value in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
        "RATE_LIMIT_PEPPER": "pepe-di-test",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_cooldown_e_latenza(monkeypatch):
    auth_service._last_sent.clear()
    # Il pavimento di latenza è testato a parte: qui aspetterebbe e basta.
    monkeypatch.setattr(auth_service, "_livella_latenza", lambda _started: _noop())
    yield
    auth_service._last_sent.clear()


async def _noop():
    return None


class _Inviate(list):
    """Lista dei nomi delle email partite, con gli argomenti a lato: i test
    confrontano i nomi (leggibile), chi deve controllare gli URL guarda `args`."""

    def __init__(self):
        super().__init__()
        self.args: list[tuple] = []


@pytest.fixture(autouse=True)
def _cattura_email(monkeypatch):
    """Le email partono in background (_spawn su _send_best_effort, che riceve
    una factory): intercettiamo la factory e leggiamo quale send_* sarebbe
    partita, senza far nascere task veri."""
    inviate = _Inviate()

    def fake_best_effort(send):
        # getattr e non send.func: se un giorno la factory diventasse una
        # lambda, meglio un nome inutile che 15 AttributeError illeggibili.
        inviate.append(getattr(send, "func", send).__name__)
        inviate.args.append(getattr(send, "args", ()))
        return _noop()

    monkeypatch.setattr(auth_service, "_send_best_effort", fake_best_effort)
    monkeypatch.setattr(auth_service, "_spawn", lambda coro: coro.close())
    return inviate


class TestRispostaIndistinguibile:
    def test_indirizzo_libero_e_indirizzo_registrato_danno_la_stessa_risposta(self):
        nuovo = post_register(FakePrimary(profilo=None))
        esistente = post_register(FakePrimary(profilo=PROFILO))

        assert nuovo.status_code == esistente.status_code == 202
        assert nuovo.content == esistente.content
        assert nuovo.json() == {"ok": True}

    def test_il_cooldown_non_si_vede_dalla_risposta(self):
        # Il vecchio 409 «Email già inviata da poco» era distinguibile dalla
        # 202: due tentativi ravvicinati devono restare identici.
        primo = post_register(FakePrimary(profilo=None))
        secondo = post_register(FakePrimary(profilo=None))

        assert primo.status_code == secondo.status_code == 202
        assert primo.content == secondo.content

    def test_entrambi_i_rami_rispondono_202(self):
        # Il 409 era l'oracolo: su questa rotta non esiste più. Asserzione
        # positiva — «!= 409» sarebbe soddisfatto anche da un 500.
        for primary in (FakePrimary(profilo=None), FakePrimary(profilo=PROFILO)):
            assert post_register(primary).status_code == 202


class TestNessunaPasswordAGoTrue:
    def test_create_user_non_riceve_la_password(self):
        primary = FakePrimary(profilo=None)
        post_register(primary)

        [payload] = primary.create_calls
        # Il cuore della difesa: con una password scelta da chi registra, lo
        # stato dell'account resterebbe osservabile dall'esterno anche con la
        # risposta neutra (vedi la docstring di auth_service). Questo test è la
        # rete che impedisce di rimetterla per distrazione.
        assert "password" not in payload
        assert payload["email_confirm"] is False

    def test_la_rotta_rifiuta_una_password_nel_body(self):
        # Difesa in profondità: se un client la mandasse comunque, non deve
        # finire in user_metadata né essere applicata.
        primary = FakePrimary(profilo=None)
        post_register(primary, password="segreta-123")

        [payload] = primary.create_calls
        assert "password" not in payload
        assert "password" not in payload["user_metadata"]


class TestAvvisoFuoriBanda:
    def test_indirizzo_confermato_riceve_hai_gia_un_account(self, _cattura_email):
        post_register(FakePrimary(profilo=PROFILO, confermato=True))
        assert _cattura_email == ["send_account_exists_email"]

    def test_indirizzo_in_attesa_riceve_l_invito_a_completare(self, _cattura_email):
        post_register(FakePrimary(profilo=PROFILO, confermato=False))
        assert _cattura_email == ["send_account_pending_email"]

    def test_indirizzo_gia_registrato_non_viene_ricreato(self):
        primary = FakePrimary(profilo=PROFILO)
        post_register(primary)
        assert primary.create_calls == []

    def test_il_ramo_in_attesa_non_emette_un_nuovo_token(self, monkeypatch, _cattura_email):
        # token_service.issue invalida i token precedenti: emetterne uno qui
        # farebbe morire il link di conferma già in casella alla vittima, su
        # richiesta di un anonimo.
        emessi: list = []
        monkeypatch.setattr(
            auth_service.token_service,
            "issue",
            lambda *a, **k: emessi.append(a) or _noop(),
        )
        post_register(FakePrimary(profilo=PROFILO, confermato=False))

        assert emessi == []
        # E il link spedito è la pagina di richiesta, senza token in coda.
        [(_, url)] = _cattura_email.args
        assert url.endswith("/conferma-email")
        assert "token=" not in url

    def test_indirizzo_libero_riceve_la_conferma(self, _cattura_email):
        post_register(FakePrimary(profilo=None))
        assert _cattura_email == ["send_confirmation_email"]


class TestCablaggio:
    """Che le difese siano collegate alla rotta, non solo che esistano.

    Senza questi test si può togliere `client_ip(request)` o `_gate_ip` dal
    router e la suite resta verde, mentre il limite per IP smette di esistere:
    `_gate_ip` esce subito quando l'IP è ignoto.
    """

    def test_l_ip_del_chiamante_arriva_ai_contatori(self):
        primary = FakePrimary(profilo=None)
        post_register(primary, headers={"cf-connecting-ip": "203.0.113.7"})

        atteso = rate_limit_service.bucket("ip", "203.0.113.7")
        assert atteso in [params["p_bucket"] for _, params in primary.rpc_calls]

    def test_burst_per_ip_diventa_un_429_col_body_del_repo(self):
        # RateLimitedError → 429 + {"error": {"code": "rate_limited", …}}: il
        # contratto che il client vede davvero, non l'attributo dell'eccezione.
        primary = FakePrimary(
            profilo=None,
            verdetti={rate_limit_service.bucket("ip", "203.0.113.7"): False},
        )

        risposta = post_register(primary, headers={"cf-connecting-ip": "203.0.113.7"})

        assert risposta.status_code == 429
        assert risposta.json()["error"]["code"] == "rate_limited"
        assert primary.create_calls == []

    def test_le_soglie_arrivano_dalla_configurazione(self):
        primary = FakePrimary(profilo=None)
        post_register(primary, headers={"cf-connecting-ip": "203.0.113.7"})

        settings = get_settings()
        burst = next(
            params
            for _, params in primary.rpc_calls
            if params["p_bucket"] == rate_limit_service.bucket("ip", "203.0.113.7")
        )
        assert burst["p_limit"] == settings.register_ip_burst_limit
        assert burst["p_window_seconds"] == settings.register_ip_burst_window_seconds

    def test_il_budget_email_sopprime_l_invio_ma_non_l_account(self, _cattura_email):
        # La proprietà dichiarata in _registra_o_avvisa: bloccare l'account
        # darebbe a un anonimo il modo di impedire l'iscrizione a una persona
        # precisa, in silenzio.
        primary = FakePrimary(
            profilo=PROFILO,
            verdetti={rate_limit_service.bucket("email", "mario@test.it"): False},
        )

        risposta = post_register(primary)

        assert risposta.status_code == 202
        assert _cattura_email == []  # nessun avviso: budget esaurito

    def test_il_pavimento_e_collegato_alla_rotta(self, monkeypatch):
        # Qui _livella_latenza è quella vera: si stub-a solo asyncio.sleep. Se
        # qualcuno togliesse il `finally` da register, questo test lo vedrebbe.
        monkeypatch.setattr(auth_service, "_livella_latenza", _LIVELLA_LATENZA)
        dormite: list[float] = []

        async def fake_sleep(secondi):
            dormite.append(secondi)

        monkeypatch.setattr(auth_service.asyncio, "sleep", fake_sleep)

        post_register(FakePrimary(profilo=None))
        post_register(FakePrimary(profilo=PROFILO))

        # Entrambi i rami passano dal pavimento, non solo quello veloce.
        assert len(dormite) == 2
        assert all(d > 0 for d in dormite)


class TestPavimentoLatenza:
    """Il quarto requisito: uniformato il corpo, il cronometro non deve
    diventare il nuovo oracolo. Usa _LIVELLA_LATENZA, la funzione vera: la
    fixture autouse la sostituisce con un no-op (sennò ogni test aspetterebbe
    un secondo e mezzo), quindi senza questa classe non la proverebbe nessuno.
    """

    @pytest.fixture()
    def dormite(self, monkeypatch):
        registrate: list[float] = []

        async def fake_sleep(secondi):
            registrate.append(secondi)

        monkeypatch.setattr(auth_service.asyncio, "sleep", fake_sleep)
        return registrate

    async def test_attende_il_tempo_mancante_al_target(self, dormite):
        await _LIVELLA_LATENZA(time.monotonic() - 0.2)

        # target 1,5s con 0,2 già trascorsi → resta circa 1,3
        assert len(dormite) == 1
        assert 1.2 < dormite[0] <= 1.31

    async def test_non_attende_se_il_ramo_ha_gia_superato_il_target(self, dormite):
        # Ramo lento (GoTrue in affanno): il pavimento non accorcia nulla e non
        # deve allungare. È il residuo dichiarato nei rischi, non un bug.
        await _LIVELLA_LATENZA(time.monotonic() - 99)

        assert dormite == []


class TestCorseECasiLimite:
    def test_duplicato_scoperto_da_gotrue_resta_neutro(self, _cattura_email):
        # Due registrazioni concorrenti sullo stesso indirizzo: il pre-check
        # dice «libero», create_user dice «esiste». Stessa uscita del ramo
        # normale, altrimenti la corsa diventa un oracolo.
        primary = FakePrimary(
            profilo=None, create_raises=Exception("User already registered")
        )
        primary.rows["profiles"] = [PROFILO]  # il concorrente ha finito nel frattempo

        risposta = post_register(primary)

        assert risposta.status_code == 202
        assert risposta.json() == {"ok": True}
        assert _cattura_email == ["send_account_exists_email"]

    def test_email_normalizzata_prima_del_confronto(self):
        primary = FakePrimary(profilo=None)
        post_register(primary, email="Mario@TEST.it")
        [payload] = primary.create_calls
        assert payload["email"] == "mario@test.it"

    def test_il_gate_email_non_impedisce_la_creazione_dell_account(self, monkeypatch):
        # Il budget email deve sopprimere l'INVIO, mai l'account: se bloccasse
        # la registrazione, un anonimo potrebbe impedire a una persona precisa
        # di iscriversi, in silenzio.
        monkeypatch.setattr(auth_service, "_cooldown_ok", lambda *_a: False)
        primary = FakePrimary(profilo=None)

        risposta = post_register(primary)

        assert risposta.status_code == 202
        assert len(primary.create_calls) == 1
