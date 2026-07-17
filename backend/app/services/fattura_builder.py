"""Costruttore del documento FatturaPA (JSON openapi) — funzione PURA.

openapi accetta la fattura come JSON strutturato (mappa i campi sul tracciato
FatturaPA). Qui si costruisce il dizionario dal purchase, dallo snapshot del
cliente e dai dati del cedente (settings). Nessun I/O: testabile a tavolino.

Regole fiscali codificate (verificate in ricerca, Fase 0):
- B2C (privato_it): CodiceDestinatario '0000000' + CF del cliente;
- azienda_it: codice destinatario a 7 char OPPURE PEC (destinatario '0000000');
- azienda_ue: CodiceDestinatario 'XXXXXXX', IdPaese estero, CAP '00000' e
  provincia 'EE', natura IVA N2.1 (reverse charge art. 7-ter), IVA a 0. La
  copia va inviata al cliente perché SDI non recapita all'estero.
"""

from decimal import Decimal


def _euro(cents: int) -> str:
    return f"{Decimal(cents) / 100:.2f}"


def _cedente(settings) -> dict:
    return {
        "DatiAnagrafici": {
            "IdFiscaleIVA": {"IdPaese": "IT", "IdCodice": settings.fattura_partita_iva},
            "CodiceFiscale": settings.fattura_codice_fiscale or None,
            "Anagrafica": {"Denominazione": settings.fattura_denominazione},
            "RegimeFiscale": settings.fattura_regime,
        },
        "Sede": {
            "Indirizzo": settings.fattura_sede_indirizzo,
            "CAP": settings.fattura_sede_cap,
            "Comune": settings.fattura_sede_comune,
            "Provincia": settings.fattura_sede_provincia or None,
            "Nazione": "IT",
        },
    }


def _cessionario(cliente: dict) -> tuple[dict, str]:
    """(blocco CessionarioCommittente, CodiceDestinatario)."""
    tipo = cliente.get("tipo_soggetto")
    if tipo == "azienda_ue":
        anagrafica = {
            "IdFiscaleIVA": {
                "IdPaese": cliente["paese"],
                "IdCodice": cliente.get("partita_iva"),
            },
            "Anagrafica": {"Denominazione": cliente.get("denominazione")},
        }
        sede = {
            "Indirizzo": cliente.get("indirizzo"),
            "CAP": "00000",
            "Comune": cliente.get("comune"),
            "Provincia": "EE",
            "Nazione": cliente["paese"],
        }
        return {"DatiAnagrafici": anagrafica, "Sede": sede}, "XXXXXXX"

    if tipo == "privato_it":
        anagrafica = {
            "CodiceFiscale": cliente.get("codice_fiscale"),
            "Anagrafica": {
                "Nome": cliente.get("nome"),
                "Cognome": cliente.get("cognome"),
            },
        }
        destinatario = "0000000"
    else:  # azienda_it
        anagrafica = {
            "IdFiscaleIVA": {"IdPaese": "IT", "IdCodice": cliente.get("partita_iva")},
            "CodiceFiscale": cliente.get("codice_fiscale") or None,
            "Anagrafica": {"Denominazione": cliente.get("denominazione")},
        }
        destinatario = (cliente.get("codice_destinatario") or "0000000")
        if destinatario == "0000000" and not cliente.get("pec"):
            destinatario = "0000000"
    sede = {
        "Indirizzo": cliente.get("indirizzo"),
        "CAP": cliente.get("cap"),
        "Comune": cliente.get("comune"),
        "Provincia": cliente.get("provincia") or None,
        "Nazione": "IT",
    }
    return {"DatiAnagrafici": anagrafica, "Sede": sede}, destinatario


def costruisci_fattura(
    *, settings, purchase: dict, cliente: dict, numero: int, serie: str, data_documento: str
) -> dict:
    """Documento FatturaPA (JSON openapi) per un purchase pagato."""
    cess, destinatario = _cessionario(cliente)

    dati_trasmissione = {
        "CodiceDestinatario": destinatario,
    }
    if cliente.get("tipo_soggetto") == "azienda_it" and destinatario == "0000000" and cliente.get("pec"):
        dati_trasmissione["PECDestinatario"] = cliente["pec"]

    imponibile = _euro(purchase["imponibile_cents"])
    imposta = _euro(purchase["iva_cents"])
    totale = _euro(purchase["totale_cents"])
    natura = purchase.get("natura_iva")
    aliquota = f"{Decimal(str(purchase['iva_aliquota'])):.2f}"

    riepilogo: dict = {
        "AliquotaIVA": aliquota,
        "ImponibileImporto": imponibile,
        "Imposta": imposta,
    }
    dettaglio_iva: dict = {}
    if natura:  # reverse charge: niente imposta, natura obbligatoria
        riepilogo["Natura"] = natura
        riepilogo["Imposta"] = "0.00"
        dettaglio_iva["Natura"] = natura
        riepilogo["RiferimentoNormativo"] = "Inversione contabile art. 7-ter DPR 633/72"

    return {
        "FatturaElettronicaHeader": {
            "DatiTrasmissione": dati_trasmissione,
            "CedentePrestatore": _cedente(settings),
            "CessionarioCommittente": cess,
        },
        "FatturaElettronicaBody": {
            "DatiGenerali": {
                "DatiGeneraliDocumento": {
                    "TipoDocumento": "TD01",
                    "Divisa": purchase.get("valuta", "EUR"),
                    "Data": data_documento,
                    "Numero": f"{serie}{numero}" if serie else str(numero),
                    "ImportoTotaleDocumento": totale,
                },
            },
            "DatiBeniServizi": {
                "DettaglioLinee": [{
                    "NumeroLinea": 1,
                    "Descrizione": purchase["descrizione"],
                    "Quantita": "1.00",
                    "PrezzoUnitario": imponibile,
                    "PrezzoTotale": imponibile,
                    "AliquotaIVA": aliquota,
                    **dettaglio_iva,
                }],
                "DatiRiepilogo": [riepilogo],
            },
        },
        # riferimento esterno per la riconciliazione anti doppia-trasmissione
        "external_reference": str(purchase["id"]),
    }
