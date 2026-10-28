"""Postgres connections for the vector database (pgvector) and the BM25 database
(AlloyDB), via the Cloud SQL / AlloyDB connectors or a direct host/unix socket.

Passwords are never logged or included in reprs.
"""

import logging
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
    name: str
    user: str
    password: str | None = field(default=None, repr=False)
    # Exactly one way to reach the server:
    instance_connection_name: str | None = None  # Cloud SQL "project:region:instance"
    instance_uri: str | None = None  # AlloyDB "projects/P/locations/R/clusters/C/instances/I"
    host: str | None = None  # TCP host (e.g. Auth Proxy on 127.0.0.1) or a unix socket dir
    port: int = 5432
    ip_type: str = "private"  # connector IP type: public | private | psc
    iam_auth: bool = False
    pool_size: int = 5
    statement_timeout_ms: int = 5000

    def __post_init__(self) -> None:
        targets = [self.instance_connection_name, self.instance_uri, self.host]
        if sum(bool(t) for t in targets) != 1:
            raise ValueError(
                f"Database {self.name!r}: set exactly one of instance_connection_name, "
                "instance_uri or host"
            )
        if self.ip_type.lower() not in {"public", "private", "psc"}:
            raise ValueError(f"Database {self.name!r}: ip_type must be public, private or psc")
        if not self.iam_auth and not self.password:
            raise ValueError(f"Database {self.name!r}: set password or iam_auth: true")


def _is_timeout(exc: BaseException) -> bool:
    original = getattr(exc, "orig", exc)
    if isinstance(original, TimeoutError):
        return True
    details = original.args[0] if getattr(original, "args", None) else None
    return isinstance(details, Mapping) and details.get("C") == _QUERY_CANCELED


class Database:
    """Pooled SQLAlchemy engine plus the connector that backs it, if any."""

    def __init__(self, config: DatabaseConfig, label: str = "database") -> None:
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
