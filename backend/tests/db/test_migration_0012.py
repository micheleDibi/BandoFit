"""Test funzionale della migration 0012 (rimozione della visura camerale).

Ogni test riceve un database fresco clonato dal template con le migration applicate.
"""


class TestVisuraRimossa:
    def test_tabella_company_documents_sparita(self, db):
        """La 0012 è distruttiva: la tabella non deve esistere più."""
        assert db.execute("select to_regclass('public.company_documents')").fetchone()[0] is None

    def test_il_dossier_certificato_resta(self, db):
        """Ciò che l'utente vede (anagrafica, sedi, persone) non veniva dal PDF."""
        superstiti = db.execute(
            """select count(*) from pg_class
               where relname in ('company_data', 'company_people')"""
        ).fetchone()[0]
        assert superstiti == 2
