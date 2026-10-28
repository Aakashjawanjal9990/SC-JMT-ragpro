"""Pytest unit tests for the compatibility API module.

Assumption:
    The supplied script is saved as backend/app/compatibility_api.py.

If its filename differs, change the import:
    import app.compatibility_api as api

Run from the repository root in PowerShell:
    $env:PYTHONPATH = "$PWD\backend"
    python -m pytest tests\test_compatibility_api.py -v
    python -m pytest tests\test_compatibility_api.py --cov=app.compatibility_api --cov-report=term-missing -v

These tests mock database/auth/retrieval dependencies; they do not require a live database.
"""
from contextlib import asynccontextmanager
from datetime import date, datetime, UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

import app.api.compat as api


def make_request(
    *,
    environment="test",
    settings=None,
    auth_service=None,
    rate_limiter=None,
    identity_limiter=None,
    database_engine=None,
    query_orchestrator=None,
    timeout=5,
    cookies=None,
    headers=None,
    client_host="127.0.0.1",
):
    state = SimpleNamespace(
        environment=environment,
        settings=settings or SimpleNamespace(
            session_cookie_name="nm_session",
            admin_session_cookie_name="nm_admin_session",
            session_cookie_samesite="lax",
            vertex_model_id="test-model",
        ),
        auth_service=auth_service,
        rate_limiter=rate_limiter,
        auth_identity_rate_limiter=identity_limiter,
        database_engine=database_engine,
        query_orchestrator=query_orchestrator,
        request_timeout_seconds=timeout,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=state),
        client=SimpleNamespace(host=client_host) if client_host else None,
        headers=headers or {},
        cookies=cookies or {},
        state=SimpleNamespace(),
    )
    return request


def make_user(*, user_id=None, session_id=None, role="user"):
    return api.AuthenticatedUser(
        subject="subject-1",
        user_id=user_id,
        session_id=session_id,
        role=role,
        name="Test User",
        email="user@example.com",
    )


def async_cm(value):
    @asynccontextmanager
    async def manager():
        yield value
    return manager()


# ---------------------------
# Pydantic request validation
# ---------------------------

def test_credentials_valid_and_password_bounds():
    obj = api.Credentials(email="test@example.com", password="password123")
    assert str(obj.email) == "test@example.com"
    with pytest.raises(ValidationError):
        api.Credentials(email="bad-email", password="password123")
    with pytest.raises(ValidationError):
        api.Credentials(email="test@example.com", password="short")


def test_credentials_forbid_extra_fields():
    with pytest.raises(ValidationError):
        api.Credentials(
            email="test@example.com", password="password123", unexpected="x"
        )


def test_admin_credentials_validation():
    obj = api.AdminCredentials(username="admin", password="password123")
    assert obj.username == "admin"
    with pytest.raises(ValidationError):
        api.AdminCredentials(username="", password="password123")
    with pytest.raises(ValidationError):
        api.AdminCredentials(username="admin", password="short")


def test_email_only_validation():
    assert str(api.EmailOnly(email="person@example.com").email) == "person@example.com"
    with pytest.raises(ValidationError):
        api.EmailOnly(email="not-an-email")


def test_registration_trims_name():
    obj = api.Registration(
        name="  Test Person  ",
        email="person@example.com",
        password="password123",
    )
    assert obj.name == "Test Person"


def test_registration_rejects_blank_name():
    with pytest.raises(ValidationError):
        api.Registration(
            name="   ",
            email="person@example.com",
            password="password123",
        )


@pytest.mark.parametrize("field", ["code", "otp", "verificationCode"])
def test_email_code_accepts_supported_code_aliases(field):
    obj = api.EmailCode(email="person@example.com", **{field: "123456"})
    assert obj.code == "123456"


@pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", "12 456"])
def test_email_code_rejects_invalid_codes(code):
    with pytest.raises(ValidationError):
        api.EmailCode(email="person@example.com", code=code)


def test_reset_password_validation():
    obj = api.ResetPassword(
        email="person@example.com", code="123456", password="newpassword123"
    )
    assert obj.password == "newpassword123"
    with pytest.raises(ValidationError):
        api.ResetPassword(
            email="person@example.com", code="123456", password="short"
        )


def test_chat_query_normalization_and_deduplicated_terms():
    obj = api.ChatQuery(
        question="  How   does this work?  ",
        normalizedQuery="  normalized   query ",
        searchTerms=[" law ", "", "law", "section"],
        maxSources=5,
    )
    assert obj.question == "How   does this work?"
    assert obj.normalized_query == "normalized query"
    assert obj.search_terms == ["law", "section"]
    assert obj.max_sources == 5


def test_chat_query_rejects_blank_question():
    with pytest.raises(ValidationError):
        api.ChatQuery(question="   ")


@pytest.mark.parametrize("max_sources", [0, 21])
def test_chat_query_rejects_invalid_max_sources(max_sources):
    with pytest.raises(ValidationError):
        api.ChatQuery(question="valid question", maxSources=max_sources)


def test_chat_query_rejects_too_many_search_terms():
    with pytest.raises(ValidationError):
        api.ChatQuery(
            question="valid question",
            searchTerms=[f"term{i}" for i in range(21)],
        )


def test_chat_query_rejects_long_search_term():
    with pytest.raises(ValidationError):
        api.ChatQuery(question="valid question", searchTerms=["x" * 201])


def test_chat_query_rejects_too_long_question():
    with pytest.raises(ValidationError):
        api.ChatQuery(question="x" * 10001)


# ---------------------------
# Basic helpers
# ---------------------------

def test_service_returns_configured_service():
    service = object()
    request = make_request(auth_service=service)
    assert api._service(request) is service


def test_service_raises_when_not_configured():
    request = make_request()
    with pytest.raises(HTTPException) as exc:
        api._service(request)
    assert exc.value.status_code == 503


def test_ip_returns_client_ip_and_none_without_client():
    assert api._ip(make_request(client_host="10.0.0.5")) == "10.0.0.5"
    assert api._ip(make_request(client_host=None)) is None


@pytest.mark.asyncio
async def test_auth_rate_limit_skips_missing_limiter_in_development():
    await api._auth_rate_limit(make_request(environment="development"))


@pytest.mark.asyncio
async def test_auth_rate_limit_requires_limiter_outside_development():
    with pytest.raises(HTTPException) as exc:
        await api._auth_rate_limit(make_request(environment="production"))
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_auth_rate_limit_checks_ip_and_hashed_identity():
    limiter = SimpleNamespace(check=AsyncMock())
    identity_limiter = SimpleNamespace(check=AsyncMock())
    request = make_request(
        rate_limiter=limiter,
        identity_limiter=identity_limiter,
    )
    await api._auth_rate_limit(request, " Person@Example.com ")
    limiter.check.assert_awaited_once_with("auth-ip:127.0.0.1")
    identity_key = identity_limiter.check.await_args.args[0]
    assert identity_key.startswith("auth-identity:")
    assert "Person@Example.com" not in identity_key


def test_token_reads_regular_cookie():
    request = make_request(cookies={"nm_session": "cookie-token"})
    assert api._token(request) == "cookie-token"


def test_token_reads_admin_cookie():
    request = make_request(cookies={"nm_admin_session": "admin-token"})
    assert api._token(request, admin=True) == "admin-token"


def test_token_reads_bearer_header():
    request = make_request(headers={"authorization": "Bearer abc123"})
    assert api._token(request) == "abc123"


@pytest.mark.parametrize("authorization", ["", "Basic abc", "Bearer", "Bearer   "])
def test_token_rejects_missing_or_invalid_header(authorization):
    request = make_request(headers={"authorization": authorization})
    with pytest.raises(HTTPException) as exc:
        api._token(request)
    assert exc.value.status_code == 401


def test_set_session_cookie_sets_security_options_and_returns_csrf():
    service = SimpleNamespace(csrf_token=MagicMock(return_value="csrf-value"))
    request = make_request(auth_service=service, environment="production")
    response = Response()
    result = api._set_session_cookie(
        response, request, "session-token", max_age=3600
    )
    assert result == "csrf-value"
    service.csrf_token.assert_called_once_with("session-token")
    cookie = response.headers["set-cookie"]
    assert "nm_session=session-token" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "Max-Age=3600" in cookie


def test_set_session_cookie_is_not_secure_in_test_environment():
    service = SimpleNamespace(csrf_token=MagicMock(return_value="csrf"))
    request = make_request(auth_service=service, environment="test")
    response = Response()
    api._set_session_cookie(response, request, "token", max_age=None)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" not in cookie
    assert "Max-Age" not in cookie


def test_clear_session_cookie_deletes_cookie():
    request = make_request(environment="production")
    response = Response()
    api._clear_session_cookie(response, request)
    cookie = response.headers["set-cookie"]
    assert "nm_session=" in cookie
    assert "Max-Age=0" in cookie
    assert "Secure" in cookie


def test_clear_admin_cookie_deletes_admin_cookie():
    request = make_request()
    response = Response()
    api._clear_session_cookie(response, request, admin=True)
    assert "nm_admin_session=" in response.headers["set-cookie"]


# ---------------------------
# Auth endpoints
# ---------------------------

@pytest.mark.asyncio
async def test_register_success():
    service = SimpleNamespace(register=AsyncMock())
    request = make_request(auth_service=service, environment="development")
    payload = api.Registration(
        name="Test User", email="test@example.com", password="password123"
    )
    result = await api.register(payload, request)
    assert result["verificationRequired"] is True
    service.register.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_duplicate_returns_409():
    service = SimpleNamespace(register=AsyncMock(side_effect=ValueError("already exists")))
    request = make_request(auth_service=service, environment="development")
    payload = api.Registration(
        name="Test User", email="test@example.com", password="password123"
    )
    with pytest.raises(HTTPException) as exc:
        await api.register(payload, request)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_verify_email_success_sets_cookie():
    service = SimpleNamespace(
        verify_email=AsyncMock(return_value=("session-token", {"id": "u1"})),
        csrf_token=MagicMock(return_value="csrf"),
    )
    request = make_request(auth_service=service, environment="development")
    response = Response()
    payload = api.EmailCode(email="test@example.com", code="123456")
    result = await api.verify_email(payload, request, response)
    assert result["csrfToken"] == "csrf"
    assert "nm_session=session-token" in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_verify_email_invalid_code_returns_400():
    service = SimpleNamespace(
        verify_email=AsyncMock(side_effect=ValueError("invalid code"))
    )
    request = make_request(auth_service=service, environment="development")
    with pytest.raises(HTTPException) as exc:
        await api.verify_email(
            api.EmailCode(email="test@example.com", code="123456"),
            request,
            Response(),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_resend_verification_success():
    service = SimpleNamespace(resend_verification=AsyncMock())
    request = make_request(auth_service=service, environment="development")
    result = await api.resend_verification(
        api.EmailOnly(email="test@example.com"), request
    )
    assert "code has been sent" in result["message"]
    service.resend_verification.assert_awaited_once_with("test@example.com")


@pytest.mark.asyncio
async def test_login_success_sets_cookie():
    service = SimpleNamespace(
        login=AsyncMock(return_value=("token", {"id": "u1"}, object())),
        csrf_token=MagicMock(return_value="csrf"),
    )
    request = make_request(auth_service=service, environment="development")
    response = Response()
    payload = api.LoginCredentials(
        email="test@example.com", password="password123", remember=True
    )
    result = await api.login(payload, request, response)
    assert result["csrfToken"] == "csrf"
    assert "nm_session=token" in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_login_invalid_credentials_returns_401():
    service = SimpleNamespace(login=AsyncMock(side_effect=PermissionError("bad credentials")))
    request = make_request(auth_service=service, environment="development")
    with pytest.raises(HTTPException) as exc:
        await api.login(
            api.LoginCredentials(
                email="test@example.com", password="password123"
            ),
            request,
            Response(),
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_forgot_password_returns_generic_message():
    service = SimpleNamespace(request_password_reset=AsyncMock())
    request = make_request(auth_service=service, environment="development")
    result = await api.forgot_password(
        api.EmailOnly(email="test@example.com"), request
    )
    assert "If an account exists" in result["message"]


@pytest.mark.asyncio
async def test_reset_password_success():
    service = SimpleNamespace(reset_password=AsyncMock())
    request = make_request(auth_service=service, environment="development")
    result = await api.reset_password(
        api.ResetPassword(
            email="test@example.com",
            code="123456",
            password="newpassword123",
        ),
        request,
    )
    assert "Password updated" in result["message"]


@pytest.mark.asyncio
async def test_reset_password_invalid_code_returns_400():
    service = SimpleNamespace(reset_password=AsyncMock(side_effect=ValueError("bad code")))
    request = make_request(auth_service=service, environment="development")
    with pytest.raises(HTTPException) as exc:
        await api.reset_password(
            api.ResetPassword(
                email="test@example.com",
                code="123456",
                password="newpassword123",
            ),
            request,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_current_user_returns_public_fields_and_csrf():
    service = SimpleNamespace(csrf_token=MagicMock(return_value="csrf"))
    request = make_request(auth_service=service)
    request.state.session_token = "token"
    user = make_user()
    result = await api.current_user(request, user)
    assert result["id"] == user.subject
    assert result["email"] == user.email
    assert result["csrfToken"] == "csrf"


@pytest.mark.asyncio
async def test_logout_calls_service_and_clears_cookie():
    service = SimpleNamespace(
        logout=AsyncMock(),
        csrf_token=MagicMock(return_value="csrf"),
    )
    request = make_request(
        auth_service=service,
        cookies={"nm_session": "session-token"},
    )
    response = Response()
    result = await api.logout(request, response, make_user())
    assert result["message"] == "Signed out."
    service.logout.assert_awaited_once_with("session-token")
    assert "nm_session=" in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_admin_login_success():
    service = SimpleNamespace(
        login=AsyncMock(return_value=("admin-token", {"role": "admin"}, object())),
        csrf_token=MagicMock(return_value="csrf"),
    )
    request = make_request(auth_service=service, environment="development")
    response = Response()
    result = await api.admin_login(
        api.AdminCredentials(username="admin", password="password123"),
        request,
        response,
    )
    assert result["csrfToken"] == "csrf"
    service.login.assert_awaited_once()
    assert service.login.await_args.kwargs["admin"] is True


@pytest.mark.asyncio
async def test_admin_login_bad_credentials_returns_401():
    service = SimpleNamespace(login=AsyncMock(side_effect=PermissionError("denied")))
    request = make_request(auth_service=service, environment="development")
    with pytest.raises(HTTPException) as exc:
        await api.admin_login(
            api.AdminCredentials(username="admin", password="password123"),
            request,
            Response(),
        )
    assert exc.value.status_code == 401


# ---------------------------
# Chat interpretation / sources
# ---------------------------

@pytest.mark.asyncio
async def test_interpret_normalizes_and_extracts_keywords():
    result = await api.interpret(
        api.ChatQuery(question="  What is the doctrine of promissory estoppel? "),
        make_user(),
    )
    assert result["normalizedQuery"] == "What is the doctrine of promissory estoppel?"
    assert "doctrine" in result["keywords"]
    assert "promissory" in result["keywords"]
    assert result["clarificationNeeded"] is False


@pytest.mark.asyncio
async def test_interpret_removes_stop_words_and_deduplicates():
    result = await api.interpret(
        api.ChatQuery(question="Explain law law and legal rules"),
        make_user(),
    )
    assert result["keywords"].count("law") == 1
    assert "and" not in result["keywords"]


def test_frontend_sources_groups_same_case_and_pages():
    c1 = SimpleNamespace(
        case_name="Example v State",
        judgment_title=None,
        citation="2024 SCC 1",
        chunk_id="chunk-1",
        decision_date=date(2024, 1, 1),
        original_page_numbers=[5, 6],
        document_id="doc-1",
        excerpt="First excerpt",
    )
    c2 = SimpleNamespace(
        case_name="example v state",
        judgment_title=None,
        citation="2024 SCC 1",
        chunk_id="chunk-2",
        decision_date=date(2024, 1, 1),
        original_page_numbers=[7],
        document_id="doc-1",
        excerpt="Second excerpt",
    )
    result = api._frontend_sources([c1, c2])
    assert len(result) == 1
    assert result[0]["pages"] == [5, 6, 7]
    assert result[0]["page"] == "5-7"
    assert result[0]["case"] == "Example v State"


def test_frontend_sources_handles_missing_case_name_and_pages():
    citation = SimpleNamespace(
        case_name=None,
        judgment_title=None,
        citation=None,
        chunk_id="chunk-1",
        decision_date=None,
        original_page_numbers=[],
        document_id="doc-1",
        excerpt="excerpt",
    )
    result = api._frontend_sources([citation])
    assert result[0]["case"] == "Supreme Court judgment"
    assert result[0]["year"] == ""
    assert result[0]["page"] == "-"
    assert result[0]["pages"] == []


# ---------------------------
# Conversation persistence helpers
# ---------------------------

@pytest.mark.asyncio
async def test_load_conversation_history_without_engine_returns_503():
    request = make_request(database_engine=None)
    with pytest.raises(HTTPException) as exc:
        await api._load_conversation_history(
            request, make_user(user_id=str(uuid4())), uuid4()
        )
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_load_conversation_history_converts_rows_to_turns():
    rows = [
        {"query_text": "Older question", "response_text": "Older answer"},
        {"query_text": "Newer question", "response_text": "Newer answer"},
    ]
    result_proxy = MagicMock()
    result_proxy.mappings.return_value.all.return_value = rows
    connection = SimpleNamespace(execute=AsyncMock(return_value=result_proxy))
    engine = SimpleNamespace(connect=lambda: async_cm(connection))
    request = make_request(database_engine=engine)
    user = make_user(user_id=str(uuid4()))

    turns = await api._load_conversation_history(request, user, uuid4())

    assert [(turn.role, turn.text) for turn in turns] == [
        ("user", "Older question"),
        ("assistant", "Older answer"),
        ("user", "Newer question"),
        ("assistant", "Newer answer"),
    ]


@pytest.mark.asyncio
async def test_load_conversation_history_maps_database_error_to_503():
    connection = SimpleNamespace(
        execute=AsyncMock(side_effect=SQLAlchemyError("db error"))
    )
    engine = SimpleNamespace(connect=lambda: async_cm(connection))
    request = make_request(database_engine=engine)
    with pytest.raises(HTTPException) as exc:
        await api._load_conversation_history(
            request, make_user(user_id=str(uuid4())), uuid4()
        )
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_persist_failed_attempt_skips_when_no_engine():
    await api._persist_failed_chat_attempt(
        make_request(),
        make_user(),
        uuid4(),
        "question",
        datetime.now(UTC),
        "test_failure",
    )


@pytest.mark.asyncio
async def test_persist_chat_turn_requires_persistent_session():
    with pytest.raises(HTTPException) as exc:
        await api._persist_chat_turn(
            make_request(),
            make_user(),
            uuid4(),
            "question",
            "answer",
            [],
            datetime.now(UTC),
        )
    assert exc.value.status_code == 503


# ---------------------------
# Database/admin helpers
# ---------------------------

def test_admin_data_engine_requires_database():
    with pytest.raises(HTTPException) as exc:
        api._admin_data_engine(make_request())
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_set_conversation_archived_not_found():
    result = SimpleNamespace(rowcount=0)
    connection = SimpleNamespace(execute=AsyncMock(return_value=result))
    engine = SimpleNamespace(begin=lambda: async_cm(connection))
    request = make_request(database_engine=engine)
    with pytest.raises(HTTPException) as exc:
        await api._set_conversation_archived(
            uuid4(), request, make_user(user_id=str(uuid4())), archived=True
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_set_conversation_archived_success():
    result = SimpleNamespace(rowcount=1)
    connection = SimpleNamespace(execute=AsyncMock(return_value=result))
    engine = SimpleNamespace(begin=lambda: async_cm(connection))
    request = make_request(database_engine=engine)
    conversation_id = uuid4()
    result_data = await api._set_conversation_archived(
        conversation_id,
        request,
        make_user(user_id=str(uuid4())),
        archived=True,
    )
    assert result_data == {
        "conversationId": str(conversation_id),
        "archived": True,
    }


@pytest.mark.asyncio
async def test_set_conversation_archived_database_error_returns_503():
    connection = SimpleNamespace(
        execute=AsyncMock(side_effect=SQLAlchemyError("database error"))
    )
    engine = SimpleNamespace(begin=lambda: async_cm(connection))
    request = make_request(database_engine=engine)
    with pytest.raises(HTTPException) as exc:
        await api._set_conversation_archived(
            uuid4(), request, make_user(user_id=str(uuid4())), archived=False
        )
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_admin_users_without_database_returns_503():
    with pytest.raises(HTTPException) as exc:
        await api.admin_users(
            make_request(), page=1, page_size=10, status_filter="all",
            search="", _=make_user(role="admin")
        )
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_admin_analytics_rejects_start_after_end():
    request = make_request()
    with pytest.raises(HTTPException) as exc:
        await api.admin_analytics(
            request,
            start=date(2026, 10, 10),
            end=date(2026, 10, 1),
            _=make_user(role="admin"),
        )
    assert exc.value.status_code == 422
