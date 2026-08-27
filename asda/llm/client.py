"""OpenAI-compatible LLM client. Providers: OpenRouter, SpaceXAI/xAI, FakeLLM."""

from __future__ import annotations

import logging
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from asda.config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    def complete(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        temperature: float = 0.4,
    ) -> str: ...

    def parse(
        self,
        system: str,
        user: str,
        schema: type[T],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
    ) -> T: ...


def wants_web(tools: list[dict[str, Any]] | None) -> bool:
    if not tools:
        return False
    for tool in tools:
        kind = str(tool.get("type") or tool.get("id") or "").lower()
        if kind in {"web_search", "web", "online"}:
            return True
    return False


def json_schema_for(schema: type[BaseModel]) -> dict[str, Any]:
    """Pydantic schema tightened for OpenRouter / OpenAI strict json_schema."""
    raw = schema.model_json_schema()
    defs = raw.get("$defs") or raw.get("definitions") or {}
    for node in (raw, *defs.values()):
        if not isinstance(node, dict):
            continue
        if node.get("type") == "object" or "properties" in node:
            props = node.get("properties") or {}
            node["type"] = "object"
            node["additionalProperties"] = False
            node["required"] = list(props.keys())
    raw.pop("title", None)
    return raw


def _extract_json(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


class OpenAICompatibleClient:
    """Works against OpenRouter and any other OpenAI-compatible /v1 endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        provider: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        if not api_key:
            raise RuntimeError(f"{provider} API key is not set")
        from openai import OpenAI

        self.provider = provider
        self._extra_headers = extra_headers or {}
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=self._extra_headers or None,
        )
        settings = get_settings()
        self.frontier = settings.resolve_model("frontier")
        self.fast = settings.resolve_model("fast")

    def _model(self, model: str | None, which: str = "frontier") -> str:
        settings = get_settings()
        return settings.remap_model(model or "", which=which)

    def complete(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        temperature: float = 0.4,
    ) -> str:
        resp = self._client.chat.completions.create(
            model=self._model(model),
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            extra_body=self._extra_body(web=False, structured=False),
        )
        return (resp.choices[0].message.content or "").strip()

    def parse(
        self,
        system: str,
        user: str,
        schema: type[T],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
    ) -> T:
        chosen = self._model(model)
        web = wants_web(tools)
        try:
            if self.provider == "xai":
                return self._parse_xai(system, user, schema, chosen, tools, temperature)
            # OpenRouter often 404s if json_schema + web plugin + require_parameters
            # are combined. Gather notes with search first, then constrain JSON.
            notes = user
            if web:
                notes = self._complete_with_web(system, user, chosen, temperature)
            return self._parse_openrouter(
                system + "\nFill every field. icp_rationale must be a single string.",
                notes if web else user,
                schema,
                chosen,
                False,
                temperature,
            )
        except Exception:
            logger.exception("Structured parse failed; falling back to JSON object")

        raw = self.complete(
            system + "\n\nReturn ONLY valid JSON matching the requested schema. Strings not objects for rationale fields.",
            user,
            model=chosen,
            temperature=temperature,
        )
        return schema.model_validate_json(_extract_json(raw))

    def _parse_openrouter(
        self,
        system: str,
        user: str,
        schema: type[T],
        model: str,
        web: bool,
        temperature: float,
    ) -> T:
        resp = self._client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": json_schema_for(schema),
                },
            },
            extra_body=self._extra_body(web=web, structured=True),
        )
        content = resp.choices[0].message.content or "{}"
        return schema.model_validate_json(_extract_json(content))

    def _parse_xai(
        self,
        system: str,
        user: str,
        schema: type[T],
        model: str,
        tools: list[dict[str, Any]] | None,
        temperature: float,
    ) -> T:
        if tools:
            resp = self._client.responses.parse(
                model=model,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                tools=tools,
                text_format=schema,
                temperature=temperature,
            )
            parsed = resp.output_parsed
            if parsed is not None:
                return parsed
        completion = self._client.beta.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=schema,
            temperature=temperature,
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise ValueError("xAI returned no parsed payload")
        return parsed

    def _complete_with_web(self, system: str, user: str, model: str, temperature: float) -> str:
        resp = self._client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            extra_body=self._extra_body(web=True, structured=False),
        )
        return (resp.choices[0].message.content or "").strip() or user

    def _extra_body(self, *, web: bool, structured: bool) -> dict[str, Any]:
        if self.provider != "openrouter":
            return {}
        body: dict[str, Any] = {}
        plugins: list[dict[str, Any]] = []
        if web:
            plugins.append({"id": "web", "max_results": 5})
        if plugins:
            body["plugins"] = plugins
        return body


class SpaceXAIClient(OpenAICompatibleClient):
    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        super().__init__(
            api_key=api_key or settings.xai_api_key,
            base_url=settings.xai_base_url,
            provider="xai",
        )


class OpenRouterClient(OpenAICompatibleClient):
    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        super().__init__(
            api_key=api_key or settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            provider="openrouter",
            extra_headers={
                "HTTP-Referer": settings.openrouter_site_url,
                "X-Title": settings.openrouter_app_name,
            },
        )


class FakeLLM:
    """Deterministic stand-in so tests and dry demos never need an API key."""

    def __init__(self, canned: dict[str, Any] | None = None) -> None:
        self.canned = canned or {}

    def complete(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        temperature: float = 0.4,
    ) -> str:
        return self.canned.get("complete", "ok")

    def parse(
        self,
        system: str,
        user: str,
        schema: type[T],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
    ) -> T:
        payload = self.canned.get(schema.__name__)
        if payload is None:
            return schema()
        if isinstance(payload, schema):
            return payload
        return schema.model_validate(payload)


_LLM: LLMClient | None = None


def _resolved_keys() -> tuple[str, str, str]:
    """OpenRouter / xAI keys from runtime overlay, then env."""
    try:
        from asda.runtime import effective

        e = effective()
        return e.openrouter_api_key, e.xai_api_key, ("openrouter" if e.openrouter_api_key else ("xai" if e.xai_api_key else "none"))
    except Exception:
        s = get_settings()
        if s.openrouter_api_key:
            return s.openrouter_api_key, s.xai_api_key, "openrouter"
        if s.xai_api_key:
            return s.openrouter_api_key, s.xai_api_key, "xai"
        return "", "", "none"


def get_llm() -> LLMClient:
    global _LLM
    openrouter, xai, provider = _resolved_keys()
    settings = get_settings()
    if settings.llm_provider in {"openrouter", "or"}:
        provider = "openrouter"
    elif settings.llm_provider in {"xai", "spacexai", "grok"}:
        provider = "xai"
    # If we cached FakeLLM but a key landed via the UI, rebuild.
    if _LLM is not None:
        if isinstance(_LLM, FakeLLM) and provider in {"openrouter", "xai"}:
            _LLM = None
        else:
            return _LLM
    if provider == "openrouter":
        if not openrouter:
            raise RuntimeError(
                "OpenRouter key is empty. Paste it on Home, or set OPENROUTER_API_KEY. "
                "https://openrouter.ai/keys"
            )
        logger.info(
            "LLM provider=openrouter frontier=%s fast=%s",
            settings.resolve_model("frontier"),
            settings.resolve_model("fast"),
        )
        _LLM = OpenRouterClient(api_key=openrouter)
    elif provider == "xai":
        if not xai:
            raise RuntimeError(
                "xAI key is empty. Paste it on Home, or set XAI_API_KEY. "
                "https://console.x.ai"
            )
        logger.info(
            "LLM provider=xai frontier=%s fast=%s",
            settings.resolve_model("frontier"),
            settings.resolve_model("fast"),
        )
        _LLM = SpaceXAIClient(api_key=xai)
    else:
        logger.warning(
            "No OPENROUTER_API_KEY or XAI_API_KEY — using FakeLLM (no live research/writing)"
        )
        _LLM = FakeLLM()
    return _LLM


def reset_llm() -> None:
    global _LLM
    _LLM = None
