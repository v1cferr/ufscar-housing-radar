"""Setup do banco: engine, criação de tabelas e sessões."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlparse

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

# Importa os modelos para que SQLModel.metadata os conheça ao criar tabelas.
from housing_radar import models  # noqa: F401
from housing_radar.config import get_settings

_engine = None


def _ensure_sqlite_dir(database_url: str) -> None:
    """Garante que o diretório do arquivo SQLite exista (ex.: data/)."""
    if not database_url.startswith("sqlite"):
        return
    # sqlite:///data/housing_radar.db -> path = data/housing_radar.db
    path = urlparse(database_url).path.lstrip("/")
    if path and path != ":memory:":
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _ensure_sqlite_dir(settings.database_url)
        connect_args = {"check_same_thread": False} if settings.is_sqlite else {}
        _engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)
    return _engine


# Colunas adicionadas após a criação inicial da tabela. SQLite não tem migrações;
# como create_all() não altera tabelas existentes, garantimos as colunas novas aqui.
# (coluna, tipo SQL). Idempotente: só adiciona o que faltar.
_SQLITE_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "listing": [
        ("rent_price", "FLOAT"),
        ("favorite", "BOOLEAN DEFAULT 0"),
        # Funil de decisão (V1C-68): o DEFAULT já faz o backfill das linhas
        # existentes (acervo atual = só venda de apto -> compra/apartamento).
        ("transacao", "VARCHAR DEFAULT 'compra'"),
        ("tipo_imovel", "VARCHAR DEFAULT 'apartamento'"),
        ("estrategia", "VARCHAR"),  # classificação manual; nasce nula
    ],
}


def _migrate_sqlite_columns(engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _SQLITE_ADDED_COLUMNS.items():
            if table not in existing_tables:
                continue
            have = {c["name"] for c in inspector.get_columns(table)}
            for name, sql_type in columns:
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))


def init_db() -> None:
    """Cria as tabelas se ainda não existirem e aplica micro-migrações de colunas."""
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    if get_settings().is_sqlite:
        _migrate_sqlite_columns(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    # expire_on_commit=False: os objetos seguem utilizáveis após o commit/close,
    # para que callers possam ler atributos fora do `with` (CLI, export, API).
    session = Session(get_engine(), expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
