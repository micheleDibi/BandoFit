"""Contratti del monitoraggio del catalogo (pannello admin «Catalogo»).

La busta è quella della funzione di monitoraggio del DB del catalogo
(contratto DB bandi §14.3-14.4, versione 1). Modelli TOLLERANTI: chiavi
sconosciute ignorate, ogni campo facoltativo e nullable, enumerazioni come
`str` (senza preavviso possono comparire chiavi, codici di segnale e valori
nuovi, §14.5). Un valore di tipo diverso non passa la validazione: per il
contratto un cambio di tipo richiede la versione 2, quindi il backend
risponde «formato_non_supportato» invece di interpretare i dati.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

StatoAccesso = Literal[
    "ok",
    "non_configurato",
    "chiave_non_valida",
    "non_disponibile",
    "accesso_db_non_valido",
    "non_raggiungibile",
    "formato_non_supportato",
]


class _Tollerante(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SegnaleMonitoraggio(_Tollerante):
    codice: str | None = None
    livello: str | None = None  # allarme | avviso
    testo: str | None = None  # frase fissa, da mostrare così com'è
    dal: datetime | None = None
    misura: float | None = None


class ProduttoreMonitoraggio(_Tollerante):
    ultimo_giro_at: datetime | None = None
    ore_dall_ultimo_giro: float | None = None
    giri_24h: int | None = None
    riavvii_24h: int | None = None
    servizio: str | None = None  # attivo | non_attivo | non_misurato


class GiroMonitoraggio(_Tollerante):
    id: int | str | None = None
    giro: str | None = None  # 00 | 06 | 12 | 18 | avvio | manuale
    avviato_at: datetime | None = None
    concluso_at: datetime | None = None
    durata_min: float | None = None
    esito: str | None = None  # ok | errore | saltato | interrotto_per_tetto
    interrotto_per_tetto: bool | None = None
    passi_non_ok: list[str] | None = None


class ControlloMonitoraggio(_Tollerante):
    avviato_at: datetime | None = None
    esito: str | None = None
    classificazioni: int | None = None
    classificazioni_fallite: int | None = None
    eventi_non_applicati: int | None = None


class LavorazioneMonitoraggio(_Tollerante):
    nome: str | None = None  # giro | controllo_pagine | ricerca_fonti | altro
    da_min: float | None = None
    ttl_min: float | None = None
    stato: str | None = None  # regolare | lunga | probabile_orfana


class IngressoMonitoraggio(_Tollerante):
    fermi_in_ingresso: int | None = None
    fermi_in_lavorazione: int | None = None
    ultimo_bando_nuovo_at: datetime | None = None


class EventiMonitoraggio(_Tollerante):
    ammessi_non_applicati: int | None = None
    proposte_7g: int | None = None
    in_attesa_pubblicazione: int | None = None


class JobOrarioMonitoraggio(_Tollerante):
    ultimo_avvio_at: datetime | None = None
    ultimo_esito: str | None = None  # succeeded | failed | non_misurato
    ultimo_ok_at: datetime | None = None
    falliti_24h: int | None = None


class RiepilogoMonitoraggio(_Tollerante):
    stato: str | None = None  # ok | attenzione | guasto
    segnali: list[SegnaleMonitoraggio] | None = None
    non_misurati: list[str] | None = None
    produttore: ProduttoreMonitoraggio | None = None
    giri: list[GiroMonitoraggio] | None = None
    controlli: list[ControlloMonitoraggio] | None = None
    lavorazioni: list[LavorazioneMonitoraggio] | None = None
    ingresso: IngressoMonitoraggio | None = None
    eventi: EventiMonitoraggio | None = None
    # null finché la verifica dello stato del catalogo non è attiva; poi un
    # oggetto di conteggi (§14.4), passato così com'è.
    da_verificare: dict[str, Any] | None = None
    job_orario: JobOrarioMonitoraggio | None = None


class BustaMonitoraggio(_Tollerante):
    versione: int | None = None
    generato_at: datetime | None = None
    calcolato_at: datetime | None = None
    aggiornato_at: datetime | None = None
    minuti_dal_calcolo: int | None = None
    in_ritardo: bool | None = None
    orologio_disallineato: bool | None = None
    riepilogo: RiepilogoMonitoraggio | None = None


class MonitoraggioCatalogoOut(BaseModel):
    stato_accesso: StatoAccesso
    # Quando il backend ha letto (o deciso di non chiamare); con la cache è
    # l'istante della lettura in cache.
    letto_at: datetime
    busta: BustaMonitoraggio | None = None  # solo con stato_accesso «ok»
