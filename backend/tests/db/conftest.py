"""Harness per i test delle migration: avvia un cluster PostgreSQL usa-e-getta
(initdb in una dir temporanea), applica le migration in un database template e
clona il template per ogni test (veloce e isolato).

Si salta l'intera suite se i binari di Postgres non sono disponibili.
"""

import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"

PG_BIN_CANDIDATES = [
    "",  # PATH
    "/opt/homebrew/opt/postgresql@17/bin",
    "/opt/homebrew/opt/postgresql@16/bin",
    "/usr/local/opt/postgresql@17/bin",
    "/usr/lib/postgresql/17/bin",
    "/usr/lib/postgresql/16/bin",
]

AUTH_STUB = """
create role anon nologin;
create role authenticated nologin;
create schema auth;
create table auth.users (
  id uuid primary key,
  email text,
  raw_user_meta_data jsonb default '{}'::jsonb,
  email_confirmed_at timestamptz,
  created_at timestamptz default now()
);
"""


def _find_pg_bin() -> str | None:
    for prefix in PG_BIN_CANDIDATES:
        initdb = os.path.join(prefix, "initdb") if prefix else shutil.which("initdb")
        if initdb and os.path.exists(initdb):
            return os.path.dirname(initdb)
    return None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def pg_cluster():
    pg_bin = _find_pg_bin()
    if pg_bin is None:
        pytest.skip("Binari PostgreSQL non trovati: test delle migration saltati")

    datadir = tempfile.mkdtemp(prefix="bandofit_pgtest_")
    port = _free_port()
    subprocess.run(
        [os.path.join(pg_bin, "initdb"), "-D", datadir, "-U", "postgres",
         "--auth=trust", "-E", "UTF8"],
        check=True, capture_output=True,
    )
    subprocess.run(
        [os.path.join(pg_bin, "pg_ctl"), "-D", datadir,
         "-o", f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories='' -c fsync=off",
         "-l", os.path.join(datadir, "log.txt"), "-w", "start"],
        check=True, capture_output=True,
    )
    try:
        yield {"port": port, "host": "127.0.0.1", "user": "postgres"}
    finally:
        subprocess.run(
            [os.path.join(pg_bin, "pg_ctl"), "-D", datadir, "-m", "immediate", "stop"],
            capture_output=True,
        )
        shutil.rmtree(datadir, ignore_errors=True)


def _dsn(cluster: dict, dbname: str) -> str:
    return f"host={cluster['host']} port={cluster['port']} user={cluster['user']} dbname={dbname}"


@pytest.fixture(scope="session")
def pg_template(pg_cluster):
    """Database template con stub auth + tutte le migration applicate in ordine."""
    with psycopg.connect(_dsn(pg_cluster, "postgres"), autocommit=True) as conn:
        conn.execute("create database bandofit_tmpl")
    with psycopg.connect(_dsn(pg_cluster, "bandofit_tmpl"), autocommit=True) as conn:
        conn.execute(AUTH_STUB)
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(migration.read_text(encoding="utf-8"))
    return "bandofit_tmpl"


_counter = 0


@pytest.fixture()
def db(pg_cluster, pg_template):
    """Connessione a un database fresco clonato dal template (uno per test)."""
    global _counter
    _counter += 1
    dbname = f"bandofit_t{_counter}"
    with psycopg.connect(_dsn(pg_cluster, "postgres"), autocommit=True) as admin:
        admin.execute(f"create database {dbname} template {pg_template}")
    conn = psycopg.connect(_dsn(pg_cluster, dbname), autocommit=True)
    try:
        yield conn
    finally:
        conn.close()
        with psycopg.connect(_dsn(pg_cluster, "postgres"), autocommit=True) as admin:
            admin.execute(f"drop database {dbname} (force)")
