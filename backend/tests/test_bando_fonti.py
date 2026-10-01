"""Test delle fonti documentali del catalogo (WP3): letture da `bando_pubblico`
e `bando_link` con un secondario finto, normalizzazione del content-type,
priorità e selezione dei candidati (unione di `bando_link` e `allegati`),
doppioni fusi da `bando_fusione`."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.services.bando_fonti_service import (
    BLOCCO_FUSIONI,
    BLOCCO_STATI,
    SELECT_FUSIONE,
    SELECT_LINK,
    SELECT_STATO,
    LinkDocumento,
    leggi_fusioni,
    leggi_link_documenti,
    leggi_stato_bandi,
    normalizza_content_type,
    seleziona_candidati,
)

# ------------------------------------------------------------ finti


class FakeQuery:
    def __init__(self, db, tabella: str) -> None:
        self._db = db
        self.tabella = tabella
        self.select_arg: str | None = None
        self.filtri: list[tuple] = []

    def select(self, colonne, *args, **kwargs):
        self.select_arg = colonne
        return self

    def eq(self, colonna, valore):
        self.filtri.append(("eq", colonna, valore))
        return self

    def in_(self, colonna, valori):
        self.filtri.append(("in", colonna, list(valori)))
        return self

    def order(self, colonna, *args, **kwargs):
        self.filtri.append(("order", colonna))
        return self

    def limit(self, n):
        self.filtri.append(("limit", n))
        return self

    async def execute(self):
        self._db.query.append(self)
        errore = self._db.errori.get(self.tabella)
        if errore is not None:
            raise errore
        sorgente = self._db.righe.get(self.tabella, [])
        righe = sorgente(self) if callable(sorgente) else sorgente
        return SimpleNamespace(data=righe, count=None)


class FakeSecondary:
    def __init__(self, righe=None, errori=None) -> None:
        self.righe = righe or {}
        self.errori = errori or {}
        self.query: list[FakeQuery] = []

    def table(self, nome: str) -> FakeQuery:
        return FakeQuery(self, nome)


def link(url, tipo="allegato", etichetta=None, content_type="application/pdf", id_=1, dominio=None):
    return LinkDocumento(
        id=id_, bando_id=42, url=url, dominio=dominio, tipo=tipo, etichetta=etichetta,
        content_type=content_type, ultimo_visto_at=None,
    )


# ------------------------------------------------------------ bando_pubblico


class TestLeggiStatoBandi:
    async def test_blocchi_da_cento_e_parsing(self):
        ids = list(range(1, 251))

        def righe(q: FakeQuery):
            (_, _, blocco), = [f for f in q.filtri if f[0] == "in"]
            return [
                {"id": i, "slug": f"bando-{i}", "stato_effettivo": "aperto",
                 "data_scadenza": "2026-12-31",
                 "ultimo_cambiamento_at": "2026-09-01T10:00:00+00:00"}
                for i in blocco if i % 50  # i multipli di 50 non sono pubblicati
            ]

        db = FakeSecondary(righe={"bando_pubblico": righe})
        stati = await leggi_stato_bandi(db, ids + [3, 3])
        assert len(db.query) == 3
        assert [len(f[2]) for q in db.query for f in q.filtri if f[0] == "in"] == [100, 100, 50]
        assert all(q.tabella == "bando_pubblico" and q.select_arg == SELECT_STATO for q in db.query)
        assert BLOCCO_STATI == 100
        assert len(stati) == 245
        assert 50 not in stati
        s = stati[7]
        assert s.slug == "bando-7"
        assert s.stato_effettivo == "aperto"
        assert s.data_scadenza == date(2026, 12, 31)
        assert s.ultimo_cambiamento_at == datetime(2026, 9, 1, 10, tzinfo=timezone.utc)

    async def test_nessun_id_nessuna_query(self):
        db = FakeSecondary()
        assert await leggi_stato_bandi(db, []) == {}
        assert db.query == []

    async def test_valori_mancanti_o_strani(self):
        db = FakeSecondary(righe={"bando_pubblico": [
            {"id": 1, "slug": None, "stato_effettivo": None, "data_scadenza": None,
             "ultimo_cambiamento_at": "non-una-data"},
            {"id": "x"},
        ]})
        stati = await leggi_stato_bandi(db, [1, 2])
        assert list(stati) == [1]
        assert stati[1].data_scadenza is None and stati[1].ultimo_cambiamento_at is None

    async def test_errore_propaga(self):
        db = FakeSecondary(errori={"bando_pubblico": APIError({"code": "42P01", "message": "x"})})
        with pytest.raises(APIError):
            await leggi_stato_bandi(db, [1])


# ------------------------------------------------------------ bando_fusione


class TestLeggiFusioni:
    async def test_blocchi_da_cento_e_parsing(self):
        ids = list(range(1, 151))

        def righe(q: FakeQuery):
            (_, _, blocco), = [f for f in q.filtri if f[0] == "in"]
            fusi = [{"bando_id": i, "master_id": 1000 + i} for i in blocco if i % 10 == 0]
            # riga malformata e id non richiesto: ignorati; master non intero → None
            return fusi + [{"bando_id": "x"}, {"bando_id": 999, "master_id": 1},
                           {"bando_id": blocco[0], "master_id": "?"}]

        db = FakeSecondary(righe={"bando_fusione": righe})
        fusi = await leggi_fusioni(db, ids + [10, True])
        assert [len(f[2]) for q in db.query for f in q.filtri if f[0] == "in"] == [100, 50]
        assert all(q.tabella == "bando_fusione" and q.select_arg == SELECT_FUSIONE
                   for q in db.query)
        assert BLOCCO_FUSIONI == 100
        assert fusi[10] == 1010 and fusi[150] == 1150
        assert fusi[1] is None and fusi[101] is None  # master non leggibile
        assert 999 not in fusi and len(fusi) == 17

    async def test_nessun_id_nessuna_query(self):
        db = FakeSecondary()
        assert await leggi_fusioni(db, []) == {}
        assert db.query == []

    async def test_errore_propaga(self):
        # A differenza di `risolvi_fusioni`: un errore non vale «non fuso».
        db = FakeSecondary(errori={"bando_fusione": APIError({"code": "42501", "message": "x"})})
        with pytest.raises(APIError):
            await leggi_fusioni(db, [1])


# ------------------------------------------------------------ bando_link


class TestLeggiLinkDocumenti:
    async def test_select_per_nome_e_filtri(self):
        db = FakeSecondary(righe={"bando_link": [
            {"id": 3, "bando_id": 42, "url": " https://ente.it/avviso.pdf ", "dominio": "ente.it",
             "tipo": "allegato", "etichetta": "Avviso", "content_type": "application/pdf",
             "ultimo_visto_at": "2026-09-01T00:00:00+00:00"},
            {"id": 4, "bando_id": 42, "url": None, "tipo": "allegato"},
            {"id": 5, "bando_id": 42, "url": "https://ente.it/x", "tipo": "candidatura"},
        ]})
        risultato = await leggi_link_documenti(db, 42)
        (q,) = db.query
        assert q.tabella == "bando_link"
        assert q.select_arg == SELECT_LINK
        assert "*" not in SELECT_LINK
        assert ("eq", "bando_id", 42) in q.filtri
        assert ("in", "tipo", ["allegato", "atto", "pagina_bando", "faq"]) in q.filtri
        assert ("order", "id") in q.filtri
        assert ("limit", 50) in q.filtri
        assert len(risultato) == 1
        assert risultato[0].url == "https://ente.it/avviso.pdf"
        assert risultato[0].etichetta == "Avviso"

    async def test_lista_vuota_non_e_errore(self):
        assert await leggi_link_documenti(FakeSecondary(righe={"bando_link": []}), 42) == []

    @pytest.mark.parametrize(
        "errore",
        [APIError({"code": "42501", "message": "permission denied"}),
         APIError({"code": "42P01", "message": "relation does not exist"}),
         TimeoutError()],
    )
    async def test_errore_restituisce_none(self, errore):
        db = FakeSecondary(errori={"bando_link": errore})
        assert await leggi_link_documenti(db, 42) is None


# ------------------------------------------------------------ content-type


class TestNormalizzaContentType:
    @pytest.mark.parametrize(
        ("ct", "url", "atteso"),
        [
            ("application/pdf", "https://e.it/x", "pdf"),
            ("application/pdf; charset=binary", "https://e.it/x", "pdf"),
            ("APPLICATION/PDF", "https://e.it/x", "pdf"),
            ("application/x-pdf", "https://e.it/x", "pdf"),
            ("text/pdf", "https://e.it/x", "pdf"),
            ("pdf", "https://e.it/x", "pdf"),
            (".pdf", "https://e.it/x", "pdf"),
            ("PDF", "https://e.it/x", "pdf"),
            ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "https://e.it/x.pdf", "docx"),
            ("application/msword", "https://e.it/x", "doc"),
            ("docx", "https://e.it/x", "docx"),
            ("application/zip", "https://e.it/x", "zip"),
            ("application/pkcs7-mime", "https://e.it/x", "p7m"),
            ("text/html; charset=utf-8", "https://e.it/x.pdf", "html"),
            ("xlsx", "https://e.it/x", "xlsx"),
            ("application/vnd.ms-excel", "https://e.it/x", "xlsx"),
            # generici o vuoti: decide l'estensione dell'URL
            ("application/octet-stream", "https://e.it/avviso.pdf", "pdf"),
            ("application/force-download", "https://e.it/avviso.PDF?dl=1", "pdf"),
            ("application/x-download", "https://e.it/a%20b.pdf", "pdf"),
            (None, "https://e.it/avviso.pdf", "pdf"),
            ("", "https://e.it/avviso.docx", "docx"),
            (None, "https://e.it/avviso.pdf.p7m", "p7m"),
            ("application/octet-stream", "https://e.it/download.php?id=3", "altro"),
            (None, "https://e.it/scarica", "altro"),
            ("image/jpeg", "https://e.it/x.pdf", "altro"),
            ("application/vnd.oasis.opendocument.text", "https://e.it/x", "altro"),
        ],
    )
    def test_formati(self, ct, url, atteso):
        assert normalizza_content_type(ct, url) == atteso


# ------------------------------------------------------------ selezione


class TestSelezionaCandidati:
    def test_priorita_atto_regole_altri_pagina_moduli_faq(self):
        links = [
            link("https://e.it/faq.pdf", tipo="faq", etichetta="FAQ", id_=1),
            link("https://e.it/modulo.pdf", etichetta="Modello di domanda del bando", id_=2),
            link("https://e.it/scheda.pdf", tipo="pagina_bando", etichetta="Scheda", id_=3),
            link("https://e.it/allegato-b.pdf", etichetta="Allegato B", id_=4),
            link("https://e.it/avviso.pdf", etichetta="Avviso pubblico", id_=5),
            link("https://e.it/atto.pdf", tipo="atto", etichetta="Determina", id_=6),
        ]
        scelti = seleziona_candidati(links, None, 10)
        assert [c.url.rsplit("/", 1)[-1] for c in scelti] == [
            "atto.pdf", "avviso.pdf", "allegato-b.pdf", "scheda.pdf", "modulo.pdf", "faq.pdf",
        ]
        assert [c.priorita for c in scelti] == [0, 1, 2, 3, 4, 5]
        assert all(c.origine == "bando_link" for c in scelti)
        assert scelti[0].link_id == 6

    def test_etichetta_dal_nome_del_file(self):
        links = [
            link("https://e.it/docs/allegato_1.pdf", etichetta="Allegato 1", id_=1),
            link("https://e.it/docs/Bando_Innovazione_2026.pdf", etichetta=None, id_=2),
            link("https://e.it/docs/fac-simile_domanda.pdf", etichetta="", id_=3),
        ]
        scelti = seleziona_candidati(links, None, 10)
        assert scelti[0].url.endswith("Bando_Innovazione_2026.pdf")
        assert scelti[0].etichetta == "Bando Innovazione 2026"
        assert scelti[-1].url.endswith("fac-simile_domanda.pdf")
        assert scelti[-1].priorita == 4

    def test_tetto_di_documenti(self):
        links = [link(f"https://e.it/d{i}.pdf", id_=i) for i in range(10)]
        assert len(seleziona_candidati(links, None, 4)) == 4
        assert seleziona_candidati(links, None, 0) == []

    def test_deduplica_tiene_la_priorita_migliore(self):
        links = [
            link("https://e.it/avviso.pdf", tipo="faq", id_=1),
            link("https://E.IT/avviso.pdf#page=2", tipo="atto", id_=2),
        ]
        (unico,) = seleziona_candidati(links, None, 4)
        assert unico.tipo == "atto"

    def test_esclusi_http_bloccati_e_non_pdf(self):
        links = [
            link("http://e.it/avviso.pdf", id_=1),
            link("https://www.obiettivoeuropa.com/avviso.pdf", id_=2),
            link("https://www.fasi.eu/avviso.pdf", id_=3),
            link("https://e.it/avviso.docx", content_type="docx", id_=4),
            link("https://e.it/avviso.pdf.p7m", content_type=None, id_=5),
            link("https://e.it/pagina", tipo="pagina_bando", content_type="text/html", id_=6),
            link("https://e.it/pagina-scaricabile", tipo="pagina_bando", content_type=None, id_=7),
            link("https://e.it/faq", tipo="faq", content_type=None, id_=8),
            link("https://e.it/foto.jpg", content_type=None, id_=9),
            link("https://e.it/buono.pdf", id_=10),
        ]
        scelti = seleziona_candidati(links, None, 10)
        assert [c.url for c in scelti] == ["https://e.it/buono.pdf"]

    def test_formato_incerto_dopo_i_pdf_dichiarati(self):
        links = [
            link("https://e.it/download.php?id=7", etichetta="Avviso pubblico",
                 content_type="application/octet-stream", id_=1),
            link("https://e.it/avviso.pdf", etichetta="Avviso pubblico", id_=2),
        ]
        scelti = seleziona_candidati(links, None, 4)
        assert [c.formato for c in scelti] == ["pdf", "altro"]
        assert scelti[1].url == "https://e.it/download.php?id=7"

    def test_allegati_del_jsonb(self):
        allegati = [
            {"label": "Avviso pubblico", "url": "https://ente.it/avviso.pdf", "tipo": "pdf"},
            {"label": "Modulo", "url": "https://ente.it/modulo.docx", "tipo": "docx"},
            {"label": "Aggregatore", "url": "https://www.bandi.it/x.pdf", "tipo": "pdf"},
            "voce non valida",
            {"label": "Senza url"},
        ]
        scelti = seleziona_candidati(None, allegati, 4)
        assert [c.url for c in scelti] == ["https://ente.it/avviso.pdf"]
        assert scelti[0].origine == "allegati"
        assert scelti[0].tipo == "allegato"
        assert scelti[0].link_id is None
        assert scelti[0].dominio == "ente.it"
        # anche con bando_link letto (qui senza righe) il jsonb resta fra i candidati
        assert [c.url for c in seleziona_candidati([], allegati, 4)] == [
            "https://ente.it/avviso.pdf"]

    def test_barra_finale_non_fa_un_doppione(self):
        # Stessa regola della scheda (contratto §3): URL uguali dopo aver tolto
        # la barra finale, anche fra bando_link e jsonb.
        links = [link("https://ente.it/avviso/", etichetta=None, id_=5),
                 link("https://ente.it/avviso", etichetta="Avviso", id_=6)]
        allegati = [{"label": "Avviso pubblico", "url": "https://ente.it/avviso", "tipo": "pdf"}]
        scelti = seleziona_candidati(links, allegati, 10)
        assert [(c.url, c.origine, c.link_id) for c in scelti] == [
            ("https://ente.it/avviso/", "bando_link", 5)]
        assert scelti[0].etichetta == "Avviso pubblico"

    def test_unione_senza_doppioni_vince_bando_link(self):
        links = [
            link("https://ente.it/avviso.pdf", tipo="atto", id_=5),
            link("https://ente.it/allegato-a.pdf", etichetta="Allegato A", id_=6),
        ]
        allegati = [
            # stesso URL di una riga: vince la riga, che prende l'etichetta
            {"label": "Avviso pubblico", "url": "https://ente.it/avviso.pdf", "tipo": "pdf"},
            {"label": "Allegato B", "url": "https://ente.it/allegato-b.pdf", "tipo": "pdf"},
            # stesso URL di una riga con etichetta: la riga tiene la sua
            {"label": "Altro nome", "url": "https://ente.it/allegato-a.pdf", "tipo": "pdf"},
        ]
        scelti = seleziona_candidati(links, allegati, 10)
        assert [(c.url, c.origine, c.link_id, c.etichetta) for c in scelti] == [
            ("https://ente.it/avviso.pdf", "bando_link", 5, "Avviso pubblico"),
            ("https://ente.it/allegato-a.pdf", "bando_link", 6, "Allegato A"),
            ("https://ente.it/allegato-b.pdf", "allegati", None, "Allegato B"),
        ]

    def test_etichetta_ripulita_dai_domini_esclusi(self):
        links = [link("https://e.it/a.pdf", etichetta="Avviso (fonte obiettivoeuropa.com)")]
        (scelto,) = seleziona_candidati(links, None, 4)
        assert "obiettivoeuropa" not in scelto.etichetta
        assert scelto.etichetta.startswith("Avviso")

    @pytest.mark.parametrize(
        ("grezza", "attesa"),
        [
            ("Avviso‮pubblico", "Avvisopubblico"),
            ("Avviso\x07 pubblico\x00", "Avviso pubblico"),
            ("Avviso​﻿ pubblico", "Avviso pubblico"),
            ("  Avviso\n\tpubblico ", "Avviso pubblico"),
            ("‮\x07​", "Bando 2026"),  # non resta nulla: il nome del file
        ],
    )
    def test_etichetta_senza_caratteri_di_controllo(self, grezza, attesa):
        # Stessa pulizia degli allegati della scheda: l'etichetta entra nel prompt.
        links = [link("https://e.it/Bando_2026.pdf", etichetta=grezza)]
        (scelto,) = seleziona_candidati(links, None, 4)
        assert scelto.etichetta == attesa

    def test_nome_del_file_senza_caratteri_di_controllo(self):
        links = [link("https://e.it/Avviso%E2%80%AE_%07pubblico.pdf")]
        (scelto,) = seleziona_candidati(links, None, 4)
        assert scelto.etichetta == "Avviso pubblico"

    def test_etichetta_del_jsonb_senza_caratteri_di_controllo(self):
        allegati = [{"label": "Allegato‮ A\x07", "url": "https://e.it/a.pdf"}]
        (scelto,) = seleziona_candidati(None, allegati, 4)
        assert scelto.etichetta == "Allegato A"

    def test_dominio_dal_catalogo_o_dall_url(self):
        (a,) = seleziona_candidati([link("https://Www.Ente.it/a.pdf", dominio="Ente.it")], None, 1)
        assert a.dominio == "ente.it"
        (b,) = seleziona_candidati([link("https://Www.Ente.it/a.pdf")], None, 1)
        assert b.dominio == "www.ente.it"
