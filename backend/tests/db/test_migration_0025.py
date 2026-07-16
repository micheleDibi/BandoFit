"""Test funzionali della migration 0025 (rate limiting auth durable).

Coprono: il conteggio a finestra di fn_consume_auth_rate_limit (consentito entro il
limite, negato oltre), il reset quando la finestra scade, l'indipendenza dei
bucket, i clamp difensivi sull'input, il GC delle finestre esaurite e la
sicurezza (RLS deny-all + revoche) — che qui conta doppio: una funzione di rate
limiting eseguibile da anon sarebbe un modo per bruciare il contatore altrui.
"""


def rate_limit(db, bucket: str, limit: int = 3, window: int = 3600) -> bool:
    return db.execute(
        "select public.fn_consume_auth_rate_limit(%s, %s, %s)", (bucket, limit, window)
    ).fetchone()[0]


def count_of(db, bucket: str) -> int | None:
    row = db.execute(
        "select count from public.auth_rate_limits where bucket = %s", (bucket,)
    ).fetchone()
    return row[0] if row else None


def age_window(db, bucket: str, seconds: int) -> None:
    """Invecchia la finestra di un bucket: è il modo per testare la scadenza
    senza far dormire il test."""
    db.execute(
        "update public.auth_rate_limits set window_start = now() - make_interval(secs => %s) "
        "where bucket = %s",
        (seconds, bucket),
    )


class TestConteggio:
    def test_prima_richiesta_consentita(self, db):
        assert rate_limit(db, "ip:aaa") is True
        assert count_of(db, "ip:aaa") == 1

    def test_entro_il_limite_consentito_oltre_negato(self, db):
        assert [rate_limit(db, "ip:bbb", limit=3) for _ in range(3)] == [True, True, True]
        assert rate_limit(db, "ip:bbb", limit=3) is False

    def test_oltre_il_limite_il_contatore_continua_a_salire(self, db):
        # Martellare non deve "riportare" nella finestra: chi supera il limite
        # resta fuori finché la finestra non scade davvero.
        for _ in range(6):
            rate_limit(db, "ip:ccc", limit=2)
        assert count_of(db, "ip:ccc") == 6
        assert rate_limit(db, "ip:ccc", limit=2) is False

    def test_bucket_indipendenti(self, db):
        for _ in range(3):
            rate_limit(db, "ip:ddd", limit=3)
        assert rate_limit(db, "ip:ddd", limit=3) is False
        # Un altro bucket non è toccato dal vicino esaurito.
        assert rate_limit(db, "email:ddd", limit=3) is True


class TestFinestra:
    def test_finestra_scaduta_resetta_il_contatore(self, db):
        for _ in range(3):
            rate_limit(db, "ip:eee", limit=3, window=3600)
        assert rate_limit(db, "ip:eee", limit=3, window=3600) is False

        age_window(db, "ip:eee", 3601)
        assert rate_limit(db, "ip:eee", limit=3, window=3600) is True
        assert count_of(db, "ip:eee") == 1

    def test_finestra_viva_non_si_sposta(self, db):
        rate_limit(db, "ip:fff", window=3600)
        first = db.execute(
            "select window_start from public.auth_rate_limits where bucket = 'ip:fff'"
        ).fetchone()[0]
        rate_limit(db, "ip:fff", window=3600)
        second = db.execute(
            "select window_start from public.auth_rate_limits where bucket = 'ip:fff'"
        ).fetchone()[0]
        assert first == second


class TestClampInput:
    def test_finestra_oltre_un_giorno_clampata(self, db):
        # 10 giorni richiesti → 1 giorno applicato: oltre il clamp il GC non
        # saprebbe più distinguere una finestra viva da una esaurita.
        rate_limit(db, "ip:ggg", limit=1, window=864000)
        age_window(db, "ip:ggg", 86401)
        assert rate_limit(db, "ip:ggg", limit=1, window=864000) is True

    def test_limite_zero_o_negativo_non_blocca_tutto(self, db):
        # greatest(1, …): un limite malformato non deve chiudere l'endpoint.
        assert rate_limit(db, "ip:hhh", limit=0) is True

    def test_parametri_null_usano_i_default(self, db):
        assert db.execute(
            "select public.fn_consume_auth_rate_limit('ip:iii', null, null)"
        ).fetchone()[0] is True


class TestGarbageCollection:
    def test_cancella_solo_le_finestre_esaurite(self, db):
        rate_limit(db, "ip:vecchio")
        rate_limit(db, "ip:recente")
        age_window(db, "ip:vecchio", 86401)

        deleted = db.execute("select public.fn_purge_auth_rate_limits(86400)").fetchone()[0]

        assert deleted == 1
        assert count_of(db, "ip:vecchio") is None
        assert count_of(db, "ip:recente") == 1

    def test_gc_su_tabella_pulita_non_esplode(self, db):
        assert db.execute("select public.fn_purge_auth_rate_limits(86400)").fetchone()[0] == 0


class TestRicercaProfiloPerEmail:
    """L'indice esatto su profiles.email regge la ricerca in registrazione.

    Il seq scan non è solo lento: con LIMIT 1 un indirizzo esistente può uscire
    subito e uno inesistente costringe a scorrere tutto, e quel divario di tempo
    è il bit che la risposta neutra nasconde.
    """

    def test_indice_esatto_presente(self, db):
        assert db.execute(
            "select 1 from pg_indexes where schemaname = 'public' "
            "and tablename = 'profiles' and indexname = 'profiles_email_exact_idx'"
        ).fetchone() is not None

    def test_il_confronto_esatto_usa_l_indice(self, db):
        # enable_seqscan off: su una tabella vuota il planner sceglierebbe
        # comunque il seq scan, e il test non direbbe nulla.
        db.execute("set enable_seqscan = off")
        plan = db.execute(
            "explain (format text) select id from public.profiles where email = 'mario@test.it' limit 1"
        ).fetchall()
        assert any("profiles_email_exact_idx" in str(riga) for riga in plan), plan

    def test_underscore_non_e_un_jolly(self, db):
        # Regressione: con ILIKE, «mario_rossi@test.it» matcherebbe
        # «marioXrossi@test.it» — cioè il profilo di un'altra persona, e
        # l'underscore negli indirizzi è comunissimo.
        db.execute(
            "insert into auth.users (id, email) values (gen_random_uuid(), 'marioXrossi@test.it')"
        )
        trovati = db.execute(
            "select email from public.profiles where email = %s", ("mario_rossi@test.it",)
        ).fetchall()
        assert trovati == []


class TestSicurezza:
    def test_rls_attiva_senza_policy(self, db):
        rls, policies = db.execute(
            "select c.relrowsecurity, (select count(*) from pg_policies p "
            "  where p.schemaname = 'public' and p.tablename = 'auth_rate_limits') "
            "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relname = 'auth_rate_limits'"
        ).fetchone()
        assert rls is True
        assert policies == 0  # deny-all: nessuna policy = nessun accesso

    def test_anon_e_authenticated_non_leggono_la_tabella(self, db):
        for role in ("anon", "authenticated"):
            for priv in ("select", "insert", "update", "delete"):
                assert (
                    db.execute(
                        "select has_table_privilege(%s, 'public.auth_rate_limits', %s)",
                        (role, priv),
                    ).fetchone()[0]
                    is False
                ), f"{role} non deve avere {priv} su auth_rate_limits"

    def test_anon_e_authenticated_non_eseguono_le_funzioni(self, db):
        # Supabase concede EXECUTE di default su ogni funzione di public: se la
        # revoke manca, chiunque può bruciare il contatore di chiunque.
        signatures = (
            "public.fn_consume_auth_rate_limit(text, integer, integer)",
            "public.fn_purge_auth_rate_limits(integer)",
        )
        for role in ("anon", "authenticated", "public"):
            for signature in signatures:
                assert (
                    db.execute(
                        "select has_function_privilege(%s, %s, 'execute')", (role, signature)
                    ).fetchone()[0]
                    is False
                ), f"{role} non deve poter eseguire {signature}"
