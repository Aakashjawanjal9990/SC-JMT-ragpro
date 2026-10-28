"""Unit tests for AuthService.

Set AUTH_MODULE to the dotted import path of the module containing AuthService
if it is not one of the common paths tried below, for example:
    set AUTH_MODULE=app.services.auth
Run:
    python -m pytest tests/test_auth_service.py -q
    python -m pytest tests/test_auth_service.py --cov=app.auth --cov-report=term-missing
"""
import importlib
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

MODULE_CANDIDATES = [
    os.getenv("AUTH_MODULE", ""),
    "app.auth",
    "app.auth_service",
    "app.services.auth",
    "app.services.auth_service",
    "app.security.auth",
]
auth_module = None
import_errors = []
for module_name in filter(None, MODULE_CANDIDATES):
    try:
        candidate = importlib.import_module(module_name)
        if hasattr(candidate, "AuthService"):
            auth_module = candidate
            break
    except Exception as exc:  # keep trying common module locations
        import_errors.append(f"{module_name}: {exc}")
if auth_module is None:
    raise ImportError(
        "Could not import AuthService. Set AUTH_MODULE to the dotted path of "
        "the module containing AuthService. Tried: " + "; ".join(import_errors)
    )

AuthService = auth_module.AuthService
DependencyUnavailable = auth_module.DependencyUnavailable


@pytest.fixture
def service():
    engine = MagicMock()
    redis = MagicMock()
    redis.set = AsyncMock()
    redis.get = AsyncMock()
    redis.delete = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    redis.eval = AsyncMock(return_value=[1, "subject-123"])
    email_sender = MagicMock()
    email_sender.send_code = AsyncMock()
    settings = SimpleNamespace(auth_challenge_secret="test-secret")
    return AuthService(engine, redis, settings, email_sender)


class FakeMappings:
    def __init__(self, value):
        self.value = value

    def one_or_none(self):
        return self.value


class FakeResult:
    def __init__(self, row=None, rowcount=1):
        self.row = row
        self.rowcount = rowcount

    def mappings(self):
        return FakeMappings(self.row)


class FakeConnection:
    def __init__(self, result=None, scalar_value=None):
        self.result = result or FakeResult()
        self.scalar_value = scalar_value
        self.execute = AsyncMock(return_value=self.result)
        self.scalar = AsyncMock(return_value=scalar_value)


class AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


def attach_connection_contexts(monkeypatch, service, connection):
    service._engine.connect.return_value = AsyncContext(connection)
    service._engine.begin.return_value = AsyncContext(connection)


def test_normalize_email_strips_and_lowercases():
    assert AuthService._normalize_email("  USER@Example.COM ") == "user@example.com"


def test_token_hash_is_sha256_and_deterministic():
    import hashlib
    assert AuthService._token_hash("abc") == hashlib.sha256(b"abc").hexdigest()
    assert AuthService._token_hash("abc") == AuthService._token_hash("abc")
    assert AuthService._token_hash("abc") != AuthService._token_hash("abcd")


def test_public_user_uses_full_name():
    row = {"user_uuid": "u-1", "full_name": "Ada Lovelace",
           "email": "ada@example.com", "role": "user"}
    assert AuthService._public_user(row) == {
        "id": "u-1", "name": "Ada Lovelace", "email": "ada@example.com", "role": "user"
    }


def test_public_user_falls_back_to_email():
    row = {"user_uuid": "u-1", "full_name": None,
           "email": "ada@example.com", "role": "admin"}
    assert AuthService._public_user(row)["name"] == "ada@example.com"


def test_code_hash_is_normalized_and_purpose_bound(service):
    a = service._code_hash(" USER@example.com ", "verify", "123456")
    b = service._code_hash("user@example.com", "verify", "123456")
    c = service._code_hash("user@example.com", "reset", "123456")
    assert a == b
    assert a != c


def test_csrf_token_is_deterministic_and_session_bound(service):
    assert service.csrf_token("token-a") == service.csrf_token("token-a")
    assert service.csrf_token("token-a") != service.csrf_token("token-b")
    assert len(service.csrf_token("token-a")) == 64


@pytest.mark.parametrize("supplied", [None, "", "short", "x" * 63, "x" * 65])
def test_verify_csrf_rejects_missing_or_wrong_length(service, supplied):
    assert service.verify_csrf_token("session", supplied) is False


def test_verify_csrf_accepts_correct_token(service):
    token = service.csrf_token("session")
    assert service.verify_csrf_token("session", token) is True


def test_verify_csrf_rejects_token_from_other_session(service):
    token = service.csrf_token("session-a")
    assert service.verify_csrf_token("session-b", token) is False


@pytest.mark.asyncio
async def test_create_challenge_stores_hashed_code_and_delivery_code(service, monkeypatch):
    monkeypatch.setattr(auth_module.secrets, "randbelow", lambda n: 42)
    code = await service._create_challenge("A@Example.com", "verify", "subject-1")
    assert code == "000042"
    key = service._challenge_key("a@example.com", "verify")
    payload = json.loads(service._redis.set.await_args_list[0].args[1])
    assert payload["subject"] == "subject-1"
    assert payload["code_hash"] == service._code_hash("a@example.com", "verify", code)
    assert service._redis.set.await_args_list[0].kwargs["ex"] == auth_module.CODE_TTL_SECONDS
    service._redis.delete.assert_awaited_once_with(key + ":attempts")


@pytest.mark.asyncio
async def test_send_challenge_sends_and_deletes_delivery_code(service):
    key = service._challenge_key("a@example.com", "verify")
    service._redis.get.side_effect = [b'{"subject":"x"}', b"123456"]
    await service._send_challenge("a@example.com", "verify")
    service._email_sender.send_code.assert_awaited_once_with("a@example.com", "verify", b"123456")
    service._redis.delete.assert_awaited_once_with(key + ":delivery")


@pytest.mark.asyncio
async def test_send_challenge_missing_challenge_raises_dependency_unavailable(service):
    service._redis.get.return_value = None
    with pytest.raises(DependencyUnavailable, match="could not be prepared"):
        await service._send_challenge("a@example.com", "verify")


@pytest.mark.asyncio
async def test_send_challenge_missing_delivery_code_raises(service):
    service._redis.get.side_effect = [b"present", None]
    with pytest.raises(DependencyUnavailable, match="email could not be prepared"):
        await service._send_challenge("a@example.com", "verify")


@pytest.mark.asyncio
async def test_create_and_send_challenge_sets_delivery_ttl(service, monkeypatch):
    monkeypatch.setattr(service, "_create_challenge", AsyncMock(return_value="123456"))
    monkeypatch.setattr(service, "_send_challenge", AsyncMock())
    await service.create_and_send_challenge("a@example.com", "verify", "subject")
    assert service._redis.set.await_args.kwargs["ex"] == 60
    service._send_challenge.assert_awaited_once_with("a@example.com", "verify")


@pytest.mark.asyncio
async def test_consume_challenge_success_returns_subject(service):
    service._redis.eval.return_value = [1, "user-uuid"]
    assert await service._consume_challenge("a@example.com", "verify", "123456") == {
        "subject": "user-uuid"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,subject", [(0, ""), (2, ""), (1, "")])
async def test_consume_challenge_rejects_invalid_expired_or_empty_subject(service, outcome, subject):
    service._redis.eval.return_value = [outcome, subject]
    monkeypatch_logger = AsyncMock()
    service._log_auth_failure = monkeypatch_logger
    with pytest.raises(ValueError, match="invalid or expired"):
        await service._consume_challenge("a@example.com", "verify", "bad")
    if outcome != 1:
        monkeypatch_logger.assert_awaited_once()


@pytest.mark.asyncio
async def test_healthcheck_true_when_database_and_redis_ok(service):
    conn = FakeConnection()
    attach_connection_contexts(None, service, conn)
    assert await service.healthcheck() is True
    conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_healthcheck_false_when_redis_ping_is_false(service):
    conn = FakeConnection()
    attach_connection_contexts(None, service, conn)
    service._redis.ping.return_value = False
    assert await service.healthcheck() is False


@pytest.mark.asyncio
async def test_fetch_user_by_email_returns_row(service):
    row = {"user_uuid": "u1", "email": "a@example.com"}
    conn = FakeConnection(FakeResult(row))
    service._engine.connect.return_value = AsyncContext(conn)
    assert await service._fetch_user_by_email("a@example.com") == row
    conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_authenticate_returns_none_for_unknown_token(service):
    conn = FakeConnection(FakeResult(None))
    service._engine.begin.return_value = AsyncContext(conn)
    assert await service.authenticate("unknown-token") is None


@pytest.mark.asyncio
async def test_authenticate_builds_authenticated_user_and_updates_activity(service):
    from uuid import uuid4
    user_id, session_id = uuid4(), uuid4()
    row = {"user_uuid": user_id, "email": "a@example.com", "full_name": "Ada",
           "role": "user", "session_uuid": session_id}
    conn = FakeConnection(FakeResult(row))
    service._engine.begin.return_value = AsyncContext(conn)
    user = await service.authenticate("raw-token")
    assert user.email == "a@example.com"
    assert user.subject == str(user_id)
    assert user.session_id == str(session_id)
    assert user.token_hash == AuthService._token_hash("raw-token")
    assert conn.execute.await_count == 2


@pytest.mark.asyncio
async def test_logout_unknown_token_does_not_write_activity(service):
    conn = FakeConnection(FakeResult(None))
    service._engine.begin.return_value = AsyncContext(conn)
    service._record_activity = AsyncMock()
    await service.logout("unknown-token")
    service._record_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_active_user_records_logout(service):
    from uuid import uuid4
    user_id, session_id = uuid4(), uuid4()
    conn = FakeConnection(FakeResult({"session_uuid": session_id, "user_uuid": user_id}),
                          scalar_value="user")
    service._engine.begin.return_value = AsyncContext(conn)
    service._record_activity = AsyncMock()
    await service.logout("token")
    service._record_activity.assert_awaited_once()
    assert service._record_activity.await_args.args[2] == "LOGOUT"


@pytest.mark.asyncio
async def test_logout_admin_records_admin_logout(service):
    from uuid import uuid4
    user_id, session_id = uuid4(), uuid4()
    conn = FakeConnection(FakeResult({"session_uuid": session_id, "user_uuid": user_id}),
                          scalar_value="admin")
    service._engine.begin.return_value = AsyncContext(conn)
    service._record_activity = AsyncMock()
    await service.logout("token")
    assert service._record_activity.await_args.args[2] == "ADMIN_LOGOUT"


@pytest.mark.asyncio
async def test_logout_swallows_activity_logging_failure(service):
    from uuid import uuid4
    conn = FakeConnection(FakeResult({"session_uuid": uuid4(), "user_uuid": uuid4()}),
                          scalar_value="user")
    service._engine.begin.return_value = AsyncContext(conn)
    service._record_activity = AsyncMock(side_effect=RuntimeError("audit down"))
    # The service deliberately treats logout as successful if audit logging fails.
    await service.logout("token")


def test_verify_password_accepts_correct_password(service):
    hashed = service._password_hasher.hash("StrongPassword!123")
    assert service._verify_password(hashed, "StrongPassword!123") is True


def test_verify_password_rejects_wrong_password(service):
    hashed = service._password_hasher.hash("StrongPassword!123")
    assert service._verify_password(hashed, "wrong-password") is False


def test_verify_password_rejects_invalid_hash(service):
    assert service._verify_password("not-a-valid-argon2-hash", "password") is False


@pytest.mark.asyncio
async def test_log_auth_failure_does_not_propagate_audit_failure(service):
    service._record_activity = AsyncMock(side_effect=RuntimeError("audit down"))
    await service._log_auth_failure("login_rejected", " A@EXAMPLE.COM ", "127.0.0.1")
    service._record_activity.assert_awaited_once()
    metadata = service._record_activity.await_args.kwargs["metadata"]
    assert len(metadata["subject_hash"]) == 64
    assert metadata["outcome"] == "FAILED"


@pytest.mark.asyncio
async def test_create_session_returns_opaque_token_and_authenticated_user(service):
    from uuid import uuid4
    user = {"user_uuid": uuid4(), "email": "a@example.com", "full_name": "Ada", "role": "user"}
    conn = FakeConnection()
    service._engine.begin.return_value = AsyncContext(conn)
    token, authenticated = await service._create_session(user, "127.0.0.1", "agent", admin=False)
    assert isinstance(token, str) and len(token) >= 32
    assert authenticated.email == "a@example.com"
    assert authenticated.role == "user"
    assert conn.execute.await_count == 1


@pytest.mark.asyncio
async def test_create_session_truncates_user_agent(service):
    from uuid import uuid4
    user = {"user_uuid": uuid4(), "email": "a@example.com", "full_name": None, "role": "user"}
    conn = FakeConnection()
    service._engine.begin.return_value = AsyncContext(conn)
    await service._create_session(user, None, "x" * 600, admin=False)
    params = conn.execute.await_args.args[1]
    assert len(params["user_agent"]) == 512


@pytest.mark.asyncio
async def test_register_duplicate_email_raises_value_error(service):
    from sqlalchemy.exc import IntegrityError
    conn = FakeConnection()
    conn.execute.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
    service._engine.begin.return_value = AsyncContext(conn)
    with pytest.raises(ValueError, match="may already exist"):
        await service.register("Ada", "ADA@example.com", "StrongPassword!123",
                               ip_address=None, user_agent=None)


@pytest.mark.asyncio
async def test_register_normalizes_email_and_records_activity(service):
    from uuid import uuid4
    conn = FakeConnection()
    service._engine.begin.return_value = AsyncContext(conn)
    service.create_and_send_challenge = AsyncMock()
    service._record_activity = AsyncMock()
    await service.register(" Ada ", " ADA@Example.com ", "StrongPassword!123",
                           ip_address="127.0.0.1", user_agent="test")
    params = conn.execute.await_args.args[1]
    assert params["email"] == "ada@example.com"
    assert params["username"] == "ada@example.com"
    assert params["name"] == "Ada"
    service.create_and_send_challenge.assert_awaited_once()
    service._record_activity.assert_awaited_once()


@pytest.mark.asyncio
async def test_resend_verification_ignores_missing_user(service):
    service._fetch_user_by_email = AsyncMock(return_value=None)
    service.create_and_send_challenge = AsyncMock()
    await service.resend_verification("missing@example.com")
    service.create_and_send_challenge.assert_not_awaited()


@pytest.mark.asyncio
async def test_resend_verification_ignores_active_user(service):
    service._fetch_user_by_email = AsyncMock(return_value={"account_status": "active"})
    service.create_and_send_challenge = AsyncMock()
    await service.resend_verification("active@example.com")
    service.create_and_send_challenge.assert_not_awaited()


@pytest.mark.asyncio
async def test_resend_verification_sends_for_pending_user(service):
    from uuid import uuid4
    user = {"account_status": "pending", "user_uuid": uuid4()}
    service._fetch_user_by_email = AsyncMock(return_value=user)
    service.create_and_send_challenge = AsyncMock()
    await service.resend_verification(" A@EXAMPLE.COM ")
    service.create_and_send_challenge.assert_awaited_once_with(
        "a@example.com", "verify", str(user["user_uuid"])
    )


@pytest.mark.asyncio
async def test_password_reset_request_is_silent_for_unknown_user(service):
    service._fetch_user_by_email = AsyncMock(return_value=None)
    service.create_and_send_challenge = AsyncMock()
    await service.request_password_reset("missing@example.com")
    service.create_and_send_challenge.assert_not_awaited()


@pytest.mark.asyncio
async def test_password_reset_request_is_silent_for_pending_user(service):
    service._fetch_user_by_email = AsyncMock(return_value={"account_status": "pending"})
    service.create_and_send_challenge = AsyncMock()
    await service.request_password_reset("pending@example.com")
    service.create_and_send_challenge.assert_not_awaited()


@pytest.mark.asyncio
async def test_password_reset_request_sends_for_active_user(service):
    from uuid import uuid4
    user = {"account_status": "active", "user_uuid": uuid4()}
    service._fetch_user_by_email = AsyncMock(return_value=user)
    service.create_and_send_challenge = AsyncMock()
    await service.request_password_reset(" A@EXAMPLE.COM ")
    service.create_and_send_challenge.assert_awaited_once_with(
        "a@example.com", "reset", str(user["user_uuid"])
    )


@pytest.mark.asyncio
async def test_reset_password_rejects_invalid_challenge_subject(service):
    service._consume_challenge = AsyncMock(return_value={})
    with pytest.raises(ValueError, match="invalid or expired"):
        await service.reset_password("a@example.com", "000000", "NewPassword!123")


@pytest.mark.asyncio
async def test_reset_password_rejects_nonexistent_or_inactive_user(service):
    from uuid import uuid4
    service._consume_challenge = AsyncMock(return_value={"subject": str(uuid4())})
    conn = FakeConnection(FakeResult(None, rowcount=0))
    service._engine.begin.return_value = AsyncContext(conn)
    with pytest.raises(ValueError, match="invalid or expired"):
        await service.reset_password("a@example.com", "123456", "NewPassword!123")


@pytest.mark.asyncio
async def test_reset_password_updates_hash_revokes_sessions_and_logs(service):
    from uuid import uuid4
    user_id = uuid4()
    service._consume_challenge = AsyncMock(return_value={"subject": str(user_id)})
    conn = FakeConnection(FakeResult({}, rowcount=1))
    service._engine.begin.return_value = AsyncContext(conn)
    service._record_activity = AsyncMock()
    await service.reset_password(" A@EXAMPLE.COM ", "123456", "NewPassword!123")
    assert conn.execute.await_count == 2
    service._record_activity.assert_awaited_once()
    assert service._record_activity.await_args.args[2] == "PASSWORD_RESET"


@pytest.mark.asyncio
async def test_verify_email_rejects_bad_subject(service):
    service._consume_challenge = AsyncMock(return_value={})
    with pytest.raises(ValueError, match="invalid or expired"):
        await service.verify_email("a@example.com", "123456", ip_address=None, user_agent=None)


@pytest.mark.asyncio
async def test_verify_email_rejects_user_not_pending(service):
    from uuid import uuid4
    service._consume_challenge = AsyncMock(return_value={"subject": str(uuid4())})
    conn = FakeConnection(FakeResult(None))
    service._engine.begin.return_value = AsyncContext(conn)
    with pytest.raises(ValueError, match="invalid or expired"):
        await service.verify_email("a@example.com", "123456", ip_address=None, user_agent=None)


@pytest.mark.asyncio
async def test_verify_email_activates_user_and_returns_public_user(service):
    from uuid import uuid4
    user_id = uuid4()
    row = {"user_uuid": user_id, "email": "a@example.com", "full_name": "Ada", "role": "user"}
    service._consume_challenge = AsyncMock(return_value={"subject": str(user_id)})
    conn = FakeConnection(FakeResult(row))
    service._engine.begin.return_value = AsyncContext(conn)
    service._create_session = AsyncMock(return_value=("token", SimpleNamespace(session_id=str(uuid4()))))
    service._record_activity = AsyncMock()
    token, public_user = await service.verify_email(
        " A@EXAMPLE.COM ", "123456", ip_address=None, user_agent=None
    )
    assert token == "token"
    assert public_user["email"] == "a@example.com"
    service._record_activity.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("password_ok,status,role,admin,expected", [
    (False, "active", "user", False, "Email or password is incorrect."),
    (True, "pending", "user", False, "Verify your email before signing in."),
    (True, "active", "user", True, "Admin credentials are incorrect."),
    (True, "active", "admin", False, "Email or password is incorrect."),
])
async def test_login_rejects_unauthorized_cases(service, monkeypatch, password_ok, status, role, admin, expected):
    from uuid import uuid4
    user = {"user_uuid": uuid4(), "email": "a@example.com", "password_hash": "hash",
            "account_status": status, "role": role, "full_name": "Ada"}
    service._fetch_user_by_email = AsyncMock(return_value=user)
    service._verify_password = MagicMock(return_value=password_ok)
    service._log_auth_failure = AsyncMock()
    with pytest.raises(PermissionError, match=expected):
        await service.login("a@example.com", "pw", ip_address=None, user_agent=None, admin=admin)


@pytest.mark.asyncio
async def test_login_unknown_user_uses_generic_error(service):
    service._fetch_user_by_email = AsyncMock(return_value=None)
    service._verify_password = MagicMock(return_value=False)
    service._log_auth_failure = AsyncMock()
    with pytest.raises(PermissionError, match="Email or password is incorrect"):
        await service.login("missing@example.com", "wrong", ip_address=None, user_agent=None)
    service._verify_password.assert_called_once_with(service._dummy_hash, "wrong")


@pytest.mark.asyncio
async def test_login_success_creates_session_and_records_activity(service):
    from uuid import uuid4
    user_id = uuid4()
    row = {"user_uuid": user_id, "email": "a@example.com", "password_hash": "hash",
           "account_status": "active", "role": "user", "full_name": "Ada"}
    service._fetch_user_by_email = AsyncMock(return_value=row)
    service._verify_password = MagicMock(return_value=True)
    service._create_session = AsyncMock(return_value=("token", SimpleNamespace(session_id=str(uuid4()))))
    service._record_activity = AsyncMock()
    conn = FakeConnection()
    service._engine.begin.return_value = AsyncContext(conn)
    token, public_user, authenticated = await service.login(
        "a@example.com", "pw", ip_address=None, user_agent=None
    )
    assert token == "token"
    assert public_user["name"] == "Ada"
    service._create_session.assert_awaited_once()
    service._record_activity.assert_awaited_once()
