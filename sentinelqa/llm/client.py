from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

OutputT = TypeVar("OutputT", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    pass


class LLMClient(Protocol):
    provider: str

    async def structured(
        self,
        *,
        prompt_name: str,
        prompt_version: str,
        system: str,
        user: str,
        output_type: type[OutputT],
    ) -> OutputT: ...


class OpenAIResponsesClient:
    provider = "openai"

    def __init__(self, model: str, api_key: str | None = None) -> None:
        if not model:
            raise ValueError("LLM_MODEL must be configured for the OpenAI provider")
        from openai import AsyncOpenAI

        self.model = model
        self._client = AsyncOpenAI(api_key=api_key)

    async def structured(
        self,
        *,
        prompt_name: str,
        prompt_version: str,
        system: str,
        user: str,
        output_type: type[OutputT],
    ) -> OutputT:
        response = await self._client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            text_format=output_type,
        )
        if response.output_parsed is None:
            raise LLMUnavailable(f"{prompt_name}@{prompt_version} returned no parsed output")
        return response.output_parsed
