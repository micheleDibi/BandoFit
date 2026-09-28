"""Post-elaborazione deterministica delle regole di partenariato (WP3, PURO).

Il modello estrae, il codice decide che cosa vale. Per ogni voce:
- la citazione si verifica sul testo inviato (`citazioni.verifica_citazione`,
  pagine adiacenti ed ellissi comprese); non ritrovata → `da_verificare`;
- i controlli di coerenza e di RANGE (partner_min ≤ partner_max, percentuali
  0–100, minimo ≤ massimo, «non ammesso» con forme ammesse, regole
  finanziarie con `bilanci_indicatori.valida_regola`) declassano la voce a
  `da_verificare` con un avviso: MAI un'eccezione, la chiamata è già pagata;
- `modalita_effettiva` = modalità dichiarata SOLO se la sua citazione è
  verificata e coerente, altrimenti `non_determinabile` (è ciò che usa il
  filtro «Ammette partenariato»);
- le regioni si mappano sugli id del catalogo (le ignote si scartano con un
  avviso), i tipi di soggetto sugli id `beneficiari`;
- `scrub_menzioni` su tutto il testo (domini esclusi dal catalogo), in tempo
  lineare anche su stringhe ostili del modello.

Le voci `da_verificare` restano visibili all'utente (con il badge) ma sono
escluse dagli usi deterministici del modulo.
"""

import math
import re
import unicodedata
from typing import Any

from app.schemas.ai_check import CitazioneBando
from app.schemas.partenariato import (
    CitazioneRegolaOut,
    ComposizioneOut,
    ComposizioneVoce,
    ConteggioOut,
    CostituzioneOut,
    DocumentoRichiestoOut,
    FormaAmmessaOut,
    ModalitaOut,
    PartenariatoEstrazione,
    QuotaOut,
    RegolaFinanziariaOut,
    RegolePartenariatoOut,
    VincoloOut,
)
from app.schemas.regole_finanziarie import RegolaFinanziaria
from app.services.bilanci_indicatori import valida_regola
from app.services.citazioni import normalizza_sezione, normalizza_testo, verifica_citazione
from app.services.partenariato_prompts import scrub_menzioni
from app.services.partenariato_vocabolario import FORME, TIPI_SOGGETTO, beneficiari_per_tipo

MAX_TESTO = 2000
MAX_URL = 2048
# La citazione della modalità decide `modalita_effettiva` (filtro «Ammette
# partenariato»): ritrovarla alla lettera non basta se è un frammento che
# compare ovunque («partenariato», «in ATS»). Sotto soglia la voce resta
# da verificare. Le altre voci non hanno soglia (citano spesso numeri brevi:
# «almeno 3 imprese»).
MIN_PAROLE_CITAZIONE_MODALITA = 3
MIN_CARATTERI_CITAZIONE_MODALITA = 15
_DOCUMENTO = re.compile(r"D(\d+)-p(\d+)")
_SEZIONE_SCHEDA = re.compile(r"S\d+")
# Prefisso minimo per riconoscere una regione per inizio del nome
# («Valle d'Aosta» → «Valle d'Aosta/Vallée d'Aoste»).
_MIN_PREFISSO_REGIONE = 5


# ------------------------------------------------------------ utilità


# Margine oltre il limite prima della pulizia: le menzioni tolte accorciano.
_MARGINE_TESTO = 512


def _testo(valore: Any, limite: int = MAX_TESTO) -> str | None:
    """Testo del modello per l'API: spazi compattati, tagliato PRIMA della
    pulizia (con un margine) e di nuovo dopo: l'output del modello non ha
    lunghezza massima per campo."""
    if not isinstance(valore, str):
        return None
    compatto = " ".join(valore.split())[: limite + _MARGINE_TESTO]
    pulito = " ".join(scrub_menzioni(compatto).split())
    return pulito[:limite] or None


def _url_https(url: Any) -> str | None:
    if not isinstance(url, str) or len(url) > MAX_URL:
        return None
    return url if url.lower().startswith("https://") else None


def _finito(numero: float | int | None) -> bool:
    return numero is not None and math.isfinite(float(numero))


def _stato(verificata: bool, avvisi: list[str]) -> str:
    return "verificata" if verificata and not avvisi else "da_verificare"


def _documenti(fonti: Any) -> dict[int, dict]:
    """Fonti (voci di `fonti_usate` o oggetti con n/etichetta/url) per numero."""
    per_numero: dict[int, dict] = {}
    for fonte in fonti or []:
        if isinstance(fonte, dict):
            n = fonte.get("n")
            voce = fonte
        else:
            n = getattr(fonte, "n", None)
            voce = {"etichetta": getattr(fonte, "etichetta", None), "url": getattr(fonte, "url", None)}
        if isinstance(n, int) and not isinstance(n, bool):
            per_numero[n] = voce
    return per_numero


def _citazione(
    citazione: CitazioneBando | None, sezioni: dict[str, str], documenti: dict[int, dict]
):
    """(CitazioneRegolaOut | None, verificata)."""
    if citazione is None:
        return None, False
    grezza = citazione.sezione if isinstance(citazione.sezione, str) else ""
    sezione = normalizza_sezione(grezza) or grezza.strip()[:40]
    verificata = verifica_citazione(grezza, citazione.testo_esatto or "", sezioni)
    url = pagina = None
    corrispondenza = _DOCUMENTO.fullmatch(sezione)
    if corrispondenza:
        n, pagina = int(corrispondenza.group(1)), int(corrispondenza.group(2))
        doc = documenti.get(n) or {}
        etichetta = _testo(doc.get("etichetta"), 200) or f"Documento {n}"
        fonte = f"{etichetta} — pag. {pagina}"
        url = _url_https(doc.get("url"))
    elif sezione == "META" or _SEZIONE_SCHEDA.fullmatch(sezione):
        fonte = "Scheda del bando"
    else:
        fonte = "Fonte non riconosciuta"
    return (
        CitazioneRegolaOut(
            sezione=sezione[:40],
            fonte_etichetta=fonte,
            testo=_testo(citazione.testo_esatto) or "",
            verificata=verificata,
            url_documento=url,
            pagina=pagina,
        ),
        verificata,
    )


# ------------------------------------------------------------ regioni


def _chiave_regione(nome: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", ascii_.casefold())


def _indice_regioni(lookups: Any) -> list[tuple[str, int, str]] | None:
    """[(chiave normalizzata, id, nome del catalogo)] dalle lookups (oggetto
    con `.regioni` o dict); None se non disponibili."""
    if lookups is None:
        return None
    regioni = lookups.get("regioni") if isinstance(lookups, dict) else getattr(lookups, "regioni", None)
    if not regioni:
        return None
    indice: list[tuple[str, int, str]] = []
    for voce in regioni:
        vid = voce.get("id") if isinstance(voce, dict) else getattr(voce, "id", None)
        nome = voce.get("nome") if isinstance(voce, dict) else getattr(voce, "nome", None)
        if isinstance(vid, int) and isinstance(nome, str) and nome.strip():
            indice.append((_chiave_regione(nome), vid, nome))
    return indice or None


def mappa_regioni(
    nomi: list[str], indice: list[tuple[str, int, str]] | None
) -> tuple[list[str], list[int], list[str]]:
    """(nomi del catalogo riconosciuti, id, nomi scartati). Confronto senza
    accenti, spazi e punteggiatura; poi per inizio del nome (≥ 5 lettere)."""
    riconosciuti: list[str] = []
    ids: list[int] = []
    scartati: list[str] = []
    for nome in nomi or []:
        if not isinstance(nome, str) or not nome.strip():
            continue
        chiave = _chiave_regione(nome)
        trovata = None
        if indice and chiave:
            trovata = next((voce for voce in indice if voce[0] == chiave), None)
            if trovata is None and len(chiave) >= _MIN_PREFISSO_REGIONE:
                simili = [v for v in indice if v[0].startswith(chiave) or chiave.startswith(v[0])]
                trovata = simili[0] if len(simili) == 1 else None
        if trovata is None:
            scartati.append(_testo(nome, 80) or "")
            continue
        if trovata[1] not in ids:
            ids.append(trovata[1])
            riconosciuti.append(trovata[2])
    return riconosciuti, ids, [s for s in scartati if s]


# ------------------------------------------------------------ voci


def _citazione_probante(citazione: CitazioneBando | None) -> bool:
    """Abbastanza lunga da fondare la modalità (non un frammento qualsiasi)."""
    testo = normalizza_testo(citazione.testo_esatto or "") if citazione else ""
    return (
        len(testo) >= MIN_CARATTERI_CITAZIONE_MODALITA
        and len(testo.split()) >= MIN_PAROLE_CITAZIONE_MODALITA
    )


def _modalita(estrazione: PartenariatoEstrazione, sezioni, documenti) -> ModalitaOut:
    citazione, verificata = _citazione(estrazione.modalita_citazione, sezioni, documenti)
    avvisi: list[str] = []
    dichiarata = estrazione.modalita
    if (
        dichiarata != "non_determinabile"
        and citazione is not None
        and not _citazione_probante(estrazione.modalita_citazione)
    ):
        avvisi.append("Il passaggio citato è troppo breve per confermare la modalità")
    if dichiarata == "non_ammesso" and estrazione.forme_ammesse:
        avvisi.append("«Non ammesso» contraddice le forme di aggregazione indicate")
    if dichiarata == "non_ammesso" and (estrazione.partner_min or 0) > 1:
        avvisi.append("«Non ammesso» contraddice il numero minimo di partner indicato")
    if dichiarata == "obbligatorio" and estrazione.partner_max == 1:
        avvisi.append("«Obbligatorio» contraddice un massimo di un solo soggetto")
    if dichiarata != "non_determinabile" and citazione is None:
        avvisi.append("Manca il passaggio del bando che lo stabilisce")
    effettiva = (
        dichiarata
        if dichiarata != "non_determinabile" and verificata and not avvisi
        else "non_determinabile"
    )
    return ModalitaOut(
        valore=dichiarata,
        effettiva=effettiva,
        stato=_stato(verificata, avvisi),
        citazione=citazione,
        avvisi=avvisi,
    )


def _conteggi(estrazione: PartenariatoEstrazione, sezioni, documenti):
    minimo, massimo = estrazione.partner_min, estrazione.partner_max
    avvisi_min: list[str] = []
    avvisi_max: list[str] = []
    if minimo is not None and minimo < 1:
        avvisi_min.append("Numero minimo di partner non plausibile")
    if massimo is not None and massimo < 1:
        avvisi_max.append("Numero massimo di partner non plausibile")
    if minimo is not None and massimo is not None and minimo > massimo:
        avvisi_min.append("Il minimo supera il massimo")
        avvisi_max.append("Il minimo supera il massimo")
    cit_min, ver_min = _citazione(estrazione.partner_min_citazione, sezioni, documenti)
    cit_max, ver_max = _citazione(estrazione.partner_max_citazione, sezioni, documenti)
    return (
        ConteggioOut(valore=minimo, stato=_stato(ver_min, avvisi_min), citazione=cit_min,
                     avvisi=avvisi_min),
        ConteggioOut(valore=massimo, stato=_stato(ver_max, avvisi_max), citazione=cit_max,
                     avvisi=avvisi_max),
    )


def _composizione(
    voce: ComposizioneVoce, sezioni, documenti, indice_regioni
) -> ComposizioneOut:
    citazione, verificata = _citazione(voce.citazione, sezioni, documenti)
    avvisi: list[str] = []
    if voce.minimo is not None and voce.minimo < 0:
        avvisi.append("Numero minimo non plausibile")
    if voce.massimo is not None and voce.massimo < 0:
        avvisi.append("Numero massimo non plausibile")
    if voce.minimo is not None and voce.massimo is not None and voce.minimo > voce.massimo:
        avvisi.append("Il minimo supera il massimo")
    testo_tipo = _testo(voce.tipo_soggetto_testo, 300)
    if voce.tipo_soggetto == "altro" and not testo_tipo:
        avvisi.append("Tipo di soggetto non specificato")
    regioni, regioni_ids, scartate = mappa_regioni(voce.regioni, indice_regioni)
    if voce.regioni and indice_regioni is None:
        avvisi.append("Regioni non verificabili sul catalogo")
    for nome in scartate:
        avvisi.append(f"Regione non riconosciuta: {nome}")
    tipo = TIPI_SOGGETTO.get(voce.tipo_soggetto)
    return ComposizioneOut(
        id=_testo(voce.id, 20) or "",
        tipo_soggetto=voce.tipo_soggetto,
        tipo_soggetto_etichetta=tipo.etichetta if tipo else voce.tipo_soggetto,
        tipo_soggetto_testo=testo_tipo,
        beneficiari=beneficiari_per_tipo(voce.tipo_soggetto),
        minimo=voce.minimo,
        massimo=voce.massimo,
        ruolo=voce.ruolo,
        regioni=regioni_ids,
        regioni_nomi=regioni,
        paesi=[p for p in (_testo(x, 80) for x in voce.paesi) if p],
        vincolo_territoriale=_testo(voce.vincolo_territoriale, 500),
        stato=_stato(verificata, avvisi),
        citazione=citazione,
        avvisi=avvisi,
    )


def _quota(voce, sezioni, documenti) -> QuotaOut:
    citazione, verificata = _citazione(voce.citazione, sezioni, documenti)
    avvisi: list[str] = []
    minimo, massimo = voce.min_percentuale, voce.max_percentuale
    for valore in (minimo, massimo):
        if valore is not None and (not _finito(valore) or not 0 <= valore <= 100):
            avvisi.append("Percentuale fuori dall'intervallo 0-100")
            break
    if _finito(minimo) and _finito(massimo) and minimo > massimo:
        avvisi.append("La percentuale minima supera la massima")
    if minimo is None and massimo is None:
        avvisi.append("Quota senza percentuali")
    if voce.ambito == "per_categoria" and voce.categoria is None:
        avvisi.append("Quota per categoria senza categoria")
    return QuotaOut(
        id=_testo(voce.id, 20) or "",
        ambito=voce.ambito,
        categoria=voce.categoria,
        min_percentuale=minimo if _finito(minimo) else None,
        max_percentuale=massimo if _finito(massimo) else None,
        base_calcolo=voce.base_calcolo,
        effetto_violazione=voce.effetto_violazione,
        stato=_stato(verificata, avvisi),
        citazione=citazione,
        avvisi=avvisi,
    )


def _vincolo(voce, sezioni, documenti) -> VincoloOut:
    citazione, verificata = _citazione(voce.citazione, sezioni, documenti)
    avvisi: list[str] = []
    parametro = voce.parametro
    if parametro is not None:
        if not _finito(parametro) or parametro < 0:
            avvisi.append("Parametro non plausibile")
        elif voce.tipo == "paesi_distinti" and parametro < 2:
            avvisi.append("Numero di paesi non plausibile")
    return VincoloOut(
        id=_testo(voce.id, 20) or "",
        tipo=voce.tipo,
        descrizione=_testo(voce.descrizione) or "",
        parametro=parametro if _finito(parametro) else None,
        momento=voce.momento,
        stato=_stato(verificata, avvisi),
        citazione=citazione,
        avvisi=avvisi,
    )


def _regola_finanziaria(voce, sezioni, documenti) -> RegolaFinanziariaOut:
    citazione, verificata = _citazione(voce.citazione, sezioni, documenti)
    campi = {nome: getattr(voce, nome) for nome in RegolaFinanziaria.model_fields}
    campi["id"] = _testo(campi["id"], 20) or ""
    campi["descrizione"] = _testo(campi["descrizione"]) or ""
    avvisi = [f"Regola non coerente: {errore}" for errore in valida_regola(RegolaFinanziaria(**campi))]
    return RegolaFinanziariaOut(
        **campi, stato=_stato(verificata, avvisi), citazione=citazione, avvisi=avvisi
    )


# ------------------------------------------------------------ ingresso


def post_elabora(
    estrazione: PartenariatoEstrazione | dict,
    sezioni: dict[str, str],
    fonti: Any,
    lookups: Any,
) -> dict:
    """Regole post-elaborate (forma `RegolePartenariatoOut`, JSON) dall'output
    del modello. `sezioni`: indice→testo ESATTAMENTE come inviato; `fonti`:
    le voci di `fonti_usate` (n, etichetta, url); `lookups`: le lookup del
    catalogo (serve `regioni`), None se non disponibili."""
    if not isinstance(estrazione, PartenariatoEstrazione):
        estrazione = PartenariatoEstrazione.model_validate(estrazione)
    documenti = _documenti(fonti)
    indice_regioni = _indice_regioni(lookups)

    modalita = _modalita(estrazione, sezioni, documenti)
    partner_min, partner_max = _conteggi(estrazione, sezioni, documenti)

    cit_cost, ver_cost = _citazione(estrazione.costituzione_citazione, sezioni, documenti)
    avvisi_cost: list[str] = []
    if estrazione.costituzione != "non_indicato" and cit_cost is None:
        avvisi_cost.append("Manca il passaggio del bando che lo stabilisce")
    costituzione = CostituzioneOut(
        valore=estrazione.costituzione,
        stato=_stato(ver_cost, avvisi_cost),
        citazione=cit_cost,
        avvisi=avvisi_cost,
    )

    forme: list[FormaAmmessaOut] = []
    for voce in estrazione.forme_ammesse:
        citazione, verificata = _citazione(voce.citazione, sezioni, documenti)
        note = _testo(voce.note, 500)
        avvisi = ["Forma non descritta"] if voce.forma == "altra" and not note else []
        forma = FORME.get(voce.forma)
        forme.append(
            FormaAmmessaOut(
                forma=voce.forma,
                etichetta=forma.etichetta if forma else voce.forma,
                note=note,
                stato=_stato(verificata, avvisi),
                citazione=citazione,
                avvisi=avvisi,
            )
        )

    documenti_richiesti: list[DocumentoRichiestoOut] = []
    for voce in estrazione.documenti_richiesti:
        citazione, verificata = _citazione(voce.citazione, sezioni, documenti)
        documenti_richiesti.append(
            DocumentoRichiestoOut(
                id=_testo(voce.id, 20) or "",
                tipo=voce.tipo,
                descrizione=_testo(voce.descrizione) or "",
                momento=voce.momento,
                stato=_stato(verificata, []),
                citazione=citazione,
            )
        )

    regole = RegolePartenariatoOut(
        modalita=modalita,
        modalita_effettiva=modalita.effettiva,
        forme_ammesse=forme,
        costituzione=costituzione,
        partner_min=partner_min,
        partner_max=partner_max,
        conteggio_note=_testo(estrazione.conteggio_note, 1000),
        composizione=[
            _composizione(voce, sezioni, documenti, indice_regioni)
            for voce in estrazione.composizione
        ],
        quote=[_quota(voce, sezioni, documenti) for voce in estrazione.quote],
        vincoli=[_vincolo(voce, sezioni, documenti) for voce in estrazione.vincoli],
        regole_finanziarie=[
            _regola_finanziaria(voce, sezioni, documenti)
            for voce in estrazione.regole_finanziarie
        ],
        documenti_richiesti=documenti_richiesti,
        fonti_insufficienti=bool(estrazione.fonti_insufficienti),
        note=_testo(estrazione.note),
        # Le incoerenze della modalità valgono per tutto il risultato.
        avvisi=[a for a in modalita.avvisi if a.startswith("«")],
    )
    # Ultima rete: nessun rimando ai domini esclusi, in nessun campo.
    return scrub_menzioni(regole.model_dump(mode="json"))
