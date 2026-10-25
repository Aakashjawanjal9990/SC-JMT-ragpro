"""Postgres connections for the source database (Cloud SQL) and the BM25 database
(AlloyDB), via the Google connectors or a direct host/unix socket (Auth Proxy, local).

Passwords are never logged or included in reprs.
"""

import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy
from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

_QUERY_CANCELED = "57014"  # Postgres SQLSTATE for statement_timeout


@dataclass(frozen=True)
class DatabaseConfig:
    name: str | None = None
    user: str | None = None
    password: str | None = field(default=None, repr=False)
    # Exactly one way to reach the server:
    instance_connection_name: str | None = None  # Cloud SQL "project:region:instance"
    instance_uri: str | None = None  # AlloyDB "projects/P/locations/R/clusters/C/instances/I"
    host: str | None = None  # TCP host (e.g. Auth Proxy on 127.0.0.1) or a unix socket dir
    port: int = 5432
    ip_type: str = "public"  # connector IP type: public | private | psc
    iam_auth: bool = False
    pool_size: int = 5
    statement_timeout_ms: int = 5000

    @property
    def configured(self) -> bool:
        return bool(self.name and (self.instance_connection_name or self.instance_uri or self.host))

    @classmethod
    def from_environment(cls, prefix: str = "") -> "DatabaseConfig":
        """Read {prefix}DB_NAME, {prefix}DB_USER, {prefix}DB_PASSWORD,
        {prefix}INSTANCE_CONNECTION_NAME, {prefix}INSTANCE_URI, {prefix}DB_HOST,
        {prefix}DB_PORT, {prefix}DB_IP_TYPE, {prefix}DB_IAM_AUTH, {prefix}DB_POOL_SIZE,
        {prefix}DB_STATEMENT_TIMEOUT_MS."""

        def get(name: str) -> str | None:
            value = os.getenv(prefix + name)
            return value.strip() if value and value.strip() else None

        defaults = cls()
        return cls(
            name=get("DB_NAME"),
            user=get("DB_USER"),
            # Not stripped: whitespace can be part of a password.
            password=os.getenv(prefix + "DB_PASSWORD") or None,
            instance_connection_name=get("INSTANCE_CONNECTION_NAME"),
            instance_uri=get("INSTANCE_URI"),
            host=get("DB_HOST"),
            port=int(get("DB_PORT") or defaults.port),
            ip_type=(get("DB_IP_TYPE") or defaults.ip_type).lower(),
            iam_auth=(get("DB_IAM_AUTH") or "").lower() in {"1", "true", "yes", "on"},
            pool_size=int(get("DB_POOL_SIZE") or defaults.pool_size),
            statement_timeout_ms=int(
                get("DB_STATEMENT_TIMEOUT_MS") or defaults.statement_timeout_ms
            ),
        )


def _is_timeout(exc: BaseException) -> bool:
    original = getattr(exc, "orig", exc)
    if isinstance(original, TimeoutError):
        return True
    details = original.args[0] if getattr(original, "args", None) else None
    return isinstance(details, Mapping) and details.get("C") == _QUERY_CANCELED


class Database:
    """Pooled SQLAlchemy engine plus the connector that backs it, if any."""

    def __init__(self, config: DatabaseConfig, label: str = "database") -> None:
        if not config.configured:
            raise ValueError(f"The {label} connection is not configured")
        self.config = config
        self.label = label
        self._connector = None
        self.engine: Engine = sqlalchemy.create_engine(
            "postgresql+pg8000://",
            creator=self._creator(),
            pool_size=config.pool_size,
            max_overflow=2,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        timeout_ms = int(config.statement_timeout_ms)

        @event.listens_for(self.engine, "connect")
        def _set_session(dbapi_connection, _record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute(f"SET statement_timeout = {timeout_ms}")
            cursor.close()
            dbapi_connection.commit()

    def _creator(self):
        cfg = self.config
        if cfg.host:
            import pg8000.dbapi

            if cfg.host.startswith("/"):
                address = {"unix_sock": f"{cfg.host}/.s.PGSQL.{cfg.port}"}
            else:
                address = {"host": cfg.host, "port": cfg.port}
            return lambda: pg8000.dbapi.connect(
                user=cfg.user, password=cfg.password, database=cfg.name, timeout=30, **address
            )
        if cfg.instance_connection_name:
            from google.cloud.sql.connector import Connector, IPTypes

            self._connector = Connector(refresh_strategy="lazy")
            instance, ip_type = cfg.instance_connection_name, IPTypes[cfg.ip_type.upper()]
        else:
            from google.cloud.alloydb.connector import Connector, IPTypes

            self._connector = Connector(refresh_strategy="lazy")
            instance, ip_type = cfg.instance_uri, IPTypes[cfg.ip_type.upper()]
        connector = self._connector
        return lambda: connector.connect(
            instance,
            "pg8000",
            user=cfg.user,
            password=None if cfg.iam_auth else cfg.password,
            db=cfg.name,
            ip_type=ip_type,
            enable_iam_auth=cfg.iam_auth,
        )

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        """A transaction whose timeouts surface as TimeoutError."""
        try:
            with self.engine.begin() as connection:
                yield connection
        except sqlalchemy.exc.DBAPIError as exc:
            if _is_timeout(exc):
                raise TimeoutError(f"{self.label} query timed out") from exc
            raise

    def fetch_all(self, sql: str, params: Mapping[str, Any] | None = None) -> list[Any]:
        with self.transaction() as connection:
            return list(connection.execute(text(sql), dict(params or {})))

    def close(self) -> None:
        self.engine.dispose()
        if self._connector is not None:
            self._connector.close()

