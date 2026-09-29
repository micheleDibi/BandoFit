"""Valutazione di qualità delle regole di partenariato (WP3).

Metriche PURE (in CI su output sintetici):
- `metriche_modalita`: accuracy e precision/recall per classe di `modalita`;
- `exact_match`: partner_min / partner_max;
- `pr_quote`: precision/recall delle tuple di quota (ambito, categoria, min,
  max) con tolleranza di 0,5 punti percentuali;
- `citazioni_verificate`: quota di voci con la citazione ritrovata nel testo;
- `costo_latenza`: costo e durata per bando.

CLI (dal backend: `python -m app.services.partenariato_valutazione ...`):
- `--offline`: SENZA modello. Legge il catalogo e i PDF ufficiali del campione
  (`tests/fixtures/partenariato/campione_v1.json`), misura il recall del
  pre-classificatore, la copertura documentale e stima token e costo per bando;
- `--prepara OUT_DIR`: come sopra e in più scrive, FUORI dal repository, un
  foglio di lavoro per bando con le pagine rilevanti (per compilare le
  etichette del campione);
- `--reale --tetto-cents N [--conferma]`: pipeline vera con
  `origine='valutazione'` e budget dedicato fail-closed (gruppo
  `valutazione`, tetto N centesimi USD al giorno). Mostra PRIMA la stima;
  senza `--conferma` non spende nulla. Si ferma al tetto. USA IL DB PRIMARIO
  (claim, righe in `bando_partenariato`, registro di spesa; migration 0034);
- `--reale --locale --tetto-cents N [--conferma --out FILE] [--solo ID,ID]`:
  la stessa pipeline SENZA DB primario, con la spesa fail-closed in memoria
  (`partenariato_valutazione_locale`). Serve solo `ANTHROPIC_API_KEY`; con
  `--conferma`, `--out` è obbligatorio e fuori dal repository (il risultato
  contiene le sezioni inviate al modello);
- `--rivaluta FILE [--rivaluta FILE2 …] --out FILE3`: SENZA modello, download
  né configurazione. Ripete convalida tollerante, post-elaborazione e metriche
  sull'output grezzo salvato da `--reale --locale` (più file: in ordine di
  lancio), con le etichette di `--campione`. `--out` obbligatorio, fuori dal
  repository.

Il catalogo si legge con la chiave anon da `SECONDARY_SUPABASE_URL` /
`SECONDARY_SUPABASE_ANON_KEY` oppure `SUPABASE_URL_BANDI` /
`PUBLIC_SUPABASE_BANDI_ANON_KEY`. Ogni PDF letto avvia un processo `spawn`
che reimporta questo modulo: il codice d'avvio sta SOLO sotto
`if __name__ == "__main__"`.
"""

import argparse
import asyncio
import json
import math
import os
import sys
import time
from pathlib import Path

MODALITA = ("obbligatorio", "ammesso", "non_ammesso", "non_determinabile")
TOLLERANZA_QUOTE = 0.5
# Output «tipico» per la stima del costo (il massimo è `max_tokens`).
OUTPUT_TIPICO_TOKEN = 8000

_BACKEND = Path(__file__).resolve().parents[2]
_REPO = _BACKEND.parent
CAMPIONE_PREDEFINITO = _BACKEND / "tests" / "fixtures" / "partenariato" / "campione_v1.json"


# ------------------------------------------------------------ metriche pure


def _rapporto(num: int | float, den: int | float) -> float | None:
    return round(num / den, 4) if den else None


def metriche_modalita(coppie: list[tuple[str | None, str | None]]) -> dict:
    """(attesa, predetta) → accuracy e P/R per classe. Le coppie senza
    etichetta attesa non contano."""
    valide = [(a, p) for a, p in coppie if a is not None]
    per_classe = {}
    for classe in MODALITA:
        tp = sum(1 for a, p in valide if a == classe and p == classe)
        fp = sum(1 for a, p in valide if a != classe and p == classe)
        fn = sum(1 for a, p in valide if a == classe and p != classe)
        per_classe[classe] = {
            "precision": _rapporto(tp, tp + fp),
            "recall": _rapporto(tp, tp + fn),
            "supporto": tp + fn,
        }
    corrette = sum(1 for a, p in valide if a == p)
    return {"n": len(valide), "accuracy": _rapporto(corrette, len(valide)), "per_classe": per_classe}


def exact_match(coppie: list[tuple[int | None, int | None]]) -> dict:
    """(atteso, predetto): None = «non indicato», uguale solo a None."""
    uguali = sum(1 for a, p in coppie if a == p)
    return {"n": len(coppie), "exact_match": _rapporto(uguali, len(coppie))}


def _vicini(a: float | None, b: float | None, tolleranza: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tolleranza


def _quota_uguale(attesa: dict, predetta: dict, tolleranza: float) -> bool:
    return (
        attesa.get("ambito") == predetta.get("ambito")
        and attesa.get("categoria") == predetta.get("categoria")
        and _vicini(attesa.get("min"), predetta.get("min"), tolleranza)
        and _vicini(attesa.get("max"), predetta.get("max"), tolleranza)
    )


def pr_quote(
    attese: list[list[dict]],
    predette: list[list[dict]],
    tolleranza: float = TOLLERANZA_QUOTE,
) -> dict:
    """Precision/recall delle tuple di quota {ambito, categoria, min, max},
    bando per bando (abbinamento uno a uno), con tolleranza sulle
    percentuali."""
    tp = fp = fn = 0
    for attese_bando, predette_bando in zip(attese, predette):
        libere = list(predette_bando)
        for attesa in attese_bando:
            indice = next(
                (i for i, p in enumerate(libere) if _quota_uguale(attesa, p, tolleranza)), None
            )
            if indice is None:
                fn += 1
            else:
                tp += 1
                libere.pop(indice)
        fp += len(libere)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": _rapporto(tp, tp + fp),
            "recall": _rapporto(tp, tp + fn)}


def _citazioni(nodo, conteggio: list[int]) -> None:
    if isinstance(nodo, dict):
        citazione = nodo.get("citazione")
        if isinstance(citazione, dict):
            conteggio[1] += 1
            conteggio[0] += 1 if citazione.get("verificata") else 0
        for chiave, valore in nodo.items():
            if chiave != "citazione":
                _citazioni(valore, conteggio)
    elif isinstance(nodo, list):
        for voce in nodo:
            _citazioni(voce, conteggio)


def citazioni_verificate(regole_per_bando: list[dict | None]) -> dict:
    """Voci con citazione e quota di quelle ritrovate nel testo."""
    conteggio = [0, 0]
    for regole in regole_per_bando:
        _citazioni(regole or {}, conteggio)
    return {"verificate": conteggio[0], "totali": conteggio[1],
            "percentuale": _rapporto(conteggio[0], conteggio[1])}


def costo_latenza(esecuzioni: list[dict]) -> dict:
    """[{bando_id, cost_cents, latenza_s}] → totali e medie."""
    costi = [int(e.get("cost_cents") or 0) for e in esecuzioni]
    latenze = [float(e["latenza_s"]) for e in esecuzioni if e.get("latenza_s") is not None]
    return {
        "n": len(esecuzioni),
        "costo_totale_cents": sum(costi),
        "costo_medio_cents": _rapporto(sum(costi), len(costi)),
        "latenza_media_s": _rapporto(sum(latenze), len(latenze)),
        "latenza_max_s": max(latenze) if latenze else None,
        "per_bando": esecuzioni,
    }


def risultato_da_regole(regole: dict | None, esito: str | None = None) -> dict:
    """Le grandezze confrontabili dalle regole post-elaborate. Un bando chiuso
    dalla guardia di costo (`nessun_segnale`, senza regole) vale
    `non_determinabile`: è la risposta del sistema, non un errore del modello."""
    regole = regole or {}
    modalita = regole.get("modalita") or {}
    predetta = regole.get("modalita_effettiva") or modalita.get("effettiva")
    if predetta is None and esito == "nessun_segnale":
        predetta = "non_determinabile"
    return {
        "modalita": predetta,
        "modalita_dichiarata": modalita.get("valore"),
        "partner_min": (regole.get("partner_min") or {}).get("valore"),
        "partner_max": (regole.get("partner_max") or {}).get("valore"),
        "quote": [
            {"ambito": q.get("ambito"), "categoria": q.get("categoria"),
             "min": q.get("min_percentuale"), "max": q.get("max_percentuale")}
            for q in regole.get("quote") or []
        ],
    }


def _etichettata(voce: dict) -> bool:
    return (voce.get("etichetta") or {}).get("modalita") is not None


def _raggiungibile(voce: dict, campo: str) -> bool:
    """False se l'etichetta di quel campo poggia su documenti che la pipeline
    non può leggere (fuori da `bando_link`, solo http…): è un limite di
    copertura, non un errore di estrazione."""
    return campo not in ((voce.get("etichetta") or {}).get("non_raggiungibili") or [])


def _metriche_campi(voci: list[dict], predetti: dict[int, dict], *, solo_raggiungibili: bool):
    def tiene(voce: dict, campo: str) -> bool:
        return not solo_raggiungibili or _raggiungibile(voce, campo)

    return {
        "modalita": metriche_modalita(
            [(v["etichetta"]["modalita"], predetti[v["bando_id"]]["modalita"])
             for v in voci if tiene(v, "modalita")]
        ),
        "partner_min": exact_match(
            [(v["etichetta"].get("partner_min"), predetti[v["bando_id"]]["partner_min"])
             for v in voci if tiene(v, "partner_min")]
        ),
        "partner_max": exact_match(
            [(v["etichetta"].get("partner_max"), predetti[v["bando_id"]]["partner_max"])
             for v in voci if tiene(v, "partner_max")]
        ),
        "quote": pr_quote(
            [v["etichetta"].get("quote") or [] for v in voci if tiene(v, "quote")],
            [predetti[v["bando_id"]]["quote"] for v in voci if tiene(v, "quote")],
        ),
    }


def calcola_metriche(campione: list[dict], risultati: dict[int, dict]) -> dict:
    """Metriche sul campione ETICHETTATO: `risultati[bando_id]` =
    {regole, esito, esito_riga, cost_cents, latenza_s}. Le voci ancora senza
    etichetta contano solo per costi, latenze e citazioni. `raggiungibili`
    ripete le metriche senza i campi che la pipeline non può leggere
    (`etichetta.non_raggiungibili`)."""
    etichettate = [v for v in campione if _etichettata(v) and v["bando_id"] in risultati]
    predetti = {
        bid: risultato_da_regole(r.get("regole"), r.get("esito_riga") or r.get("esito"))
        for bid, r in risultati.items()
    }
    return {
        "etichettate": len(etichettate),
        **_metriche_campi(etichettate, predetti, solo_raggiungibili=False),
        "raggiungibili": _metriche_campi(etichettate, predetti, solo_raggiungibili=True),
        "citazioni": citazioni_verificate([r.get("regole") for r in risultati.values()]),
        "costi": costo_latenza(
            [{"bando_id": bid, "cost_cents": r.get("cost_cents"), "latenza_s": r.get("latenza_s")}
             for bid, r in sorted(risultati.items())]
        ),
    }


def _positivo(voce: dict) -> bool | None:
    """Verità del pre-classificatore: l'ETICHETTA (il bando dice qualcosa sul
    partenariato: obbligatorio, ammesso o non ammesso). Il gruppo di
    campionamento vale solo per le voci non ancora etichettate."""
    modalita = voce.get("modalita_attesa")
    if modalita is not None:
        return modalita != "non_determinabile"
    gruppo = voce.get("gruppo")
    return True if gruppo == "positivo" else False if gruppo == "negativo" else None


def recall_preclassificatore(analisi: list[dict]) -> dict:
    """Positivi con segnali (livello ≠ nessuno) / positivi; negativi con
    segnali forti (falsi allarmi che farebbero spendere). Positivo/negativo
    dall'etichetta (`modalita_attesa`), dal gruppo solo se manca."""
    positivi = [a for a in analisi if _positivo(a) is True]
    negativi = [a for a in analisi if _positivo(a) is False]
    return {
        "positivi": len(positivi),
        "recall": _rapporto(sum(1 for a in positivi if a.get("livello") != "nessuno"),
                            len(positivi)),
        "recall_forte": _rapporto(sum(1 for a in positivi if a.get("livello") == "forte"),
                                  len(positivi)),
        "negativi": len(negativi),
        "negativi_con_segnali_forti": sum(1 for a in negativi if a.get("livello") == "forte"),
    }


# ------------------------------------------------------------ analisi (I/O)


def carica_campione(percorso: Path | str = CAMPIONE_PREDEFINITO) -> list[dict]:
    dati = json.loads(Path(percorso).read_text(encoding="utf-8"))
    return [v for v in dati.get("campione") or [] if isinstance(v.get("bando_id"), int)]


async def analizza_bando(secondary, bando_id: int) -> dict:
    """Pipeline SENZA modello su un bando: documenti, lettura,
    pre-classificazione, input e stima dei costi."""
    from app.core.config import get_settings
    from app.services import (
        bandi_service,
        bando_fonti_service,
        download_sicuro,
        partenariato_service,
        pdf_testo,
    )
    from app.services.ai_check_prompts import serializza_sezioni
    from app.services.ai_prezzi import CARATTERI_PER_TOKEN, costo_cents, stima_cents
    from app.services.partenariato_preclassificatore import preclassifica
    from app.services.partenariato_prompts import (
        SYSTEM_PARTENARIATO,
        build_partenariato_input,
        meta_partenariato,
        seleziona_pagine,
    )

    settings = get_settings()
    slug = await partenariato_service._slug_da_id(secondary, bando_id)
    bando = await bandi_service.fetch_bando_for_ai(secondary, slug)
    links = await bando_fonti_service.leggi_link_documenti(secondary, bando_id)
    candidati = bando_fonti_service.seleziona_candidati(
        links, bando.get("allegati") if links is None else None,
        settings.partenariato_max_documenti,
    )
    scaricati = [
        await download_sicuro.scarica_pdf(
            c.url, max_bytes=settings.partenariato_pdf_max_bytes,
            timeout_s=settings.partenariato_download_timeout_seconds,
        )
        for c in candidati
    ]
    testi = [
        await pdf_testo.estrai_testo(
            s.contenuto, max_pagine=settings.partenariato_pdf_max_pagine,
            timeout_s=settings.partenariato_pdf_timeout_seconds,
        )
        if s.stato == "ok" and s.contenuto
        else None
        for s in scaricati
    ]
    documenti, fonti = partenariato_service.documenti_letti(candidati, scaricati, testi)
    sezioni = {"META": meta_partenariato(bando), **dict(serializza_sezioni(bando.get("contenuto")))}
    for doc in documenti:
        for numero, testo in doc.pagine:
            sezioni[f"D{doc.n}-p{numero}"] = testo
    pre = preclassifica(sezioni)
    selezionati = seleziona_pagine(
        documenti, max_caratteri=settings.partenariato_max_caratteri_documenti,
        per_sezione=pre.per_sezione,
    )
    testo, _, _ = build_partenariato_input(bando, bando.get("contenuto"), selezionati)
    caratteri = len(SYSTEM_PARTENARIATO) + len(testo) + len(partenariato_service._schema_json())
    token_input = math.ceil(caratteri / CARATTERI_PER_TOKEN)
    modello = settings.partenariato_ai_model
    return {
        "bando_id": bando_id,
        "slug": bando.get("slug"),
        "livello": pre.livello,
        "segnali": len(pre.segnali),
        "per_sezione": pre.per_sezione,
        "segnali_dettaglio": [s.a_dict() for s in pre.segnali],
        "documenti": {
            "candidati": len(candidati),
            "scaricati": sum(1 for s in scaricati if s.stato == "ok"),
            "letti": sum(1 for d in documenti if d.pagine),
            "non_leggibili": sum(1 for d in documenti if d.stato == "non_leggibile"),
            "pagine_lette": sum(len(d.pagine) for d in documenti),
            "pagine_incluse": sum(len(d.pagine) for d in selezionati),
            "caratteri_letti": sum(len(t) for d in documenti for _, t in d.pagine),
            "stati": [f["stato"] for f in fonti],
        },
        "token_input_stimati": token_input,
        "costo_tipico_cents": costo_cents(modello, token_input, OUTPUT_TIPICO_TOKEN),
        "costo_max_cents": stima_cents(modello, caratteri, settings.partenariato_ai_max_tokens),
        "_sezioni": sezioni,
    }


async def valuta_offline(secondary, campione: list[dict]) -> dict:
    """Recall del pre-classificatore, copertura documentale e stime di costo
    sul campione, senza chiamare il modello."""
    analisi: list[dict] = []
    for voce in campione:
        try:
            risultato = await analizza_bando(secondary, voce["bando_id"])
        except Exception as exc:  # noqa: BLE001 — un bando non blocca il campione
            risultato = {"bando_id": voce["bando_id"], "errore": type(exc).__name__,
                         "livello": None}
        risultato.pop("_sezioni", None)
        risultato.pop("segnali_dettaglio", None)
        risultato["gruppo"] = voce.get("gruppo")
        risultato["modalita_attesa"] = (voce.get("etichetta") or {}).get("modalita")
        analisi.append(risultato)
    stimati = [a for a in analisi if "costo_max_cents" in a]
    return {
        "preclassificatore": recall_preclassificatore(analisi),
        "copertura": {
            "bandi": len(analisi),
            "con_documenti_letti": sum(1 for a in stimati if a["documenti"]["letti"]),
            "senza_documenti": sum(1 for a in stimati if not a["documenti"]["candidati"]),
            "errori": sum(1 for a in analisi if a.get("errore")),
        },
        "stima": {
            "costo_tipico_totale_cents": sum(a["costo_tipico_cents"] for a in stimati),
            "costo_max_totale_cents": sum(a["costo_max_cents"] for a in stimati),
        },
        "bandi": analisi,
    }


def _dentro_repo(percorso: Path) -> bool:
    try:
        percorso.resolve().relative_to(_REPO.resolve())
        return True
    except ValueError:
        return False


async def prepara(secondary, campione: list[dict], cartella: Path) -> list[Path]:
    """Un foglio di lavoro JSON per bando (segnali e pagine rilevanti), FUORI
    dal repository: contiene testo dei documenti ufficiali."""
    if _dentro_repo(cartella):
        raise ValueError("La cartella di lavoro deve stare fuori dal repository")
    cartella.mkdir(parents=True, exist_ok=True)
    scritti: list[Path] = []
    for voce in campione:
        analisi = await analizza_bando(secondary, voce["bando_id"])
        sezioni = analisi.pop("_sezioni")
        rilevanti = [k for k in sezioni if analisi["per_sezione"].get(k) or k == "META"]
        foglio = {
            **analisi,
            "gruppo": voce.get("gruppo"),
            "etichetta_attuale": voce.get("etichetta"),
            "pagine_rilevanti": [{"sezione": k, "testo": sezioni[k]} for k in rilevanti],
        }
        percorso = cartella / f"bando_{voce['bando_id']}.json"
        percorso.write_text(json.dumps(foglio, ensure_ascii=False, indent=1), encoding="utf-8")
        scritti.append(percorso)
    return scritti


async def valuta_reale(
    primary, secondary, ai, campione: list[dict], *, tetto_cents: int
) -> dict:
    """Pipeline vera (origine `valutazione`, budget del gruppo `valutazione`
    = tetto giornaliero, fail-closed nella RPC). Si ferma al primo rifiuto di
    budget. I bandi con un risultato ancora fresco non si ripagano."""
    from app.core.errors import AppError
    from app.services import bandi_service, partenariato_service

    risultati: dict[int, dict] = {}
    fermato = None
    for voce in campione:
        bando_id = voce["bando_id"]
        inizio = time.monotonic()
        try:
            slug = await partenariato_service._slug_da_id(secondary, bando_id)
            bando = await bandi_service.fetch_bando_for_ai(secondary, slug)
            esito = await partenariato_service.esegui_per_bando(
                primary, secondary, ai, bando, origine="valutazione", budget_cents=tetto_cents
            )
        except AppError as exc:
            if exc.code == "ai_sospesa_oggi":
                fermato = "tetto_raggiunto"
                break
            esito = exc.code
        latenza = round(time.monotonic() - inizio, 2)
        riga = await partenariato_service._leggi_riga(primary, bando_id) or {}
        risultati[bando_id] = {
            "esito": esito,
            # esito della riga (anche quando la pipeline non è partita: fresca)
            "esito_riga": riga.get("esito"),
            "regole": riga.get("regole"),
            "cost_cents": riga.get("cost_cents") if esito == "estratta" else 0,
            "latenza_s": latenza,
        }
    return {"fermato": fermato, "metriche": calcola_metriche(campione, risultati),
            "esiti": {bid: r["esito"] for bid, r in risultati.items()}}


# ------------------------------------------------------------------ CLI


async def _crea_secondario():
    """Client anon del catalogo dalle variabili d'ambiente (sola lettura)."""
    from supabase import acreate_client

    url = os.environ.get("SECONDARY_SUPABASE_URL") or os.environ.get("SUPABASE_URL_BANDI")
    chiave = os.environ.get("SECONDARY_SUPABASE_ANON_KEY") or os.environ.get(
        "PUBLIC_SUPABASE_BANDI_ANON_KEY"
    )
    if not url or not chiave:
        raise SystemExit(
            "Servono SECONDARY_SUPABASE_URL e SECONDARY_SUPABASE_ANON_KEY "
            "(oppure SUPABASE_URL_BANDI e PUBLIC_SUPABASE_BANDI_ANON_KEY)"
        )
    return await acreate_client(url, chiave)


async def _crea_primario_e_ai():
    from app.clients.anthropic_ai import AiCheckClient
    from app.clients.supabase import create_primary_client
    from app.core.config import get_settings

    settings = get_settings()
    return await create_primary_client(settings), AiCheckClient(settings)


def _argomenti(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.services.partenariato_valutazione",
        description="Valutazione delle regole di partenariato (WP3)",
    )
    modo = parser.add_mutually_exclusive_group(required=True)
    modo.add_argument("--offline", action="store_true", help="senza modello: recall e stime")
    modo.add_argument("--prepara", metavar="OUT_DIR", help="fogli di lavoro fuori dal repo")
    modo.add_argument("--reale", action="store_true", help="pipeline vera (spende)")
    modo.add_argument("--rivaluta", metavar="FILE", action="append",
                      help="senza modello: rivaluta l'output grezzo di --reale --locale "
                           "(ripetibile, in ordine di lancio; vuole --out)")
    parser.add_argument("--locale", action="store_true",
                        help="con --reale: senza DB primario, spesa in memoria")
    parser.add_argument("--solo", metavar="ID,ID", default=None,
                        help="con --reale --locale: solo questi bandi del campione")
    parser.add_argument("--tetto-cents", type=int, default=None, help="tetto di spesa (USD cent)")
    parser.add_argument("--conferma", action="store_true", help="spende davvero (con --reale)")
    parser.add_argument("--campione", default=str(CAMPIONE_PREDEFINITO))
    parser.add_argument("--out", default=None, help="file JSON del risultato (default stdout)")
    return parser.parse_args(argv)


def _scrivi(risultato: dict, out: str | None) -> None:
    testo = json.dumps(risultato, ensure_ascii=False, indent=1, default=str)
    if out:
        Path(out).write_text(testo, encoding="utf-8")
    else:
        print(testo)


async def _esegui(args: argparse.Namespace) -> int:
    campione = carica_campione(args.campione)
    if args.rivaluta:
        if args.locale or args.solo is not None or args.tetto_cents is not None or args.conferma:
            print("--rivaluta non spende: niente --locale, --solo, --tetto-cents, --conferma",
                  file=sys.stderr)
            return 2
        from app.services import partenariato_valutazione_locale as locale

        return await locale.rivaluta_cli(args.rivaluta, campione, out=args.out)
    if args.locale and not args.reale:
        print("--locale vale solo con --reale", file=sys.stderr)
        return 2
    if args.solo is not None and not args.locale:
        print("--solo vale solo con --reale --locale", file=sys.stderr)
        return 2
    if args.reale and (args.tetto_cents is None or args.tetto_cents <= 0):
        print("--reale richiede --tetto-cents N (> 0)", file=sys.stderr)
        return 2
    if args.locale:
        from app.services import partenariato_valutazione_locale as locale

        return await locale.esegui_cli(
            campione, tetto_cents=args.tetto_cents, conferma=args.conferma, out=args.out,
            solo=args.solo,
        )
    if args.prepara and _dentro_repo(Path(args.prepara)):
        print("La cartella di lavoro deve stare fuori dal repository", file=sys.stderr)
        return 2
    secondary = await _crea_secondario()
    if args.prepara:
        scritti = await prepara(secondary, campione, Path(args.prepara))
        print(f"Scritti {len(scritti)} fogli di lavoro in {args.prepara}")
        return 0
    offline = await valuta_offline(secondary, campione)
    if args.offline:
        _scrivi(offline, args.out)
        return 0
    stima = offline["stima"]
    print(
        f"Stima: {stima['costo_tipico_totale_cents']} cent tipici, "
        f"{stima['costo_max_totale_cents']} cent al massimo; tetto {args.tetto_cents} cent.",
        file=sys.stderr,
    )
    if not args.conferma:
        print("Nessuna spesa: aggiungi --conferma per eseguire.", file=sys.stderr)
        return 0
    primary, ai = await _crea_primario_e_ai()
    if not ai.enabled:
        print("API Anthropic non configurata", file=sys.stderr)
        return 2
    try:
        risultato = await valuta_reale(primary, secondary, ai, campione,
                                       tetto_cents=args.tetto_cents)
    finally:
        await ai.aclose()
    _scrivi({"offline": offline, "reale": risultato}, args.out)
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_esegui(_argomenti(argv)))


if __name__ == "__main__":
    sys.exit(main())
