"""Thin wrapper around the Claude API for structured (Pydantic-validated) outputs."""

from __future__ import annotations

import logging
import os
from typing import TypeVar

from pydantic import BaseModel

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class ClaudeLLM:
    def __init__(self, model: str = "claude-opus-5-5", effort: str = "medium"):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    def parse(self, *, system: str, prompt: str, schema: type[T], cached_context: str | None = None,
              max_tokens: int = 16000) -> T:
        """One request returning a validated `schema` instance.

        `cached_context` (your master CV) is identical across every job in a run, so it is
        placed in the system prompt with a cache breakpoint and billed at the cache rate
        after the first call.
        """
        anthropic = self._anthropic
        system_blocks = [{"type": "text", "text": system}]
        if cached_context:
            system_blocks.append({"type": "text", "text": cached_context, "cache_control": {"type": "ephemeral"}})
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=system_blocks,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                # If a safety classifier declines, retry server-side on Anthropic's recommended fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as exc:
            raise LLMError("Claude API authentication failed - check ANTHROPIC_API_KEY") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Claude API rate limit hit - try again later or lower the per-run limits") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"could not reach the Claude API: {exc}") from exc

        if response.stop_reason == "refusal":
            raise LLMError("Claude declined this request")
        if response.stop_reason == "max_tokens":
            raise LLMError("Claude's answer was cut off (max_tokens)")
        if response.parsed_output is None:
            raise LLMError("Claude returned no structured output")
        return response.parsed_output


def make_llm(cfg: dict) -> ClaudeLLM | None:
    """Return a client if credentials exist, else None (jobbot then uses keyword-only logic)."""
    if not ClaudeLLM.available():
        log.info("ANTHROPIC_API_KEY not set - using keyword-based matching and tailoring")
        return None
    llm_cfg = cfg.get("llm") or {}
    return ClaudeLLM(model=llm_cfg.get("model", "claude-opus-5-5"), effort=llm_cfg.get("effort", "medium"))
