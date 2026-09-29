"""Email della decisione di moderazione dei partenariati (WP9, DSA art. 17).

Proprietà difese:
- porta lo statement of reasons completo (testo e HTML), con i dati escapati
  nell'HTML e le righe conservate;
- è un avviso obbligatorio: nessuna disiscrizione (né link né header RFC
  8058), oggetto fisso senza dati;
- con un tipo di contenuto sconosciuto o uno statement vuoto non parte;
- log con l'indirizzo mascherato (mai in chiaro), senza testi.
"""

import inspect
import logging

import pytest

from app.services import email_service
from app.services import partenariato_moderazione_testi as testi
from app.services.email_service import send_moderazione_decisione_email

DESTINATARIO = "mario.rossi@azienda.it"
CTA = "https://app.bandofit.test/app/partenariati/segnalazioni/abc?azienda=c1"
AZIENDA = "Alfa <Srl> & co"
STATEMENT = testi.genera_statement(
    oggetto_tipo="call",
    motivazione="Nel testo c'è <script>alert(1)</script> e un numero di telefono.",
    deciso_at=testi.adesso(),
    origine="segnalazione",
    riferimento="5e000000",
    motivo_segnalazione="contatti_nel_testo",
)


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    for chiave, valore in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
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


async def test_statement_completo_escapato_senza_disiscrizione(posta, caplog):
    caplog.set_level(logging.INFO, logger="bandofit.email")
    assert await send_moderazione_decisione_email(
        DESTINATARIO, oggetto_tipo="call", statement=STATEMENT, cta_url=CTA,
        azienda_destinataria=AZIENDA) is True
    [message] = posta
    html, testo = parti(message)
    assert message["Subject"] == "Decisione di moderazione su un tuo contenuto — BandoFit"
    assert message["List-Unsubscribe"] is None and message["List-Unsubscribe-Post"] is None
    assert "unsubscribe" not in html.lower() and "disattiva" not in testo.lower()
    # testo semplice: lo statement per intero, righe comprese
    assert STATEMENT in testo and testi.INTESTAZIONE_BOZZA in testo
    assert "la tua azienda «Alfa <Srl> & co»" in testo
    # HTML: dati escapati, righe conservate (pre-wrap)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "Alfa &lt;Srl&gt; &amp; co" in html
    assert "white-space:pre-wrap" in html
    assert CTA.replace("&", "&amp;") in html
    # log: indirizzo mascherato, nessun testo della decisione
    log = caplog.text
    assert DESTINATARIO not in log and "numero di telefono" not in log
    assert email_service.mask_email(DESTINATARIO) in log


@pytest.mark.parametrize("oggetto", ["profilo", "messaggio"])
async def test_oggetti_ammessi(posta, oggetto):
    assert await send_moderazione_decisione_email(
        DESTINATARIO, oggetto_tipo=oggetto, statement=STATEMENT, cta_url=CTA) is True
    _, testo = parti(posta[0])
    assert testi.NOME_OGGETTO[oggetto].split()[-1] in testo
    assert "della tua azienda." in testo


@pytest.mark.parametrize(("oggetto", "statement"), [
    ("segnalazione", STATEMENT), ("call", ""), ("call", "   "), ("call", None)])
async def test_non_parte_senza_contenuto_o_motivazione(posta, caplog, oggetto, statement):
    caplog.set_level(logging.WARNING, logger="bandofit.email")
    assert await send_moderazione_decisione_email(
        DESTINATARIO, oggetto_tipo=oggetto, statement=statement, cta_url=CTA) is False
    assert posta == []
    assert DESTINATARIO not in caplog.text
    assert email_service.mask_email(DESTINATARIO) in caplog.text


def test_firma_senza_dati_di_chi_ha_segnalato():
    parametri = set(inspect.signature(send_moderazione_decisione_email).parameters)
    assert parametri == {"to_email", "oggetto_tipo", "statement", "cta_url",
                         "azienda_destinataria"}


# ------------------------------------------------------ template dello statement


class TestTemplate:
    def test_deterministico_e_con_gli_elementi_del_dsa(self):
        istante = testi.adesso()
        uno = testi.genera_statement(oggetto_tipo="messaggio", motivazione="x" * 30,
                                     deciso_at=istante, origine="segnalazione")
        due = testi.genera_statement(oggetto_tipo="messaggio", motivazione="x" * 30,
                                     deciso_at=istante, origine="segnalazione")
        assert uno == due
        for parte in ("Cosa abbiamo deciso", "Fatti e circostanze", "Fondamento",
                      "Uso di mezzi automatizzati", "Come contestare la decisione",
                      testi.SOR_VERSIONE, "[BOZZA — DA RIVEDERE CON IL LEGALE]"):
            assert parte in uno
        assert "Chi ha segnalato non viene identificato" in uno
        assert "controlli automatici" not in uno  # pertinenti solo per i contatti

    def test_vie_di_ricorso_per_origine(self):
        istante = testi.adesso()
        segnalazione = testi.genera_statement(oggetto_tipo="call", motivazione="m" * 25,
                                              deciso_at=istante, origine="segnalazione")
        ufficio = testi.genera_statement(oggetto_tipo="call", motivazione="m" * 25,
                                         deciso_at=istante, origine="ufficio")
        ricorso = testi.genera_statement(oggetto_tipo="call", motivazione="m" * 25,
                                         deciso_at=istante, origine="ricorso")
        entro = testi.data_it(testi.scadenza_ricorso(istante))
        assert f"entro il {entro}, dalla pagina della segnalazione" in segnalazione
        for testo in (ufficio, ricorso):
            assert "Puoi chiedere un riesame scrivendo a" in testo and entro in testo
        assert "d'ufficio" in ufficio and "senza una segnalazione" in ufficio
        assert "ricorso di chi aveva segnalato" in ricorso
        for testo in (segnalazione, ufficio, ricorso):
            assert "art. 21" in testo and "autorità giudiziaria" in testo

    @pytest.mark.parametrize(("inizio", "atteso"), [
        ("2026-08-31T10:00:00+00:00", "2027-02-28T10:00:00+00:00"),
        ("2027-08-31T10:00:00+00:00", "2028-02-29T10:00:00+00:00"),
        ("2026-03-15T10:00:00+00:00", "2026-09-15T10:00:00+00:00"),
        ("2026-12-01T10:00:00+00:00", "2027-06-01T10:00:00+00:00"),
    ])
    def test_sei_mesi_come_postgres(self, inizio, atteso):
        from datetime import datetime

        assert testi.scadenza_ricorso(datetime.fromisoformat(inizio)) == \
            datetime.fromisoformat(atteso)

    def test_oggetto_sconosciuto(self):
        with pytest.raises(ValueError):
            testi.genera_statement(oggetto_tipo="segnalazione", motivazione="m" * 25,
                                   deciso_at=testi.adesso(), origine="ufficio")
