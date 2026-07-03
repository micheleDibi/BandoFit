"""Test del token service: hashing, validazione formato e query PostgREST
(builder reale, nessuna rete — stesso pattern dei test sui filtri bandi)."""

import secrets

from app.services.token_service import TTL, hash_token, is_well_formed


class TestHashing:
    def test_hash_deterministico_e_sha256(self):
        token = "abc123_-XYZ" * 4
        assert hash_token(token) == hash_token(token)
        assert len(hash_token(token)) == 64  # sha256 esadecimale
        assert hash_token(token) != hash_token(token + "x")

    def test_token_urlsafe_e_ben_formato(self):
        for _ in range(20):
            assert is_well_formed(secrets.token_urlsafe(32))

    def test_formati_rifiutati(self):
        assert not is_well_formed("")
        assert not is_well_formed("corto")
        assert not is_well_formed("x" * 200)  # troppo lungo
        assert not is_well_formed("token con spazi e lunghezza sufficiente!!")
        assert not is_well_formed("a" * 43 + ";")  # carattere fuori alfabeto

    def test_ttl_recovery_piu_corto(self):
        assert TTL["recovery"] < TTL["confirm_email"]
        assert TTL["recovery"] < TTL["invite"]
