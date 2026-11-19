"""Conexão SQLite compartilhada entre threads, com migrações versionadas.

A conexão é única e protegida por lock: o motor grava o histórico na thread dele e a interface
lê na principal. O volume é baixo (eventos), então um lock simples é mais seguro que um pool.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_PROFILE_NAME = "Padrão"

# Cada item é uma migração; a posição + 1 é o user_version após aplicá-la.
# Nunca edite uma migração já publicada: acrescente uma nova.
_MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE profiles (
        id         INTEGER PRIMARY KEY,
        name       TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE rules (
        id               INTEGER PRIMARY KEY,
        profile_id       INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        name             TEXT NOT NULL,
        status           TEXT NOT NULL CHECK (status IN ('active', 'paused', 'disabled')),
        config           TEXT NOT NULL,
        position         INTEGER NOT NULL DEFAULT 0,
        detections_total INTEGER NOT NULL DEFAULT 0,
        clicks_total     INTEGER NOT NULL DEFAULT 0,
        created_at       TEXT NOT NULL,
        updated_at       TEXT NOT NULL
    );
    CREATE INDEX idx_rules_profile ON rules(profile_id, position);
    CREATE TABLE activity_log (
        id        INTEGER PRIMARY KEY,
        ts        TEXT NOT NULL,
        level     TEXT NOT NULL,
        kind      TEXT NOT NULL,
        rule_id   INTEGER,
        message   TEXT NOT NULL
    );
    CREATE INDEX idx_activity_ts ON activity_log(id DESC);
    """,
    # 2: zonas de observação (Fase 2). Coordenadas em pixels físicos do desktop virtual.
    """
    CREATE TABLE zones (
        id          INTEGER PRIMARY KEY,
        profile_id  INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        name        TEXT NOT NULL,
        color       TEXT NOT NULL,
        x           INTEGER NOT NULL,
        y           INTEGER NOT NULL,
        width       INTEGER NOT NULL CHECK (width > 0),
        height      INTEGER NOT NULL CHECK (height > 0),
        monitor     TEXT NOT NULL,
        interval_ms INTEGER NOT NULL CHECK (interval_ms BETWEEN 20 AND 60000),
        enabled     INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
        position    INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );
    CREATE INDEX idx_zones_profile ON zones(profile_id, position);
    """,
    # 3: último ajuste do "Testar cor" por zona (Fase 3); ponto de partida da automação (Fase 4).
    """
    ALTER TABLE zones ADD COLUMN color_draft TEXT;
    """,
    # 4: regras ligadas a zonas (Fase 4). Excluir a zona NÃO apaga a regra: ela fica "sem zona".
    """
    ALTER TABLE rules ADD COLUMN zone_id INTEGER REFERENCES zones(id) ON DELETE SET NULL;
    ALTER TABLE rules ADD COLUMN executions_total INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE rules ADD COLUMN last_fired_at TEXT;
    CREATE INDEX idx_rules_zone ON rules(zone_id);
    """,
)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Database:
    def __init__(self, path: Path | str) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=3000")
        self._migrate()
        self._ensure_default_profile()

    @property
    def schema_version(self) -> int:
        return int(self.query_one("PRAGMA user_version")[0])

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        if row is None:
            raise LookupError(f"Consulta sem resultado: {sql}")
        return row

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _migrate(self) -> None:
        current = self.schema_version
        if current > len(_MIGRATIONS):
            raise RuntimeError(
                f"Banco de dados de uma versão mais nova (schema {current}). Atualize o aplicativo."
            )
        for version, script in enumerate(_MIGRATIONS[current:], start=current + 1):
            with self._lock:
                # executescript faz COMMIT implícito; por isso o BEGIN fica dentro do script.
                self._conn.executescript(
                    f"BEGIN IMMEDIATE;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;"
                )

    def _ensure_default_profile(self) -> None:
        if self.query_one("SELECT COUNT(*) FROM profiles")[0] == 0:
            now = utc_now_iso()
            self.execute(
                "INSERT INTO profiles (name, created_at, updated_at) VALUES (?, ?, ?)",
                (DEFAULT_PROFILE_NAME, now, now),
            )
