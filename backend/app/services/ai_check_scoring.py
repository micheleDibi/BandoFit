"""Stadio C dell'AI-check: scoring DETERMINISTICO e assemblaggio del report.

Funzioni pure (zero I/O, zero LLM). Il modello linguistico produce solo
verdetti qualitativi ancorati a citazioni; qui:
  * i pre-check strutturati (confronto esatto coi facet del catalogo) fanno
    da autorità: nel merge coi verdetti LLM della stessa categoria vince
    sempre il PEGGIORE — mai promuovere;
  * il gate di ammissibilità è binario e prevale su qualunque punteggio;
  * il punteggio è una funzione aritmetica dei verdetti: `stima` se il bando
    pubblica la griglia (punti per criterio), `euristico` con pesi interni
    altrimenti;
  * le guardie anti-allucinazione retrocedono i verdetti non citabili.
"""

from app.schemas.ai_check import ExtractionResult, MatchingResult
from app.services.openapi_mapping import company_regioni_ids

DISCLAIMER = (
    "Analisi automatica generata da un modello linguistico a scopo orientativo: "
    "verifica sempre il testo ufficiale del bando prima di candidarti."
)

# Frazione di soddisfacimento per verdetto. `dato_mancante` vale 0 e resta nel
# denominatore: il punteggio non si gonfia mai per dati assenti.
FRAC = {
    "soddisfatto": 1.0,
    "parzialmente_soddisfatto": 0.5,
    "non_soddisfatto": 0.0,
    "dato_mancante": 0.0,
}

# Pesi del punteggio euristico (griglia non pubblicata). Il peso è spostato
# su requisiti e criteri — che cambiano da bando a bando — e non sui confronti
# di catalogo (settore/regione/beneficiari), quasi identici per bandi simili:
# altrimenti aziende e bandi diversi finiscono tutti sullo stesso punteggio.
HEURISTIC_WEIGHTS = {
    "requisiti": 30,     # quota di requisiti obbligatori soddisfatti (post-merge)
    "criteri": 40,       # media dei criteri valutati dall'LLM
    "settoriale": 12,    # pre-check ateco/settore (best-of)
    "territoriale": 9,   # pre-check regione
    "beneficiari": 9,    # pre-check beneficiari (tipologia/dimensione)
}

# Categoria del requisito → pre-check "effettivo" corrispondente. ATECO e
# settore sono EVIDENZE ALTERNATIVE dello stesso fatto (appartenenza di
# settore: i tag di catalogo sono tematici e non esaustivi, l'ATECO è il
# filtro giuridicamente preciso): vengono combinati in un unico esito
# "settoriale" dove basta un match (best-of), mai l'uno a smentire l'altro.
_PRECHECK_BY_CATEGORY = {
    "territoriale": "regione",
    "settoriale": "settoriale",
    "soggettivo": "beneficiari",
    "dimensionale": "beneficiari",
}

_GATE_FACET_LABELS = {
    "regione": ("territoriale", "le regioni ammesse"),
    "settoriale": ("settoriale", "i settori / codici ATECO ammessi"),
    "beneficiari": ("soggettivo", "le tipologie di beneficiari ammesse"),
}


# ----------------------------------------------------------------- prechecks

def _division(code: str | None) -> str | None:
    """Divisione ATECO a 2 cifre da un codice qualsiasi ("62.01" → "62")."""
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    return digits[:2].zfill(2) if len(digits) >= 2 else None


def _junction_items(bando: dict, junction: str, inner: str) -> list[dict]:
    items = []
    for row in bando.get(junction) or []:
        item = row.get(inner) if isinstance(row, dict) else None
        if isinstance(item, dict):
            items.append(item)
    return items


def facet_prechecks(bando: dict, company: dict | None, derived: dict | None) -> dict:
    """Confronti ESATTI tra i facet del bando (catalogo) e i dati aziendali.

    Esiti: soddisfatto | non_soddisfatto | dato_mancante | non_applicabile
    (facet non specificata dal bando)."""
    company = company or {}
    derived = derived or {}
    checks: dict[str, dict] = {}

    # Regione — TUTTE le sedi (sede legale + unità locali): basta una sede in
    # una regione ammessa perché il vincolo territoriale sia soddisfatto.
    bando_regioni = _junction_items(bando, "bando_regioni", "regioni")
    if not bando_regioni:
        checks["regione"] = {"esito": "non_applicabile", "bando": [], "azienda": company.get("regione_nome")}
    else:
        nomi = [r.get("nome") for r in bando_regioni]
        regioni_ids = company_regioni_ids(company, derived)
        if not regioni_ids:
            esito = "dato_mancante"
        else:
            esito = (
                "soddisfatto"
                if any(r.get("id") in regioni_ids for r in bando_regioni)
                else "non_soddisfatto"
            )
        checks["regione"] = {
            "esito": esito,
            "bando": nomi,
            "azienda": company.get("regione_nome") or derived.get("regione_nome"),
            "azienda_sedi": len(regioni_ids),
        }

    # ATECO (divisioni a 2 cifre; contano anche i secondari certificati)
    bando_ateco = _junction_items(bando, "bando_codici_ateco", "codici_ateco")
    company_divisions = {
        d
        for d in (
            [_division(company.get("ateco_codice")), _division(derived.get("ateco_principale"))]
            + [_division(c) for c in derived.get("ateco_secondari") or []]
        )
        if d
    }
    if not bando_ateco:
        checks["ateco"] = {"esito": "non_applicabile", "bando": [], "azienda": sorted(company_divisions)}
    elif not company_divisions:
        checks["ateco"] = {"esito": "dato_mancante", "bando": [a.get("codice") for a in bando_ateco], "azienda": []}
    else:
        bando_divisions = {_division(a.get("codice")) for a in bando_ateco}
        esito = "soddisfatto" if company_divisions & bando_divisions else "non_soddisfatto"
        checks["ateco"] = {
            "esito": esito,
            "bando": sorted(d for d in bando_divisions if d),
            "azienda": sorted(company_divisions),
        }

    # Settore
    bando_settori = _junction_items(bando, "bando_settori", "settori")
    if not bando_settori:
        checks["settore"] = {"esito": "non_applicabile", "bando": [], "azienda": company.get("settore_nome")}
    elif company.get("settore_id") is None:
        checks["settore"] = {"esito": "dato_mancante", "bando": [s.get("nome") for s in bando_settori], "azienda": None}
    else:
        esito = (
            "soddisfatto"
            if any(s.get("id") == company.get("settore_id") for s in bando_settori)
            else "non_soddisfatto"
        )
        checks["settore"] = {"esito": esito, "bando": [s.get("nome") for s in bando_settori], "azienda": company.get("settore_nome")}

    # Beneficiari (derivati dall'import certificato)
    bando_beneficiari = _junction_items(bando, "bando_beneficiari", "beneficiari")
    company_beneficiari = derived.get("beneficiari") or []
    if not bando_beneficiari:
        checks["beneficiari"] = {"esito": "non_applicabile", "bando": [], "azienda": [b.get("nome") for b in company_beneficiari]}
    elif not company_beneficiari:
        checks["beneficiari"] = {"esito": "dato_mancante", "bando": [b.get("nome") for b in bando_beneficiari], "azienda": []}
    else:
        bando_ids = {b.get("id") for b in bando_beneficiari}
        esito = (
            "soddisfatto"
            if any(b.get("id") in bando_ids for b in company_beneficiari)
            else "non_soddisfatto"
        )
        checks["beneficiari"] = {
            "esito": esito,
            "bando": [b.get("nome") for b in bando_beneficiari],
            "azienda": [b.get("nome") for b in company_beneficiari],
        }

    # Stato del bando: informativo (non entra nel gate né nel punteggio).
    checks["stato_bando"] = {"esito": "soddisfatto" if bando.get("stato_bando") == "aperto" else "non_soddisfatto", "valore": bando.get("stato_bando")}

    return checks


# ----------------------------------------------------------- sanitizzazione

def _sanitize_matching(
    extraction: ExtractionResult, matching: MatchingResult
) -> tuple[dict[str, dict], dict[str, dict], list[dict]]:
    """Guardie anti-allucinazione applicate in codice, non solo nel prompt:
    - verdetti con id inesistenti nell'estrazione: scartati;
    - `soddisfatto`/`non_soddisfatto` senza `dato_azienda` citabile:
      retrocessi a `dato_mancante`;
    - citazioni dell'estrazione non ritrovate alla lettera nella sezione
      citata: flag `citazione_non_verificata` (non invalidano il verdetto,
      ma il report lo dichiara).
    Ritorna (verdetti requisiti per id, verdetti criteri per id, dati_mancanti extra)."""
    known_req = {r.id for r in extraction.requisiti_obbligatori}
    known_cri = {c.id for c in extraction.criteri_valutazione}
    extra_missing: list[dict] = []

    def _clean(verdicts, known_ids):
        out: dict[str, dict] = {}
        for verdict in verdicts:
            if verdict.id not in known_ids:
                continue
            entry = {
                "esito": verdict.esito,
                "dato_azienda": verdict.dato_azienda.model_dump() if verdict.dato_azienda else None,
                "motivazione": verdict.motivazione,
            }
            if verdict.esito in ("soddisfatto", "parzialmente_soddisfatto", "non_soddisfatto") and not entry["dato_azienda"]:
                entry["esito"] = "dato_mancante"
                entry["motivazione"] = (
                    "Verdetto retrocesso: il modello non ha citato quale dato aziendale "
                    "lo sosteneva. " + entry["motivazione"]
                )
                extra_missing.append(
                    {"campo": None, "descrizione": f"Dato non citabile per {verdict.id}", "ref": verdict.id}
                )
            out[verdict.id] = entry
        return out

    return _clean(matching.requisiti, known_req), _clean(matching.criteri, known_cri), extra_missing


def _citation_verified(citazione, sections: dict[str, str]) -> bool:
    # Il modello può copiare l'indice come lo vede nel testo ("[S1]"): la
    # chiave va normalizzata prima del lookup.
    key = citazione.sezione.strip().lstrip("[").rstrip("]").strip()
    section_text = sections.get(key) or sections.get(key.upper())
    if not section_text:
        return False
    needle = " ".join(citazione.testo_esatto.split()).lower()
    haystack = " ".join(section_text.split()).lower()
    # Il testo del bando può contenere i marcatori **grassetto** della
    # serializzazione: si confronta anche la versione senza marcatori.
    return needle in haystack or needle.replace("**", "") in haystack.replace("**", "")


def _combined_settoriale(prechecks: dict) -> str:
    """ATECO e settore combinati best-of: basta un match per soddisfare."""
    esiti = [
        (prechecks.get(key) or {}).get("esito")
        for key in ("ateco", "settore")
    ]
    esiti = [e for e in esiti if e and e != "non_applicabile"]
    if not esiti:
        return "non_applicabile"
    if "soddisfatto" in esiti:
        return "soddisfatto"
    if "non_soddisfatto" in esiti:
        return "non_soddisfatto"
    return "dato_mancante"


def _effective_prechecks(prechecks: dict) -> dict[str, str]:
    """Esiti autoritativi per il merge e per il gate: regione, settoriale
    combinato (best-of ateco/settore), beneficiari."""
    return {
        "regione": (prechecks.get("regione") or {}).get("esito") or "non_applicabile",
        "settoriale": _combined_settoriale(prechecks),
        "beneficiari": (prechecks.get("beneficiari") or {}).get("esito") or "non_applicabile",
    }


def _merge_precheck(verdict: dict, categoria: str, effective: dict[str, str]) -> dict:
    """Un pre-check strutturato in CONTRADDIZIONE (non_soddisfatto) retrocede
    il verdetto del modello — mai promuovere. Un pre-check `dato_mancante`
    invece NON retrocede: significa solo che il confronto esatto non era
    possibile qui (es. manca l'import certificato), mentre il modello può
    aver verificato con dati del form più ricchi e citabili."""
    key = _PRECHECK_BY_CATEGORY.get(categoria)
    if key and effective.get(key) == "non_soddisfatto" and verdict["esito"] != "non_soddisfatto":
        verdict = {
            **verdict,
            "esito": "non_soddisfatto",
            "motivazione": verdict["motivazione"]
            + f" (Verifica strutturata sui dati del catalogo: {key} → non soddisfatto.)",
        }
    return verdict


# ------------------------------------------------------------------ scoring

def _round_or_none(value: float | None) -> int | None:
    return None if value is None else max(0, min(100, round(value)))


def score_report(
    extraction: ExtractionResult,
    matching: MatchingResult,
    prechecks: dict,
    sections: dict[str, str],
    meta: dict,
) -> dict:
    """Assembla il report finale: gate di ammissibilità + punteggio
    deterministico + evidenze punto-punto."""
    req_verdicts, cri_verdicts, extra_missing = _sanitize_matching(extraction, matching)
    effective = _effective_prechecks(prechecks)

    # --- Requisiti obbligatori: merge coi pre-check e gate ---
    requisiti_out: list[dict] = []
    gate_esiti: list[str] = []
    for req in extraction.requisiti_obbligatori:
        verdict = req_verdicts.get(req.id) or {
            "esito": "dato_mancante",
            "dato_azienda": None,
            "motivazione": "Il modello non ha emesso un verdetto per questo requisito.",
        }
        verdict = _merge_precheck(verdict, req.categoria, effective)
        gate_esiti.append(verdict["esito"])
        requisiti_out.append(
            {
                "id": req.id,
                "testo": req.testo,
                "categoria": req.categoria,
                "verdetto": verdict["esito"],
                "riferimento_bando": {
                    "sezione": req.citazione.sezione,
                    "testo": req.citazione.testo_esatto,
                    "verificata": _citation_verified(req.citazione, sections),
                },
                "dato_azienda": verdict["dato_azienda"],
                "motivazione": verdict["motivazione"],
            }
        )

    # I vincoli di catalogo in CONTRADDIZIONE entrano nel gate anche quando
    # l'estrazione non ha prodotto alcun requisito della categoria (LLM non
    # deterministico o cache): senza questo, un bando di un'altra regione
    # potrebbe risultare "ammissibile" col mismatch visibile solo nelle
    # verifiche strutturate. La voce sintetica rende il verdetto spiegabile.
    covered = {req.categoria for req in extraction.requisiti_obbligatori}
    for key, (categoria, label) in _GATE_FACET_LABELS.items():
        if effective.get(key) != "non_soddisfatto":
            continue
        mapped = {c for c, k in _PRECHECK_BY_CATEGORY.items() if k == key}
        if covered & mapped:
            continue  # già rappresentato (e mergiato) da un requisito estratto
        if key == "settoriale":
            source = next(
                (
                    prechecks[k]
                    for k in ("ateco", "settore")
                    if (prechecks.get(k) or {}).get("esito") == "non_soddisfatto"
                ),
                {},
            )
        else:
            source = prechecks.get(key) or {}
        bando_values = source.get("bando") or []
        azienda_value = source.get("azienda")
        gate_esiti.append("non_soddisfatto")
        requisiti_out.append(
            {
                "id": f"V{len(requisiti_out) + 1}",
                "testo": f"Vincolo di catalogo: {label}",
                "categoria": categoria,
                "verdetto": "non_soddisfatto",
                "riferimento_bando": {
                    "sezione": "META",
                    "testo": ", ".join(str(v) for v in bando_values) or label,
                    "verificata": True,
                },
                "dato_azienda": {
                    "campo": key,
                    "valore": ", ".join(map(str, azienda_value))
                    if isinstance(azienda_value, list)
                    else str(azienda_value),
                },
                "motivazione": (
                    f"Verifica strutturata sui dati del catalogo: l'azienda non rientra tra {label}."
                ),
            }
        )

    if any(e == "non_soddisfatto" for e in gate_esiti):
        esito_ammissibilita = "non_ammissibile"
    elif any(e == "dato_mancante" for e in gate_esiti):
        esito_ammissibilita = "da_verificare"
    else:
        esito_ammissibilita = "ammissibile"

    # --- Criteri: punteggio ---
    griglia = extraction.griglia
    punti_max_disponibili = [
        c.punti_max for c in extraction.criteri_valutazione if c.punti_max
    ]
    use_grid = bool(griglia.presente and punti_max_disponibili)

    criteri_out: list[dict] = []
    criteri_fracs: list[float] = []
    punti_ottenuti = 0.0
    for criterio in extraction.criteri_valutazione:
        verdict = cri_verdicts.get(criterio.id) or {
            "esito": "dato_mancante",
            "dato_azienda": None,
            "motivazione": "Il modello non ha emesso un verdetto per questo criterio.",
        }
        frac = FRAC.get(verdict["esito"], 0.0)
        criteri_fracs.append(frac)
        punteggio_parziale = None
        if use_grid and criterio.punti_max:
            punteggio_parziale = round(frac * criterio.punti_max, 1)
            punti_ottenuti += punteggio_parziale
        criteri_out.append(
            {
                "id": criterio.id,
                "nome": criterio.nome,
                "categoria": criterio.categoria,
                "verdetto": verdict["esito"],
                "punti_max": criterio.punti_max if use_grid else None,
                "punteggio_parziale": punteggio_parziale,
                "riferimento_bando": {
                    "sezione": criterio.citazione.sezione,
                    "testo": criterio.citazione.testo_esatto,
                    "verificata": _citation_verified(criterio.citazione, sections),
                },
                "dato_azienda": verdict["dato_azienda"],
                "motivazione": verdict["motivazione"],
            }
        )

    if use_grid:
        tipo_punteggio = "stima"
        sum_punti = sum(punti_max_disponibili)
        # L'estrazione può divergere dal totale dichiarato (griglia parziale o
        # punti gonfiati): si normalizza sul massimo tra i due, mai sopra 100.
        max_totale = max(griglia.punteggio_max_totale or 0, sum_punti)
        punteggio_totale = _round_or_none(100 * punti_ottenuti / max_totale) if max_totale else None
    else:
        tipo_punteggio = "euristico"
        components: list[tuple[int, float]] = []

        # Quota dei requisiti obbligatori soddisfatti (verdetti post-merge,
        # voci sintetiche comprese): i dato_mancante valgono 0 ma restano nel
        # denominatore — l'assenza di dati non gonfia mai il punteggio.
        if gate_esiti:
            requisiti_frac = sum(FRAC.get(e, 0.0) for e in gate_esiti) / len(gate_esiti)
            components.append((HEURISTIC_WEIGHTS["requisiti"], requisiti_frac))
        if criteri_fracs:
            components.append((HEURISTIC_WEIGHTS["criteri"], sum(criteri_fracs) / len(criteri_fracs)))
        # Solo i pre-check con un esito accertato (sì/no) pesano: un
        # dato_mancante qui significa "confronto esatto non possibile"
        # (es. manca l'import certificato) e non deve costare punti — è già
        # segnalato in dati_mancanti e, se obbligatorio, nel gate.
        for weight_key, facet_key in (
            ("settoriale", "settoriale"),
            ("territoriale", "regione"),
            ("beneficiari", "beneficiari"),
        ):
            esito = effective.get(facet_key)
            if esito in ("soddisfatto", "non_soddisfatto"):
                components.append((HEURISTIC_WEIGHTS[weight_key], FRAC[esito]))

        total_weight = sum(w for w, _ in components)
        punteggio_totale = (
            _round_or_none(100 * sum(w * f for w, f in components) / total_weight)
            if total_weight
            else None
        )
        # I pesi effettivi sono dichiarati nel report per trasparenza.
        for entry in criteri_out:
            entry["peso"] = None

    # --- Punti di forza/debolezza e dati mancanti ---
    dati_mancanti = [d.model_dump() for d in matching.dati_mancanti] + extra_missing
    punti_di_debolezza = [p.model_dump() for p in matching.punti_di_debolezza]
    if (
        use_grid
        and griglia.soglia_minima
        and punteggio_totale is not None
        and punti_ottenuti < griglia.soglia_minima
        # Griglia parziale (punti estratti < soglia dichiarata): il confronto
        # sarebbe tra scale diverse — meglio nessun avviso di un avviso falso.
        and sum(punti_max_disponibili) >= griglia.soglia_minima
    ):
        punti_di_debolezza.append(
            {
                "testo": (
                    f"Il punteggio stimato ({round(punti_ottenuti, 1)}) è sotto la "
                    f"soglia minima del bando ({griglia.soglia_minima})."
                ),
                "ref": None,
            }
        )
    if bando_stato := prechecks.get("stato_bando"):
        if bando_stato.get("esito") == "non_soddisfatto":
            punti_di_debolezza.append(
                {"testo": f"Il bando non risulta aperto (stato: {bando_stato.get('valore')}).", "ref": None}
            )

    return {
        "schema_version": 1,
        "esito_ammissibilita": esito_ammissibilita,
        "requisiti": requisiti_out,
        "criteri": criteri_out,
        "punteggio_totale": punteggio_totale,
        "tipo_punteggio": tipo_punteggio,
        "griglia": {
            "presente": griglia.presente,
            "fonte": griglia.fonte,
            "punteggio_max_totale": griglia.punteggio_max_totale,
            "punti_ottenuti_stimati": round(punti_ottenuti, 1) if use_grid else None,
            "soglia_minima": griglia.soglia_minima,
            "note": griglia.note,
        },
        "pesi_euristici": HEURISTIC_WEIGHTS if tipo_punteggio == "euristico" else None,
        "verifiche_strutturate": prechecks,
        "punti_di_forza": [p.model_dump() for p in matching.punti_di_forza],
        "punti_di_debolezza": punti_di_debolezza,
        "dati_mancanti": dati_mancanti,
        "disclaimer": DISCLAIMER,
        "meta": meta,
    }
