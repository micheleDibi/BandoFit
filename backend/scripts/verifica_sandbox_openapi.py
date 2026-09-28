"""Verifica in SANDBOX dei prodotti openapi usati dai bilanci (piano partenariati, gate G1).

Uso (dalla radice del repo, con le chiavi di TEST esportate nella shell):

    export OPENAPI_SANDBOX_EMAIL=...        # email dell'account openapi
    export OPENAPI_SANDBOX_API_KEY=...      # API key di TEST (non quella di produzione)
    backend/.venv/bin/python backend/scripts/verifica_sandbox_openapi.py [--bilancio] [--piva ...]

Cosa fa:
- chiama IT-full e IT-advanced sulle P.IVA di test (default: esempi ufficiali della sandbox);
- chiama Visure Camerali `/impresa`; con `--bilancio` richiede anche un bilancio ottico
  (asincrono: attende fino a 20 minuti) e ispeziona lo ZIP degli allegati;
- salva le risposte ANONIMIZZATE in `backend/tests/fixtures/openapi/sandbox/`;
- stampa un riepilogo dei path che servono al mapping (ecofin, operatingResults, voci CEE,
  balanceSheets, forma giuridica, stati del bilancio ottico).

Gli host sono SOLO quelli di test (`test.*`): lo script non può spendere credito reale.
Chiavi e token non vengono mai stampati.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx

OAUTH = "https://test.oauth.openapi.it"
COMPANY = "https://test.company.openapi.com"
VISURE = "https://test.visurecamerali.openapi.it"

SCOPE_COMPANY = [
    "GET:test.company.openapi.com/IT-full",
    "GET:test.company.openapi.com/IT-check_id",
    "GET:test.company.openapi.com/IT-advanced",
]
SCOPE_VISURE = [
    "POST:test.visurecamerali.openapi.it/bilancio-ottico",
    "GET:test.visurecamerali.openapi.it/bilancio-ottico",
    "GET:test.visurecamerali.openapi.it/impresa",
]

# Esempi ufficiali della sandbox Company (docs.openapi.it/company-sandbox-examples.html).
PIVA_DEFAULT = ["12485671007", "02590530347", "02591680216"]

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "openapi" / "sandbox"

# Chiavi con dati personali o di contatto: il valore viene sostituito da un segnaposto.
_CHIAVI_PERSONALI = {
    "name", "surname", "birthDate", "birthTown", "age", "telephoneNumber", "fax",
    "email", "pec", "website", "streetName", "owner",
}
_CF_PERSONA = re.compile(r"^[A-Z]{6}[0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]$")


def _anonimizza(nodo: Any) -> Any:
    """Copia del payload senza dati di persone fisiche né contatti. Struttura, codici e
    importi restano intatti (servono al mapping)."""
    if isinstance(nodo, dict):
        out = {}
        for chiave, valore in nodo.items():
            if chiave in _CHIAVI_PERSONALI and valore not in (None, "", [], {}):
                out[chiave] = f"<{chiave}>"
            elif chiave == "taxCode" and isinstance(valore, str) and _CF_PERSONA.match(valore):
                out[chiave] = "<cf-persona>"
            else:
                out[chiave] = _anonimizza(valore)
        return out
    if isinstance(nodo, list):
        return [_anonimizza(v) for v in nodo]
    return nodo


def _mint(http: httpx.Client, email: str, api_key: str, scopes: list[str]) -> str:
    resp = http.post(f"{OAUTH}/token", auth=(email, api_key), json={"scopes": scopes, "ttl": 3600})
    body = resp.json()
    token = body.get("token")
    if not body.get("success") or not token:
        raise SystemExit(
            f"Mint del token fallito (HTTP {resp.status_code}): {body.get('message')!r} "
            f"error={body.get('error')!r}. Controlla sandbox attiva, credito virtuale e API attivate."
        )
    return token


def _get(http: httpx.Client, token: str, url: str) -> tuple[int, dict]:
    resp = http.get(url, headers={"Authorization": f"Bearer {token}"}, follow_redirects=False)
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {"_non_json": resp.text[:200]}


def _salva(nome: str, dati: Any) -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    percorso = FIXTURE_DIR / nome
    percorso.write_text(json.dumps(_anonimizza(dati), ensure_ascii=False, indent=2) + "\n")
    return percorso


def _blocchi_cee(dati: dict) -> dict[str, list[str]]:
    """Blocchi di primo livello che sono liste di voci {code, value}."""
    out: dict[str, list[str]] = {}
    for chiave, valore in dati.items():
        if (
            isinstance(valore, list)
            and valore
            and all(isinstance(v, dict) and "code" in v and "value" in v for v in valore)
        ):
            out[chiave] = sorted(str(v["code"]) for v in valore)
    return out


def verifica_it_full(http: httpx.Client, token: str, piva: str) -> None:
    status, body = _get(http, token, f"{COMPANY}/IT-full/{piva}")
    inizio = time.monotonic()
    while isinstance(body.get("data"), dict) and str(body["data"].get("state", "")).upper() in {
        "PENDING", "IN_PROGRESS", "RUNNING",
    }:
        if time.monotonic() - inizio > 120:
            print(f"  IT-full {piva}: ancora PENDING dopo 120 s")
            return
        time.sleep(3)
        status, body = _get(http, token, f"{COMPANY}/IT-check_id/{body['data']['id']}")
    print(f"  IT-full {piva}: HTTP {status} success={body.get('success')} error={body.get('error')}")
    dati = body.get("data")
    if not isinstance(dati, dict):
        return
    print(f"    salvata in {_salva(f'it_full_{piva}.json', body).name}")
    print(f"    legalForm: {json.dumps(dati.get('legalForm'), ensure_ascii=False)}")
    print(f"    ecofin: {json.dumps(dati.get('ecofin'), ensure_ascii=False)}")
    print(f"    operatingResults: {json.dumps(dati.get('operatingResults'), ensure_ascii=False)}")
    print(f"    balanceSheets presente: {'balanceSheets' in dati}")
    for blocco, codici in _blocchi_cee(dati).items():
        print(f"    CEE {blocco}: {', '.join(codici)}")


def verifica_it_advanced(http: httpx.Client, token: str, piva: str) -> None:
    status, body = _get(http, token, f"{COMPANY}/IT-advanced/{piva}")
    print(f"  IT-advanced {piva}: HTTP {status} success={body.get('success')} error={body.get('error')}")
    dati = body.get("data")
    if not dati:
        return
    print(f"    salvata in {_salva(f'it_advanced_{piva}.json', body).name}")
    primo = dati[0] if isinstance(dati, list) else dati
    print(f"    data è una lista: {isinstance(dati, list)}; chiavi: {sorted(primo)[:40]}")
    identita = {k: primo.get(k) for k in ("vatCode", "taxCode", "id")}
    print(f"    identificativi di primo livello: {identita}")
    bs = primo.get("balanceSheets") or {}
    print(f"    balanceSheets.last: {json.dumps(bs.get('last'), ensure_ascii=False)}")
    for riga in bs.get("all") or []:
        pieni = sorted(k for k, v in riga.items() if v is not None)
        print(f"    all[{riga.get('year')}]: campi valorizzati {pieni}")


def verifica_impresa(http: httpx.Client, token: str, piva: str) -> None:
    status, body = _get(http, token, f"{VISURE}/impresa/{piva}")
    print(f"  /impresa {piva}: HTTP {status} success={body.get('success')} error={body.get('error')}")
    if body.get("data"):
        _salva(f"visure_impresa_{piva}.json", body)
        for voce in body["data"] if isinstance(body["data"], list) else [body["data"]]:
            print(
                f"    natura giuridica={voce.get('codice_natura_giuridica')!r} "
                f"bilancio-ottico disponibile="
                f"{any('bilancio-ottico' in c for c in voce.get('chiamate_disponibili') or [])}"
            )


class _SenzaDtd(ET.TreeBuilder):
    def doctype(self, name, pubid, system):  # noqa: D401 - firma imposta da ElementTree
        raise ValueError("DTD non ammessa")


def _ispeziona_xbrl(dati: bytes) -> None:
    parser = ET.XMLParser(target=_SenzaDtd())
    parser.feed(dati)
    radice = parser.close()
    schema = [
        el.get("{http://www.w3.org/1999/xlink}href")
        for el in radice
        if el.tag.endswith("}schemaRef")
    ]
    namespace = sorted({el.tag.split("}")[0].lstrip("{") for el in radice if "}" in el.tag})
    print(f"      schemaRef: {schema}")
    print(f"      namespace dei fatti: {namespace}")
    print(f"      figli diretti della radice: {len(radice)}")


def verifica_bilancio_ottico(http: httpx.Client, token: str, piva: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    resp = http.post(f"{VISURE}/bilancio-ottico", headers=headers, json={"cf_piva_id": piva})
    body = resp.json()
    print(f"  POST bilancio-ottico {piva}: HTTP {resp.status_code} error={body.get('error')}")
    _salva(f"bilancio_ottico_post_{piva}.json", body)
    richiesta = body.get("data") or {}
    rid = richiesta.get("id")
    if not rid:
        return
    stati_visti = [richiesta.get("stato_richiesta")]
    inizio = time.monotonic()
    while time.monotonic() - inizio < 20 * 60:
        time.sleep(20)
        status, body = _get(http, token, f"{VISURE}/bilancio-ottico/{rid}")
        stato = (body.get("data") or {}).get("stato_richiesta")
        if stato != stati_visti[-1]:
            stati_visti.append(stato)
            print(f"    stato: {stato}")
        if stato and stato.lower() in {"dati disponibili", "dati disponbili", "visura evasa", "annullata"}:
            break
    _salva(f"bilancio_ottico_stato_{piva}.json", body)
    print(f"    sequenza stati: {stati_visti}")
    status, body = _get(http, token, f"{VISURE}/bilancio-ottico/{rid}/allegati")
    print(f"    allegati: HTTP {status} error={body.get('error')}")
    allegato = body.get("data") or {}
    contenuto = allegato.get("file") if isinstance(allegato, dict) else None
    if not contenuto:
        return
    forma = {k: (v if k != "file" else f"<base64 {len(v)} caratteri>") for k, v in allegato.items()}
    _salva(f"bilancio_ottico_allegati_forma_{piva}.json", {"data": forma})
    archivio = zipfile.ZipFile(io.BytesIO(base64.b64decode(contenuto)))
    for info in archivio.infolist():
        print(f"      membro: {info.filename} ({info.file_size} byte, compresso {info.compress_size})")
        if info.filename.lower().endswith((".xbrl", ".xml")) and info.file_size < 20_000_000:
            _ispeziona_xbrl(archivio.read(info))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--piva", nargs="*", default=PIVA_DEFAULT)
    parser.add_argument("--bilancio", action="store_true", help="richiede anche un bilancio ottico")
    args = parser.parse_args()

    email = os.environ.get("OPENAPI_SANDBOX_EMAIL", "")
    api_key = os.environ.get("OPENAPI_SANDBOX_API_KEY", "")
    if not email or not api_key:
        print("Esporta OPENAPI_SANDBOX_EMAIL e OPENAPI_SANDBOX_API_KEY (chiave di TEST).")
        return 2

    with httpx.Client(timeout=40) as http:
        token_company = _mint(http, email, api_key, SCOPE_COMPANY)
        print("Company (IT-full, IT-advanced):")
        for piva in args.piva:
            verifica_it_full(http, token_company, piva)
            verifica_it_advanced(http, token_company, piva)
        try:
            token_visure = _mint(http, email, api_key, SCOPE_VISURE)
        except SystemExit as exc:
            print(f"Visure Camerali non disponibile: {exc}")
            return 1
        print("Visure Camerali:")
        for piva in args.piva:
            verifica_impresa(http, token_visure, piva)
        if args.bilancio:
            verifica_bilancio_ottico(http, token_visure, args.piva[0])
    print(f"Fixture anonimizzate in {FIXTURE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
