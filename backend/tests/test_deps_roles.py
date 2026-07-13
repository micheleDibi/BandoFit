"""Guardie di ruolo: la parità admin ↔ progettista vive in require_progettista
(l'area progettista è di entrambi); l'area admin resta solo degli admin."""

import pytest

from app.api.deps import require_admin, require_progettista
from app.core.errors import ForbiddenError


class TestRequireProgettista:
    async def test_accetta_progettista(self):
        user = {"id": "u1", "role": "progettista"}
        assert await require_progettista(user) is user

    async def test_accetta_admin(self):
        user = {"id": "u1", "role": "admin"}
        assert await require_progettista(user) is user

    async def test_rifiuta_cliente(self):
        with pytest.raises(ForbiddenError):
            await require_progettista({"id": "u1", "role": "cliente"})


class TestRequireAdmin:
    async def test_accetta_admin(self):
        user = {"id": "u1", "role": "admin"}
        assert await require_admin(user) is user

    async def test_rifiuta_progettista(self):
        """La parità è a senso unico: il progettista non entra nell'area admin."""
        with pytest.raises(ForbiddenError):
            await require_admin({"id": "u1", "role": "progettista"})
