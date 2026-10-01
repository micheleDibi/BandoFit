"""Test dei builder di input dell'AI-check su BANDI REALI (fixture estratte
dal dump del catalogo): serializzazione indicizzata, citazioni risolvibili,
hash della cache, company pack."""

import json
from pathlib import Path

import pytest

from app.services.ai_check_prompts import (
    NON_DISPONIBILE,
    build_bando_input,
    build_company_pack,
    build_matching_input,
    compute_content_hash,
)
from app.services.bando_scheda_link import calcola_allegati
from app.services.bandi_service import normalize_contenuto

FIXTURES = Path(__file__).parent / "fixtures" / "ai_check"


def load_bando(name: str) -> dict:
    bando = json.loads((FIXTURES / f"{name}.json").read_text())
    bando["contenuto"] = normalize_contenuto(bando.get("contenuto"))
    return bando


class TestBuildBandoInput:
    def test_guida_con_faq_sezioni_indicizzate(self):
        bando = load_bando("bando_guida_faq")
        text, sections = build_bando_input(bando, bando["contenuto"])
        assert "[META]" in text and "[S1]" in text and "[S21]" in text
        assert len(sections) == 22  # META + 21 sezioni
        # ogni indice del testo è risolvibile nella mappa (contratto citazioni)
        for key, value in sections.items():
            assert f"[{key}]" in text
            assert value in text
        # la FAQ è resa come D:/R:
        faq = [s for s in sections.values() if s.startswith("D: ")]
        assert faq and "R: " in faq[0]

    def test_meta_contiene_tutti_i_facet_del_catalogo(self):
        bando = load_bando("bando_guida_faq")
        _, sections = build_bando_input(bando, bando["contenuto"])
        meta = sections["META"]
        assert "Regioni ammesse (catalogo): Lazio" in meta
        assert "Codici ATECO (catalogo):" in meta and "13 — Industrie tessili" in meta
        assert "Beneficiari (catalogo): Imprese" in meta
        assert "Ente erogatore: Regione Lazio" in meta
        assert "Allegati ufficiali (NON inclusi in questo testo):" in meta

    def test_flash_con_liste_puntate(self):
        bando = load_bando("bando_flash")
        text, sections = build_bando_input(bando, bando["contenuto"])
        assert any(v.startswith("- ") or "\n- " in v for v in sections.values())
        assert "## " in text  # heading h2

    def test_contenuto_corrotto_degrada_a_solo_meta(self):
        # 5 bandi reali hanno contenuto doppio-encodato corrotto all'origine:
        # normalize_contenuto → None, l'input resta il solo blocco META.
        bando = load_bando("bando_double_encoded")
        assert bando["contenuto"] is None
        text, sections = build_bando_input(bando, bando["contenuto"])
        assert list(sections) == ["META"]
        assert bando["titolo"] and bando["titolo"] in sections["META"]

    def test_hook_allegati_futuri(self):
        bando = load_bando("bando_flash")
        text, sections = build_bando_input(
            bando, bando["contenuto"], allegati_texts=[("Avviso pubblico", "Testo del PDF")]
        )
        assert "[A1] Avviso pubblico" in text
        assert sections["A1"] == "Testo del PDF"


class TestContentHash:
    def test_hash_del_testo_serializzato_non_di_hash_bando(self):
        # hash_bando del catalogo NON copre i facet delle junction che entrano
        # in [META] e da cui l'estrazione deriva requisiti: la cache deve
        # invalidarsi su TUTTO ciò che il modello vede.
        bando = load_bando("bando_guida_faq")
        text, _ = build_bando_input(bando, bando["contenuto"])
        h = compute_content_hash(bando, text)
        assert len(h) == 64
        assert h != bando["hash_bando"]

    def test_cambia_se_cambiano_le_junction(self):
        bando = load_bando("bando_guida_faq")
        text1, _ = build_bando_input(bando, bando["contenuto"])
        bando["bando_regioni"] = [{"regioni": {"id": 10, "nome": "Lombardia"}}]
        text2, _ = build_bando_input(bando, bando["contenuto"])
        assert compute_content_hash(bando, text1) != compute_content_hash(bando, text2)

    def test_stabile_a_parita_di_input(self):
        bando = load_bando("bando_flash")
        text1, _ = build_bando_input(bando, bando["contenuto"])
        text2, _ = build_bando_input(bando, bando["contenuto"])
        assert text1 == text2
        assert compute_content_hash(bando, text1) == compute_content_hash(bando, text2)


DEPRECATE = ("link_candidatura", "link_bando", "allegati")


def _link(id_: int, tipo: str, url: str, etichetta: str | None = None) -> dict:
    return {"id": id_, "bando_id": 1, "url": url, "dominio": None, "tipo": tipo,
            "etichetta": etichetta, "content_type": None}


def _riga_meta(sezioni: dict, prefisso: str) -> str:
    return next(r for r in sezioni["META"].splitlines() if r.startswith(prefisso))


class TestAllegatiNelMeta:
    """La riga «Allegati ufficiali» del [META] viene dalla stessa lista della
    scheda (`calcola_allegati`); per chi non ha allegati in nessuna fonte
    testo e hash non cambiano."""

    @staticmethod
    def _etichette(bando: dict, link: list[dict]) -> list[str]:
        return [a.etichetta for a in calcola_allegati(bando, link)]

    def test_senza_chiave_allegati_e_con_lista_vuota_stesso_testo(self):
        bando = load_bando("bando_flash")
        senza = {k: v for k, v in bando.items() if k != "allegati"}
        con_vuoto = {**bando, "allegati": []}
        testi = {
            build_bando_input(b, b["contenuto"], allegati_etichette=e)[0]
            for b in (senza, con_vuoto)
            for e in (None, [])
        }
        [testo] = testi
        assert "Allegati ufficiali (NON inclusi in questo testo): non indicato" in testo

    @pytest.mark.parametrize("nome", ["bando_flash", "bando_guida_faq", "bando_double_encoded"])
    def test_stessa_lista_della_scheda_stesso_testo_e_hash(self, nome):
        # Senza righe `bando_link` la lista della scheda coincide con il
        # jsonb: la chiave della cache non cambia.
        bando = load_bando(nome)
        storico, _ = build_bando_input(bando, bando["contenuto"])
        nuovo, _ = build_bando_input(
            bando, bando["contenuto"], allegati_etichette=self._etichette(bando, [])
        )
        assert nuovo == storico
        assert compute_content_hash(bando, nuovo) == compute_content_hash(bando, storico)

    def test_etichette_dalle_righe_bando_link_filtrate(self):
        bando = load_bando("bando_guida_faq")
        link = [
            _link(1, "atto", "https://www.lazioeuropa.it/x/Decreto.pdf", "Decreto dirigenziale"),
            _link(2, "allegato", "https://www.youtube.com/watch?v=1", "Video"),
            _link(3, "allegato", "https://www.lazioeuropa.it/x/Allegato%20A.pdf"),
        ]
        _, sezioni = build_bando_input(
            bando, bando["contenuto"], allegati_etichette=self._etichette(bando, link)
        )
        riga = _riga_meta(sezioni, "Allegati ufficiali")
        # Righe per id (l'URL scartato dal filtro dei link non c'è), poi il jsonb.
        assert riga.startswith(
            "Allegati ufficiali (NON inclusi in questo testo): Decreto dirigenziale, Allegato A, "
        )
        assert riga.endswith("Avviso pubblico, Modulistica e istruzioni")
        assert "Video" not in riga and "youtube" not in riga

    def test_riga_senza_colonne_deprecate_nessun_errore(self):
        # Forma della riga con la select senza ripieghi (passo c2): stesso
        # testo di oggi per un bando senza allegati.
        bando = load_bando("bando_flash")
        riga = {k: v for k, v in bando.items() if k not in DEPRECATE}
        testo, sezioni = build_bando_input(
            riga, riga["contenuto"], allegati_etichette=self._etichette(riga, [])
        )
        assert _riga_meta(sezioni, "Allegati ufficiali").endswith(": non indicato")
        assert testo == build_bando_input(bando, bando["contenuto"])[0]

    @pytest.mark.parametrize(
        ("effettivo", "atteso"),
        [(None, "aperto"), ("", "aperto"), ("chiuso", "chiuso"), ("sospeso", "sospeso")],
    )
    def test_stato_da_stato_effettivo_con_ripiego_su_stato_bando(self, effettivo, atteso):
        bando = {**load_bando("bando_flash"), "stato_effettivo": effettivo}
        assert bando["stato_bando"] == "aperto"
        _, sezioni = build_bando_input(bando, bando["contenuto"])
        assert _riga_meta(sezioni, "Stato:") == f"Stato: {atteso}"

    def test_stato_effettivo_uguale_allo_stato_salvato_stesso_testo(self):
        bando = load_bando("bando_flash")
        con = {**bando, "stato_effettivo": bando["stato_bando"]}
        assert build_bando_input(con, con["contenuto"])[0] == (
            build_bando_input(bando, bando["contenuto"])[0]
        )


PROFILE = {"nome": "Michele", "cognome": "Rossi", "codice_fiscale": "RSSMRA80A01H501U",
           "cf_verified_at": "2026-07-01T00:00:00Z"}
COMPANY = {"ragione_sociale": "ACME Srl", "partita_iva": "01234567890",
           "regione_nome": "Lazio", "ateco_codice": "62.01", "settore_nome": None,
           "classe_dimensionale": "piccola", "numero_dipendenti": 12}


class TestCompanyPack:
    def test_campi_assenti_resi_non_disponibile(self):
        pack = build_company_pack(PROFILE, COMPANY, None, None, [])
        assert "settore_nome: NON DISPONIBILE" in pack
        assert "regione_nome: Lazio" in pack

    def test_cf_personale_mai_in_chiaro_nel_pack(self):
        # Il report è visibile a tutta l'azienda: il CF del titolare non deve
        # poter trapelare via dato_azienda — nel pack va solo lo stato.
        pack = build_company_pack(PROFILE, COMPANY, None, None, [])
        assert PROFILE["codice_fiscale"] not in pack.split("## Dati aziendali")[0]
        assert "codice_fiscale (stato): presente, verificato all'Anagrafe Tributaria" in pack
        senza_cf = build_company_pack({**PROFILE, "codice_fiscale": None}, COMPANY, None, None, [])
        assert "codice_fiscale (stato): NON DISPONIBILE" in senza_cf

    def test_dossier_e_derived_appiattiti_con_percorsi_citabili(self):
        dossier = {"anagrafica": {"denominazione": "ACME Srl", "rea": None},
                   "flags": {"startup_innovativa": True}}
        derived = {"ateco_secondari": ["63.01"], "ateco_divisione": "62"}
        pack = build_company_pack(PROFILE, COMPANY, dossier, derived, [])
        assert "dossier.anagrafica.denominazione: ACME Srl" in pack
        assert "dossier.flags.startup_innovativa: True" in pack
        assert "derived.ateco_divisione: 62" in pack
        assert "rea" not in pack.split("## Dossier")[1].split("##")[0]  # i None non compaiono

    def test_persone_e_cariche(self):
        people = [{"kind": "manager", "nome": "Anna", "cognome": "Bianchi",
                   "ruoli": [{"role": "Presidente"}], "is_legale_rappresentante": True}]
        pack = build_company_pack(PROFILE, COMPANY, None, None, people)
        assert "- manager: Anna Bianchi [legale rappresentante] (Presidente)" in pack

    def test_senza_company_row(self):
        pack = build_company_pack(PROFILE, None, None, None, [])
        assert NON_DISPONIBILE in pack


class TestMatchingInput:
    def test_composizione(self):
        text = build_matching_input({"requisiti_obbligatori": []}, {"regione": {"esito": "soddisfatto"}}, "PACK")
        assert "## Estrazione dal bando" in text
        assert "## Verifiche strutturate" in text
        assert text.endswith("## Profilo azienda\nPACK")
        assert "soddisfatto" in text


class TestBilanciNelCompanyPack:
    """WP1: i bilanci entrano come righe per esercizio + indicatori calcolati."""

    @staticmethod
    def _esercizi():
        from datetime import date
        from decimal import Decimal

        from app.services.bilanci_indicatori import EsercizioBilancio

        def es(anno, fatturato, utile, fonte="it_advanced", **altri):
            valori = {"fatturato": Decimal(fatturato), "risultato_esercizio": Decimal(utile)}
            valori.update({k: Decimal(v) for k, v in altri.items()})
            return EsercizioBilancio(
                anno=anno, data_chiusura=date(anno, 12, 31), valori=valori,
                fonti={k: fonte for k in valori},
            )

        return [
            es(2019, "3530126", "201877"),
            es(2020, "3712554", "312004"),
            es(2021, "4432761.00", "469366", fonte="it_full", patrimonio_netto="563473"),
            es(2022, "5102233", "-12000.50"),
        ]

    def test_righe_per_esercizio_con_fonte(self):
        pack = build_company_pack(
            PROFILE, COMPANY, None, None, [], bilanci=self._esercizi(), storico_completo=True
        )
        blocco = pack.split("## Bilanci per esercizio (valori in euro)\n")[1]
        assert "bilanci.numero_esercizi: 4\n" in blocco
        assert (
            "bilanci.2021.fatturato: 4432761 (fonte: Registro Imprese, ultimo bilancio depositato)"
            in blocco
        )
        assert "bilanci.2022.risultato_esercizio: -12000.5 (fonte: storico" in blocco
        assert "bilanci.2022.data_chiusura: 2022-12-31" in blocco
        assert "bilanci.2020.patrimonio_netto: NON DISPONIBILE" in blocco
        assert "bilanci.2019." not in blocco  # solo gli ultimi 3 voce per voce

    def test_storico_incompleto_numero_esercizi_minimo(self):
        """Solo IT-full (storico mai recuperato, saltato, in errore…): il
        conteggio è un minimo, mai un totale — altrimenti «almeno 2 bilanci»
        diventa un falso non_soddisfatto → non_ammissibile."""
        solo_it_full = self._esercizi()[2:3]
        for pack in (
            build_company_pack(PROFILE, COMPANY, None, None, [], bilanci=solo_it_full),
            build_company_pack(
                PROFILE, COMPANY, None, None, [], bilanci=solo_it_full, storico_completo=False
            ),
        ):
            assert "bilanci.numero_esercizi: almeno 1 (storico dei bilanci non recuperato" in pack
            assert "bilanci.numero_esercizi: 1\n" not in pack

    def test_indicatori_gia_calcolati(self):
        pack = build_company_pack(PROFILE, COMPANY, None, None, [], bilanci=self._esercizi())
        assert "bilanci.indicatori.fatturato_medio_3: 4415849.33 euro (esercizi 2020-2022" in pack
        assert "bilanci.indicatori.indipendenza_finanziaria: NON DISPONIBILE (" in pack
        assert "bilanci.indicatori.copertura_immobilizzazioni: NON DISPONIBILE" in pack

    def test_senza_bilanci_non_disponibile(self):
        for bilanci in (None, []):
            pack = build_company_pack(PROFILE, COMPANY, None, None, [], bilanci=bilanci)
            assert "## Bilanci per esercizio (valori in euro)\nbilanci: NON DISPONIBILE" in pack

    def test_blocco_bilanci_del_dossier_escluso(self):
        dossier = {
            "anagrafica": {"denominazione": "ACME Srl"},
            "bilanci": {"fatturato": 999, "utile": 1, "anno": 2021},
        }
        pack = build_company_pack(PROFILE, COMPANY, dossier, None, [], bilanci=self._esercizi())
        assert "dossier.anagrafica.denominazione: ACME Srl" in pack
        assert "dossier.bilanci" not in pack
        assert "999" not in pack

    def test_regola_di_matching_sui_bilanci(self):
        from app.services.ai_check_prompts import SYSTEM_MATCH

        assert "bilanci.<anno>.<voce>" in SYSTEM_MATCH
        assert "bilanci.indicatori.*" in SYSTEM_MATCH
        assert "NON fare calcoli" in SYSTEM_MATCH
        # storico incompleto: «almeno N» non diventa mai non_soddisfatto
        assert "«almeno N»" in SYSTEM_MATCH
        # organico e fasce dichiarate restano verificabili senza bilanci
        # (prima di WP1 lo erano: niente regressione a dato_mancante)
        assert "SOLO i campi `bilanci" not in SYSTEM_MATCH
        for campo in ("dossier.dipendenti.*", "numero_dipendenti", "fascia_fatturato"):
            assert campo in SYSTEM_MATCH

    def test_senza_bilanci_organico_dichiarato_nel_pack(self):
        pack = build_company_pack(
            PROFILE, {**COMPANY, "numero_dipendenti": 12, "fascia_fatturato": "500k_2m"},
            None, None, [], bilanci=None,
        )
        assert "numero_dipendenti: 12" in pack and "fascia_fatturato: 500k_2m" in pack
        assert "bilanci: NON DISPONIBILE" in pack
