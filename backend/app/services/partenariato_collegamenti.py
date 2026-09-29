"""Collegamenti societari tra aziende (WP6, docs/partenariati.md M2, Q17).

Due aziende collegate (stesso gruppo, partecipazioni dirette, soci o
esponenti in comune) non si suggeriscono l'una all'altra: nel matching sia un
collegamento `certo` sia uno `possibile` escludono (filtro `collegata`), nel
validatore del consorzio (WP8) `certo` → rosso e `possibile` → grigio.

Chiavi (`company_collegamenti`): mai un CF, una P.IVA o un nome in chiaro, ma
`hmac_dominio("partenariati.collegamenti.v1", "<prefisso>:<valore normalizzato>")`
(HMAC con chiave derivata dal pepper: uno sha256 semplice si invertirebbe per
enumerazione delle P.IVA). Il prefisso rende confrontabili i tipi che parlano
della stessa cosa: `cf:` per P.IVA e codici fiscali (identità, soci,
partecipate, controllate, esponenti), `nome:` per ragione sociale e
capogruppo (e per soci, partecipate e controllate senza codice fiscale),
`gruppo:` per il nome del gruppo. Fonti (`chiavi_collegamento`):
- `identita`: P.IVA e CF dell'azienda (`company_profiles` e registro);
- `nome`: ragione sociale e denominazione del registro senza forma legale;
- `socio` (con quota): soci di `company_people` (kind `shareholder`), in
  mancanza `raw.shareholders`; un socio persona fisica senza CF non conta;
- `partecipata` (con quota): `raw.affiliateCompanies`;
- `controllata`: `raw.subsidiaries` (quota se il registro la dà);
- `esponente`: amministratori con CF (`company_people` kind `manager`, in
  mancanza `raw.managers`); i sindaci no;
- `gruppo` e `capogruppo`: `raw.corporateGroups` solo se `belongsToGroup`
  (il registro non dà il CF della capogruppo: si confronta per nome).

Soglie (Q17, `valuta_collegamento`):
- CERTO: stessa identità; stesso gruppo; partecipazione diretta ≥ 25% in una
  delle due direzioni (partecipata, controllata o socio che è l'altra
  azienda); socio comune con quota > 50% in entrambe;
- POSSIBILE: partecipazione diretta a quota ignota (o controllata sotto il
  25%); socio comune con quota ≥ 25% (o ignota) in entrambe; esponente comune
  (o esponente che è l'altra azienda, per esempio una ditta individuale);
  capogruppo che ha il nome dell'altra azienda o della sua capogruppo.

Chi (`ricostruisci`, `rimuovi`, `backfill`): solo con il flag del modulo e
solo per le aziende IDONEE (vive, con opt-in visibile o con una call non
chiusa): per le altre le chiavi si cancellano. Il marker
`company_collegamenti_stato` (versione dell'algoritmo + `fetched_at` dei dati
del registro usati) dice se le chiavi sono aggiornate (`marker_aggiornato`,
che confronta anche le chiavi d'identità salvate con quelle che la riga
dell'azienda genera adesso: identità cambiata senza import o chiave HMAC
ruotata): se manca o è vecchio il matching tratta l'azienda come
«collegamenti non calcolati» ed esclude (fail-closed). Per questo `ricostruisci` toglie il
marker PER PRIMO e lo riscrive per ULTIMO: un ricalcolo interrotto lascia
l'azienda esclusa finché il backfill non passa, mai con chiavi a metà.

Log: solo id dell'azienda, conteggi e codici d'errore; mai CF, P.IVA, nomi.
"""

import logging
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.core.privacy import hmac_dominio
from app.services.partenariato_anonimato import nome_senza_forma_legale

logger = logging.getLogger("bandofit.partenariati")

ALGORITMO_VERSIONE = 1
DOMINIO_HMAC = "partenariati.collegamenti.v1"

TipoChiave = Literal[
    "identita", "nome", "socio", "partecipata", "controllata", "esponente", "gruppo",
    "capogruppo",
]
Grado = Literal["certo", "possibile"]
TIPI_CHIAVE: frozenset[str] = frozenset(
    {"identita", "nome", "socio", "partecipata", "controllata", "esponente", "gruppo",
     "capogruppo"}
)

# Q17. Partecipazione diretta: ≥ 25% → certo. Socio comune: > 50% in entrambe
# → certo, ≥ 25% (o ignota) in entrambe → possibile.
SOGLIA_PARTECIPAZIONE = Decimal(25)
SOGLIA_SOCIO_COMUNE_CERTO = Decimal(50)
SOGLIA_SOCIO_COMUNE_POSSIBILE = Decimal(25)

# Unico testo mostrabile (solo al titolare interessato): mai quale socio o
# esponente collega le due aziende, né il tipo di chiave.
TESTO_COLLEGAMENTO = "possibile collegamento societario: verifica con visura"

# Call che rendono un'azienda idonea anche senza opt-in (docs §2.6 M2).
STATI_CALL_NON_CHIUSE = ("bozza", "pubblicata", "sospesa_moderazione")

# Letture: pagine sotto il max-rows 1000 di PostgREST, `in_` a blocchi di 100
# id (URL corti).
PAGINA = 1000
BLOCCO_ID = 100

_CENTO = Decimal(100)
_ZERO = Decimal(0)
_MILLESIMI = Decimal("0.001")


@dataclass(frozen=True)
class ChiaveCollegamento:
    """Una riga di `company_collegamenti`: `chiave` è l'HMAC esadecimale (64
    caratteri), `quota` la percentuale per soci, partecipate e controllate
    (None = ignota o non pertinente)."""

    tipo: TipoChiave
    chiave: str
    quota: Decimal | None = None


# ------------------------------------------------------------ normalizzazione


def normalizza_cf(valore: Any) -> str | None:
    """P.IVA o codice fiscale in forma canonica: solo lettere e cifre
    maiuscole (anche da caratteri a larghezza piena), senza il prefisso `IT`
    della partita IVA comunitaria. None se non ha la forma di una P.IVA / CF
    di persona giuridica (11 cifre, non tutte zero) o di un CF di persona
    fisica (16 caratteri che iniziano con 6 lettere). Un intero (JSON senza
    virgolette) perde gli zeri iniziali: si ricompone a 11 cifre."""
    if isinstance(valore, bool):
        return None
    if isinstance(valore, int):
        testo = str(valore).zfill(11) if 0 < valore < 10**11 else ""
    elif isinstance(valore, str):
        testo = unicodedata.normalize("NFKC", valore)
    else:
        return None
    testo = "".join(ch for ch in testo if ch.isascii() and ch.isalnum()).upper()
    if len(testo) == 13 and testo.startswith("IT") and testo[2:].isdigit():
        testo = testo[2:]
    if len(testo) == 11 and testo.isdigit():
        return None if testo == "0" * 11 else testo
    if len(testo) == 16 and testo[:6].isalpha():
        return testo
    return None


def normalizza_nome(valore: Any) -> str | None:
    """Ragione sociale (o nome di gruppo) confrontabile: maiuscole, senza
    accenti né punteggiatura, senza forme legali (SRL, SPA, SRLS, SAS, SNC,
    SCARL, SOC COOP, «& C.», «in liquidazione», …) come fa l'anonimato
    (`nome_senza_forma_legale`). None se ciò che resta è troppo corto o solo
    numerico: un nome così collegherebbe aziende a caso."""
    if not isinstance(valore, str):
        return None
    nome = nome_senza_forma_legale(unicodedata.normalize("NFKC", valore))
    return nome.upper() if nome else None


def _quota(valore: Any) -> Decimal | None:
    """Percentuale 0..100 con al più 3 decimali (numeric(6,3)); None se
    assente o non plausibile (una quota fuori scala è ignota, non zero)."""
    if valore is None or isinstance(valore, bool):
        return None
    try:
        numero = Decimal(valore.strip().replace(",", ".") if isinstance(valore, str)
                         else str(valore))
    except (InvalidOperation, ValueError):
        return None
    if not numero.is_finite() or numero < 0 or numero > _CENTO:
        return None
    return numero.quantize(_MILLESIMI)


def _vero(valore: Any) -> bool:
    return valore is True or (isinstance(valore, str) and valore.strip().lower() == "true")


def _hmac(prefisso: str, valore: str) -> str:
    return hmac_dominio(DOMINIO_HMAC, f"{prefisso}:{valore}")


# ------------------------------------------------------------------- chiavi


def _campo(riga: Any, *percorso: str) -> Any:
    corrente = riga
    for chiave in percorso:
        if not isinstance(corrente, Mapping):
            return None
        corrente = corrente.get(chiave)
    return corrente


def _lista(valore: Any) -> list[Mapping]:
    if not isinstance(valore, (list, tuple)):
        return []
    return [v for v in valore if isinstance(v, Mapping)]


def _chiave_soggetto(cf: Any, nome: Any) -> str | None:
    """Chiave di un soggetto terzo (socio, partecipata, controllata): per CF
    se c'è, altrimenti per nome (solo per le imprese: il nome di una persona
    fisica è troppo ambiguo, il chiamante non lo passa)."""
    codice = normalizza_cf(cf)
    if codice:
        return _hmac("cf", codice)
    nome_norm = normalizza_nome(nome)
    return _hmac("nome", nome_norm) if nome_norm else None


class _Raccolta:
    """Chiavi per (tipo, chiave) con le quote di ogni occorrenza."""

    def __init__(self) -> None:
        self.quote: dict[tuple[str, str], list[Decimal | None]] = {}

    def aggiungi(self, tipo: str, chiave: str | None, quota: Decimal | None = None) -> None:
        if chiave:
            self.quote.setdefault((tipo, chiave), []).append(quota)

    def chiavi(self) -> list[ChiaveCollegamento]:
        out = [
            ChiaveCollegamento(tipo, chiave, _quota_totale(tipo, quote))  # type: ignore[arg-type]
            for (tipo, chiave), quote in self.quote.items()
        ]
        return sorted(out, key=lambda c: (c.tipo, c.chiave))


_TIPI_CON_QUOTA = frozenset({"socio", "partecipata", "controllata"})


def _quota_totale(tipo: str, quote: list[Decimal | None]) -> Decimal | None:
    """Più righe per lo stesso soggetto (quote di categorie diverse): si
    sommano, al massimo 100. Con una parte ignota la somma nota è solo un
    minimo: vale se decide già la soglia della partecipazione (≥ 25%),
    altrimenti la quota è ignota (→ al più «possibile»)."""
    if tipo not in _TIPI_CON_QUOTA:
        return None
    note = [q for q in quote if q is not None]
    totale = min(_CENTO, sum(note, _ZERO))
    if len(note) < len(quote) and totale < SOGLIA_PARTECIPAZIONE:
        return None
    return totale.quantize(_MILLESIMI)


def _persone(people: Iterable[Any] | None, kind: str) -> list[Mapping]:
    return [p for p in people or () if isinstance(p, Mapping) and p.get("kind") == kind]


def chiavi_collegamento(
    company_row: Mapping | None, raw: Mapping | None, people: Iterable[Any] | None
) -> list[ChiaveCollegamento]:
    """Chiavi (già HMAC) di un'azienda, ordinate per (tipo, chiave).

    - `company_row`: riga di `company_profiles` (`partita_iva`,
      `codice_fiscale`, `ragione_sociale`);
    - `raw`: `company_data.raw` (IT-full), può mancare;
    - `people`: righe di `company_people` (`kind`, `codice_fiscale`,
      `denominazione`, `quota_percentuale`). Sono estratte dallo stesso
      `raw` all'import: per soci ed esponenti valgono loro, e `raw` solo se
      per quel tipo non ce n'è nessuna (mai entrambi, o le quote si
      sommerebbero due volte)."""
    company_row = company_row or {}
    raw = raw if isinstance(raw, Mapping) else {}
    r = _Raccolta()

    for valore in (
        company_row.get("partita_iva"),
        company_row.get("codice_fiscale"),
        _campo(raw, "companyDetails", "vatCode"),
        _campo(raw, "companyDetails", "taxCode"),
    ):
        codice = normalizza_cf(valore)
        r.aggiungi("identita", _hmac("cf", codice) if codice else None)
    for valore in (company_row.get("ragione_sociale"), _campo(raw, "companyDetails", "companyName")):
        nome = normalizza_nome(valore)
        r.aggiungi("nome", _hmac("nome", nome) if nome else None)

    soci = _persone(people, "shareholder")
    if soci:
        for socio in soci:
            nome = socio.get("denominazione")  # solo i soci persona giuridica
            r.aggiungi("socio", _chiave_soggetto(socio.get("codice_fiscale"), nome),
                       _quota(socio.get("quota_percentuale")))
    else:
        for socio in _lista(raw.get("shareholders")):
            r.aggiungi("socio", _chiave_soggetto(socio.get("taxCode"), socio.get("companyName")),
                       _quota(socio.get("percentShare")))

    for voce in _lista(raw.get("affiliateCompanies")):
        r.aggiungi("partecipata", _chiave_soggetto(voce.get("taxCode"), voce.get("companyName")),
                   _quota(voce.get("percentShare")))
    for voce in _lista(raw.get("subsidiaries")):
        r.aggiungi("controllata", _chiave_soggetto(voce.get("taxCode"), voce.get("companyName")),
                   _quota(voce.get("percentShare")))

    esponenti = _persone(people, "manager")
    codici_esponenti = (
        [p.get("codice_fiscale") for p in esponenti]
        if esponenti
        else [m.get("taxCode") for m in _lista(raw.get("managers"))]
    )
    for valore in codici_esponenti:
        codice = normalizza_cf(valore)
        r.aggiungi("esponente", _hmac("cf", codice) if codice else None)

    gruppi = raw.get("corporateGroups")
    if isinstance(gruppi, Mapping) and _vero(gruppi.get("belongsToGroup")):
        gruppo = normalizza_nome(gruppi.get("groupName"))
        r.aggiungi("gruppo", _hmac("gruppo", gruppo) if gruppo else None)
        for valore in (
            gruppi.get("holdingCompanyName"),
            _campo(gruppi, "nationalParentCompany", "companyName"),
        ):
            capogruppo = normalizza_nome(valore)
            # stesso prefisso di `nome`: si confronta con la ragione sociale altrui
            r.aggiungi("capogruppo", _hmac("nome", capogruppo) if capogruppo else None)
    return r.chiavi()


def chiavi_da_righe(righe: Iterable[Any]) -> tuple[ChiaveCollegamento, ...]:
    """Le chiavi lette da `company_collegamenti` (per l'indice del matching).
    Righe con tipo sconosciuto o chiave malformata si scartano."""
    out: list[ChiaveCollegamento] = []
    for riga in righe or ():
        if not isinstance(riga, Mapping):
            continue
        tipo, chiave = riga.get("tipo"), riga.get("chiave")
        if tipo not in TIPI_CHIAVE or not isinstance(chiave, str) or len(chiave) != 64:
            continue
        out.append(ChiaveCollegamento(tipo, chiave, _quota(riga.get("quota"))))
    return tuple(out)


# -------------------------------------------------------------- valutazione


class _Indice:
    def __init__(self, chiavi: Iterable[ChiaveCollegamento]) -> None:
        self.per_tipo: dict[str, dict[str, Decimal | None]] = {}
        for c in chiavi:
            self.per_tipo.setdefault(c.tipo, {})[c.chiave] = c.quota

    def chiavi(self, *tipi: str) -> set[str]:
        return {k for tipo in tipi for k in self.per_tipo.get(tipo, {})}

    def voci(self, tipo: str) -> Iterator[tuple[str, Decimal | None]]:
        return iter(self.per_tipo.get(tipo, {}).items())

    def quota(self, tipo: str, chiave: str) -> Decimal | None:
        return self.per_tipo.get(tipo, {}).get(chiave)


def _grado_diretto(quota: Decimal | None, *, controllo: bool) -> Grado | None:
    if quota is None:
        return "possibile"
    if quota >= SOGLIA_PARTECIPAZIONE:
        return "certo"
    # una controllata dichiarata dal registro resta un indizio anche sotto soglia
    return "possibile" if controllo else None


def _diretti(da: _Indice, verso: _Indice) -> Iterator[Grado | None]:
    """`da` partecipa a `verso`: tra le partecipate o le controllate di `da`
    c'è `verso` (per CF o per nome), oppure `da` è tra i soci di `verso`."""
    bersagli_verso = verso.chiavi("identita", "nome")
    for tipo in ("partecipata", "controllata"):
        for chiave, quota in da.voci(tipo):
            if chiave in bersagli_verso:
                yield _grado_diretto(quota, controllo=tipo == "controllata")
    bersagli_da = da.chiavi("identita", "nome")
    for chiave, quota in verso.voci("socio"):
        if chiave in bersagli_da:
            yield _grado_diretto(quota, controllo=False)


def _rilevante(quota: Decimal | None) -> bool:
    return quota is None or quota >= SOGLIA_SOCIO_COMUNE_POSSIBILE


def _soci_comuni(a: _Indice, b: _Indice) -> Iterator[Grado | None]:
    for chiave in a.chiavi("socio") & b.chiavi("socio"):
        qa, qb = a.quota("socio", chiave), b.quota("socio", chiave)
        if (
            qa is not None and qb is not None
            and qa > SOGLIA_SOCIO_COMUNE_CERTO and qb > SOGLIA_SOCIO_COMUNE_CERTO
        ):
            yield "certo"
        elif _rilevante(qa) and _rilevante(qb):
            yield "possibile"


def valuta_collegamento(
    a: Iterable[ChiaveCollegamento], b: Iterable[ChiaveCollegamento]
) -> Grado | None:
    """Grado del collegamento tra due aziende (soglie Q17, docstring del
    modulo). Simmetrica. None = nessun collegamento noto."""
    ia, ib = _Indice(a), _Indice(b)
    gradi: list[Grado | None] = []
    if ia.chiavi("identita") & ib.chiavi("identita"):
        gradi.append("certo")
    if ia.chiavi("gruppo") & ib.chiavi("gruppo"):
        gradi.append("certo")
    gradi.extend(_diretti(ia, ib))
    gradi.extend(_diretti(ib, ia))
    gradi.extend(_soci_comuni(ia, ib))
    if "certo" in gradi:
        return "certo"
    if (
        ia.chiavi("esponente") & ib.chiavi("esponente", "identita")
        or ib.chiavi("esponente") & ia.chiavi("identita")
        or ia.chiavi("capogruppo") & ib.chiavi("nome", "capogruppo")
        or ib.chiavi("capogruppo") & ia.chiavi("nome")
    ):
        gradi.append("possibile")
    return "possibile" if "possibile" in gradi else None


def collegamenti_tra(
    chiavi_per_azienda: Mapping[str, Iterable[ChiaveCollegamento]],
) -> dict[str, dict[str, Grado]]:
    """Tutte le coppie collegate tra le aziende date (per l'indice del
    matching): `{azienda: {altra: grado}}`, simmetrico, solo le coppie con un
    grado. Ogni regola di `valuta_collegamento` richiede una chiave uguale
    nelle due aziende, quindi si confrontano solo le coppie che ne
    condividono almeno una (indice inverso per chiave), non tutte le N²."""
    chiavi = {str(cid): tuple(voci) for cid, voci in chiavi_per_azienda.items()}
    per_chiave: dict[str, set[str]] = {}
    for cid, voci in chiavi.items():
        for voce in voci:
            per_chiave.setdefault(voce.chiave, set()).add(cid)
    out: dict[str, dict[str, Grado]] = {}
    for cid, voci in chiavi.items():
        vicine = set().union(*(per_chiave[v.chiave] for v in voci)) if voci else set()
        for altra in sorted(vicine):
            if altra <= cid:
                continue
            grado = valuta_collegamento(voci, chiavi[altra])
            if grado:
                out.setdefault(cid, {})[altra] = grado
                out.setdefault(altra, {})[cid] = grado
    return out


# ------------------------------------------------------------------- marker


def _ts(valore: Any) -> datetime | None:
    if isinstance(valore, datetime):
        return valore if valore.tzinfo else valore.replace(tzinfo=timezone.utc)
    if isinstance(valore, str) and valore.strip():
        try:
            letto = datetime.fromisoformat(valore.strip())
        except ValueError:
            return None
        return letto if letto.tzinfo else letto.replace(tzinfo=timezone.utc)
    return None


def chiavi_attese(azienda: Mapping | None) -> frozenset[tuple[str, str]]:
    """Le chiavi `(tipo, chiave)` che i soli campi di `company_profiles`
    presenti in `azienda` (P.IVA, codice fiscale, ragione sociale) generano
    ADESSO, con la chiave HMAC corrente."""
    return frozenset((c.tipo, c.chiave) for c in chiavi_collegamento(azienda, None, None))


def marker_aggiornato(
    marker: Mapping | None,
    fonte_fetched_at: Any,
    *,
    azienda: Mapping | None = None,
    chiavi: Iterable[ChiaveCollegamento] = (),
) -> bool:
    """True se le chiavi dell'azienda sono calcolate con l'algoritmo corrente
    e sui dati del registro correnti (`fonte_fetched_at` =
    `company_data.fetched_at`, None se l'azienda non ha un import). False →
    `collegamenti_non_calcolati` (fail-closed): nessun marker, un'altra
    versione dell'algoritmo (anche più nuova: dopo un rollback si ricalcola),
    un import più recente del calcolo o un `fetched_at` illeggibile.

    Con `azienda` (riga di `company_profiles`) e `chiavi` (quelle salvate):
    anche False se le chiavi che quella riga genera adesso non sono tra
    quelle salvate. Copre ciò che il `fetched_at` non vede: P.IVA, codice
    fiscale o ragione sociale cambiati dalla scheda dell'azienda senza un
    nuovo import, e una rotazione della chiave HMAC (le chiavi salvate non
    corrispondono più): in entrambi i casi il backfill ricalcola."""
    if not isinstance(marker, Mapping):
        return False
    try:
        versione = int(marker.get("algoritmo_versione"))
    except (TypeError, ValueError):
        return False
    if versione != ALGORITMO_VERSIONE:
        return False
    if fonte_fetched_at is not None:
        fonte = _ts(fonte_fetched_at)
        usato = _ts(marker.get("fonte_fetched_at"))
        if fonte is None or usato is None or usato < fonte:
            return False
    if azienda is not None:
        salvate = {(c.tipo, c.chiave) for c in chiavi}
        if not chiavi_attese(azienda) <= salvate:
            return False
    return True


# ---------------------------------------------------------------------- I/O


def _attivo() -> bool:
    return bool(get_settings().partenariati_attivo)


def _blocchi(ids: Sequence[str]) -> Iterator[list[str]]:
    for inizio in range(0, len(ids), BLOCCO_ID):
        yield list(ids[inizio : inizio + BLOCCO_ID])


async def _tutte(costruisci, chiave: str) -> list[dict]:
    """Tutte le righe a keyset su `chiave`, a pagine di PAGINA."""
    righe: list[dict] = []
    ultimo: Any = None
    while True:
        query = costruisci()
        if ultimo is not None:
            query = query.gt(chiave, ultimo)
        resp = await query.order(chiave).limit(PAGINA).execute()
        dati = [r for r in resp.data or [] if isinstance(r, dict)]
        righe.extend(dati)
        if len(resp.data or []) < PAGINA or not dati:
            return righe
        ultimo = dati[-1][chiave]


async def _vive(primary, ids: Sequence[str]) -> set[str]:
    vive: set[str] = set()
    for blocco in _blocchi(ids):
        resp = (
            await primary.table("company_profiles")
            .select("id,deleted_at,archived_at")
            .in_("id", blocco)
            .execute()
        )
        for riga in resp.data or []:
            if not riga.get("deleted_at") and not riga.get("archived_at"):
                vive.add(str(riga["id"]))
    return vive


async def idonee(primary, company_ids: Iterable[str]) -> set[str]:
    """Tra `company_ids`, le aziende vive con opt-in visibile o con una call
    non chiusa: le sole per cui si calcolano i collegamenti."""
    ids = sorted({str(c) for c in company_ids})
    candidate: set[str] = set()
    for blocco in _blocchi(ids):
        profili = (
            await primary.table("company_partner_profiles")
            .select("company_profile_id")
            .in_("company_profile_id", blocco)
            .eq("visibile_come_partner", True)
            .execute()
        )
        call = (
            await primary.table("partner_calls")
            .select("company_profile_id")
            .in_("company_profile_id", blocco)
            .in_("stato", list(STATI_CALL_NON_CHIUSE))
            .execute()
        )
        candidate.update(str(r["company_profile_id"]) for r in profili.data or [])
        candidate.update(str(r["company_profile_id"]) for r in call.data or [])
    return await _vive(primary, sorted(candidate)) if candidate else set()


async def _idonee_tutte(primary) -> set[str]:
    profili = await _tutte(
        lambda: primary.table("company_partner_profiles")
        .select("company_profile_id")
        .eq("visibile_come_partner", True),
        "company_profile_id",
    )
    call = await _tutte(
        lambda: primary.table("partner_calls")
        .select("id,company_profile_id")
        .in_("stato", list(STATI_CALL_NON_CHIUSE)),
        "id",
    )
    candidate = {str(r["company_profile_id"]) for r in profili + call}
    return await _vive(primary, sorted(candidate)) if candidate else set()


async def _una(primary, tabella: str, colonne: str, colonna_id: str, company_id: str):
    resp = (
        await primary.table(tabella).select(colonne).eq(colonna_id, company_id).limit(1).execute()
    )
    return resp.data[0] if resp.data else None


async def rimuovi(primary, company_id: str) -> None:
    """Cancella chiavi e marker di un'azienda (revoca dell'opt-in senza call
    non chiuse, azienda non più idonea). Prima le chiavi, poi il marker: un
    marker rimasto da solo riguarda un'azienda che il matching non vede e il
    backfill lo ripulisce. Solleva sugli errori (il chiamante è
    best-effort)."""
    cid = str(company_id)
    await primary.table("company_collegamenti").delete().eq("company_profile_id", cid).execute()
    await primary.table("company_collegamenti_stato").delete().eq(
        "company_profile_id", cid
    ).execute()


async def ricostruisci(primary, company_id: str) -> bool:
    """Ricalcola le chiavi di UN'azienda (delete + insert) e riscrive il
    marker. Solo con il flag acceso (spento: nessuna lettura né scrittura →
    False) e solo per un'azienda idonea: se non lo è, le chiavi esistenti si
    cancellano (`rimuovi`) → False. True = chiavi e marker scritti.

    Ordine: marker tolto, chiavi tolte, chiavi nuove, marker nuovo con il
    `fetched_at` dei dati letti. Un errore a metà lascia l'azienda senza
    marker (esclusa dal matching) finché il backfill non la ricalcola.
    Solleva sugli errori di I/O: il chiamante (import, consenso, scheduler)
    è best-effort e logga solo l'id. Mai CF o P.IVA nelle scritture: solo
    HMAC."""
    if not _attivo():
        return False
    cid = str(company_id)
    if cid not in await idonee(primary, [cid]):
        await rimuovi(primary, cid)
        return False
    company = await _una(
        primary, "company_profiles", "id,partita_iva,codice_fiscale,ragione_sociale", "id", cid
    )
    if company is None:  # cancellata tra le due letture
        await rimuovi(primary, cid)
        return False
    dati = await _una(primary, "company_data", "raw,fetched_at", "company_profile_id", cid)
    resp = (
        await primary.table("company_people")
        .select("kind,codice_fiscale,denominazione,quota_percentuale")
        .eq("company_profile_id", cid)
        .execute()
    )
    chiavi = chiavi_collegamento(company, (dati or {}).get("raw"), resp.data or [])

    await primary.table("company_collegamenti_stato").delete().eq(
        "company_profile_id", cid
    ).execute()
    await primary.table("company_collegamenti").delete().eq("company_profile_id", cid).execute()
    if chiavi:
        await primary.table("company_collegamenti").insert(
            [
                {
                    "company_profile_id": cid,
                    "tipo": c.tipo,
                    "chiave": c.chiave,
                    "quota": None if c.quota is None else str(c.quota),
                }
                for c in chiavi
            ]
        ).execute()
    await primary.table("company_collegamenti_stato").upsert(
        {
            "company_profile_id": cid,
            "algoritmo_versione": ALGORITMO_VERSIONE,
            "fonte_fetched_at": (dati or {}).get("fetched_at"),
            "calcolato_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="company_profile_id",
    ).execute()
    logger.info("partenariati: collegamenti ricalcolati (azienda %s, %d chiavi)", cid, len(chiavi))
    return True


def _codice_errore(exc: Exception) -> str:
    """Solo il codice: il detail di PostgREST può riportare la riga."""
    return str(exc.code) if isinstance(exc, APIError) else type(exc).__name__


async def backfill(primary, *, limite: int = 200) -> dict:
    """Passo dello scheduler (`backfill_collegamenti`), solo con il flag:
    - ricalcola al più `limite` aziende idonee senza marker, con un'altra
      versione dell'algoritmo, con un import più recente del calcolo o con
      chiavi d'identità diverse da quelle che la riga dell'azienda genera
      adesso (`marker_aggiornato` con `azienda`);
    - ripulisce al più `limite` aziende con marker ma non più idonee.
    Ogni azienda è isolata: un errore si conta e si logga con il solo id.
    → `{"ricalcolate": n, "rimosse": n, "errori": n}`."""
    esito = {"ricalcolate": 0, "rimosse": 0, "errori": 0}
    if not _attivo():
        return esito
    ammesse = await _idonee_tutte(primary)
    marker = {
        str(r["company_profile_id"]): r
        for r in await _tutte(
            lambda: primary.table("company_collegamenti_stato").select(
                "company_profile_id,algoritmo_versione,fonte_fetched_at"
            ),
            "company_profile_id",
        )
    }
    fetched: dict[str, Any] = {}
    for blocco in _blocchi(sorted(ammesse)):
        resp = (
            await primary.table("company_data")
            .select("company_profile_id,fetched_at")
            .in_("company_profile_id", blocco)
            .execute()
        )
        fetched.update({str(r["company_profile_id"]): r.get("fetched_at") for r in resp.data or []})

    # Marker validi per versione e import: si confrontano anche le chiavi
    # d'identità salvate con quelle che la riga dell'azienda genera adesso
    # (P.IVA, CF o ragione sociale cambiati senza import; chiave HMAC ruotata).
    da_verificare = sorted(
        cid for cid in ammesse if marker_aggiornato(marker.get(cid), fetched.get(cid))
    )
    identita: dict[str, dict] = {}
    salvate: dict[str, list[dict]] = {}
    for blocco in _blocchi(da_verificare):
        aziende = (
            await primary.table("company_profiles")
            .select("id,partita_iva,codice_fiscale,ragione_sociale")
            .in_("id", blocco)
            .execute()
        )
        identita.update({str(r["id"]): r for r in aziende.data or []})
        righe = (
            await primary.table("company_collegamenti")
            .select("company_profile_id,tipo,chiave")
            .in_("company_profile_id", blocco)
            .in_("tipo", ["identita", "nome"])
            .execute()
        )
        for riga in righe.data or []:
            salvate.setdefault(str(riga["company_profile_id"]), []).append(riga)
    verificate = set(da_verificare)

    def aggiornata(cid: str) -> bool:
        return cid in verificate and marker_aggiornato(
            marker.get(cid), fetched.get(cid), azienda=identita.get(cid),
            chiavi=chiavi_da_righe(salvate.get(cid, ())),
        )

    da_rimuovere = sorted(set(marker) - ammesse)[: max(0, limite)]
    da_ricalcolare = sorted(cid for cid in ammesse if not aggiornata(cid))[: max(0, limite)]

    for cid in da_rimuovere:
        try:
            await rimuovi(primary, cid)
            esito["rimosse"] += 1
        except Exception as exc:  # passo isolato per azienda
            esito["errori"] += 1
            logger.error("partenariati: rimozione collegamenti non riuscita (azienda %s, %s)",
                         cid, _codice_errore(exc))
    for cid in da_ricalcolare:
        try:
            if await ricostruisci(primary, cid):
                esito["ricalcolate"] += 1
        except Exception as exc:  # passo isolato per azienda
            esito["errori"] += 1
            logger.error("partenariati: ricalcolo collegamenti non riuscito (azienda %s, %s)",
                         cid, _codice_errore(exc))
    return esito
