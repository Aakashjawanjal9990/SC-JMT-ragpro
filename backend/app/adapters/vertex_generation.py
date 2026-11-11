"""Evidence-grounded answer generation through Vertex AI Gemini."""

import asyncio
import json
from collections.abc import Sequence

from google import genai
from google.genai import types
from pydantic import ValidationError

from app.grounded_prompt import build_grounded_prompt
from app.models import ConversationTurn, EvidenceInput, GeneratedAnswer
from app.ports import AnswerGenerationPort, DependencyUnavailable
from app.settings import Settings

_ANSWER_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "answer": types.Schema(type=types.Type.STRING),
        "evidence_ids": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(type=types.Type.STRING),
        ),
    },
    required=["answer", "evidence_ids"],
)


class VertexAnswerGenerationAdapter(AnswerGenerationPort):
    def __init__(self, settings: Settings) -> None:
        if not self.is_configured(settings):
            raise ValueError("Vertex project, location, and model ID are required.")
        self._model = settings.vertex_model_id
        self._timeout = settings.vertex_generation_timeout_seconds
        self._client = genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )
        self._async_client = self._client.aio

    @staticmethod
    def is_configured(settings: Settings) -> bool:
        return bool(
            settings.google_cloud_project
            and settings.google_cloud_location
            and settings.vertex_model_id
        )

    async def generate(
        self,
        question: str,
        evidence: Sequence[EvidenceInput],
        *,
        think: bool = False,
        conversation_history: Sequence[ConversationTurn] = (),
    ) -> GeneratedAnswer:
        prompt = build_grounded_prompt(
            question,
            evidence,
            think=think,
            conversation_history=conversation_history,
        )
        try:
            async with asyncio.timeout(self._timeout):
                response = await self._async_client.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=_ANSWER_SCHEMA,
                        temperature=0,
                        max_output_tokens=4096,
                    ),
                )
        except Exception as exc:
            raise DependencyUnavailable("Vertex AI answer generation failed.") from exc
        if not response.text:
            return GeneratedAnswer(answer="", evidence_ids=[])
        try:
            return GeneratedAnswer.model_validate(json.loads(response.text))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise DependencyUnavailable("Vertex AI returned an invalid answer response.") from exc

    async def healthcheck(self) -> bool:
        try:
            await self._async_client.models.get(model=self._model)
        except Exception:
            return False
        return True

    async def close(self) -> None:
        await self._async_client.aclose()
        self._client.close()
