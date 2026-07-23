"""Test del resolver dell'azienda attiva (`deps.active_company`).

Nuovo comportamento della fase 1 multi-azienda: senza header si usa l'azienda
viva più vecchia del titolare (l'unica, per i non-Advisor); l'header
`X-Active-Company` è onorato solo se punta a un'azienda VIVA del titolare
corrente (mai di un altro owner, mai cancellata/archiviata).
"""

from types import SimpleNamespace

import pytest

from app.api.deps import ActiveCompany, active_company
from app.core.errors import NotFoundError

OWNER = "a0000000-0000-0000-0000-000000000001"
COMPANY = "c0000000-0000-0000-0000-000000000001"
OTHER_COMPANY = "c0000000-0000-0000-0000-0000000000ff"


class FakeQuery:
    def __init__(self, primary, table):
        self._primary = primary
        self._table = table
        self.filters: dict = {}

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def is_(self, col, val):
        self.filters[f"{col}__is"] = val
        return self

    def in_(self, col, vals):
        self.filters[f"{col}__in"] = vals
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a):
        return self

    async def execute(self):
        rows = self._primary.resolve(self._table, self.filters)
        return SimpleNamespace(data=rows)


class FakePrimary:
    """`company_rows` = righe company_profiles (con parent_id/deleted_at/
    archived_at); il fake applica i filtri rilevanti del resolver.
    `access` = id azienda visibili al membro (0031)."""

    def __init__(self, company_rows: list[dict], membership: dict | None = None,
                 max_aziende: int = 1, access: list[str] | None = None):
        self.company_rows = company_rows
        self.membership = membership
        self.max_aziende = max_aziende
        self.access = access or []

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, name, params):
        primary = self

        class _Rpc:
            async def execute(self_inner):
                data = primary.max_aziende if name == "fn_effective_max_aziende" else None
                return SimpleNamespace(data=data)

        return _Rpc()

    def resolve(self, table, filters):
        if table == "family_members":
            return [self.membership] if self.membership else []
        if table == "family_member_company_access":
            rows = []
            for cid in self.access:
                company = next((r for r in self.company_rows if r["id"] == cid), None)
                embed = {**company, "ragione_sociale": "ACME", "partita_iva": "0" * 11,
                         "created_at": company.get("created_at") or "2026-01-01"} if company else None
                rows.append({"company_profile_id": cid, "company_profiles": embed})
            return rows
        if table == "company_profiles":
            rows = []
            for r in self.company_rows:
                if "id" in filters and r["id"] != filters["id"]:
                    continue
                if "parent_id" in filters and r["parent_id"] != filters["parent_id"]:
                    continue
                if filters.get("deleted_at__is") == "null" and r.get("deleted_at") is not None:
                    continue
                if filters.get("archived_at__is") == "null" and r.get("archived_at") is not None:
                    continue
                rows.append({"id": r["id"]})
            return rows
        return []


def _request(header: str | None = None):
    headers = {"X-Active-Company": header} if header is not None else {}
    return SimpleNamespace(headers=headers)


def _company(company_id=COMPANY, parent_id=OWNER, deleted_at=None, archived_at=None):
    return {"id": company_id, "parent_id": parent_id,
            "deleted_at": deleted_at, "archived_at": archived_at}


USER = {"id": OWNER}


def _membership(company: str | None = None):
    return {"id": "m0000000-0000-0000-0000-000000000001", "status": "active",
            "parent_id": "p-parent", "company_profile_id": company}


class TestDefault:
    async def test_azienda_unica_del_titolare(self):
        primary = FakePrimary([_company()])
        active = await active_company(_request(), USER, primary)
        assert isinstance(active, ActiveCompany)
        assert active.company_id == COMPANY
        assert active.owner_id == OWNER
        assert active.editable is True

    async def test_senza_azienda_company_id_none(self):
        active = await active_company(_request(), USER, FakePrimary([]))
        assert active.company_id is None and active.owner_id == OWNER

    async def test_figlio_attivo_eredita_owner_e_sola_lettura(self):
        # Membro attivo: owner = padre, sola lettura; default = APPARTENENZA
        # (0031), risolta dentro l'insieme visibilità ∩ vive.
        primary = FakePrimary(
            [_company(parent_id="p-parent")],
            membership=_membership(company=COMPANY),
            access=[COMPANY],
        )
        active = await active_company(_request(), {"id": "child"}, primary)
        assert active.owner_id == "p-parent"
        assert active.editable is False
        assert active.company_id == COMPANY

    async def test_figlio_header_fuori_visibilita_404(self):
        # L'azienda esiste ed è del padre, ma NON è stata concessa: 404.
        primary = FakePrimary(
            [_company(parent_id="p-parent"),
             _company(company_id=OTHER_COMPANY, parent_id="p-parent")],
            membership=_membership(company=COMPANY),
            access=[COMPANY],
        )
        with pytest.raises(NotFoundError):
            await active_company(_request(OTHER_COMPANY), {"id": "child"}, primary)

    async def test_figlio_header_dentro_visibilita_ok(self):
        primary = FakePrimary(
            [_company(parent_id="p-parent"),
             _company(company_id=OTHER_COMPANY, parent_id="p-parent")],
            membership=_membership(company=COMPANY),
            access=[COMPANY, OTHER_COMPANY],
        )
        active = await active_company(_request(OTHER_COMPANY), {"id": "child"}, primary)
        assert active.company_id == OTHER_COMPANY

    async def test_figlio_appartenenza_archiviata_fallback(self):
        # La sua azienda è stata archiviata (reconcile): default = la più
        # vecchia VISIBILE viva — mai bloccato.
        primary = FakePrimary(
            [_company(parent_id="p-parent", archived_at="2026-07-01T00:00:00+00:00"),
             _company(company_id=OTHER_COMPANY, parent_id="p-parent")],
            membership=_membership(company=COMPANY),
            access=[COMPANY, OTHER_COMPANY],
        )
        active = await active_company(_request(), {"id": "child"}, primary)
        assert active.company_id == OTHER_COMPANY

    async def test_figlio_senza_visibilita_company_none(self):
        primary = FakePrimary(
            [_company(parent_id="p-parent")],
            membership=_membership(company=None),
            access=[],
        )
        active = await active_company(_request(), {"id": "child"}, primary)
        assert active.company_id is None

    async def test_esclude_cancellata_e_archiviata(self):
        primary = FakePrimary([
            _company(deleted_at="2026-07-01T00:00:00+00:00"),
            _company(company_id="c-arch", archived_at="2026-07-01T00:00:00+00:00"),
        ])
        active = await active_company(_request(), USER, primary)
        assert active.company_id is None

    async def test_is_multi_dal_limite_di_piano(self):
        # limite 1 (default) → non-Advisor, is_multi False
        base = FakePrimary([_company()])
        assert (await active_company(_request(), USER, base)).is_multi is False
        # limite > 1 → Advisor, is_multi True
        advisor = FakePrimary([_company()], max_aziende=10)
        assert (await active_company(_request(), USER, advisor)).is_multi is True


class TestHeader:
    async def test_header_valido_onorato(self):
        primary = FakePrimary([
            _company(company_id=COMPANY),
            _company(company_id=OTHER_COMPANY),
        ])
        active = await active_company(_request(OTHER_COMPANY), USER, primary)
        assert active.company_id == OTHER_COMPANY

    async def test_header_di_altro_owner_respinto(self):
        primary = FakePrimary([_company(company_id=OTHER_COMPANY, parent_id="altro-owner")])
        with pytest.raises(NotFoundError):
            await active_company(_request(OTHER_COMPANY), USER, primary)

    async def test_header_cancellata_respinta(self):
        primary = FakePrimary([
            _company(company_id=OTHER_COMPANY, deleted_at="2026-07-01T00:00:00+00:00"),
        ])
        with pytest.raises(NotFoundError):
            await active_company(_request(OTHER_COMPANY), USER, primary)

    async def test_header_malformato_e_un_404(self):
        with pytest.raises(NotFoundError):
            await active_company(_request("non-un-uuid"), USER, FakePrimary([_company()]))

    async def test_header_vuoto_ignorato_usa_default(self):
        primary = FakePrimary([_company()])
        active = await active_company(_request(""), USER, primary)
        assert active.company_id == COMPANY
