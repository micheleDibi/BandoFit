"""Email transazionali (`services/email_service.py`): gli URL negli attributi
`href` sono escapati, i testi rimandano alle sezioni con il loro nome nella UI.
L'invio è sostituito da una cattura: nessun provider."""

import pytest

from app.services import email_service


@pytest.fixture
def inviate(monkeypatch) -> list[dict]:
    """`_dispatch` finto: le email che partirebbero."""
    catturate: list[dict] = []

    async def dispatch(to_email, subject, html_body, text_body, headers=None):
        catturate.append({"to": to_email, "subject": subject, "html": html_body,
                          "text": text_body})
        return True

    monkeypatch.setattr(email_service, "_dispatch", dispatch)
    return catturate


class TestAccountEsistente:
    LOGIN = "https://app.bandofit.test/login"

    async def test_link_di_recupero_escapato_nell_href(self, inviate):
        recupero = 'https://app.bandofit.test/recupera?a=1&b=2"><b>x</b>'
        assert await email_service.send_account_exists_email(
            "mario@azienda.it", self.LOGIN, recupero
        ) is True
        [email] = inviate
        atteso = (
            "https://app.bandofit.test/recupera?a=1&amp;b=2&quot;&gt;&lt;b&gt;x&lt;/b&gt;"
        )
        assert f'href="{atteso}"' in email["html"]
        assert recupero not in email["html"] and "<b>x</b>" not in email["html"]
        # il testo semplice resta con l'URL così com'è
        assert recupero in email["text"]

    async def test_link_di_recupero_normale(self, inviate):
        recupero = "https://app.bandofit.test/recupera-password?email=mario%40azienda.it&x=1"
        await email_service.send_account_exists_email("mario@azienda.it", self.LOGIN, recupero)
        [email] = inviate
        assert f'href="{recupero.replace("&", "&amp;")}"' in email["html"]


class TestRicevutaPagamento:
    async def test_rimanda_agli_acquisti_dell_abbonamento(self, inviate):
        assert await email_service.send_ricevuta_pagamento_email(
            "mario@azienda.it", "Piano Pro", 12_200, "https://app.bandofit.test/app/abbonamento"
        ) is True
        [email] = inviate
        assert "nella sezione «Acquisti» del tuo abbonamento" in email["html"]
        assert "Vedi gli acquisti" in email["html"]
        assert "I tuoi acquisti" not in email["html"]
        assert "Vedi i tuoi acquisti" not in email["html"]
