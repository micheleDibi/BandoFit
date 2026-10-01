"""Email di evento dei partenariati (WP7): invito, candidatura, esito e nuovi
messaggi.

Proprietà difese:
- ogni email porta link e header RFC 8058 della disiscrizione dagli EVENTI
  (`tipo=eventi`, mai il digest), e il link funziona sulla rotta pubblica;
- senza un token di disiscrizione valido l'email non parte;
- contenuti: solo il titolo del bando, lo pseudonimo per call della
  controparte (dove previsto) e il nome dell'azienda destinataria; le firme
  non accettano testi dei messaggi, motivi o dati di altre aziende; dati
  escapati nell'HTML; oggetti fissi;
- log con l'indirizzo mascherato, senza titolo del bando né pseudonimo.
"""

import inspect
import json
import logging
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.routers import partenariati_email
from app.core.errors import register_exception_handlers
from app.services import email_service, partenariato_notifiche
from app.services.email_service import (
    send_partner_candidatura_email,
    send_partner_esito_email,
    send_partner_invito_email,
    send_partner_messaggi_email,
)

API = "https://api.bandofit.test/api/v1"
TOKEN = "3f2a1c4e-9b7d-4e21-8c3a-5d6e7f809a1b"
DISISCRIZIONE = f"{API}/partenariati/email/unsubscribe?token={TOKEN}&tipo=eventi"
CTA = "https://app.bandofit.test/app/partenariati/call/c1?azienda=a1&tab=candidature"
BANDO = "Bando sintetico <b>furbo</b> & co"
PSEUDO = "ABCDEFGHIJ234567"
AZIENDA = "Alfa <Srl>"
DESTINATARIO = "mario.rossi@azienda.it"

# Tutte le email di evento, con tutti i dati che accettano.
EMAIL = {
    "invito": (send_partner_invito_email, {
        "bando_titolo": BANDO, "cta_url": CTA, "unsubscribe_token": TOKEN,
        "azienda_destinataria": AZIENDA, "scadenza": "13/10/2026"}),
    "candidatura": (send_partner_candidatura_email, {
        "bando_titolo": BANDO, "pseudonimo": PSEUDO, "cta_url": CTA,
        "unsubscribe_token": TOKEN, "azienda_destinataria": AZIENDA}),
    "esito_candidatura": (send_partner_esito_email, {
        "esito": "accettata", "tipo": "candidatura", "bando_titolo": BANDO, "cta_url": CTA,
        "unsubscribe_token": TOKEN, "azienda_destinataria": AZIENDA}),
    "esito_invito": (send_partner_esito_email, {
        "esito": "rifiutata", "tipo": "invito", "bando_titolo": BANDO, "cta_url": CTA,
        "unsubscribe_token": TOKEN, "pseudonimo": PSEUDO, "azienda_destinataria": AZIENDA}),
    "messaggi": (send_partner_messaggi_email, {
        "bando_titolo": BANDO, "cta_url": CTA, "unsubscribe_token": TOKEN,
        "pseudonimo": PSEUDO, "azienda_destinataria": AZIENDA}),
}
FUNZIONI = {
    send_partner_invito_email, send_partner_candidatura_email, send_partner_esito_email,
    send_partner_messaggi_email,
}
# Gli unici dati che le email di evento possono ricevere (e quindi mostrare).
PARAMETRI_AMMESSI = {
    "to_email", "bando_titolo", "cta_url", "unsubscribe_token", "azienda_destinataria",
    "scadenza", "pseudonimo", "esito", "tipo",
}


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    for chiave, valore in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
        "API_PUBLIC_URL": API + "/",
        "FRONTEND_URL": "https://app.bandofit.test",
        "SMTP_HOST": "",
        "RESEND_API_KEY": "",
    }.items():
        monkeypatch.setenv(chiave, valore)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def posta(monkeypatch):
    """SMTP finto: i messaggi veri (EmailMessage) che partirebbero."""
    import aiosmtplib

    messaggi = []

    async def invia(message, **kwargs):
        messaggi.append(message)

    monkeypatch.setattr(aiosmtplib, "send", invia)
    monkeypatch.setenv("SMTP_HOST", "smtp.test.it")
    monkeypatch.setenv("EMAIL_FROM", "BandoFit <notifiche@bandofit.it>")
    from app.core.config import get_settings

    get_settings.cache_clear()
    return messaggi


def parti(message) -> tuple[str, str]:
    return (message.get_body(("html",)).get_content(),
            message.get_body(("plain",)).get_content())


async def manda(nome: str, **modifiche):
    funzione, argomenti = EMAIL[nome]
    return await funzione(DESTINATARIO, **{**argomenti, **modifiche})


# ------------------------------------------------------ disiscrizione e header


class TestDisiscrizioneEventi:
    @pytest.mark.parametrize("nome", list(EMAIL))
    async def test_header_rfc_8058_verso_gli_eventi(self, posta, nome):
        assert await manda(nome) is True
        [message] = posta
        assert message["List-Unsubscribe"] == f"<{DISISCRIZIONE}>"
        assert message["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        html_part, text_part = parti(message)
        assert DISISCRIZIONE in text_part
        assert DISISCRIZIONE.replace("&", "&amp;") in html_part
        assert "Disattivale con un clic" in html_part
        assert "tipo=digest" not in message.as_string()

    async def test_token_normalizzato(self, posta):
        await manda("invito", unsubscribe_token=TOKEN.upper())
        assert posta[0]["List-Unsubscribe"] == f"<{DISISCRIZIONE}>"

    @pytest.mark.parametrize("token", [None, "", "non-un-token", "123", object()])
    async def test_senza_token_valido_non_parte(self, posta, caplog, token):
        with caplog.at_level(logging.INFO, logger="bandofit.email"):
            for nome in EMAIL:
                assert await manda(nome, unsubscribe_token=token) is False
        assert posta == []
        testo = "\n".join(r.getMessage() for r in caplog.records)
        assert "m***@azienda.it" in testo and "mario.rossi" not in testo

    async def test_il_link_disiscrive_dagli_eventi_sulla_rotta_pubblica(self, posta,
                                                                        monkeypatch):
        chiamate = []

        async def disiscrivi(primary, token, tipo):
            chiamate.append((token, tipo))

        monkeypatch.setattr(partenariato_notifiche, "unsubscribe_by_token", disiscrivi)
        await manda("messaggi")
        link = posta[0]["List-Unsubscribe"].strip("<>")
        parti_url = urlsplit(link)
        assert parse_qs(parti_url.query) == {"token": [TOKEN], "tipo": ["eventi"]}
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(partenariati_email.router, prefix="/api/v1")
        app.dependency_overrides[deps.get_primary] = lambda: object()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.post(f"{parti_url.path}?{parti_url.query}")  # one-click
        assert resp.status_code == 204
        assert chiamate == [(TOKEN, "eventi")]

    async def test_conferma_html_porta_agli_avvisi_email(self, monkeypatch):
        # Dal browser la conferma ha il link alla scheda «Avvisi email».
        async def disiscrivi(primary, token, tipo):
            return None

        monkeypatch.setattr(partenariato_notifiche, "unsubscribe_by_token", disiscrivi)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(partenariati_email.router, prefix="/api/v1")
        app.dependency_overrides[deps.get_primary] = lambda: object()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/partenariati/email/unsubscribe?token={TOKEN}&tipo=eventi",
                headers={"accept": "text/html,application/xhtml+xml"},
            )
        assert resp.status_code == 200
        assert "/app/preferenze?tab=avvisi" in resp.text


# ------------------------------------------------------------------ contenuti


class TestContenuti:
    async def test_invito(self, posta):
        await manda("invito")
        [message] = posta
        html_part, text_part = parti(message)
        assert message["Subject"] == (
            "Hai ricevuto un invito a una call di partenariato — BandoFit")
        assert ("Un'azienda che cerca partner per il bando «Bando sintetico <b>furbo</b> & co» "
                "ha invitato la tua azienda «Alfa <Srl>» a partecipare alla sua call di "
                "partenariato.") in text_part
        assert "L'invito scade il 13/10/2026." in text_part
        assert f"Vedi l'invito: {CTA}" in text_part
        assert "Vedi l&#x27;invito" in html_part  # etichetta escapata da `_branded_html`
        # link della CTA escapato una sola volta (niente «&amp;amp;»)
        assert f'href="{CTA.replace("&", "&amp;")}"' in html_part
        assert "riferimento per questa call" not in text_part  # del creatore nessun dato

    async def test_invito_minimo(self, posta):
        await send_partner_invito_email(DESTINATARIO, bando_titolo=None, cta_url=CTA,
                                        unsubscribe_token=TOKEN)
        _, text_part = parti(posta[0])
        assert ("Un'azienda che cerca partner ha invitato la tua azienda a partecipare alla "
                "sua call di partenariato.") in text_part
        assert "scade" not in text_part

    async def test_candidatura(self, posta):
        await manda("candidatura")
        [message] = posta
        html_part, text_part = parti(message)
        assert message["Subject"] == (
            "Una nuova candidatura alla tua call di partenariato — BandoFit")
        assert (f"Un'azienda (riferimento per questa call: {PSEUDO}) si è candidata alla call "
                "di partenariato della tua azienda «Alfa <Srl>» per il bando «Bando sintetico "
                "<b>furbo</b> & co».") in text_part
        assert PSEUDO in html_part and "Vedi la candidatura" in html_part

    async def test_candidatura_senza_azienda_ne_bando(self, posta):
        await send_partner_candidatura_email(DESTINATARIO, bando_titolo=None, pseudonimo=PSEUDO,
                                             cta_url=CTA, unsubscribe_token=TOKEN)
        _, text_part = parti(posta[0])
        assert (f"Un'azienda (riferimento per questa call: {PSEUDO}) si è candidata alla tua "
                "call di partenariato.") in text_part

    @pytest.mark.parametrize(("tipo", "esito", "oggetto", "frase", "cta"), [
        ("candidatura", "accettata", "La tua candidatura è stata accettata — BandoFit",
         "L'azienda che ha pubblicato la call di partenariato per il bando «Horizon» ha "
         "accettato la candidatura della tua azienda.", "Apri la conversazione"),
        ("candidatura", "rifiutata", "Aggiornamento sulla tua candidatura — BandoFit",
         "L'azienda che ha pubblicato la call di partenariato per il bando «Horizon» non ha "
         "accolto la candidatura della tua azienda.", "Vedi la candidatura"),
        ("invito", "accettata", "Il tuo invito è stato accettato — BandoFit",
         f"Un'azienda (riferimento per questa call: {PSEUDO}) ha accettato l'invito alla tua "
         "call di partenariato per il bando «Horizon».", "Apri la conversazione"),
        ("invito", "rifiutata", "Aggiornamento sul tuo invito — BandoFit",
         f"Un'azienda (riferimento per questa call: {PSEUDO}) ha rifiutato l'invito alla tua "
         "call di partenariato per il bando «Horizon».", "Vedi la call"),
    ])
    async def test_esito(self, posta, tipo, esito, oggetto, frase, cta):
        assert await send_partner_esito_email(
            DESTINATARIO, esito=esito, tipo=tipo, bando_titolo="Horizon", cta_url=CTA,
            unsubscribe_token=TOKEN, pseudonimo=PSEUDO) is True
        [message] = posta
        _, text_part = parti(message)
        assert message["Subject"] == oggetto
        assert frase in text_part
        assert f"{cta}: {CTA}" in text_part
        # Chi ha creato la call non ha pseudonimo: alla candidata non si mostra mai.
        assert (PSEUDO in text_part) is (tipo == "invito")
        assert ("verificate i dati dell'altra azienda sul Registro Imprese" in text_part) is (
            esito == "accettata")

    async def test_esito_con_l_azienda_destinataria(self, posta):
        await manda("esito_candidatura")
        _, text_part = parti(posta[0])
        assert "la candidatura della tua azienda «Alfa <Srl>»." in text_part

    @pytest.mark.parametrize(("esito", "tipo"), [
        ("ritirata", "candidatura"), ("accettata", "altro"), ("", ""), (None, None)])
    async def test_esito_fuori_vocabolario_non_parte(self, posta, esito, tipo):
        assert await manda("esito_candidatura", esito=esito, tipo=tipo) is False
        assert posta == []

    async def test_messaggi(self, posta):
        await manda("messaggi")
        [message] = posta
        html_part, text_part = parti(message)
        assert message["Subject"] == "Hai nuovi messaggi su una call di partenariato — BandoFit"
        assert ("Ci sono nuovi messaggi per la tua azienda «Alfa <Srl>» nella conversazione "
                "sulla call di partenariato per il bando «Bando sintetico <b>furbo</b> & co», "
                f"con l'azienda (riferimento per questa call: {PSEUDO}).") in text_part
        assert "il testo dei messaggi non viene inviato via email" in text_part
        assert "finché non avrai letto questi messaggi" in text_part
        assert "Leggi i messaggi" in html_part

    async def test_messaggi_verso_la_candidata(self, posta):
        await send_partner_messaggi_email(DESTINATARIO, bando_titolo="Horizon", cta_url=CTA,
                                          unsubscribe_token=TOKEN)
        _, text_part = parti(posta[0])
        assert ("Ci sono nuovi messaggi nella conversazione sulla call di partenariato per il "
                "bando «Horizon».") in text_part
        assert "riferimento" not in text_part

    @pytest.mark.parametrize("nome", list(EMAIL))
    async def test_dati_escapati_nell_html(self, posta, nome):
        await manda(nome)
        html_part, _ = parti(posta[0])
        assert "<b>furbo</b>" not in html_part and "Alfa <Srl>" not in html_part
        assert "Bando sintetico &lt;b&gt;furbo&lt;/b&gt; &amp; co" in html_part
        assert CTA.replace("&", "&amp;") in html_part  # href del bottone escapato
        assert "Famiglia" not in html_part


# ------------------------------------------------------------ dati vietati


class TestNessunDatoVietato:
    @pytest.mark.parametrize("funzione", sorted(FUNZIONI, key=lambda f: f.__name__))
    def test_le_firme_non_accettano_altri_dati(self, funzione):
        """Niente testo dei messaggi, della candidatura o del motivo, niente
        nome, P.IVA, contatti o referente di un'altra azienda: chi chiama non
        può passarli nemmeno per sbaglio."""
        parametri = inspect.signature(funzione).parameters
        assert set(parametri) <= PARAMETRI_AMMESSI
        assert not any(p.kind is p.VAR_KEYWORD or p.kind is p.VAR_POSITIONAL
                       for p in parametri.values())
        assert [n for n, p in parametri.items() if p.kind is p.POSITIONAL_OR_KEYWORD] == [
            "to_email"]  # il resto solo per nome: nessuno scambio di posizione

    async def test_argomenti_sconosciuti_rifiutati(self):
        for funzione, argomenti in EMAIL.values():
            with pytest.raises(TypeError):
                await funzione(DESTINATARIO, **argomenti, testo="Ecco la nostra offerta")

    @pytest.mark.parametrize("nome", list(EMAIL))
    async def test_solo_i_dati_passati_e_oggetto_fisso(self, posta, nome):
        await manda(nome)
        [message] = posta
        html_part, text_part = parti(message)
        # L'oggetto (che finisce nei log) non porta nessun dato.
        for dato in ("furbo", PSEUDO, "Alfa", "13/10/2026"):
            assert dato not in message["Subject"]
        # Nessun indirizzo email nei corpi, nemmeno quello del destinatario.
        assert "@" not in html_part and "@" not in text_part

    async def test_lo_pseudonimo_non_esce_dove_non_serve(self, posta):
        await send_partner_esito_email(
            DESTINATARIO, esito="rifiutata", tipo="candidatura", bando_titolo="Horizon",
            cta_url=CTA, unsubscribe_token=TOKEN, pseudonimo=PSEUDO)
        html_part, text_part = parti(posta[0])
        assert PSEUDO not in html_part and PSEUDO not in text_part


# ------------------------------------------------------------------------ log


class TestLog:
    async def test_indirizzo_mascherato_e_nessun_dato_nei_log(self, caplog, monkeypatch):
        import aiosmtplib

        async def rifiuta(message, **kwargs):
            raise aiosmtplib.SMTPRecipientsRefused(
                [aiosmtplib.SMTPRecipientRefused(550, "no such user", DESTINATARIO)])

        with caplog.at_level(logging.INFO, logger="bandofit.email"):
            for nome in EMAIL:  # fallback di sviluppo (solo log)
                assert await manda(nome) is True
            monkeypatch.setenv("SMTP_HOST", "smtp.test.it")
            from app.core.config import get_settings

            get_settings.cache_clear()
            monkeypatch.setattr(aiosmtplib, "send", rifiuta)
            for nome in EMAIL:
                assert await manda(nome) is False
        testo = "\n".join(r.getMessage() for r in caplog.records)
        assert "m***@azienda.it" in testo and "SMTPRecipientsRefused" in testo
        for dato in ("mario.rossi", PSEUDO, "furbo", "Alfa", TOKEN):
            assert dato not in testo, dato


class TestResend:
    async def test_payload_con_testo_e_header(self, monkeypatch):
        payloads = []

        def handler(request: httpx.Request) -> httpx.Response:
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "x"})

        reale = httpx.AsyncClient

        def client_finto(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return reale(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", client_finto)
        monkeypatch.setenv("RESEND_API_KEY", "rk")
        from app.core.config import get_settings

        get_settings.cache_clear()
        assert await manda("candidatura") is True
        [payload] = payloads
        assert payload["to"] == [DESTINATARIO]
        assert payload["text"].startswith("Hai ricevuto una candidatura.")
        assert payload["headers"] == {
            "List-Unsubscribe": f"<{DISISCRIZIONE}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }


def test_branded_html_escapa_il_link_della_cta():
    """L'`href` della CTA è escapato con le virgolette: un URL non può
    chiudere l'attributo né aprire un tag."""
    out = email_service._branded_html("T", [], "Apri", 'https://x.test/a?b=1&c="><script>', "f")
    assert 'href="https://x.test/a?b=1&amp;c=&quot;&gt;&lt;script&gt;"' in out
    assert "<script>" not in out


def test_modulo_senza_dipendenze_nuove():
    """Le email di evento usano solo la busta comune (`_branded_html`,
    `_dispatch`)."""
    sorgente = inspect.getsource(email_service._invia_evento_partner)
    assert "_branded_html(" in sorgente and "_dispatch(" in sorgente
