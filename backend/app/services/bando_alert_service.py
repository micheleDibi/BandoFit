"""Alert email giornalieri sui nuovi bandi compatibili.

NESSUNA pre-schedulazione: l'idoneità di ogni coppia (utente, bando) si
ricalcola A OGNI RUN dallo stato corrente — piano (col suo ritardo di invio),
opt-in, email verificata, account attivo, bando ancora aperto. Lo stato
persistito (migration 0021) è solo: ledger degli invii (unique su
utente+bando = idempotenza a DB), impostazioni utente e registro delle run.

Riferimento temporale = coalesce(data_pubblicazione, created_at in Europe/
Rome): si invia quando `oggi >= riferimento + ritardo del piano`. Aritmetica
su DATE nel fuso italiano; i ritardi sono giorni interi.

Testabilità: le funzioni di calcolo ricevono `oggi: date` — niente now()
inline nei percorsi di decisione.

INNESTO CODA (futuro): il loop per-destinatario di `esegui_run` passa da
`_invia_digest(...)`, un destinatario alla volta. Una coda outbox potrà
accodare gli stessi payload e un worker consumarli chiamando la stessa
funzione, senza toccare la selezione.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.schemas.bando import LookupsOut
from app.services import email_service, lookup_service, notification_service
from app.services.bandi_service import (
    LIST_SELECT,
    SCORING_EMBEDS,
    apply_open_tier,
    bando_facet_ids,
)
from app.services.compatibility import CompanyFacets, build_company_facets, compute_compatibilita

logger = logging.getLogger("bandofit.bando_alerts")

# Soglia di compatibilità (decisione di prodotto): punteggio del pre-check.
PUNTEGGIO_MINIMO = 60

CANDIDATE_SELECT = LIST_SELECT + SCORING_EMBEDS + ",created_at"

_DIMENSIONI_LABEL = {
    "regioni": "Regioni",
    "ateco": "ATECO",
    "settori": "Settore",
    "beneficiari": "Beneficiari",
}


@dataclass
class BandoCandidato:
    id: int
    slug: str
    titolo: str | None
    titolo_breve: str | None
    ente_erogatore: str | None
    importo_totale_eur: int | None
    importo_max_per_progetto_eur: int | None
    data_scadenza: date | None
    riferimento: date  # coalesce(data_pubblicazione, created_at::date italiana)
    facets: dict  # id junction per compute_compatibilita


@dataclass
class AziendaAlert:
    """Un'azienda viva nel run, col suo profilo di compatibilità e lo scope del
    ledger. `scope_value` = l'id azienda per gli owner multi-azienda (Advisor),
    None per tutti gli altri — così ledger e notifiche dei non-Advisor restano
    a company_profile_id NULL, identici a prima."""

    company_id: str
    ragione_sociale: str | None
    facets: CompanyFacets
    scope_value: str | None


# ---------------------------------------------------------------------------
# Funzioni PURE (oggi iniettato, nessun I/O)
# ---------------------------------------------------------------------------


def data_riferimento(row: dict, fuso: ZoneInfo) -> date | None:
    """La data ufficiale di pubblicazione; se assente, il giorno (italiano)
    dell'ingestione in piattaforma."""
    if row.get("data_pubblicazione"):
        return date.fromisoformat(row["data_pubblicazione"])
    if row.get("created_at"):
        return datetime.fromisoformat(row["created_at"]).astimezone(fuso).date()
    return None


def filtra_candidati(
    rows: list[dict],
    *,
    oggi: date,
    attivazione: date,
    orizzonte_giorni: int,
    fuso: ZoneInfo,
) -> tuple[list[BandoCandidato], int]:
    """Candidati nella finestra utile. Ritorna anche il conteggio degli
    scartati per orizzonte (mai in silenzio: il chiamante li logga)."""
    cutoff = max(attivazione, oggi - timedelta(days=orizzonte_giorni))
    candidati: list[BandoCandidato] = []
    fuori_orizzonte = 0
    for row in rows:
        riferimento = data_riferimento(row, fuso)
        if riferimento is None or riferimento > oggi:
            continue  # senza date utilizzabili o pubblicazione futura
        if riferimento < cutoff:
            if riferimento >= attivazione:
                fuori_orizzonte += 1
            continue
        candidati.append(
            BandoCandidato(
                id=row["id"],
                slug=row["slug"],
                titolo=row.get("titolo"),
                titolo_breve=row.get("titolo_breve"),
                ente_erogatore=row.get("ente_erogatore"),
                importo_totale_eur=row.get("importo_totale_eur"),
                importo_max_per_progetto_eur=row.get("importo_max_per_progetto_eur"),
                data_scadenza=(
                    date.fromisoformat(row["data_scadenza"])
                    if row.get("data_scadenza")
                    else None
                ),
                riferimento=riferimento,
                facets=bando_facet_ids(row),
            )
        )
    return candidati, fuori_orizzonte


def bandi_eleggibili(
    candidati: list[BandoCandidato],
    facets: CompanyFacets,
    *,
    totale_regioni: int,
    ritardo_giorni: int,
    oggi: date,
) -> list[tuple[BandoCandidato, dict]]:
    """I bandi del run per QUESTA azienda: compatibili (punteggio >= soglia)
    e col ritardo del piano già maturato."""
    out: list[tuple[BandoCandidato, dict]] = []
    for candidato in candidati:
        if oggi < candidato.riferimento + timedelta(days=ritardo_giorni):
            continue
        compat = compute_compatibilita(
            facets, candidato.facets, totale_regioni=totale_regioni
        )
        if compat is None or compat["punteggio"] < PUNTEGGIO_MINIMO:
            continue
        out.append((candidato, compat))
    return out


def motivo_compatibilita(compat: dict, lookups: LookupsOut) -> str:
    """Il «perché lo vedi» dell'email: le dimensioni soddisfatte con i NOMI
    dei valori in comune (max 3 per dimensione)."""
    nomi_per_dim: dict[str, dict[int, str]] = {
        "regioni": {r.id: r.nome for r in lookups.regioni},
        "settori": {s.id: s.nome for s in lookups.settori},
        "beneficiari": {b.id: b.nome for b in lookups.beneficiari},
        "ateco": {a.id: a.codice for a in lookups.codici_ateco},
    }
    parti: list[str] = []
    for dim, dati in compat["dimensioni"].items():
        if not dati["soddisfatta"]:
            continue
        if dim == "regioni" and dati.get("nazionale"):
            parti.append("Aperto a tutta Italia")
            continue
        nomi = [nomi_per_dim[dim].get(i) for i in dati["matched_ids"]]
        nomi = [n for n in nomi if n][:3]
        if nomi:
            parti.append(f"{_DIMENSIONI_LABEL[dim]}: {', '.join(nomi)}")
    return " · ".join(parti)


def giorni_alla_scadenza(scadenza: date | None, oggi: date) -> int | None:
    if scadenza is None:
        return None
    return (scadenza - oggi).days


def _digest_item(
    candidato: BandoCandidato, compat: dict, lookups: LookupsOut, oggi: date, frontend_url: str
) -> dict:
    return {
        "titolo": candidato.titolo_breve or candidato.titolo,
        "ente_erogatore": candidato.ente_erogatore,
        "importo_eur": candidato.importo_totale_eur,
        "importo_max_eur": candidato.importo_max_per_progetto_eur,
        "scadenza_label": (
            candidato.data_scadenza.strftime("%d/%m/%Y") if candidato.data_scadenza else None
        ),
        "giorni_alla_scadenza": giorni_alla_scadenza(candidato.data_scadenza, oggi),
        "motivo": motivo_compatibilita(compat, lookups),
        "url": f"{frontend_url.rstrip('/')}/app/bandi/{candidato.slug}",
    }


# ---------------------------------------------------------------------------
# I/O: caricamenti batch
# ---------------------------------------------------------------------------


async def carica_candidati(
    secondary, *, oggi: date, attivazione: date, orizzonte_giorni: int, fuso: ZoneInfo
) -> list[BandoCandidato]:
    """Bandi visibili, non chiusi, con riferimento nella finestra utile."""
    cutoff = max(attivazione, oggi - timedelta(days=orizzonte_giorni))
    query = (
        secondary.table("bando")
        .select(CANDIDATE_SELECT)
        .eq("stato_processing", "completed")
        .not_.is_("slug", "null")
    )
    query = apply_open_tier(query, oggi)
    # Pre-filtro grezzo a DB (la finestra esatta la applica filtra_candidati):
    # pubblicazione recente O (pubblicazione assente E ingestione recente).
    query = query.or_(
        f"data_pubblicazione.gte.{cutoff.isoformat()},"
        f"and(data_pubblicazione.is.null,created_at.gte.{cutoff.isoformat()})"
    )
    resp = await query.execute()
    candidati, fuori_orizzonte = filtra_candidati(
        resp.data or [],
        oggi=oggi,
        attivazione=attivazione,
        orizzonte_giorni=orizzonte_giorni,
        fuso=fuso,
    )
    if fuori_orizzonte:
        logger.warning(
            "alert bandi: %s candidati oltre l'orizzonte di %s giorni, scartati",
            fuori_orizzonte,
            orizzonte_giorni,
        )
    return candidati


async def carica_limiti_aziende(primary, owner_ids: list[str]) -> dict[str, int]:
    """Limite effettivo di aziende per owner (override profilo > piano > 1), in
    batch: replica `fn_effective_max_aziende` senza una RPC per owner. È il gate
    dello scope — solo gli owner con limite > 1 (Advisor) segregano ledger e
    notifiche per azienda; tutti gli altri restano a company_profile_id NULL."""
    if not owner_ids:
        return {}
    profs = (
        await primary.table("profiles")
        .select("id,max_aziende_override")
        .in_("id", owner_ids)
        .execute()
    )
    override = {r["id"]: r.get("max_aziende_override") for r in profs.data or []}
    subs = (
        await primary.table("user_subscriptions")
        .select("user_id,subscription_plans(max_aziende)")
        .in_("user_id", owner_ids)
        .eq("status", "active")
        .execute()
    )
    piano: dict[str, int] = {}
    for row in subs.data or []:
        plan = row.get("subscription_plans") or {}
        if plan.get("max_aziende") is not None:
            piano[row["user_id"]] = int(plan["max_aziende"])
    return {o: int(override.get(o) or piano.get(o) or 1) for o in owner_ids}


async def carica_company_facets(
    primary, lookups: LookupsOut
) -> dict[str, list[AziendaAlert]]:
    """Aziende VIVE (non cancellate/archiviate) con facet sufficienti,
    raggruppate per owner. Sostituisce il vecchio `carica_owner_facets`: NON
    collassa più su parent_id (un Advisor ha N aziende, ognuna col proprio
    profilo di compatibilità e la propria sezione nel digest). Lo scope del
    ledger è l'id azienda per gli owner multi-azienda, NULL per gli altri."""
    companies = (
        await primary.table("company_profiles")
        .select("id,parent_id,ragione_sociale,ateco_id,settore_id,regione_id,beneficiari")
        .is_("deleted_at", "null")
        .is_("archived_at", "null")
        .order("created_at")
        .execute()
    )
    rows = companies.data or []
    if not rows:
        return {}
    data = (
        await primary.table("company_data")
        .select("company_profile_id,derived")
        .in_("company_profile_id", [r["id"] for r in rows])
        .execute()
    )
    derived_map = {r["company_profile_id"]: r.get("derived") for r in data.data or []}
    limiti = await carica_limiti_aziende(primary, list({r["parent_id"] for r in rows}))

    per_owner: dict[str, list[AziendaAlert]] = {}
    for company in rows:
        facets = build_company_facets(company, derived_map.get(company["id"]), lookups)
        if not facets.sufficiente:
            continue
        owner = company["parent_id"]
        multi = limiti.get(owner, 1) > 1
        per_owner.setdefault(owner, []).append(
            AziendaAlert(
                company_id=company["id"],
                ragione_sociale=company.get("ragione_sociale"),
                facets=facets,
                scope_value=company["id"] if multi else None,
            )
        )
    return per_owner


async def carica_ritardi_piano(primary, owner_ids: list[str]) -> dict[str, int]:
    """Ritardo di invio per titolare. Gate della feature: piano attivo con
    alert_attivo E alert_ritardo_giorni valorizzato."""
    if not owner_ids:
        return {}
    resp = (
        await primary.table("user_subscriptions")
        .select("user_id,subscription_plans(alert_attivo,alert_ritardo_giorni)")
        .in_("user_id", owner_ids)
        .eq("status", "active")
        .execute()
    )
    ritardi: dict[str, int] = {}
    for row in resp.data or []:
        plan = row.get("subscription_plans") or {}
        if plan.get("alert_attivo") and plan.get("alert_ritardo_giorni") is not None:
            ritardi[row["user_id"]] = int(plan["alert_ritardo_giorni"])
    return ritardi


async def carica_destinatari(primary, owner_ids: list[str]) -> dict[str, list[dict]]:
    """Per ogni titolare: lui + gli account collegati ATTIVI, tutti con
    profilo attivo ed email presente."""
    if not owner_ids:
        return {}
    members = (
        await primary.table("family_members")
        .select("parent_id,member_id")
        .in_("parent_id", owner_ids)
        .eq("status", "active")
        .execute()
    )
    user_to_owner: dict[str, str] = {owner: owner for owner in owner_ids}
    for row in members.data or []:
        user_to_owner[row["member_id"]] = row["parent_id"]
    profili = (
        await primary.table("profiles")
        .select("id,email,is_active")
        .in_("id", list(user_to_owner))
        .execute()
    )
    per_owner: dict[str, list[dict]] = {}
    for profilo in profili.data or []:
        if not profilo.get("is_active") or not profilo.get("email"):
            continue
        per_owner.setdefault(user_to_owner[profilo["id"]], []).append(profilo)
    return per_owner


async def carica_visibilita_membri(primary, owner_ids: list[str]) -> dict[str, set[str]]:
    """Visibilità aziende dei membri ATTIVI (0031): member_id → id azienda
    concessi. Il digest di un membro va INTERSECATO con questo insieme (le
    sezioni per-azienda di un multi-azienda non devono rivelare aziende non
    concesse); l'owner riceve sempre tutto."""
    if not owner_ids:
        return {}
    members = (
        await primary.table("family_members")
        .select("id,member_id")
        .in_("parent_id", owner_ids)
        .eq("status", "active")
        .execute()
    )
    membership_to_member = {str(r["id"]): str(r["member_id"]) for r in members.data or []}
    if not membership_to_member:
        return {}
    access = (
        await primary.table("family_member_company_access")
        .select("family_member_id,company_profile_id")
        .in_("family_member_id", list(membership_to_member))
        .execute()
    )
    visibilita: dict[str, set[str]] = {m: set() for m in membership_to_member.values()}
    for row in access.data or []:
        member = membership_to_member.get(str(row["family_member_id"]))
        if member:
            visibilita[member].add(str(row["company_profile_id"]))
    return visibilita


async def filtra_recapitabili(primary, destinatari: list[dict]) -> list[dict]:
    """Solo email VERIFICATE (auth.users, via RPC batch) e non in
    suppression list (confronto case-insensitive)."""
    if not destinatari:
        return []
    resp = await primary.rpc(
        "fn_email_verificate", {"p_user_ids": [d["id"] for d in destinatari]}
    ).execute()
    verificati = {str(v) for v in (resp.data or [])}
    soppressi_resp = await primary.table("email_suppressions").select("email").execute()
    soppressi = {r["email"].lower() for r in soppressi_resp.data or []}
    return [
        d
        for d in destinatari
        if str(d["id"]) in verificati and d["email"].lower() not in soppressi
    ]


async def ensure_settings(primary, user_ids: list[str]) -> dict[str, dict]:
    """Garantisce la riga impostazioni (serve il token di disiscrizione da
    mettere in email) e la ritorna. La riga nasce col default abilitati=true."""
    if not user_ids:
        return {}
    await primary.table("bando_alert_settings").upsert(
        [{"user_id": uid} for uid in user_ids],
        on_conflict="user_id",
        ignore_duplicates=True,
    ).execute()
    resp = (
        await primary.table("bando_alert_settings")
        .select("user_id,abilitati,unsubscribe_token")
        .in_("user_id", user_ids)
        .execute()
    )
    return {r["user_id"]: r for r in resp.data or []}


# ---------------------------------------------------------------------------
# Ledger: claim-by-insert (idempotenza) e finalizzazione
# ---------------------------------------------------------------------------


async def claim_ledger(
    primary,
    user_id: str,
    scope_value: str | None,
    eleggibili: list[tuple[BandoCandidato, dict]],
    *,
    oggi: date,
    max_tentativi: int,
) -> dict[int, int]:
    """Rivendica le coppie (utente, azienda, bando) da inviare in questo run.
    `scope_value` = l'id azienda (Advisor) o None (legacy/non-Advisor): entra
    nella chiave di idempotenza, così lo stesso bando può essere inviato a più
    aziende dello stesso owner (righe di ledger distinte).
    Ritorna {bando_id: id riga ledger} per le sole coppie claimate:
    - mai inviate → insert (l'unique è l'arbiter: chi perde la corsa salta);
    - fallite ritentabili → update condizionale a in_invio (tentativi+1);
    - inviata / incerta / in_invio / fallita esausta → skip.
    """
    bando_ids = [candidato.id for candidato, _ in eleggibili]
    if not bando_ids:
        return {}
    existing_q = (
        primary.table("bando_alert_sends")
        .select("id,bando_id,stato,tentativi")
        .eq("user_id", str(user_id))
        .in_("bando_id", bando_ids)
    )
    # Scope azienda nella lettura: la stessa (utente, bando) può esistere per
    # un'altra azienda (già inviata) ed essere nuova per questa.
    if scope_value is None:
        existing_q = existing_q.is_("company_profile_id", "null")
    else:
        existing_q = existing_q.eq("company_profile_id", str(scope_value))
    existing = await existing_q.execute()
    per_bando = {r["bando_id"]: r for r in existing.data or []}

    claimed: dict[int, int] = {}
    nuove: list[dict] = []
    for candidato, _compat in eleggibili:
        row = per_bando.get(candidato.id)
        if row is None:
            nuove.append(
                {
                    "user_id": str(user_id),
                    "company_profile_id": scope_value,
                    "bando_id": candidato.id,
                    "bando_slug": candidato.slug,
                    "run_giorno": oggi.isoformat(),
                }
            )
        elif row["stato"] == "fallita" and row["tentativi"] < max_tentativi:
            upd = (
                await primary.table("bando_alert_sends")
                .update(
                    {
                        "stato": "in_invio",
                        "tentativi": row["tentativi"] + 1,
                        "run_giorno": oggi.isoformat(),
                    }
                )
                .eq("id", row["id"])
                .eq("stato", "fallita")  # condizionale: una run parallela ha già claimato?
                .execute()
            )
            if upd.data:
                claimed[candidato.id] = row["id"]
    if nuove:
        # Idempotenza su (user_id, company_profile_id, bando_id) con NULLS NOT
        # DISTINCT (0023): le righe legacy (company NULL) deduplicano come
        # prima, quelle per-azienda distinguono per company_profile_id.
        ins = await primary.table("bando_alert_sends").upsert(
            nuove, on_conflict="user_id,company_profile_id,bando_id", ignore_duplicates=True
        ).execute()
        for r in ins.data or []:
            claimed[r["bando_id"]] = r["id"]
    return claimed


async def finalizza_invio(primary, ledger_ids: list[int], *, esito: str, errore: str | None) -> None:
    if not ledger_ids:
        return
    await primary.table("bando_alert_sends").update(
        {"stato": esito, "errore": errore}
    ).in_("id", ledger_ids).execute()


async def marca_in_invio_stantie(primary) -> int:
    """Righe rimaste in_invio da una run interrotta tra invio e conferma:
    l'esito è ignoto → 'incerta', MAI ritentate (at-most-once)."""
    resp = (
        await primary.table("bando_alert_sends")
        .update({"stato": "incerta", "errore": "run interrotta"})
        .eq("stato", "in_invio")
        .execute()
    )
    quante = len(resp.data or [])
    if quante:
        logger.warning("alert bandi: %s invii di run interrotte marcati incerti", quante)
    return quante


# ---------------------------------------------------------------------------
# Invio per destinatario + run completa
# ---------------------------------------------------------------------------


async def _invia_digest(
    primary,
    destinatario: dict,
    sezioni: list[tuple[AziendaAlert, list[tuple[BandoCandidato, dict]]]],
    settings_row: dict,
    *,
    oggi: date,
    lookups: LookupsOut,
) -> tuple[int, int]:
    """Invia il digest a UN destinatario. `sezioni` = una coppia (azienda,
    eleggibili) per azienda dell'owner. Una sola email; il ledger viene
    rivendicato per azienda (scope company_profile_id). Ritorna (inviate,
    fallite) in email. Punto d'innesto della futura coda outbox."""
    settings = get_settings()
    # Rivendica per azienda: stessa email, ma righe di ledger distinte per
    # company. Tengo insieme azienda, item da inviare e id di ledger claimati.
    inviabili: list[tuple[AziendaAlert, list[dict], list[int]]] = []
    for azienda, eleggibili in sezioni:
        claimed = await claim_ledger(
            primary,
            destinatario["id"],
            azienda.scope_value,
            eleggibili,
            oggi=oggi,
            max_tentativi=settings.alert_max_tentativi,
        )
        if not claimed:
            continue
        items = [
            _digest_item(c, compat, lookups, oggi, settings.frontend_url)
            for c, compat in eleggibili
            if c.id in claimed
        ]
        inviabili.append((azienda, items, list(claimed.values())))
    if not inviabili:
        return (0, 0)

    is_multi = any(azienda.scope_value for azienda, _, _ in inviabili)
    cta_url = f"{settings.frontend_url.rstrip('/')}/app/bandi"
    unsubscribe_url = (
        f"{settings.api_public_url.rstrip('/')}/alerts/unsubscribe"
        f"?token={settings_row['unsubscribe_token']}"
    )
    if is_multi:
        ok = await email_service.send_bandi_digest_email_multi(
            destinatario["email"],
            [
                {"azienda": azienda.ragione_sociale, "bandi": items}
                for azienda, items, _ in inviabili
            ],
            cta_url,
            unsubscribe_url,
        )
    else:
        # Non-Advisor: sezione unica, digest classico (parità byte-identica).
        [(_, items, _)] = inviabili
        ok = await email_service.send_bandi_digest_email(
            destinatario["email"], items, cta_url, unsubscribe_url
        )

    await finalizza_invio(
        primary,
        [ledger_id for _, _, ledger_ids in inviabili for ledger_id in ledger_ids],
        esito="inviata" if ok else "fallita",
        errore=None if ok else "invio email fallito (vedi log)",
    )
    if ok:
        await _notifica_digest(primary, destinatario["id"], inviabili, is_multi, oggi=oggi)
    return (1, 0) if ok else (0, 1)


async def _notifica_digest(
    primary,
    user_id: str,
    inviabili: list[tuple[AziendaAlert, list[dict], list[int]]],
    is_multi: bool,
    *,
    oggi: date,
) -> None:
    """Notifica in-app dell'avvenuto invio. Per i non-Advisor: una notifica
    aggregata (identica a prima). Per gli Advisor: una notifica per azienda, con
    `company_profile_id` (il centro alert la filtra) e la company nel dedup_key."""
    if not is_multi:
        quanti = sum(len(items) for _, items, _ in inviabili)
        await notification_service.notify(
            primary,
            [user_id],
            tipo="bando_alert.digest",
            titolo=(
                "Un nuovo bando compatibile con la tua azienda"
                if quanti == 1
                else f"{quanti} nuovi bandi compatibili con la tua azienda"
            ),
            corpo="Ti abbiamo inviato i dettagli via email.",
            url="/app/bandi",
            dedup_key=f"bando-alert:{oggi.isoformat()}",
        )
        return
    for azienda, items, _ in inviabili:
        quanti = len(items)
        nome = azienda.ragione_sociale or "la tua azienda"
        await notification_service.notify(
            primary,
            [user_id],
            tipo="bando_alert.digest",
            titolo=(
                f"{nome}: un nuovo bando compatibile"
                if quanti == 1
                else f"{nome}: {quanti} nuovi bandi compatibili"
            ),
            corpo="Ti abbiamo inviato i dettagli via email.",
            url="/app/notifiche",
            dedup_key=f"bando-alert:{oggi.isoformat()}:{azienda.company_id}",
            company_profile_id=azienda.company_id,
        )


async def esegui_run(primary, secondary, oggi: date) -> dict:
    """Una esecuzione completa del job. Non solleva MAI verso il chiamante:
    l'esito (anche di errore) finisce nella riga di bando_alert_runs."""
    settings = get_settings()
    fuso = ZoneInfo(settings.alert_fuso)
    riepilogo = {
        "giorno": oggi.isoformat(),
        "esito": "ok",
        "bandi_candidati": 0,
        "destinatari": 0,
        "email_inviate": 0,
        "email_fallite": 0,
        "dettagli": {},
    }
    try:
        incerte = await marca_in_invio_stantie(primary)
        if incerte:
            riepilogo["dettagli"]["invii_incerti"] = incerte

        candidati = await carica_candidati(
            secondary,
            oggi=oggi,
            attivazione=date.fromisoformat(settings.alert_data_attivazione),
            orizzonte_giorni=settings.alert_orizzonte_giorni,
            fuso=fuso,
        )
        riepilogo["bandi_candidati"] = len(candidati)
        if candidati:
            lookups = await lookup_service.get_lookups(secondary)
            aziende_per_owner = await carica_company_facets(primary, lookups)
            ritardi = await carica_ritardi_piano(primary, list(aziende_per_owner))
            owner_abilitati = [o for o in aziende_per_owner if o in ritardi]
            destinatari_per_owner = await carica_destinatari(primary, owner_abilitati)
            visibilita_membri = await carica_visibilita_membri(primary, owner_abilitati)
            totale_regioni = len(lookups.regioni)

            for owner_id in owner_abilitati:
                # Una sezione per azienda dell'owner (per gli Advisor sono N).
                sezioni = [
                    (azienda, eleggibili)
                    for azienda in aziende_per_owner[owner_id]
                    if (
                        eleggibili := bandi_eleggibili(
                            candidati,
                            azienda.facets,
                            totale_regioni=totale_regioni,
                            ritardo_giorni=ritardi[owner_id],
                            oggi=oggi,
                        )
                    )
                ]
                if not sezioni:
                    continue
                recapitabili = await filtra_recapitabili(
                    primary, destinatari_per_owner.get(owner_id, [])
                )
                if not recapitabili:
                    continue
                impostazioni = await ensure_settings(
                    primary, [d["id"] for d in recapitabili]
                )
                for destinatario in recapitabili:
                    settings_row = impostazioni.get(destinatario["id"])
                    if not settings_row or not settings_row["abilitati"]:
                        continue  # opt-out: valutato ADESSO, all'invio
                    # 0031: un MEMBRO riceve solo le sezioni delle aziende a
                    # lui visibili (scope_value None = azienda unica legacy:
                    # sempre inclusa, la visibilità la copre per costruzione).
                    sezioni_destinatario = sezioni
                    if destinatario["id"] != owner_id:
                        vis = visibilita_membri.get(str(destinatario["id"]), set())
                        sezioni_destinatario = [
                            (azienda, eleggibili)
                            for azienda, eleggibili in sezioni
                            if azienda.scope_value is None or str(azienda.scope_value) in vis
                        ]
                        if not sezioni_destinatario:
                            continue
                    riepilogo["destinatari"] += 1
                    inviate, fallite = await _invia_digest(
                        primary,
                        destinatario,
                        sezioni_destinatario,
                        settings_row,
                        oggi=oggi,
                        lookups=lookups,
                    )
                    riepilogo["email_inviate"] += inviate
                    riepilogo["email_fallite"] += fallite
                    if inviate or fallite:
                        await asyncio.sleep(settings.alert_pausa_invii_secondi)
    except APIError as exc:
        riepilogo["esito"] = "errore"
        riepilogo["dettagli"]["errore"] = f"{exc.code}: {exc.message}"
        logger.error("run alert bandi fallita: %s", exc, exc_info=True)
    except Exception as exc:  # difensivo: la run non deve uccidere lo scheduler
        riepilogo["esito"] = "errore"
        riepilogo["dettagli"]["errore"] = str(exc)
        logger.error("run alert bandi fallita: %s", exc, exc_info=True)

    try:
        await primary.table("bando_alert_runs").upsert(
            {
                "giorno": riepilogo["giorno"],
                "finished_at": datetime.now(ZoneInfo("UTC")).isoformat(),
                "esito": riepilogo["esito"],
                "bandi_candidati": riepilogo["bandi_candidati"],
                "destinatari": riepilogo["destinatari"],
                "email_inviate": riepilogo["email_inviate"],
                "email_fallite": riepilogo["email_fallite"],
                "dettagli": riepilogo["dettagli"],
            },
            on_conflict="giorno",
        ).execute()
    except Exception:
        logger.warning("bando_alert_runs non scrivibile", exc_info=True)

    logger.info(
        "run alert bandi %s: %s — candidati=%s destinatari=%s inviate=%s fallite=%s",
        riepilogo["giorno"],
        riepilogo["esito"],
        riepilogo["bandi_candidati"],
        riepilogo["destinatari"],
        riepilogo["email_inviate"],
        riepilogo["email_fallite"],
    )
    return riepilogo


# ---------------------------------------------------------------------------
# Impostazioni utente (toggle in-app e disiscrizione via token)
# ---------------------------------------------------------------------------


async def set_abilitati(primary, user_id: str, abilitati: bool) -> None:
    """Toggle in-app: upsert della riga (stessa fonte di verità del token)."""
    await primary.table("bando_alert_settings").upsert(
        {"user_id": str(user_id), "abilitati": abilitati}, on_conflict="user_id"
    ).execute()


async def get_abilitati(primary, user_id: str) -> bool:
    resp = (
        await primary.table("bando_alert_settings")
        .select("abilitati")
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    )
    return resp.data[0]["abilitati"] if resp.data else True  # assenza = abilitati


async def unsubscribe_by_token(primary, token: str) -> None:
    """Disiscrizione a un clic: idempotente, silenziosa anche con token
    ignoto (nessuna enumerazione possibile). Audit best-effort."""
    resp = (
        await primary.table("bando_alert_settings")
        .update({"abilitati": False})
        .eq("unsubscribe_token", token)
        .execute()
    )
    if resp.data:
        try:
            await primary.table("audit_log").insert(
                {
                    "actor_id": str(resp.data[0]["user_id"]),
                    "action": "alerts.unsubscribed",
                    "target_user_id": str(resp.data[0]["user_id"]),
                    "payload": {"canale": "email"},
                }
            ).execute()
        except Exception:
            logger.warning("audit_log non scrivibile per alerts.unsubscribed", exc_info=True)


async def alert_settings_for_user(primary, user: dict) -> dict:
    """Impostazioni + piano EFFETTIVO: per i collegati attivi vale il piano
    del titolare (stessa regola delle quote)."""
    from app.services import family_service, user_service  # import locale: evita cicli

    abilitati = await get_abilitati(primary, user["id"])
    owner_id = str(user["id"])
    membership = await family_service.get_membership(primary, owner_id)
    if membership and membership["status"] == "active":
        owner_id = membership["parent_id"]
    subscription = await user_service._fetch_active_subscription(primary, owner_id)
    plan = subscription.plan if subscription else None
    include = bool(plan and plan.alert_attivo and plan.alert_ritardo_giorni is not None)
    return {
        "abilitati": abilitati,
        "piano_include_alert": include,
        "ritardo_giorni": plan.alert_ritardo_giorni if include else None,
    }
