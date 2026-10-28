"""Groq chat client from Pratik's retrieval branch."""

import threading

from groq import APITimeoutError, Groq


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
        self._client: Groq | None = None
        self._lock = threading.Lock()

    def _get_client(self) -> Groq:
        with self._lock:
            if self._client is None:
                self._client = Groq(
                    api_key=self._api_key,
                    timeout=self.timeout_seconds,
                    max_retries=0,
                )
        return self._client

    def warmup(self) -> None:
        self._get_client()

    def complete(self, system: str, user: str) -> str:
        extra = {}
        if self.reasoning_effort:
            extra = {"reasoning_effort": self.reasoning_effort, "include_reasoning": False}
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
        return (response.choices[0].message.content or "").strip()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
