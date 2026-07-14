"""Test del validatore telefono (E.164). I vettori sono CONDIVISI con il
gemello frontend `frontend/src/lib/telefono.ts`: se si aggiunge un caso qui,
aggiungerlo anche lì."""

from app.services.telefono import is_valid_telefono, normalize_telefono


class TestNormalizzazione:
    def test_mobile_italiano_senza_prefisso(self):
        assert normalize_telefono("347 1234567") == "+393471234567"

    def test_fisso_italiano_mantiene_lo_zero(self):
        # L'E.164 italiano CONSERVA lo zero dei fissi: 02… → +3902…
        assert normalize_telefono("02 5551234") == "+39025551234"

    def test_doppio_zero_diventa_piu(self):
        assert normalize_telefono("0039 347 1234567") == "+393471234567"

    def test_internazionale_con_piu(self):
        assert normalize_telefono("+44 20 7946 0958") == "+442079460958"

    def test_separatori_rimossi(self):
        assert normalize_telefono(" (347) 123-45.67/ ") == "+393471234567"


class TestValidazione:
    def test_validi(self):
        assert is_valid_telefono("+393471234567")
        assert is_valid_telefono("+39025551234")
        assert is_valid_telefono("+442079460958")
        assert is_valid_telefono("+390212345")  # fisso corto: 6 cifre nazionali

    def test_rifiuti(self):
        assert not is_valid_telefono("+39abc123")
        # Cifre Unicode (fullwidth/arabe): il \d di JavaScript le rifiuta,
        # quello di Python le accetterebbe senza re.ASCII — gemelli identici.
        assert not is_valid_telefono("+39３４７１２３４５６７")
        assert not is_valid_telefono("+39٣٤٧١٢٣٤٥٦٧")
        assert not is_valid_telefono("3471234567")  # non normalizzato
        assert not is_valid_telefono("+012345678")  # prefisso paese con 0
        assert not is_valid_telefono("+3912345")  # 5 cifre nazionali: troppo corto
        assert not is_valid_telefono("+39123456789012")  # 12 nazionali: troppo lungo
        assert not is_valid_telefono("+1234567890123456")  # oltre 15 cifre totali
        assert not is_valid_telefono("")


class TestEndToEnd:
    def test_input_utente_tipico(self):
        for raw, atteso in [
            ("347 1234567", "+393471234567"),
            ("02 5551234", "+39025551234"),
            ("0039 347 1234567", "+393471234567"),
            ("+44 20 7946 0958", "+442079460958"),
        ]:
            normalized = normalize_telefono(raw)
            assert normalized == atteso
            assert is_valid_telefono(normalized)

    def test_input_invalido_dopo_normalizzazione(self):
        for raw in ["abc", "12345", "telefono"]:
            assert not is_valid_telefono(normalize_telefono(raw))
