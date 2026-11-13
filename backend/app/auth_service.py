"""Password, email-code, and opaque session-token operations."""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import secrets
import struct
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.email import SMTPEmailSender
from app.ports import DependencyUnavailable
from app.security import AuthenticatedUser
from app.settings import Settings

CODE_TTL_SECONDS = 600
MAX_CODE_ATTEMPTS = 5
SESSION_DAYS = 30
ADMIN_SESSION_HOURS = 8
TOTP_PERIOD_SECONDS = 30
logger = logging.getLogger("supreme_court_rag.security")


class InvalidMfaSecret(RuntimeError):
    """Stored MFA ciphertext cannot be authenticated or decoded safely."""


_CONSUME_CHALLENGE_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return {0, ''} end
local payload = cjson.decode(raw)
if payload['code_hash'] == ARGV[1] then
  redis.call('DEL', KEYS[1], KEYS[2], KEYS[3])
  return {1, payload['subject']}
end
local attempts = redis.call('INCR', KEYS[2])
if attempts == 1 then redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3])) end
if attempts >= tonumber(ARGV[2]) then redis.call('DEL', KEYS[1], KEYS[2], KEYS[3]) end
return {2, ''}
"""


class AuthService:
    """Implements auth using the existing users, user_sessions, and Redis stores."""

    def __init__(
        self,
        engine: AsyncEngine,
        redis: Redis,
        settings: Settings,
        email_sender: SMTPEmailSender,
    ) -> None:
        self._engine = engine
        self._redis = redis
        self._email_sender = email_sender
        self._settings = settings
        self._challenge_secret = (
            settings.auth_challenge_secret.encode("utf-8")
            if settings.auth_challenge_secret
            else secrets.token_bytes(32)
        )
        encryption_key = hmac.new(
            self._challenge_secret, b"admin-totp-encryption:v1", hashlib.sha256
        ).digest()
        self._totp_cipher = AESGCM(encryption_key)
        self._password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
        self._dummy_hash = self._password_hasher.hash(secrets.token_urlsafe(32))

    async def healthcheck(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1 FROM users LIMIT 1"))
            return bool(await self._redis.ping())
        except (SQLAlchemyError, OSError):
            return False

    async def begin_admin_mfa_setup(
        self, user_id: str, password: str, current_code: str | None
    ) -> dict[str, str]:
        account = await self._fetch_user_by_id(UUID(user_id))
        if not account or not self._verify_password(account["password_hash"], password):
            raise PermissionError("Password is incorrect.")
        if account["admin_mfa_enabled_at"] is not None:
            active_secret = self._decrypt_totp_secret(
                account["admin_mfa_secret_ciphertext"], user_id
            )
            active_counter = self._matching_totp_counter(active_secret, current_code or "")
            if active_counter is None or not await self._consume_admin_totp_counter(
                UUID(user_id), active_counter
            ):
                raise PermissionError("Current authenticator code is incorrect.")
        secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
        nonce = secrets.token_bytes(12)
        encrypted_secret = self._totp_cipher.encrypt(
            nonce, secret.encode("ascii"), user_id.encode()
        )
        encrypted = nonce + encrypted_secret
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """UPDATE users SET admin_mfa_pending_secret_ciphertext = :secret,
                              admin_mfa_pending_expires_at = now() + interval '10 minutes'
                       WHERE user_uuid = :user_id AND role = 'admin'
                         AND account_status = 'active'"""
                ),
                {
                    "secret": base64.urlsafe_b64encode(encrypted).decode("ascii"),
                    "user_id": UUID(user_id),
                },
            )
            if result.rowcount != 1:
                raise ValueError("Active administrator account not found.")
        label = quote(f"Supreme Court RAG:{user_id}")
        uri = (
            f"otpauth://totp/{label}?secret={secret}&issuer=Supreme%20Court%20RAG"
            "&algorithm=SHA1&digits=6&period=30"
        )
        return {"secret": secret, "otpauthUri": uri}

    async def admin_mfa_status(self, user_id: str) -> dict[str, bool]:
        async with self._engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        """SELECT admin_mfa_enabled_at
                           FROM users WHERE user_uuid = :user_id
                             AND role = 'admin' AND account_status = 'active'"""
                    ),
                    {"user_id": UUID(user_id)},
                )
            ).mappings().one_or_none()
        if row is None:
            raise ValueError("Active administrator account not found.")
        return {
            "enabled": row["admin_mfa_enabled_at"] is not None,
            "required": self._settings.admin_mfa_required,
        }

    async def confirm_admin_mfa_setup(self, user_id: str, code: str) -> None:
        async with self._engine.begin() as connection:
            row = (
                await connection.execute(
                    text(
                        """SELECT admin_mfa_pending_secret_ciphertext,
                                  admin_mfa_pending_expires_at
                           FROM users WHERE user_uuid = :user_id AND role = 'admin'
                           FOR UPDATE"""
                    ),
                    {"user_id": UUID(user_id)},
                )
            ).mappings().one_or_none()
            if not row or not row["admin_mfa_pending_secret_ciphertext"]:
                raise ValueError("Start MFA setup before confirming it.")
            if (
                not row["admin_mfa_pending_expires_at"]
                or row["admin_mfa_pending_expires_at"] < datetime.now(UTC)
            ):
                raise ValueError("MFA setup expired. Start setup again.")
            secret = self._decrypt_totp_secret(
                row["admin_mfa_pending_secret_ciphertext"], user_id
            )
            counter = self._matching_totp_counter(secret, code)
            if counter is None:
                raise ValueError("Authenticator code is invalid or expired.")
            result = await connection.execute(
                text(
                    """UPDATE users SET admin_mfa_secret_ciphertext =
                              admin_mfa_pending_secret_ciphertext,
                              admin_mfa_enabled_at = now(), admin_mfa_last_counter = :counter,
                              admin_mfa_pending_secret_ciphertext = NULL,
                              admin_mfa_pending_expires_at = NULL, updated_at = now()
                       WHERE user_uuid = :user_id AND role = 'admin'
                         AND admin_mfa_pending_expires_at > now()"""
                ),
                {"counter": counter, "user_id": UUID(user_id)},
            )
            if result.rowcount != 1:
                raise ValueError("MFA setup expired. Start setup again.")
            await connection.execute(
                text(
                    """UPDATE user_sessions SET status = 'REVOKED', logout_at = now()
                       WHERE user_uuid = :user_id AND status = 'ACTIVE'"""
                ),
                {"user_id": UUID(user_id)},
            )
        await self._record_admin_mfa_activity(
            UUID(user_id), "ADMIN_MFA_ENABLED", "Administrator enabled MFA"
        )

    async def disable_admin_mfa(self, user_id: str, password: str, code: str) -> None:
        if self._settings.admin_mfa_required:
            raise PermissionError("Admin MFA is required by the current security policy.")
        row = await self._fetch_user_by_id(UUID(user_id))
        if not row or not self._verify_password(row["password_hash"], password):
            raise PermissionError("Password or authenticator code is incorrect.")
        if not row["admin_mfa_secret_ciphertext"]:
            raise ValueError("MFA is not enabled.")
        secret = self._decrypt_totp_secret(row["admin_mfa_secret_ciphertext"], user_id)
        counter = self._matching_totp_counter(secret, code)
        if counter is None or not await self._consume_admin_totp_counter(UUID(user_id), counter):
            raise PermissionError("Password or authenticator code is incorrect.")
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """UPDATE users SET admin_mfa_secret_ciphertext = NULL,
                              admin_mfa_enabled_at = NULL, admin_mfa_last_counter = -1,
                              admin_mfa_pending_secret_ciphertext = NULL,
                              admin_mfa_pending_expires_at = NULL,
                              updated_at = now() WHERE user_uuid = :user_id AND role = 'admin'"""
                ),
                {"user_id": UUID(user_id)},
            )
        await self._record_admin_mfa_activity(
            UUID(user_id), "ADMIN_MFA_DISABLED", "Administrator disabled MFA"
        )

    @staticmethod
    def _totp_code(secret: str, counter: int) -> str:
        key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
        digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
        return f"{binary % 1_000_000:06d}"

    @classmethod
    def _matching_totp_counter(cls, secret: str, code: str) -> int | None:
        if len(code) != 6 or not code.isdigit():
            return None
        current = int(time.time()) // TOTP_PERIOD_SECONDS
        for counter in (current - 1, current, current + 1):
            if counter >= 0 and hmac.compare_digest(cls._totp_code(secret, counter), code):
                return counter
        return None

    def _decrypt_totp_secret(self, ciphertext: str, user_id: str) -> str:
        try:
            raw = base64.b64decode(ciphertext.encode("ascii"), altchars=b"-_", validate=True)
            if len(raw) < 12 + 16:
                raise ValueError("Ciphertext is too short.")
            return self._totp_cipher.decrypt(
                raw[:12], raw[12:], user_id.encode("ascii")
            ).decode("ascii")
        except (binascii.Error, InvalidTag, UnicodeDecodeError, TypeError, ValueError) as exc:
            raise InvalidMfaSecret("Stored MFA secret is invalid.") from exc

    async def _consume_admin_totp_counter(self, user_id: UUID, counter: int) -> bool:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """UPDATE users SET admin_mfa_last_counter = :counter
                       WHERE user_uuid = :user_id AND role = 'admin'
                         AND admin_mfa_enabled_at IS NOT NULL
                         AND admin_mfa_last_counter < :counter"""
                ),
                {"counter": counter, "user_id": user_id},
            )
            return result.rowcount == 1

    async def register(
        self,
        name: str,
        email: str,
        password: str,
        *,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        normalized_email = self._normalize_email(email)
        user_id = uuid4()
        password_hash = self._password_hasher.hash(password)
        try:
            async with self._engine.begin() as connection:
                await connection.execute(
                    text(
                        """INSERT INTO users (
                               user_uuid, username, email, password_hash, full_name,
                               role, account_status
                           ) VALUES (
                               :user_id, :username, :email, :password_hash, :name,
                               'user', 'pending'
                           )"""
                    ),
                    {
                        "user_id": user_id,
                        "username": normalized_email,
                        "email": normalized_email,
                        "password_hash": password_hash,
                        "name": name.strip(),
                    },
                )
        except IntegrityError as exc:
            raise ValueError("An account with that email may already exist.") from exc
        await self.create_and_send_challenge(normalized_email, "verify", str(user_id))
        await self._record_activity(
            user_id, None, "REGISTER", "Account registration requested", ip_address, user_agent
        )

    async def verify_email(
        self,
        email: str,
        code: str,
        *,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[str, dict[str, str]]:
        normalized_email = self._normalize_email(email)
        challenge = await self._consume_challenge(normalized_email, "verify", code)
        try:
            user_id = UUID(challenge["subject"])
        except (KeyError, ValueError) as exc:
            raise ValueError("The code is invalid or expired.") from exc
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """UPDATE users SET account_status = 'active', updated_at = now()
                       WHERE user_uuid = :user_id AND email = :email
                         AND account_status = 'pending'
                       RETURNING user_uuid, email, full_name, role"""
                ),
                {"user_id": user_id, "email": normalized_email},
            )
            user = result.mappings().one_or_none()
        if user is None:
            raise ValueError("The code is invalid or expired.")
        token, authenticated = await self._create_session(user, ip_address, user_agent, admin=False)
        await self._record_activity(
            user_id,
            UUID(authenticated.session_id),
            "EMAIL_VERIFIED",
            "Email verification completed",
            ip_address,
            user_agent,
        )
        return token, self._public_user(user)

    async def resend_verification(self, email: str) -> None:
        normalized_email = self._normalize_email(email)
        result = await self._fetch_user_by_email(normalized_email)
        if result is None or result["account_status"] != "pending":
            return
        await self.create_and_send_challenge(normalized_email, "verify", str(result["user_uuid"]))

    async def login(
        self,
        email: str,
        password: str,
        *,
        ip_address: str | None,
        user_agent: str | None,
        admin: bool = False,
        mfa_code: str | None = None,
    ) -> tuple[str, dict[str, str], AuthenticatedUser]:
        normalized_email = self._normalize_email(email)
        result = await self._fetch_user_by_email(normalized_email)
        stored_hash = result["password_hash"] if result else self._dummy_hash
        if not self._verify_password(stored_hash, password):
            await self._log_auth_failure(
                "login_rejected",
                normalized_email,
                ip_address,
                user_agent,
                user_id=UUID(str(result["user_uuid"])) if result else None,
            )
            raise PermissionError("Email or password is incorrect.")
        if result is None or result["account_status"] != "active":
            if result and result["account_status"] == "pending":
                await self._log_auth_failure(
                    "login_unverified",
                    normalized_email,
                    ip_address,
                    user_agent,
                    user_id=UUID(str(result["user_uuid"])),
                )
                raise PermissionError("Verify your email before signing in.")
            await self._log_auth_failure(
                "login_rejected",
                normalized_email,
                ip_address,
                user_agent,
                user_id=UUID(str(result["user_uuid"])) if result else None,
            )
            raise PermissionError("Email or password is incorrect.")
        if admin and result["role"] != "admin":
            await self._log_auth_failure(
                "admin_login_rejected",
                normalized_email,
                ip_address,
                user_agent,
                user_id=UUID(str(result["user_uuid"])),
            )
            raise PermissionError("Admin credentials are incorrect.")
        if admin and (
            (result["admin_mfa_enabled_at"] is not None)
            != bool(result["admin_mfa_secret_ciphertext"])
        ):
            await self._log_auth_failure(
                "admin_login_mfa_state_invalid",
                normalized_email,
                ip_address,
                user_agent,
                user_id=UUID(str(result["user_uuid"])),
            )
            raise PermissionError("Admin credentials or authenticator code are incorrect.")
        if admin and self._settings.admin_mfa_required and result["admin_mfa_enabled_at"] is None:
            await self._log_auth_failure(
                "admin_login_mfa_enrollment_required",
                normalized_email,
                ip_address,
                user_agent,
                user_id=UUID(str(result["user_uuid"])),
            )
            raise PermissionError("Admin MFA enrollment is required before signing in.")
        if admin and result["admin_mfa_enabled_at"] is not None:
            try:
                secret = self._decrypt_totp_secret(
                    result["admin_mfa_secret_ciphertext"], str(result["user_uuid"])
                )
            except InvalidMfaSecret as exc:
                await self._log_auth_failure(
                    "admin_mfa_secret_invalid",
                    normalized_email,
                    ip_address,
                    user_agent,
                    user_id=UUID(str(result["user_uuid"])),
                )
                raise PermissionError(
                    "Admin credentials or authenticator code are incorrect."
                ) from exc
            counter = self._matching_totp_counter(secret, mfa_code or "")
            if counter is None or not await self._consume_admin_totp_counter(
                UUID(str(result["user_uuid"])), counter
            ):
                await self._log_auth_failure(
                    "admin_mfa_rejected",
                    normalized_email,
                    ip_address,
                    user_agent,
                    user_id=UUID(str(result["user_uuid"])),
                )
                raise PermissionError("Admin credentials or authenticator code are incorrect.")
            admin_mfa_verified = True
        else:
            admin_mfa_verified = False
        if not admin and result["role"] == "admin":
            # A regular login would otherwise grant admin APIs a 30-day session.
            await self._log_auth_failure(
                "admin_login_requires_admin_flow",
                normalized_email,
                ip_address,
                user_agent,
                user_id=UUID(str(result["user_uuid"])),
            )
            raise PermissionError("Email or password is incorrect.")
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE users SET last_login_at = now(), updated_at = now() "
                    "WHERE user_uuid = :id"
                ),
                {"id": result["user_uuid"]},
            )
        token, user = await self._create_session(
            result,
            ip_address,
            user_agent,
            admin=admin,
            admin_mfa_verified=admin_mfa_verified,
        )
        await self._record_activity(
            UUID(str(result["user_uuid"])),
            UUID(user.session_id),
            "ADMIN_LOGIN" if admin else "LOGIN",
            "User signed in",
            ip_address,
            user_agent,
        )
        return token, self._public_user(result), user

    async def request_password_reset(self, email: str) -> None:
        normalized_email = self._normalize_email(email)
        user = await self._fetch_user_by_email(normalized_email)
        if user is None or user["account_status"] != "active":
            return
        await self.create_and_send_challenge(normalized_email, "reset", str(user["user_uuid"]))

    async def reset_password(self, email: str, code: str, password: str) -> None:
        normalized_email = self._normalize_email(email)
        challenge = await self._consume_challenge(normalized_email, "reset", code)
        try:
            user_id = UUID(challenge["subject"])
        except (KeyError, ValueError) as exc:
            raise ValueError("The code is invalid or expired.") from exc
        new_hash = self._password_hasher.hash(password)
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """UPDATE users SET password_hash = :password_hash, updated_at = now()
                       WHERE user_uuid = :user_id AND email = :email
                         AND account_status = 'active'"""
                ),
                {"password_hash": new_hash, "user_id": user_id, "email": normalized_email},
            )
            if result.rowcount != 1:
                raise ValueError("The code is invalid or expired.")
            await connection.execute(
                text(
                    """UPDATE user_sessions SET status = 'REVOKED', logout_at = now()
                       WHERE user_uuid = :user_id AND status = 'ACTIVE'"""
                ),
                {"user_id": user_id},
            )
        await self._record_activity(
            user_id, None, "PASSWORD_RESET", "Password reset completed", None, None
        )

    async def authenticate(self, token: str) -> AuthenticatedUser | None:
        token_hash = self._token_hash(token)
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """SELECT u.user_uuid, u.email, u.full_name, u.role,
                              s.session_uuid,
                              (s.admin_mfa_verified AND u.admin_mfa_enabled_at IS NOT NULL)
                                  AS admin_mfa_verified
                       FROM user_sessions AS s
                       JOIN users AS u ON u.user_uuid = s.user_uuid
                       WHERE s.session_token_hash = :token_hash
                         AND s.status = 'ACTIVE' AND s.logout_at IS NULL
                         AND s.expires_at > now() AND u.account_status = 'active'"""
                ),
                {"token_hash": token_hash},
            )
            row = result.mappings().one_or_none()
            if row is None:
                return None
            await connection.execute(
                text(
                    "UPDATE user_sessions SET last_activity_at = now() "
                    "WHERE session_uuid = :session_id"
                ),
                {"session_id": row["session_uuid"]},
            )
        return AuthenticatedUser(
            subject=str(row["user_uuid"]),
            user_id=str(row["user_uuid"]),
            email=row["email"],
            name=row["full_name"] or row["email"],
            role=row["role"],
            session_id=str(row["session_uuid"]),
            token_hash=token_hash,
            admin_mfa_verified=bool(row["admin_mfa_verified"]),
        )

    async def logout(self, token: str) -> None:
        token_hash = self._token_hash(token)
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """UPDATE user_sessions
                       SET status = 'REVOKED', logout_at = now()
                       WHERE session_token_hash = :token_hash AND status = 'ACTIVE'
                       RETURNING session_uuid, user_uuid"""
                ),
                {"token_hash": token_hash},
            )
            session = result.mappings().one_or_none()
            role = None
            if session is not None:
                role = await connection.scalar(
                    text("SELECT role FROM users WHERE user_uuid = :user_id"),
                    {"user_id": session["user_uuid"]},
                )
        if session is not None:
            event_type = "ADMIN_LOGOUT" if role == "admin" else "LOGOUT"
            try:
                await self._record_activity(
                    session["user_uuid"],
                    session["session_uuid"],
                    event_type,
                    "Administrator signed out"
                    if event_type == "ADMIN_LOGOUT"
                    else "User signed out",
                    None,
                    None,
                )
            except Exception:
                logger.exception("security.logout_activity_write_failed")

    async def _create_session(
        self,
        user,
        ip_address: str | None,
        user_agent: str | None,
        *,
        admin: bool,
        admin_mfa_verified: bool = False,
    ) -> tuple[str, AuthenticatedUser]:
        token = secrets.token_urlsafe(32)
        session_id = uuid4()
        expires_at = datetime.now(UTC) + (
            timedelta(hours=ADMIN_SESSION_HOURS) if admin else timedelta(days=SESSION_DAYS)
        )
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """INSERT INTO user_sessions (
                           session_uuid, user_uuid, session_token_hash, login_at,
                           last_activity_at, expires_at,
                           ip_address, user_agent, status, admin_mfa_verified
                       ) VALUES (
                           :session_id, :user_id, :token_hash, now(), now(), :expires_at,
                           CAST(:ip_address AS inet), :user_agent, 'ACTIVE', :mfa_verified
                       )"""
                ),
                {
                    "session_id": session_id,
                    "user_id": user["user_uuid"],
                    "token_hash": self._token_hash(token),
                    "expires_at": expires_at,
                    "ip_address": ip_address,
                    "user_agent": (user_agent or "")[:512],
                    "mfa_verified": admin_mfa_verified,
                },
            )
        authenticated = AuthenticatedUser(
            subject=str(user["user_uuid"]),
            user_id=str(user["user_uuid"]),
            email=user["email"],
            name=user["full_name"] or user["email"],
            role=user["role"],
            session_id=str(session_id),
            token_hash=self._token_hash(token),
            admin_mfa_verified=admin_mfa_verified,
        )
        return token, authenticated

    async def _fetch_user_by_email(self, email: str):
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """SELECT user_uuid, username, email, password_hash, full_name,
                              role, account_status, admin_mfa_secret_ciphertext,
                              admin_mfa_enabled_at
                       FROM users
                       WHERE lower(email) = :email OR lower(username) = :email
                       LIMIT 1"""
                ),
                {"email": email},
            )
            return result.mappings().one_or_none()

    async def _fetch_user_by_id(self, user_id: UUID):
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """SELECT user_uuid, email, password_hash, role,
                              admin_mfa_secret_ciphertext, admin_mfa_enabled_at
                       FROM users WHERE user_uuid = :user_id AND role = 'admin'"""
                ),
                {"user_id": user_id},
            )
            return result.mappings().one_or_none()

    async def _create_challenge(self, email: str, purpose: str, subject: str) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        challenge_key = self._challenge_key(email, purpose)
        payload = {
            "subject": subject,
            "code_hash": self._code_hash(email, purpose, code),
        }
        await self._redis.set(challenge_key, json.dumps(payload), ex=CODE_TTL_SECONDS)
        await self._redis.delete(challenge_key + ":attempts")
        return code

    async def _send_challenge(self, email: str, purpose: str) -> None:
        key = self._challenge_key(email, purpose)
        raw = await self._redis.get(key)
        if not raw:
            raise DependencyUnavailable("Verification code could not be prepared.")
        try:
            # The raw value is deliberately not stored. Recover only by storing it
            # in a separate short-lived delivery key before sending.
            delivery_key = key + ":delivery"
            code = await self._redis.get(delivery_key)
            if not code:
                raise DependencyUnavailable("Verification email could not be prepared.")
            await self._email_sender.send_code(email, purpose, code)
            await self._redis.delete(delivery_key)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise DependencyUnavailable("Verification email could not be prepared.") from exc

    async def create_and_send_challenge(self, email: str, purpose: str, subject: str) -> None:
        code = await self._create_challenge(email, purpose, subject)
        await self._redis.set(self._challenge_key(email, purpose) + ":delivery", code, ex=60)
        await self._send_challenge(email, purpose)

    async def _consume_challenge(self, email: str, purpose: str, code: str) -> dict[str, str]:
        key = self._challenge_key(email, purpose)
        attempts_key = key + ":attempts"
        delivery_key = key + ":delivery"
        outcome, subject = await self._redis.eval(
            _CONSUME_CHALLENGE_SCRIPT,
            3,
            key,
            attempts_key,
            delivery_key,
            self._code_hash(email, purpose, code),
            MAX_CODE_ATTEMPTS,
            CODE_TTL_SECONDS,
        )
        if int(outcome) != 1:
            await self._log_auth_failure(f"{purpose}_code_rejected", email, None)
            raise ValueError("The code is invalid or expired.")
        if not isinstance(subject, str) or not subject:
            raise ValueError("The code is invalid or expired.")
        return {"subject": subject}

    async def _log_auth_failure(
        self,
        event: str,
        identity: str,
        ip_address: str | None,
        user_agent: str | None = None,
        *,
        user_id: UUID | None = None,
    ) -> None:
        identity_hash = hashlib.sha256(identity.strip().lower().encode("utf-8")).hexdigest()
        logger.warning(
            "security.authentication.failure",
            extra={
                "security_event": event,
                "outcome": "denied",
                "subject_hash": identity_hash,
                "client_ip": ip_address,
            },
        )
        if event.startswith("admin_login"):
            event_type = "admin_login_failed"
        elif event.startswith("login"):
            event_type = "login_failed"
        else:
            event_type = event.replace("_rejected", "_failed")
        try:
            await self._record_activity(
                user_id,
                None,
                event_type,
                "Administrator sign-in failed"
                if event_type.startswith("admin_")
                else "Sign-in failed",
                ip_address,
                user_agent,
                metadata={"outcome": "FAILED", "subject_hash": identity_hash},
            )
        except Exception:
            # Authentication failures remain generic even if audit storage is unavailable.
            logger.exception("security.authentication.failure_audit_write_failed")

    async def _record_activity(
        self,
        user_id: UUID | None,
        session_id: UUID | None,
        event_type: str,
        action: str,
        ip_address: str | None,
        user_agent: str | None,
        *,
        metadata: dict[str, str] | None = None,
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """INSERT INTO user_activity_logs (
                           user_activity_log_uuid, user_uuid, session_uuid,
                           event_type, action, ip_address, user_agent, metadata
                       ) VALUES (
                           :event_id, :user_id, :session_id, :event_type,
                           :action, CAST(:ip_address AS inet), :user_agent,
                           CAST(:metadata AS jsonb)
                       )"""
                ),
                {
                    "event_id": uuid4(),
                    "user_id": user_id,
                    "session_id": session_id,
                    "event_type": event_type,
                    "action": action,
                    "ip_address": ip_address,
                    "user_agent": (user_agent or "")[:512],
                    "metadata": json.dumps(metadata or {}),
                },
            )

    async def _record_admin_mfa_activity(
        self, user_id: UUID, event_type: str, action: str
    ) -> None:
        try:
            await self._record_activity(user_id, None, event_type, action, None, None)
        except Exception:
            logger.exception(
                "security.admin_mfa_activity_write_failed",
                extra={"security_event": event_type, "user_id": str(user_id)},
            )

    def _verify_password(self, stored_hash: str, password: str) -> bool:
        try:
            return self._password_hasher.verify(stored_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()

    @staticmethod
    def _public_user(row) -> dict[str, str]:
        return {
            "id": str(row["user_uuid"]),
            "name": row["full_name"] or row["email"],
            "email": row["email"],
            "role": row["role"],
        }

    def _code_hash(self, email: str, purpose: str, code: str) -> str:
        message = f"{purpose}:{self._normalize_email(email)}:{code}".encode()
        return hmac.new(self._challenge_secret, message, hashlib.sha256).hexdigest()

    def csrf_token(self, session_token: str) -> str:
        """Return a session-bound CSRF token without storing another secret."""
        message = b"csrf:" + session_token.encode("utf-8")
        return hmac.new(self._challenge_secret, message, hashlib.sha256).hexdigest()

    def verify_csrf_token(self, session_token: str, supplied_token: str | None) -> bool:
        if not supplied_token or len(supplied_token) != 64:
            return False
        return hmac.compare_digest(self.csrf_token(session_token), supplied_token)

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _challenge_key(self, email: str, purpose: str) -> str:
        email_hash = hashlib.sha256(self._normalize_email(email).encode("utf-8")).hexdigest()
        return f"auth:challenge:{purpose}:{email_hash}"
