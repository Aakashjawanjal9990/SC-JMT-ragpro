"""Compatibility API for the current Tanvi frontend contract."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth_service import ADMIN_SESSION_HOURS, SESSION_DAYS
from app.models import QueryFilters, QueryOptions, QueryRequest
from app.ports import AdapterNotConfigured, DependencyUnavailable, UnsupportedQueryFilter
from app.retrieval.errors import FilterError, RetrievalFailure, RetrievalTimeoutError
from app.security import (
    AuthenticatedUser,
    require_admin_user,
    require_authenticated_user,
    require_trusted_origin,
)

router = APIRouter(prefix="/api", dependencies=[Depends(require_trusted_origin)])


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class AdminCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=8, max_length=128)


class LoginCredentials(Credentials):
    remember: bool = True


class Registration(Credentials):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be blank.")
        return value


class EmailCode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    code: str = Field(
        pattern=r"^[0-9]{6}$",
        validation_alias=AliasChoices("code", "otp", "verificationCode"),
    )


class ResetPassword(EmailCode):
    password: str = Field(min_length=8, max_length=128)


class ChatQuery(BaseModel):
    # Tanvi's frontend sends additional interpretation controls that this
    # backend does not currently use. Accept the contract without rejecting it.
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    question: str = Field(min_length=1, max_length=10_000)
    conversation_id: UUID | None = Field(default=None, alias="conversationId")
    filters: QueryFilters = Field(default_factory=QueryFilters)
    max_sources: int = Field(default=8, ge=1, le=20, alias="maxSources")

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Question cannot be blank.")
        return value


def _service(request: Request):
    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Authentication is not configured.")
    return service


def _ip(request: Request) -> str | None:
    # Do not trust forwarded headers unless a trusted proxy has been configured.
    return request.client.host if request.client else None


async def _auth_rate_limit(request: Request) -> None:
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        if request.app.state.environment == "development":
            return
        raise HTTPException(status_code=503, detail="Request protection is not configured.")
    await limiter.check(f"auth-ip:{_ip(request) or 'unknown'}")


def _token(request: Request) -> str:
    cookie_token = request.cookies.get(request.app.state.settings.session_cookie_name)
    if cookie_token:
        return cookie_token
    value = request.headers.get("authorization", "")
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="An authenticated session is required.")
    return token.strip()


def _set_session_cookie(
    response: Response, request: Request, token: str, *, max_age: int | None
) -> str:
    settings = request.app.state.settings
    secure = request.app.state.environment not in {"development", "test"}
    cookie_options: dict[str, Any] = {
        "key": settings.session_cookie_name,
        "value": token,
        "path": "/",
        "secure": secure,
        "httponly": True,
        "samesite": settings.session_cookie_samesite,
    }
    if max_age is not None:
        cookie_options["max_age"] = max_age
    response.set_cookie(**cookie_options)
    return _service(request).csrf_token(token)


def _clear_session_cookie(response: Response, request: Request) -> None:
    settings = request.app.state.settings
    secure = request.app.state.environment not in {"development", "test"}
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=secure,
        httponly=True,
        samesite=settings.session_cookie_samesite,
    )


@router.post("/auth/register", status_code=status.HTTP_202_ACCEPTED)
async def register(payload: Registration, request: Request) -> dict[str, Any]:
    await _auth_rate_limit(request)
    try:
        await _service(request).register(
            payload.name, str(payload.email), payload.password,
            ip_address=_ip(request), user_agent=request.headers.get("user-agent"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"verificationRequired": True, "message": "Check your email for a verification code."}


@router.post("/auth/verify")
async def verify_email(
    payload: EmailCode, request: Request, response: Response
) -> dict[str, Any]:
    await _auth_rate_limit(request)
    try:
        token, user = await _service(request).verify_email(
            str(payload.email), payload.code, ip_address=_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    csrf_token = _set_session_cookie(
        response, request, token, max_age=SESSION_DAYS * 24 * 60 * 60
    )
    return {"user": user, "csrfToken": csrf_token}


@router.post("/auth/resend", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(payload: dict[str, str], request: Request) -> dict[str, str]:
    await _auth_rate_limit(request)
    email = payload.get("email", "")
    if "@" not in email:
        raise HTTPException(status_code=422, detail="A valid email is required.")
    await _service(request).resend_verification(email)
    return {"message": "If the account needs verification, a code has been sent."}


@router.post("/auth/login")
async def login(
    payload: LoginCredentials, request: Request, response: Response
) -> dict[str, Any]:
    await _auth_rate_limit(request)
    try:
        token, user, _ = await _service(request).login(
            str(payload.email), payload.password, ip_address=_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    csrf_token = _set_session_cookie(
        response,
        request,
        token,
        max_age=SESSION_DAYS * 24 * 60 * 60 if payload.remember else None,
    )
    return {"user": user, "csrfToken": csrf_token}


@router.post("/auth/forgot", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(payload: dict[str, str], request: Request) -> dict[str, str]:
    await _auth_rate_limit(request)
    email = payload.get("email", "")
    if "@" not in email:
        raise HTTPException(status_code=422, detail="A valid email is required.")
    await _service(request).request_password_reset(email)
    return {"message": "If an account exists, password reset instructions have been sent."}


@router.post("/auth/reset")
async def reset_password(payload: ResetPassword, request: Request) -> dict[str, str]:
    await _auth_rate_limit(request)
    try:
        await _service(request).reset_password(str(payload.email), payload.code, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "Password updated. Sign in with your new password."}


@router.get("/auth/me")
async def current_user(
    request: Request, user: AuthenticatedUser = Depends(require_authenticated_user)
) -> dict[str, Any]:
    token = getattr(request.state, "session_token", None)
    service = getattr(request.app.state, "auth_service", None)
    csrf_token = service.csrf_token(token) if service is not None and token else None
    return {
        "id": user.user_id or user.subject,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "csrfToken": csrf_token,
    }


@router.post("/auth/logout")
async def logout(
    request: Request,
    response: Response,
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> dict[str, str]:
    del user
    await _service(request).logout(_token(request))
    _clear_session_cookie(response, request)
    return {"message": "Signed out."}


@router.post("/admin/login")
async def admin_login(
    payload: AdminCredentials, request: Request, response: Response
) -> dict[str, Any]:
    await _auth_rate_limit(request)
    try:
        token, user, _ = await _service(request).login(
            payload.username, payload.password, ip_address=_ip(request),
            user_agent=request.headers.get("user-agent"), admin=True,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    csrf_token = _set_session_cookie(
        response, request, token, max_age=ADMIN_SESSION_HOURS * 60 * 60
    )
    return {"user": user, "csrfToken": csrf_token}


@router.post("/chat/interpret")
async def interpret(payload: ChatQuery, user: AuthenticatedUser = Depends(require_authenticated_user)) -> dict[str, Any]:
    del user
    return {
        "normalizedQuery": payload.question,
        "keywords": [],
        "alternatives": [],
        "clarificationNeeded": False,
        "clarificationQuestion": "",
    }


@router.post("/chat/query")
async def chat_query(
    payload: ChatQuery, request: Request,
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> dict[str, Any]:
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is not None:
        await limiter.check(user.subject)
    orchestrator = request.app.state.query_orchestrator
    if not user.user_id or not user.session_id:
        raise HTTPException(status_code=503, detail="Persistent chat history requires an app session.")
    conversation_id = payload.conversation_id or uuid4()
    if payload.conversation_id is not None:
        engine = getattr(request.app.state, "database_engine", None)
        if engine is None:
            raise HTTPException(status_code=503, detail="Conversation history is unavailable.")
        async with engine.connect() as connection:
            ownership_result = await connection.execute(
                text(
                """SELECT EXISTS (
                           SELECT 1 FROM query_runs
                           WHERE user_uuid = :user_id
                             AND metadata->>'conversation_id' = :conversation_id
                       ) AS owned,
                       COALESCE(BOOL_OR(metadata->>'archived' = 'true'), false) AS archived
                   FROM query_runs
                   WHERE user_uuid = :user_id
                     AND metadata->>'conversation_id' = :conversation_id"""
                ),
                {
                    "user_id": UUID(user.user_id),
                    "conversation_id": str(conversation_id),
                },
            )
            ownership = ownership_result.mappings().one()
        if not ownership["owned"]:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        if ownership["archived"]:
            raise HTTPException(
                status_code=409,
                detail="Restore this conversation before continuing it.",
            )
    query = QueryRequest(
        question=payload.question,
        filters=payload.filters,
        options=QueryOptions(max_sources=payload.max_sources),
    )
    started = datetime.now(UTC)
    try:
        async with asyncio.timeout(request.app.state.request_timeout_seconds):
            response = await orchestrator.answer(query, request_id=str(uuid4()))
    except FilterError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    except RetrievalTimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail="Retrieval did not respond in time.",
            headers={"Retry-After": "5"},
        ) from exc
    except RetrievalFailure as exc:
        raise HTTPException(
            status_code=503,
            detail="Retrieval is temporarily unavailable.",
            headers={"Retry-After": "5"},
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="The query dependencies did not respond in time.") from exc
    except UnsupportedQueryFilter as exc:
        raise HTTPException(status_code=422, detail="The requested filter is not supported.") from exc
    except AdapterNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Query dependencies are not configured yet.") from exc
    except DependencyUnavailable as exc:
        raise HTTPException(status_code=503, detail="A query dependency is temporarily unavailable.") from exc

    answer = response.answer.strip() or "I could not find enough support in the judgments to answer this."
    sources = _frontend_sources(response.citations)
    try:
        await _persist_chat_turn(
            request, user, conversation_id, payload.question, answer, sources, started,
        )
    except SQLAlchemyError as exc:
        # The frontend must not show an answer that was not saved to its history.
        raise HTTPException(status_code=503, detail="Conversation history could not be saved.") from exc
    return {
        "conversationId": str(conversation_id),
        "answer": answer,
        "sources": sources,
        "status": response.status,
        "limitations": response.limitations,
    }


def _frontend_sources(citations: list[Any]) -> list[dict[str, Any]]:
    """Collapse chunk citations into one frontend source per case."""
    grouped: dict[str, dict[str, Any]] = {}
    for citation in citations:
        case_name = citation.case_name or citation.judgment_title or citation.citation or "Supreme Court judgment"
        key = case_name.strip().casefold()
        source = grouped.setdefault(key, {
            "id": citation.chunk_id,
            "case": case_name,
            "year": str(citation.decision_date.year) if citation.decision_date else "",
            "pages": set(),
            "citation": citation.citation,
            "excerpt": citation.excerpt,
            "judgmentId": citation.document_id,
        })
        source["pages"].update(citation.original_page_numbers)

    result: list[dict[str, Any]] = []
    for source in grouped.values():
        pages = sorted(source.pop("pages"))
        source["pages"] = pages
        source["page"] = "-" if not pages else str(pages[0]) if len(pages) == 1 else f"{pages[0]}-{pages[-1]}"
        result.append(source)
    return result


async def _persist_chat_turn(
    request: Request, user: AuthenticatedUser, conversation_id: UUID,
    question: str, answer: str, sources: list[dict[str, Any]], started: datetime,
) -> None:
    engine = getattr(request.app.state, "database_engine", None)
    if engine is None or not user.user_id or not user.session_id:
        raise HTTPException(status_code=503, detail="Persistent chat history requires an app session.")
    elapsed = max(0, int((datetime.now(UTC) - started).total_seconds() * 1000))
    async with engine.begin() as connection:
        await connection.execute(text(
            """INSERT INTO query_runs (
                   query_run_uuid, user_uuid, session_uuid, request_type, query_text,
                   response_text, retrieved_chunks, metadata, status, latency_ms, model_name
               ) VALUES (
                   :run_id, :user_id, :session_id, 'CHAT', :question, :answer,
                   CAST(:sources AS jsonb), CAST(:metadata AS jsonb), :status, :latency, :model
               )"""
        ), {
            "run_id": uuid4(), "user_id": UUID(user.user_id),
            "session_id": UUID(user.session_id), "question": question,
            "answer": answer, "sources": json.dumps(sources),
            "metadata": json.dumps({"conversation_id": str(conversation_id)}),
            "status": "COMPLETED", "latency": elapsed,
            "model": request.app.state.settings.vertex_model_id or "configured-model",
        })


@router.get("/chat/conversations")
async def list_conversations(
    request: Request,
    archived: bool = Query(default=False),
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> list[dict[str, Any]]:
    engine = request.app.state.database_engine
    if engine is None or not user.user_id:
        raise HTTPException(status_code=503, detail="Conversation history is unavailable.")
    try:
        async with engine.connect() as connection:
            rows = (await connection.execute(text(
                """SELECT metadata->>'conversation_id' AS id,
                          left((array_agg(query_text ORDER BY created_at ASC))[1], 48) AS title,
                          max(created_at) AS updated_at
                   FROM query_runs
                   WHERE user_uuid = :user_id AND metadata ? 'conversation_id'
                   GROUP BY metadata->>'conversation_id'
                   HAVING COALESCE(BOOL_OR(metadata->>'archived' = 'true'), false) = :archived
                   ORDER BY max(created_at) DESC LIMIT 100"""
            ), {"user_id": UUID(user.user_id), "archived": archived})).mappings().all()
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Conversation history is unavailable.") from exc
    return [{"id": row["id"], "title": row["title"] or "New conversation"} for row in rows]


async def _set_conversation_archived(
    conversation_id: UUID,
    request: Request,
    user: AuthenticatedUser,
    *,
    archived: bool,
) -> dict[str, Any]:
    engine = request.app.state.database_engine
    if engine is None or not user.user_id:
        raise HTTPException(status_code=503, detail="Conversation history is unavailable.")
    try:
        async with engine.begin() as connection:
            result = await connection.execute(text(
                """UPDATE query_runs
                   SET metadata = jsonb_set(
                       COALESCE(metadata, '{}'::jsonb),
                       '{archived}', CAST(:archived AS jsonb), true
                   )
                   WHERE user_uuid = :user_id
                     AND metadata->>'conversation_id' = :conversation_id"""
            ), {
                "user_id": UUID(user.user_id),
                "conversation_id": str(conversation_id),
                "archived": json.dumps(archived),
            })
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Conversation history could not be updated.") from exc
    if not result.rowcount:
        # Scope the update by user so another user's conversation is never exposed.
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"conversationId": str(conversation_id), "archived": archived}


@router.post("/chat/conversations/{conversation_id}/archive")
async def archive_conversation(
    conversation_id: UUID,
    request: Request,
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> dict[str, Any]:
    return await _set_conversation_archived(
        conversation_id, request, user, archived=True
    )


@router.post("/chat/conversations/{conversation_id}/restore")
async def restore_conversation(
    conversation_id: UUID,
    request: Request,
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> dict[str, Any]:
    return await _set_conversation_archived(
        conversation_id, request, user, archived=False
    )


@router.get("/chat/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: UUID, request: Request,
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> list[dict[str, Any]]:
    engine = request.app.state.database_engine
    if engine is None or not user.user_id:
        raise HTTPException(status_code=503, detail="Conversation history is unavailable.")
    async with engine.connect() as connection:
        rows = (await connection.execute(text(
            """SELECT query_text, response_text, retrieved_chunks, created_at
               FROM query_runs
               WHERE user_uuid = :user_id AND metadata->>'conversation_id' = :conversation_id
               ORDER BY created_at"""
        ), {"user_id": UUID(user.user_id), "conversation_id": str(conversation_id)})).mappings().all()
    if not rows:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    messages: list[dict[str, Any]] = []
    for row in rows:
        messages.append({"role": "user", "text": row["query_text"]})
        messages.append({"role": "assistant", "text": row["response_text"], "sources": row["retrieved_chunks"] or []})
    return messages


@router.get("/admin/users")
async def admin_users(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100, alias="pageSize"),
    status_filter: str = Query("all", alias="status", pattern="^(all|active|pending|disabled)$"),
    search: str = Query("", max_length=320),
    _: AuthenticatedUser = Depends(require_admin_user),
) -> dict[str, Any]:
    engine = request.app.state.database_engine
    conditions: list[str] = []
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
    if status_filter != "all":
        conditions.append("account_status = :account_status")
        params["account_status"] = status_filter
    if search.strip():
        conditions.append("(full_name ILIKE :search OR email ILIKE :search)")
        params["search"] = f"%{search.strip()}%"
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    async with engine.connect() as connection:
        total = await connection.scalar(text(f"SELECT count(*) FROM users {where_clause}"), params)
        rows = (await connection.execute(text(
            f"""SELECT user_uuid AS id, full_name AS name, email,
                       created_at AS registered_at, account_status AS status
                FROM users {where_clause}
                ORDER BY created_at DESC LIMIT :limit OFFSET :offset"""
        ), params)).mappings().all()
    return {"items": [dict(row) for row in rows], "page": page,
            "pageSize": page_size, "total": total or 0}


@router.get("/admin/auth-activity")
async def admin_activity(
    request: Request,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> list[dict[str, Any]]:
    engine = request.app.state.database_engine
    async with engine.connect() as connection:
        rows = (await connection.execute(text(
            """SELECT l.user_activity_log_uuid AS id, l.user_uuid AS user_id,
                      l.session_uuid AS session_id, l.event_type,
                      l.created_at AS occurred_at, s.logout_at, s.expires_at,
                      'SUCCESS' AS event_status
               FROM user_activity_logs l
               LEFT JOIN user_sessions s ON s.session_uuid = l.session_uuid
               ORDER BY l.created_at DESC LIMIT 200"""
        ))).mappings().all()
    return [dict(row) for row in rows]


@router.get("/admin/sessions")
async def admin_sessions(
    request: Request,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> list[dict[str, Any]]:
    engine = request.app.state.database_engine
    async with engine.connect() as connection:
        rows = (await connection.execute(text(
            """SELECT s.session_uuid AS session_id, s.user_uuid AS user_id,
                      u.full_name AS name, u.email, s.login_at AS created_at,
                      s.last_activity_at AS last_activity, s.expires_at,
                      s.logout_at, s.status
               FROM user_sessions s JOIN users u USING (user_uuid)
               ORDER BY s.login_at DESC LIMIT 200"""
        ))).mappings().all()
    return [dict(row) for row in rows]


@router.get("/admin/analytics")
async def admin_analytics(
    request: Request,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    _: AuthenticatedUser = Depends(require_admin_user),
) -> dict[str, Any]:
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    end_date = end or today
    start_date = start or end_date - timedelta(days=6)
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="The start date must be on or before the end date.")
    engine = request.app.state.database_engine
    async with engine.connect() as connection:
        total = await connection.scalar(text(
            """SELECT count(*) FROM query_runs
               WHERE request_type = 'CHAT'
                 AND (created_at AT TIME ZONE 'Asia/Kolkata')::date BETWEEN :start AND :end"""
        ), {"start": start_date, "end": end_date})
        rows = (await connection.execute(text(
            """WITH days AS (
                   SELECT generate_series(CAST(:start AS date), CAST(:end AS date), interval '1 day')::date AS day
               ), daily AS (
                   SELECT (created_at AT TIME ZONE 'Asia/Kolkata')::date AS day, count(*) AS queries
                   FROM query_runs
                   WHERE request_type = 'CHAT'
                     AND (created_at AT TIME ZONE 'Asia/Kolkata')::date BETWEEN :start AND :end
                   GROUP BY 1
               )
               SELECT days.day AS date, COALESCE(daily.queries, 0) AS queries
               FROM days LEFT JOIN daily USING (day) ORDER BY days.day"""
        ), {"start": start_date, "end": end_date})).mappings().all()
    return {
        "totalQueries": total or 0,
        "source": "query_runs",
        "daily": [{"date": row["date"].isoformat(), "queries": row["queries"]} for row in rows],
    }
