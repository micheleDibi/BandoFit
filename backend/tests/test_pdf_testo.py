"""Test della lettura dei PDF in processo separato (WP3): PDF sintetici
generati con reportlab, cifrati con pypdf, corrotti; timeout con kill del
figlio; pulizia di intestazioni e piè di pagina."""

import ast
import io
import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services import pdf_testo
from app.services.pdf_testo import TestoPdf, estrai_testo, rimuovi_ripetuti

TestoPdf.__test__ = False  # non è una classe di test (nome che inizia per «Test»)

PARAGRAFO = (
    "Possono presentare domanda le piccole e medie imprese in forma singola o associata. "
    "Il partenariato deve essere composto da almeno tre soggetti indipendenti tra loro. "
    "Nessun partner può sostenere più del settanta per cento dei costi ammissibili."
)


NOMI = ["alfa", "beta lunga", "gamma ancora più lunga", "delta", "epsilon molto molto lungo",
        "zeta", "eta", "theta"]


def pagina_di_prova(i: int) -> str:
    """Testo diverso per ogni pagina, anche ai bordi (le righe identiche in
    testa o in coda a più pagine sono intestazioni e verrebbero tolte)."""
    nome = NOMI[(i - 1) % len(NOMI)]
    return f"Sezione {nome}. {PARAGRAFO} Chiusura della sezione {nome}."


def _normale(testo: str) -> str:
    return " ".join(testo.split())


def _righe(testo: str, larghezza: int = 90) -> list[str]:
    parole, righe, corrente = testo.split(), [], ""
    for parola in parole:
        if len(corrente) + len(parola) + 1 > larghezza:
            righe.append(corrente)
            corrente = parola
        else:
            corrente = f"{corrente} {parola}".strip()
    if corrente:
        righe.append(corrente)
    return righe


def genera_pdf(
    pagine: list[str | None],
    *,
    intestazione: str | None = None,
    pie_di_pagina: bool = False,
) -> bytes:
    """Una pagina per voce: testo (a capo automatico) oppure None = pagina
    senza testo (solo grafica, come una scansione)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    totale = len(pagine)
    for numero, testo in enumerate(pagine, start=1):
        if intestazione:
            c.drawString(40, 810, intestazione)
        if testo is None:
            c.rect(60, 200, 400, 400, fill=1)
        else:
            y = 780
            for riga in _righe(testo):
                c.drawString(40, y, riga)
                y -= 14
        if pie_di_pagina:
            c.drawString(260, 30, f"Pagina {numero} di {totale}")
        c.showPage()
    c.save()
    return buf.getvalue()


def cifra(pdf: bytes, *, utente: str, proprietario: str = "proprietario") -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    writer.encrypt(user_password=utente, owner_password=proprietario)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


async def leggi(pdf: bytes, **opzioni) -> TestoPdf:
    parametri = {"max_pagine": 150, "timeout_s": 30.0, **opzioni}
    return await estrai_testo(pdf, **parametri)


# Figlio finto per il timeout: top-level (lo spawn lo reimporta). Scrive il
# proprio PID nel file indicato dai «byte del PDF» e poi dorme.
def figlio_dormiente(conn, pdf_bytes: bytes, limiti: dict) -> None:
    Path(pdf_bytes[len(b"%PDF-") :].decode()).write_text(str(os.getpid()))
    time.sleep(60)


def figlio_muto(conn, pdf_bytes: bytes, limiti: dict) -> None:
    conn.close()  # esce senza rispondere (crash simulato)


def figlio_json_rotto(conn, pdf_bytes: bytes, limiti: dict) -> None:
    conn.send_bytes(b"{non json")
    conn.close()


# ------------------------------------------------------------ lettura


class TestLettura:
    async def test_pdf_leggibile(self):
        pdf = genera_pdf([pagina_di_prova(1), pagina_di_prova(2)])
        esito = await leggi(pdf)
        assert esito.stato == "letto"
        assert esito.pagine_totali == 2
        assert [n for n, _ in esito.pagine] == [1, 2]
        assert "almeno tre soggetti indipendenti" in _normale(esito.pagine[0][1])
        assert "Chiusura della sezione beta lunga." in _normale(esito.pagine[1][1])
        assert esito.caratteri == sum(len(t) for _, t in esito.pagine)

    async def test_intestazioni_e_pie_di_pagina_rimossi(self):
        pdf = genera_pdf(
            [pagina_di_prova(i) for i in range(1, 5)],
            intestazione="Regione Piemonte - Avviso pubblico welfare aziendale 2026",
            pie_di_pagina=True,
        )
        esito = await leggi(pdf)
        assert esito.stato == "letto"
        for i, (_, testo) in enumerate(esito.pagine, start=1):
            assert "Regione Piemonte - Avviso pubblico" not in testo
            assert "Pagina" not in testo
            assert "forma singola o associata" in _normale(testo)
            assert _normale(testo).startswith(f"Sezione {NOMI[i - 1]}.")

    async def test_oltre_il_tetto_di_pagine_letto_parziale(self):
        pdf = genera_pdf([pagina_di_prova(i) for i in range(1, 6)])
        esito = await leggi(pdf, max_pagine=3)
        assert esito.stato == "letto_parziale"
        assert esito.pagine_totali == 5
        assert [n for n, _ in esito.pagine] == [1, 2, 3]

    async def test_tetto_di_caratteri_per_documento(self):
        pdf = genera_pdf([pagina_di_prova(i) for i in range(1, 5)])
        esito = await leggi(pdf, max_caratteri_doc=len(PARAGRAFO) + 150)
        assert esito.stato == "letto_parziale"
        assert esito.caratteri <= len(PARAGRAFO) + 150
        assert [n for n, _ in esito.pagine] == [1, 2]

    async def test_tetto_di_caratteri_per_pagina(self):
        pdf = genera_pdf([pagina_di_prova(1) + PARAGRAFO * 2, pagina_di_prova(2) + PARAGRAFO * 2])
        esito = await leggi(pdf, max_caratteri_pagina=300)
        assert esito.stato == "letto_parziale"
        assert all(len(t) <= 300 for _, t in esito.pagine)

    async def test_pdf_senza_testo_non_leggibile(self):
        pdf = genera_pdf([None, None, None])
        esito = await leggi(pdf)
        assert esito.stato == "non_leggibile"
        assert esito.pagine_totali == 3
        assert esito.caratteri == 0

    async def test_pdf_quasi_senza_testo_non_leggibile(self):
        # Scansione con il solo numero di pagina: media sotto 200 caratteri.
        pdf = genera_pdf([None, "Allegato 1", None, None])
        esito = await leggi(pdf)
        assert esito.stato == "non_leggibile"

    async def test_pdf_cifrato_con_password_protetto(self):
        pdf = cifra(genera_pdf([pagina_di_prova(1)]), utente="segreta")
        esito = await leggi(pdf)
        assert esito.stato == "protetto"
        assert esito.pagine == []

    async def test_pdf_cifrato_senza_password_utente_letto(self):
        pdf = cifra(genera_pdf([pagina_di_prova(1), pagina_di_prova(2)]), utente="")
        esito = await leggi(pdf)
        assert esito.stato == "letto"
        assert "almeno tre soggetti" in _normale(esito.pagine[0][1])

    async def test_byte_corrotti(self):
        esito = await leggi(b"%PDF-1.7\n" + os.urandom(2000))
        assert esito.stato == "corrotto"

    async def test_pdf_troncato(self):
        pdf = genera_pdf([pagina_di_prova(1), pagina_di_prova(2)])
        esito = await leggi(pdf[: len(pdf) // 3])
        assert esito.stato in ("corrotto", "non_leggibile")

    async def test_non_pdf_scartato_senza_avviare_processi(self, monkeypatch):
        def vietato(*args, **kwargs):
            raise AssertionError("nessun processo per un file che non è un PDF")

        monkeypatch.setattr(pdf_testo, "_esegui_figlio", vietato)
        assert (await leggi(b"<html>no</html>")).stato == "corrotto"
        assert (await leggi(b"")).stato == "corrotto"


class TestProcessoFiglio:
    async def test_timeout_uccide_il_figlio(self, tmp_path):
        file_pid = tmp_path / "pid"
        inizio = time.monotonic()
        esito = await estrai_testo(
            b"%PDF-" + str(file_pid).encode(),
            max_pagine=10,
            timeout_s=3.0,
            _bersaglio=figlio_dormiente,
        )
        durata = time.monotonic() - inizio
        assert esito.stato == "timeout"
        assert durata < 15
        # Nessun figlio resta vivo: ucciso con kill() e raccolto con join().
        assert [p for p in multiprocessing.active_children() if p.is_alive()] == []
        if file_pid.exists():  # il figlio ha fatto in tempo a partire (quasi sempre)
            with pytest.raises(ProcessLookupError):
                os.kill(int(file_pid.read_text()), 0)

    async def test_figlio_che_muore_senza_risposta(self):
        esito = await estrai_testo(
            b"%PDF-1.4", max_pagine=10, timeout_s=10.0, _bersaglio=figlio_muto
        )
        assert esito.stato == "corrotto"

    async def test_risposta_non_json(self):
        esito = await estrai_testo(
            b"%PDF-1.4", max_pagine=10, timeout_s=10.0, _bersaglio=figlio_json_rotto
        )
        assert esito.stato == "corrotto"

    def test_il_modulo_del_figlio_importa_solo_la_stdlib(self):
        sorgente = Path(pdf_testo.__file__).read_text()
        moduli: set[str] = set()
        for nodo in ast.walk(ast.parse(sorgente)):
            if isinstance(nodo, ast.Import):
                moduli.update(alias.name.split(".")[0] for alias in nodo.names)
            elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                moduli.add(nodo.module.split(".")[0])
        esterni = {m for m in moduli if m not in sys.stdlib_module_names}
        # pypdf si importa solo dentro il figlio (funzione `_estrai`).
        assert esterni == {"pypdf"}
        testa = ast.parse(sorgente).body
        importati_in_testa = {
            (n.module if isinstance(n, ast.ImportFrom) else n.names[0].name).split(".")[0]
            for n in testa
            if isinstance(n, ast.Import | ast.ImportFrom)
        }
        assert importati_in_testa <= set(sys.stdlib_module_names)


# ------------------------------------------------------------ pulizia (pura)


class TestRimuoviRipetuti:
    def test_intestazione_e_numerazione(self):
        pagine = [
            (i, f"Avviso Welfare 2026 - Regione\nTesto utile numero {i}.\nPag. {i} di 3")
            for i in range(1, 4)
        ]
        pulite = rimuovi_ripetuti(pagine)
        assert pulite == [(i, f"Testo utile numero {i}.") for i in range(1, 4)]

    def test_intestazione_con_numero_di_pagina_variabile(self):
        pagine = [
            (i, f"Decreto 12/2026 - Allegato A - pag. {i}\nContenuto {c}\naltro {c}")
            for i, c in enumerate(["uno", "due", "tre"], start=1)
        ]
        assert [t for _, t in rimuovi_ripetuti(pagine)] == [
            f"Contenuto {c}\naltro {c}" for c in ["uno", "due", "tre"]
        ]

    def test_titoli_di_articolo_diversi_restano(self):
        pagine = [(i, f"Art. {i}\nTesto dell'articolo {c}.") for i, c in
                  enumerate(["primo", "secondo", "terzo"], start=1)]
        assert rimuovi_ripetuti(pagine) == pagine

    def test_numero_di_pagina_nudo_ai_bordi(self):
        pagine = [(i, f"{i}\nContenuto {'abc' * i}\n") for i in range(1, 5)]
        assert [t for _, t in rimuovi_ripetuti(pagine)] == [
            f"Contenuto {'abc' * i}" for i in range(1, 5)
        ]

    def test_riga_ripetuta_solo_in_meno_della_meta_resta(self):
        pagine = [
            (1, "Titolo comune\nuno"),
            (2, "Altro titolo\ndue"),
            (3, "Terzo\ntre"),
            (4, "Quarto\nquattro"),
        ]
        assert rimuovi_ripetuti(pagine) == pagine

    def test_riga_ripetuta_in_mezzo_alla_pagina_resta(self):
        centro = "riga centrale ripetuta"
        pagine = [(i, f"a{i}\nb{i}\n{centro}\nc{i}\nd{i}") for i in range(1, 4)]
        assert all(centro in t for _, t in rimuovi_ripetuti(pagine))

    def test_pagina_singola_intatta_salvo_numerazione(self):
        pagine = [(1, "Titolo\nTesto\nPagina 1 di 1")]
        assert rimuovi_ripetuti(pagine) == [(1, "Titolo\nTesto")]

    def test_numerazione_esplicita_rimossa_ovunque(self):
        pagine = [(1, "Testo\nPage 3 of 12\naltro testo"), (2, "x\n- Pagina 4/12 -\ny")]
        assert rimuovi_ripetuti(pagine) == [(1, "Testo\naltro testo"), (2, "x\ny")]

    def test_vuoto(self):
        assert rimuovi_ripetuti([]) == []
        assert rimuovi_ripetuti([(1, "")]) == [(1, "")]

    @pytest.mark.parametrize(
        "riga",
        [
            "Pag. 1" + " " * 19_000 + "x",  # prefisso di numerazione, poi spazi e una lettera
            "Pagina 3" + " " * 19_000 + "di",  # NBSP
            " " * 20_000 + "x",  # senza prefisso: prima era quadratico
            "- Pag. 3 " + "- " * 9_000 + "y",
        ],
    )
    def test_righe_ostili_in_tempo_lineare(self, riga):
        """Una riga lunga fatta apposta non deve bloccare il processo: la
        pulizia gira nel processo principale, fuori dal timeout del figlio."""
        pagine = [(n, f"Titolo\n{riga}\nTesto utile, sezione {n}.") for n in range(1, 5)]
        inizio = time.perf_counter()
        pulite = rimuovi_ripetuti(pagine)
        assert time.perf_counter() - inizio < 0.5
        assert all("Testo utile" in t for _, t in pulite)

    @pytest.mark.parametrize(
        "riga",
        ["Pagina 3 di 40", "  Pag. 3/40  ", "Page 3 of 12", "- Pagina 4/12 -", "pag 7",
         "p. 12", "Pag.3/40", "Pagina 3 di 40", "— Pag. 1 su 9 —"],
    )
    def test_numerazioni_riconosciute(self, riga):
        assert rimuovi_ripetuti([(1, f"Testo\n{riga}\naltro")]) == [(1, "Testo\naltro")]

    @pytest.mark.parametrize(
        "riga", ["Pagamento entro 30 giorni", "Pag. 3 del decreto n. 12 del 2026", "Pagina"]
    )
    def test_non_numerazioni_restano(self, riga):
        assert rimuovi_ripetuti([(1, f"Testo\n{riga}\naltro")]) == [(1, f"Testo\n{riga}\naltro")]
