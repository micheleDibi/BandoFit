"""Admin del modulo partenariati (WP9, docs/partenariati.md W3; verifica
dell'identità da parte dell'admin, decisione di Michele del 2026-09-29).

- `lista_call`: le call di tutte le aziende con filtri per stato e testo
  (titolo della call o del bando, denominazione del Registro Imprese
  dell'azienda creatrice, oppure l'id esatto), con creatore,
  conteggi di candidature e inviti, esito dell'ultima validazione e
  segnalazioni aperte. Letture a blocchi e a keyset (sotto il max-rows 1000).
- `metriche` e `costi`: le RPC `fn_admin_metriche_partenariati` e
  `fn_admin_costi_partenariati` della 0041, aggregate in SQL (niente
  max-rows); i costi per provider, servizio, esito e VALUTA (EUR openapi,
  USD Anthropic), mai sommati tra valute. Periodo di default: gli ultimi 30
  giorni (Europe/Rome), al massimo 3660 giorni.
- verifica dell'identità: coda delle richieste con i dati del Registro
  Imprese che servono all'admin per verificare (recapiti della sede dal
  registro, mai quelli scritti dall'utente), decisione (`verificata` con il
  metodo obbligatorio, `rifiutata`) e revoca con motivo, tutte via RPC
  (`fn_identita_decidi`, `fn_identita_revoca`), notifica in-app al titolare
  sull'esito. Si verifica solo con i dati del registro coerenti (T5, e in
  produzione non di sandbox): stessa regola di `fn_partenariato_identita_ok`.

Chi agisce è sempre un AdminUser (lo ricontrollano le RPC:
`admin_non_autorizzato`). Log: solo id e codici.
"""

import logging
import re
import uuid
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError, UpstreamError
from app.schemas.common import Page
from app.schemas.partenariato_admin import (
    BandoCallAdminOut,
    CallAdminOut,
    CostiOut,
    CreatoreCallAdminOut,
    IdentitaAdminOut,
    IdentitaDecisioneIn,
    IdentitaEsitoOut,
    IdentitaRevocaIn,
    MetricheOut,
    RegistroIdentitaOut,
    TitolareIdentitaOut,
)
from app.services import partenariato_indice
from app.services import partenariato_moderazione_testi as testi
from app.services.notification_service import notify
from app.services.partenariato_errori import RPC_ERRORS, raise_from_rpc
from app.services.partner_profile_service import richiedi_non_sandbox

logger = logging.getLogger("bandofit.partenariati")

CALL_ADMIN_SELECT = (
    "id,company_profile_id,titolo,stato,stato_prima_sospensione,visibilita,anonima,bando_id,"
    "bando_slug,bando_titolo,pubblicata_at,scadenza_call,sospesa_at,sospeso_motivo,"
    "validazione_esito,created_at"
)
IDENTITA_SELECT = (
    "company_profile_id,stato,metodo,verificata_at,richiesta_at,aggiornato_at"
)
# Dal payload del registro solo i recapiti della sede e l'anagrafica che
# servono alla verifica (mai il raw intero).
REGISTRO_SELECT = (
    "company_profile_id,piva_fetched,denominazione,stato_impresa,sandbox,"
    "pec:raw->>pec,telefono:raw->contacts->>telephoneNumber,comune:raw->address->>town,"
    "provincia:raw->address->province->>code"
)
STATI_SEGNALAZIONE_APERTI: tuple[str, ...] = ("ricevuta", "in_esame", "ricorso_presentato")
PERIODO_DEFAULT_GIORNI = 30
PERIODO_MAX_GIORNI = 3660
Q_MAX = 100
_BLOCCO = 100
_PAGINA = 1000

MSG_AZIENDA_NON_TROVATA = "Azienda non trovata"
MSG_REGISTRO_INCOERENTE = (
    "I dati del Registro Imprese di questa azienda non sono coerenti (partita IVA diversa, "
    "impresa non attiva, dati non importati o di prova): non si può verificare l'identità"
)
# Detail con un messaggio per l'utente finale nella mappa comune: qui parla
# all'admin (status e code restano quelli della mappa).
_ERRORI_ADMIN: dict[str, tuple[int, str, str]] = {
    "identita_non_verificata": (409, "identita_non_verificata", MSG_REGISTRO_INCOERENTE),
    "company_not_found": (404, "not_found", MSG_AZIENDA_NON_TROVATA),
}


# ------------------------------------------------------------------ utilità


def _errore_rpc(exc: APIError):
    detail = (exc.details or "").strip()
    if detail in _ERRORI_ADMIN:
        raise AppError(*_ERRORI_ADMIN[detail]) from exc
    raise_from_rpc(exc)


async def _rpc(primary, nome: str, parametri: dict) -> Any:
    try:
        resp = await primary.rpc(nome, parametri).execute()
    except APIError as exc:
        _errore_rpc(exc)
    return resp.data


def _blocchi(ids: list[str]) -> Iterable[list[str]]:
    for inizio in range(0, len(ids), _BLOCCO):
        yield ids[inizio : inizio + _BLOCCO]


async def _tutte(costruisci, chiave: str = "id") -> list[dict]:
    """Righe a keyset su `chiave` (pagine sotto il max-rows 1000)."""
    righe: list[dict] = []
    ultimo: Any = None
    while True:
        query = costruisci()
        if ultimo is not None:
            query = query.gt(chiave, ultimo)
        resp = await query.order(chiave).limit(_PAGINA).execute()
        pagina = [r for r in resp.data or [] if isinstance(r, dict)]
        righe.extend(pagina)
        if len(pagina) < _PAGINA:
            return righe
        ultimo = pagina[-1][chiave]


def _uuid(valore: Any, messaggio: str) -> str:
    try:
        return str(uuid.UUID(str(valore).strip()))
    except (ValueError, AttributeError, TypeError):
        raise NotFoundError(messaggio) from None


def pulisci_ricerca(q: str | None) -> str | None:
    """Testo di ricerca sicuro per un filtro `or` di PostgREST: senza i
    caratteri della sua sintassi (virgole, parentesi, asterischi, virgolette,
    due punti, barre) e al massimo 100 caratteri."""
    if not q:
        return None
    testo = re.sub(r"[,()*%\\\"':]", " ", q)
    testo = re.sub(r"\s+", " ", testo).strip()[:Q_MAX].strip()
    return testo or None


# ------------------------------------------------------------------ call


async def _conteggi_candidature(primary, call_ids: list[str]) -> dict[str, dict[str, int]]:
    uscita: dict[str, dict[str, int]] = {cid: {"candidatura": 0, "invito": 0}
                                         for cid in call_ids}
    for blocco in _blocchi(call_ids):
        righe = await _tutte(lambda b=blocco: primary.table("partner_candidature")
                             .select("id,partner_call_id,tipo").in_("partner_call_id", b))
        for riga in righe:
            conteggio = uscita.get(str(riga.get("partner_call_id")))
            if conteggio is not None and riga.get("tipo") in conteggio:
                conteggio[riga["tipo"]] += 1
    return uscita


async def _membri_attivi(primary, call_ids: list[str]) -> dict[str, int]:
    uscita = dict.fromkeys(call_ids, 0)
    for blocco in _blocchi(call_ids):
        righe = await _tutte(lambda b=blocco: primary.table("partner_call_membri")
                             .select("id,partner_call_id,stato").in_("partner_call_id", b))
        for riga in righe:
            chiave = str(riga.get("partner_call_id"))
            if chiave in uscita and riga.get("stato") != "uscito":
                uscita[chiave] += 1
    return uscita


async def _segnalazioni_aperte(primary, call_ids: list[str]) -> dict[str, int]:
    uscita = dict.fromkeys(call_ids, 0)
    for blocco in _blocchi(call_ids):
        righe = await _tutte(lambda b=blocco: primary.table("partner_segnalazioni")
                             .select("id,oggetto_id").eq("oggetto_tipo", "call")
                             .in_("oggetto_id", b)
                             .in_("stato", list(STATI_SEGNALAZIONE_APERTI)))
        for riga in righe:
            chiave = str(riga.get("oggetto_id"))
            if chiave in uscita:
                uscita[chiave] += 1
    return uscita


async def _ragioni_sociali(primary, company_ids: Iterable[Any]) -> dict[str, str | None]:
    ids = sorted({str(c) for c in company_ids if c})
    uscita: dict[str, str | None] = {}
    for blocco in _blocchi(ids):
        resp = await (primary.table("company_profiles").select("id,ragione_sociale")
                      .in_("id", blocco).execute())
        uscita.update({str(r["id"]): r.get("ragione_sociale") for r in resp.data or []})
    return uscita


async def _creatori_per_denominazione(primary, testo: str, stato: str | None) -> list[str]:
    """Le aziende che hanno creato call (nello `stato`, se indicato) e la cui
    denominazione del Registro Imprese (`company_data.denominazione`) contiene
    `testo`, senza distinguere le maiuscole. I creatori si leggono a keyset
    sulle call, le denominazioni a blocchi di 100 aziende (una riga per
    azienda): ogni lettura resta sotto il max-rows 1000."""
    def call_creatori():
        query = primary.table("partner_calls").select("id,company_profile_id")
        return query.eq("stato", stato) if stato else query

    creatori = sorted({str(r["company_profile_id"]) for r in await _tutte(call_creatori)
                       if r.get("company_profile_id")})
    trovati: set[str] = set()
    for blocco in _blocchi(creatori):
        resp = await (primary.table("company_data").select("company_profile_id")
                      .in_("company_profile_id", blocco)
                      .ilike("denominazione", f"*{testo}*").execute())
        trovati |= {str(r["company_profile_id"]) for r in resp.data or []
                    if isinstance(r, dict) and r.get("company_profile_id")}
    return sorted(trovati)


def _istante(valore: Any) -> datetime:
    try:
        istante = datetime.fromisoformat(str(valore).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=ZoneInfo("UTC"))
    return istante if istante.tzinfo else istante.replace(tzinfo=ZoneInfo("UTC"))


async def _pagina_per_testo_o_creatori(
    primary, testo: str, creatori: list[str], stato: str | None, offset: int, page_size: int,
) -> tuple[list[dict], int]:
    """Una pagina delle call che corrispondono a `testo` nel titolo o nel
    bando OPPURE che sono di uno dei `creatori`, dalla più recente. L'unione
    si calcola qui (id e date a keyset, i creatori a blocchi di 100: un filtro
    `or` con una lista lunga di aziende non sta in una richiesta); poi si
    leggono le sole righe della pagina. → (righe, totale)."""
    def base():
        query = primary.table("partner_calls").select("id,created_at")
        return query.eq("stato", stato) if stato else query

    trovate = {
        str(r["id"]): r for r in await _tutte(
            lambda: base().or_(f"titolo.ilike.*{testo}*,bando_titolo.ilike.*{testo}*"))
    }
    for blocco in _blocchi(creatori):
        for riga in await _tutte(lambda b=blocco: base().in_("company_profile_id", b)):
            trovate[str(riga["id"])] = riga
    ordinate = sorted(trovate.values(),
                      key=lambda r: (_istante(r.get("created_at")), str(r["id"])), reverse=True)
    pagina = [str(r["id"]) for r in ordinate[offset : offset + page_size]]
    if not pagina:
        return [], len(ordinate)
    resp = await (primary.table("partner_calls").select(CALL_ADMIN_SELECT)
                  .in_("id", pagina).execute())
    per_id = {str(r["id"]): r for r in resp.data or [] if isinstance(r, dict)}
    return [per_id[i] for i in pagina if i in per_id], len(ordinate)


async def lista_call(primary, *, stato: str | None = None, q: str | None = None,
                     page: int = 1, page_size: int = 50) -> Page[CallAdminOut]:
    """Le call di tutte le aziende, dalla più recente. `q`: id esatto della
    call se è un uuid, altrimenti testo nel titolo della call, nel titolo del
    bando o nella denominazione del Registro Imprese dell'azienda creatrice
    (`_creatori_per_denominazione`)."""
    offset = (page - 1) * page_size
    testo = pulisci_ricerca(q)
    creatori: list[str] = []
    per_id: str | None = None
    if testo:
        try:
            per_id = str(uuid.UUID(testo))
        except ValueError:
            creatori = await _creatori_per_denominazione(primary, testo, stato)
    if creatori:
        righe, totale = await _pagina_per_testo_o_creatori(
            primary, testo or "", creatori, stato, offset, page_size)
    else:
        query = primary.table("partner_calls").select(CALL_ADMIN_SELECT, count="exact")
        if stato:
            query = query.eq("stato", stato)
        if per_id is not None:
            query = query.eq("id", per_id)
        elif testo:
            query = query.or_(f"titolo.ilike.*{testo}*,bando_titolo.ilike.*{testo}*")
        resp = await (query.order("created_at", desc=True)
                      .range(offset, offset + page_size - 1).execute())
        righe = [r for r in resp.data or [] if isinstance(r, dict)]
        totale = resp.count or 0
    ids = [str(r["id"]) for r in righe]
    conteggi = await _conteggi_candidature(primary, ids)
    membri = await _membri_attivi(primary, ids)
    aperte = await _segnalazioni_aperte(primary, ids)
    nomi = await _ragioni_sociali(primary, [r.get("company_profile_id") for r in righe])
    items = [
        CallAdminOut(
            **{k: r.get(k) for k in (
                "id", "titolo", "stato", "stato_prima_sospensione", "visibilita", "anonima",
                "pubblicata_at", "sospesa_at", "sospeso_motivo", "validazione_esito",
                "created_at",
            )},
            scadenza_call=str(r["scadenza_call"])[:10] if r.get("scadenza_call") else None,
            bando=BandoCallAdminOut(id=r.get("bando_id"), slug=r.get("bando_slug"),
                                    titolo=r.get("bando_titolo")),
            creatore=CreatoreCallAdminOut(
                company_profile_id=r["company_profile_id"],
                ragione_sociale=nomi.get(str(r["company_profile_id"]))),
            candidature=conteggi.get(str(r["id"]), {}).get("candidatura", 0),
            inviti=conteggi.get(str(r["id"]), {}).get("invito", 0),
            membri=membri.get(str(r["id"]), 0),
            segnalazioni_aperte=aperte.get(str(r["id"]), 0),
        )
        for r in righe
    ]
    return Page.build(items, totale, page, page_size)


# ------------------------------------------------------- metriche e costi


def oggi_roma() -> date:
    return datetime.now(ZoneInfo(get_settings().alert_fuso)).date()


def periodo(da: date | None, a: date | None) -> tuple[date, date]:
    """[da, a] con gli estremi compresi; default: gli ultimi 30 giorni fino a
    oggi (Europe/Rome). Fuori limite 400 `periodo_non_valido` (lo stesso
    controllo della RPC)."""
    fine = a or oggi_roma()
    inizio = da or fine - timedelta(days=PERIODO_DEFAULT_GIORNI - 1)
    if inizio > fine or (fine - inizio).days > PERIODO_MAX_GIORNI:
        raise AppError(*RPC_ERRORS["periodo_non_valido"])
    return inizio, fine


async def metriche(primary, da: date | None = None, a: date | None = None) -> MetricheOut:
    inizio, fine = periodo(da, a)
    dati = await _rpc(primary, "fn_admin_metriche_partenariati",
                      {"p_da": inizio.isoformat(), "p_a": fine.isoformat()})
    if not isinstance(dati, dict):
        raise UpstreamError()
    return MetricheOut.model_validate(dati)


async def costi(primary, da: date | None = None, a: date | None = None) -> CostiOut:
    inizio, fine = periodo(da, a)
    dati = await _rpc(primary, "fn_admin_costi_partenariati",
                      {"p_da": inizio.isoformat(), "p_a": fine.isoformat()})
    if not isinstance(dati, dict):
        raise UpstreamError()
    return CostiOut.model_validate(dati)


# ---------------------------------------------- verifica dell'identità


def registro_motivo(azienda: Mapping | None, dati: Mapping | None) -> str | None:
    """Stessa regola di `fn_partenariato_identita_ok` (T5), con i dati di
    prova ammessi solo con `OPENAPI_ENV=sandbox`. None = coerenti."""
    if azienda is None or dati is None:
        return "dati_non_importati"
    piva = azienda.get("partita_iva")
    if not piva or dati.get("piva_fetched") != piva:
        return "piva_diversa"
    if str(dati.get("stato_impresa") or "").strip().lower() != "attiva":
        return "impresa_non_attiva"
    if dati.get("sandbox") is not False and richiedi_non_sandbox():
        return "dati_sandbox"
    return None


async def _per_id(primary, tabella: str, colonne: str, colonna: str, ids: list[str]
                  ) -> dict[str, dict]:
    uscita: dict[str, dict] = {}
    for blocco in _blocchi(ids):
        resp = await primary.table(tabella).select(colonne).in_(colonna, blocco).execute()
        uscita.update({str(r[colonna]): r for r in resp.data or [] if isinstance(r, dict)})
    return uscita


async def _note_richiesta(primary, company_ids: list[str]) -> dict[str, str | None]:
    """Nota dell'ULTIMA richiesta di ogni azienda (registro append-only)."""
    uscita: dict[str, str | None] = {}
    for blocco in _blocchi(company_ids):
        righe = await _tutte(lambda b=blocco: primary.table("company_identita_verifiche")
                             .select("id,company_profile_id,nota")
                             .eq("azione", "richiesta").in_("company_profile_id", b))
        for riga in righe:  # id crescente: l'ultima vince
            uscita[str(riga["company_profile_id"])] = riga.get("nota")
    return uscita


async def coda_identita(primary, *, stato: str = "richiesta", page: int = 1,
                        page_size: int = 50) -> Page[IdentitaAdminOut]:
    """Richieste di verifica (default: in attesa, dalla più vecchia) o le
    aziende in un altro stato (`verificata` per le revoche), con i dati del
    registro e il titolare da contattare."""
    query = primary.table("company_identita_stato").select(IDENTITA_SELECT, count="exact")
    if stato != "tutte":
        query = query.eq("stato", stato)
    if stato == "richiesta":
        query = query.order("richiesta_at")
    else:
        query = query.order("aggiornato_at", desc=True)
    offset = (page - 1) * page_size
    resp = await query.range(offset, offset + page_size - 1).execute()
    righe = [r for r in resp.data or [] if isinstance(r, dict)]
    ids = [str(r["company_profile_id"]) for r in righe]
    aziende = await _per_id(primary, "company_profiles",
                            "id,parent_id,ragione_sociale,partita_iva", "id", ids)
    registri = await _per_id(primary, "company_data", REGISTRO_SELECT, "company_profile_id",
                             ids)
    titolari = await _per_id(primary, "profiles", "id,email,nome,cognome", "id",
                             sorted({str(a["parent_id"]) for a in aziende.values()
                                     if a.get("parent_id")}))
    note = await _note_richiesta(primary, ids)
    items = []
    for riga in righe:
        cid = str(riga["company_profile_id"])
        azienda = aziende.get(cid)
        dati = registri.get(cid)
        titolare = titolari.get(str((azienda or {}).get("parent_id")))
        coerente = bool(azienda and dati and azienda.get("partita_iva")
                        and dati.get("piva_fetched") == azienda.get("partita_iva"))
        motivo = registro_motivo(azienda, dati)
        nome = " ".join(p.strip() for p in ((titolare or {}).get("nome"),
                                            (titolare or {}).get("cognome"))
                        if isinstance(p, str) and p.strip()) or None
        items.append(IdentitaAdminOut(
            company_profile_id=cid,
            ragione_sociale=(azienda or {}).get("ragione_sociale"),
            denominazione_registro=(dati or {}).get("denominazione") if coerente else None,
            partita_iva=(azienda or {}).get("partita_iva"),
            stato=riga["stato"],
            metodo=riga.get("metodo"),
            richiesta_at=riga.get("richiesta_at"),
            verificata_at=riga.get("verificata_at"),
            aggiornato_at=riga.get("aggiornato_at"),
            nota=note.get(cid),
            titolare=TitolareIdentitaOut(nome=nome, email=titolare.get("email"))
            if titolare else None,
            registro_ok=motivo is None,
            registro_motivo=motivo,
            # Registro di un'altra P.IVA: nessun dato (non è di questa azienda).
            registro=RegistroIdentitaOut(
                denominazione=(dati or {}).get("denominazione") if coerente else None,
                partita_iva=(dati or {}).get("piva_fetched"),
                stato_impresa=(dati or {}).get("stato_impresa") if coerente else None,
                comune=(dati or {}).get("comune") if coerente else None,
                provincia=(dati or {}).get("provincia") if coerente else None,
                pec=(dati or {}).get("pec") if coerente else None,
                telefono=(dati or {}).get("telefono") if coerente else None,
            ),
        ))
    return Page.build(items, resp.count or 0, page, page_size)


async def _motivo_attuale(primary, company_id: str) -> str | None:
    azienda = (await _per_id(primary, "company_profiles", "id,parent_id,partita_iva", "id",
                             [company_id])).get(company_id)
    if azienda is None:
        raise NotFoundError(MSG_AZIENDA_NON_TROVATA)
    dati = (await _per_id(primary, "company_data",
                          "company_profile_id,piva_fetched,stato_impresa,sandbox",
                          "company_profile_id", [company_id])).get(company_id)
    return registro_motivo(azienda, dati)


def _esito_identita(company_id: str, esito: Mapping) -> IdentitaEsitoOut:
    stato = esito.get("stato") if isinstance(esito.get("stato"), dict) else {}
    return IdentitaEsitoOut(
        company_profile_id=company_id,
        stato=stato.get("stato") or "non_richiesta",
        metodo=stato.get("metodo"),
        verificata_at=stato.get("verificata_at"),
        modificato=bool(esito.get("modificato")),
    )


async def _notifica_titolare(primary, esito: Mapping, company_id: str, azione: str) -> None:
    """In-app al titolare (non solleva: `notify` è best-effort)."""
    owner = esito.get("family_parent_id")
    if not owner:
        return
    stato = esito.get("stato") if isinstance(esito.get("stato"), dict) else {}
    istante = stato.get("aggiornato_at") or uuid.uuid4().hex
    await notify(
        primary, [str(owner)], tipo=testi.TIPO_IDENTITA,
        titolo=testi.titolo_identita(azione), corpo=testi.corpo_identita(azione),
        url=f"/app/azienda?azienda={company_id}#partner",
        dedup_key=f"identita:{company_id}:{azione}:{istante}",
        company_profile_id=company_id,
    )


async def decidi_identita(primary, admin: dict, company_id: Any, dati: IdentitaDecisioneIn
                          ) -> IdentitaEsitoOut:
    """L'admin verifica (metodo obbligatorio; dati del registro coerenti) o
    rifiuta una richiesta in attesa. Errori: 404, 400 `metodo_obbligatorio`,
    409 `identita_non_richiesta`, `identita_non_verificata`."""
    cid = _uuid(company_id, MSG_AZIENDA_NON_TROVATA)
    verificata = dati.esito == "verificata"
    if verificata:
        if dati.metodo is None:
            raise AppError(*RPC_ERRORS["metodo_obbligatorio"])
        if await _motivo_attuale(primary, cid) is not None:
            raise AppError(*_ERRORI_ADMIN["identita_non_verificata"])
    esito = await _rpc(primary, "fn_identita_decidi", {
        "p_company": cid,
        "p_admin": str(admin["id"]),
        "p_esito": dati.esito,
        "p_metodo": dati.metodo if verificata else None,
        "p_nota": dati.nota,
    })
    if not isinstance(esito, dict):
        raise UpstreamError()
    partenariato_indice.invalida()
    await _notifica_titolare(primary, esito, cid, dati.esito)
    return _esito_identita(cid, esito)


async def revoca_identita(primary, admin: dict, company_id: Any, dati: IdentitaRevocaIn
                          ) -> IdentitaEsitoOut:
    """L'admin revoca una verifica (motivo ≤ 500): le viste future non
    mostrano più l'identità dell'azienda. Un'azienda non verificata:
    nessuna scrittura (`modificato` false). Errori: 404, 400
    `motivo_obbligatorio`."""
    cid = _uuid(company_id, MSG_AZIENDA_NON_TROVATA)
    esito = await _rpc(primary, "fn_identita_revoca", {
        "p_company": cid, "p_admin": str(admin["id"]), "p_motivo": dati.motivo,
    })
    if not isinstance(esito, dict):
        raise UpstreamError()
    if esito.get("modificato"):
        partenariato_indice.invalida()
        await _notifica_titolare(primary, esito, cid, "revocata")
    return _esito_identita(cid, esito)
