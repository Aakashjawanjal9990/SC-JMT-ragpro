"""Groq chat client shared by the pseudo query step and answer generation.

The API key is read from the environment variable named in config.yaml (on Cloud Run,
--set-secrets GROQ_API_KEY=...:latest), never from config.yaml itself.
"""

import os
import threading


class GroqChat:
    def __init__(
        self,
        model: str,
        api_key_env: str,
        *,
        max_tokens: int,
        timeout_seconds: float,
        reasoning_effort: str | None = None,
    ) -> None:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise ValueError(f"Groq model {model} needs the {api_key_env} environment variable")
        self.model = model
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.reasoning_effort = reasoning_effort
        self._api_key = api_key
        self._client = None
        self._lock = threading.Lock()

    def _get_client(self):
        with self._lock:
            if self._client is None:
                from groq import Groq

                # No retries: callers fall back or fail within their own deadline.
                self._client = Groq(api_key=self._api_key, timeout=self.timeout_seconds, max_retries=0)
        return self._client

    def warmup(self) -> None:
        self._get_client()

    def complete(self, system: str, user: str) -> str:
        """The model's reply to one system + user message (empty string if none)."""
        extra = {}
        if self.reasoning_effort:
            # Reasoning models (gpt-oss, qwen3): reasoning is used but not returned.
            extra = {"reasoning_effort": self.reasoning_effort, "include_reasoning": False}
        from groq import APITimeoutError

        try:
            response = (self._client or self._get_client()).chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0,
                max_tokens=self.max_tokens,
                **extra,
            )
        except APITimeoutError as exc:
            raise TimeoutError(f"{self.model} did not reply within {self.timeout_seconds}s") from exc
        return (response.choices[0].message.content or "").strip()
