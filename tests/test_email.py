"""Unit tests for SMTPEmailSender.

Run from the project root with:
    $env:PYTHONPATH = "$PWD\backend"
    $env:EMAIL_MODULE = "app.email"   # change if the implementation has another module path
    python -m pytest tests/test_email_sender.py --cov=app.email --cov-report=term-missing -v

If your module is not app.email, update EMAIL_MODULE and the --cov target to match.
All SMTP/network interactions are mocked; these tests do not send real emails.
"""

import asyncio
import importlib
import os
import smtplib
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


MODULE_NAME = os.getenv("EMAIL_MODULE", "app.email")
email_module = importlib.import_module(MODULE_NAME)
SMTPEmailSender = email_module.SMTPEmailSender
DependencyUnavailable = email_module.DependencyUnavailable


def make_settings(**overrides):
    """Return a minimal Settings-like object with safe test defaults."""
    values = {
        "smtp_host": "smtp.example.test",
        "smtp_port": 587,
        "smtp_username": "test-user",
        "smtp_password": "test-password",
        "smtp_from_email": "noreply@example.test",
        "smtp_starttls": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def run(coro):
    """Run an async method without requiring pytest-asyncio."""
    return asyncio.run(coro)


def test_init_copies_smtp_settings():
    settings = make_settings()
    sender = SMTPEmailSender(settings)

    assert sender._host == settings.smtp_host
    assert sender._port == settings.smtp_port
    assert sender._username == settings.smtp_username
    assert sender._password == settings.smtp_password
    assert sender._from_email == settings.smtp_from_email
    assert sender._starttls is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"smtp_host": ""},
        {"smtp_host": None},
        {"smtp_from_email": ""},
        {"smtp_from_email": None},
    ],
)
def test_send_code_raises_when_delivery_is_not_configured(overrides):
    sender = SMTPEmailSender(make_settings(**overrides))

    with pytest.raises(DependencyUnavailable, match="not configured"):
        run(sender.send_code("person@example.test", "verify", "123456"))


def test_send_code_builds_verification_email_and_dispatches(monkeypatch):
    sender = SMTPEmailSender(make_settings())
    captured = {}

    async def fake_to_thread(function, message):
        captured["function"] = function
        captured["message"] = message

    monkeypatch.setattr(email_module.asyncio, "to_thread", fake_to_thread)

    run(sender.send_code("person@example.test", "verify", "123456"))

    message = captured["message"]
    assert isinstance(message, EmailMessage)
    assert message["Subject"] == "Verify your Nyaya-Mitra account"
    assert message["From"] == "noreply@example.test"
    assert message["To"] == "person@example.test"
    body = message.get_content()
    assert "123456" in body
    assert "expires in 10 minutes" in body
    assert "ignore this email" in body
    assert captured["function"] == sender._send_sync


def test_send_code_uses_password_reset_subject_for_non_verify_purpose(monkeypatch):
    sender = SMTPEmailSender(make_settings())
    captured = {}

    async def fake_to_thread(function, message):
        captured["message"] = message

    monkeypatch.setattr(email_module.asyncio, "to_thread", fake_to_thread)

    run(sender.send_code("person@example.test", "reset", "654321"))

    assert captured["message"]["Subject"] == "Reset your Nyaya-Mitra password"
    assert "654321" in captured["message"].get_content()


@pytest.mark.parametrize(
    "exception",
    [
        OSError("network unavailable"),
        smtplib.SMTPException("SMTP failure"),
    ],
)
def test_send_code_wraps_delivery_errors_as_dependency_unavailable(monkeypatch, exception):
    sender = SMTPEmailSender(make_settings())

    async def failing_to_thread(*args, **kwargs):
        raise exception

    monkeypatch.setattr(email_module.asyncio, "to_thread", failing_to_thread)

    with pytest.raises(DependencyUnavailable, match="temporarily unavailable") as exc_info:
        run(sender.send_code("person@example.test", "verify", "123456"))

    assert exc_info.value.__cause__ is exception


def test_send_sync_uses_smtp_starttls_login_and_send_message(monkeypatch):
    settings = make_settings(smtp_starttls=True)
    sender = SMTPEmailSender(settings)
    smtp_client = MagicMock()
    smtp_context = MagicMock()
    smtp_context.__enter__.return_value = smtp_client
    smtp_context.__exit__.return_value = False
    smtp_factory = MagicMock(return_value=smtp_context)
    monkeypatch.setattr(email_module.smtplib, "SMTP", smtp_factory)
    message = EmailMessage()
    message["To"] = "person@example.test"

    sender._send_sync(message)

    smtp_factory.assert_called_once_with(settings.smtp_host, settings.smtp_port, timeout=10)
    smtp_client.starttls.assert_called_once_with()
    smtp_client.login.assert_called_once_with(settings.smtp_username, settings.smtp_password)
    smtp_client.send_message.assert_called_once_with(message)
    smtp_context.__exit__.assert_called_once()


def test_send_sync_skips_starttls_when_disabled(monkeypatch):
    sender = SMTPEmailSender(make_settings(smtp_starttls=False))
    smtp_client = MagicMock()
    smtp_context = MagicMock()
    smtp_context.__enter__.return_value = smtp_client
    smtp_context.__exit__.return_value = False
    monkeypatch.setattr(email_module.smtplib, "SMTP", MagicMock(return_value=smtp_context))
    message = EmailMessage()

    sender._send_sync(message)

    smtp_client.starttls.assert_not_called()
    smtp_client.send_message.assert_called_once_with(message)


@pytest.mark.parametrize(
    "username,password,should_login",
    [
        ("", "secret", False),
        ("user", "", False),
        ("", "", False),
        ("user", "secret", True),
    ],
)
def test_send_sync_logs_in_only_when_both_credentials_are_present(
    monkeypatch, username, password, should_login
):
    sender = SMTPEmailSender(
        make_settings(smtp_starttls=False, smtp_username=username, smtp_password=password)
    )
    smtp_client = MagicMock()
    smtp_context = MagicMock()
    smtp_context.__enter__.return_value = smtp_client
    smtp_context.__exit__.return_value = False
    monkeypatch.setattr(email_module.smtplib, "SMTP", MagicMock(return_value=smtp_context))

    sender._send_sync(EmailMessage())

    if should_login:
        smtp_client.login.assert_called_once_with(username, password)
    else:
        smtp_client.login.assert_not_called()
    smtp_client.send_message.assert_called_once()


def test_send_sync_propagates_smtp_errors_to_async_wrapper(monkeypatch):
    sender = SMTPEmailSender(make_settings())
    smtp_factory = MagicMock(side_effect=smtplib.SMTPException("cannot connect"))
    monkeypatch.setattr(email_module.smtplib, "SMTP", smtp_factory)

    with pytest.raises(smtplib.SMTPException, match="cannot connect"):
        sender._send_sync(EmailMessage())
