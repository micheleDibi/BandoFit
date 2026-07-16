"""IP del client dietro Cloudflare + nginx (app/core/net.py).

Sbagliare qui è insidioso perché non rompe nulla di visibile: il rate limit per
IP continua a «funzionare» contando su una chiave sbagliata — condivisa da
tutti, e quindi capace di bloccare tutti insieme, o falsificabile a piacere dal
client. Da qui l'insistenza sul contare gli hop da DESTRA.
"""

import pytest
from starlette.requests import Request

from app.core.config import get_settings
from app.core.net import client_ip


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    for key, value in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
        "TRUSTED_PROXY_HOPS": "2",  # Cloudflare + nginx, come in produzione
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_request(headers: dict[str, str] | None = None, peer: str = "172.17.0.1") -> Request:
    return Request(
        {
            "type": "http",
            "headers": [
                (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
            ],
            "client": (peer, 51234),
        }
    )


class TestCloudflare:
    def test_cf_connecting_ip_ha_la_precedenza(self):
        req = make_request(
            {"cf-connecting-ip": "203.0.113.7", "x-forwarded-for": "9.9.9.9, 8.8.8.8"}
        )
        assert client_ip(req) == "203.0.113.7"

    def test_cf_connecting_ip_malformato_ripiega_su_xff(self):
        req = make_request(
            {"cf-connecting-ip": "non-un-ip", "x-forwarded-for": "203.0.113.7, 198.51.100.1"}
        )
        assert client_ip(req) == "203.0.113.7"


class TestForwardedFor:
    def test_ultimo_hop_fidato_e_non_il_primo(self):
        # Catena reale: client → Cloudflare → nginx. Ogni hop APPENDE, quindi
        # con 2 hop fidati il client vero è il penultimo elemento.
        req = make_request({"x-forwarded-for": "203.0.113.7, 198.51.100.1"})
        assert client_ip(req) == "203.0.113.7"

    def test_xff_iniettato_dal_client_non_sposta_il_risultato(self):
        # L'attaccante manda «6.6.6.6»: Cloudflare gli appende il suo IP reale e
        # nginx appende quello di Cloudflare. Il valore iniettato scivola in
        # testa, dove non lo guardiamo.
        req = make_request({"x-forwarded-for": "6.6.6.6, 203.0.113.7, 198.51.100.1"})
        assert client_ip(req) == "203.0.113.7"

    def test_xff_piu_corto_degli_hop_attesi_non_solleva(self):
        # Con parts[-2] su un solo elemento si avrebbe un IndexError, cioè un
        # 500 su OGNI registrazione. Meglio dichiarare l'IP ignoto.
        assert client_ip(make_request({"x-forwarded-for": "203.0.113.7"})) is None

    def test_senza_xff_ip_ignoto(self):
        assert client_ip(make_request()) is None

    def test_valore_non_ip_ignorato(self):
        req = make_request({"x-forwarded-for": "unknown, 198.51.100.1"})
        assert client_ip(req) is None

    def test_spazi_tollerati(self):
        req = make_request({"x-forwarded-for": "  203.0.113.7 ,  198.51.100.1 "})
        assert client_ip(req) == "203.0.113.7"


class TestIPv6:
    def test_troncato_alla_64(self):
        # Un utente IPv6 ha tipicamente un /64 intero: limitare il /128 sarebbe
        # aggirabile cambiando indirizzo a ogni richiesta.
        req = make_request({"cf-connecting-ip": "2001:db8:1234:5678:abcd:ef01:2345:6789"})
        assert client_ip(req) == "2001:db8:1234:5678::/64"

    def test_indirizzi_nello_stesso_64_condividono_il_bucket(self):
        primo = client_ip(make_request({"cf-connecting-ip": "2001:db8:1:2:3::1"}))
        secondo = client_ip(make_request({"cf-connecting-ip": "2001:db8:1:2:ffff::9"}))
        assert primo == secondo


class TestSenzaProxy:
    def test_hops_zero_usa_il_peer(self, monkeypatch):
        # Sviluppo locale: nessun proxy davanti, il peer È il client.
        monkeypatch.setenv("TRUSTED_PROXY_HOPS", "0")
        get_settings.cache_clear()
        assert client_ip(make_request(peer="127.0.0.1")) == "127.0.0.1"

    def test_il_default_non_si_fida_del_peer(self, monkeypatch):
        # Senza la env var: è il DEFAULT del codice a dover valere 2. In Docker
        # il peer è il gateway della bridge, uguale per tutti — prenderlo per
        # l'IP del client bloccherebbe l'intero pianeta al primo abusatore.
        monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
        get_settings.cache_clear()

        assert get_settings().trusted_proxy_hops == 2
        assert client_ip(make_request(peer="172.17.0.1")) is None
