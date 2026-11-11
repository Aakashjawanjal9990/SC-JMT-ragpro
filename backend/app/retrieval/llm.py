"""Groq chat client from Pratik's retrieval branch."""

import threading
from typing import Any

from app.ports import DependencyRateLimited


class GroqChat:
    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        max_tokens: int,
        timeout_seconds: float,
        reasoning_effort: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("GROQ_API_KEY is required for the configured Groq model")
        self.model = model
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.reasoning_effort = reasoning_effort
        self._api_key = api_key
        self._client: Any | None = None
        self._lock = threading.Lock()

    def _get_client(self) -> Any:
        with self._lock:
            if self._client is None:
                from groq import Groq

                self._client = Groq(
                    api_key=self._api_key,
                    timeout=self.timeout_seconds,
                    max_retries=2,
                )
        return self._client

    def warmup(self) -> None:
        self._get_client()

    def healthcheck(self) -> bool:
        response = self._get_client().models.list()
        return any(model.id == self.model for model in response.data)

    def complete(
        self,
        system: str,
        user: str,
        *,
        reasoning_effort: str | None = None,
    ) -> str:
        from groq import APITimeoutError, RateLimitError

        extra = {}
        effective_reasoning_effort = reasoning_effort or self.reasoning_effort
        if effective_reasoning_effort:
            extra = {
                "reasoning_effort": effective_reasoning_effort,
                "include_reasoning": False,
            }
        try:
            response = self._get_client().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0,
                max_tokens=self.max_tokens,
                **extra,
            )
        except APITimeoutError as exc:
            raise TimeoutError(
                f"{self.model} did not reply within {self.timeout_seconds}s"
            ) from exc
        except RateLimitError as exc:
            retry_after_value = exc.response.headers.get("retry-after", "30")
            try:
                retry_after = int(float(retry_after_value))
            except (TypeError, ValueError):
                retry_after = 30
            raise DependencyRateLimited(
                "The answer provider is rate limiting requests.", retry_after
            ) from exc
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise RuntimeError("The answer was truncated at the model token limit.")
        return (choice.message.content or "").strip()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
