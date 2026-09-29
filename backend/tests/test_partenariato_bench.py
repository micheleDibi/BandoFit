"""Benchmark sintetico del matching (docs/partenariati.md §10): 500 aziende
con opt-in e 200 call pubblicate, dati da `popola_sintetico` (seed fisso).
Obiettivi: ricarica dell'indice ≤ 20 query, ranking dei suggeriti per call
p95 < 50 ms, «Per te» per azienda p95 < 20 ms.

Saltato di default: si lancia con `BANDOFIT_BENCH=1 .venv/bin/pytest
tests/test_partenariato_bench.py -q -s` (i tempi dipendono dalla macchina).
"""

import os
import statistics
import time

import pytest

from app.services import partenariato_indice
from app.services import partenariato_matching as pm
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariato_indice import (  # noqa: F401 — fixture autouse
    FakePrimary,
    ambiente_wp6,
    popola_sintetico,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("BANDOFIT_BENCH") != "1", reason="benchmark: BANDOFIT_BENCH=1 per lanciarlo"
)


def _p95(campioni: list[float]) -> float:
    return statistics.quantiles(campioni, n=20)[-1]


async def test_benchmark_500_aziende_200_call():
    db, sec = popola_sintetico(FakePrimary(), n_aziende=500, n_call=200)
    inizio = time.perf_counter()
    idx = await partenariato_indice.indice(db, sec)
    ricarica_ms = (time.perf_counter() - inizio) * 1000
    assert idx.query <= 20, idx.query

    pesi = pm.PesiMatching()
    per_call = []
    for call_id in list(idx.matching.calls)[:100]:
        t = time.perf_counter()
        pm.suggeriti_per_call(idx.matching, call_id, oggi=g.OGGI, pesi=pesi)
        per_call.append((time.perf_counter() - t) * 1000)
    per_te = []
    for company_id in list(idx.matching.candidati)[:200]:
        t = time.perf_counter()
        pm.per_te(idx.matching, company_id, oggi=g.OGGI, pesi=pesi)
        per_te.append((time.perf_counter() - t) * 1000)

    print(f"\nricarica: {ricarica_ms:.0f} ms, {idx.query} query; "
          f"suggeriti p95 {_p95(per_call):.1f} ms; per te p95 {_p95(per_te):.1f} ms")
    assert _p95(per_call) < 50
    assert _p95(per_te) < 20
