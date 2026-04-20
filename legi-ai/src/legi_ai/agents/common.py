"""Shared LLM client + structured output helper for agents."""
from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel

from legi_ai.config import settings
from legi_ai.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


def get_llm(fast: bool = False):
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=settings.claude_model_fast if fast else settings.claude_model_primary,
        api_key=settings.anthropic_api_key,
        max_tokens=4096,
        temperature=0.2,
    )


def structured_call(schema: type[T], system: str, user: str, fast: bool = False) -> T:
    """Call Claude with structured output coerced to a Pydantic schema."""
    llm = get_llm(fast=fast).with_structured_output(schema)
    msg = llm.invoke(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )
    if isinstance(msg, schema):
        return msg
    if isinstance(msg, dict):
        return schema.model_validate(msg)
    if isinstance(msg, str):
        return schema.model_validate(json.loads(msg))
    raise TypeError(f"unexpected structured output: {type(msg)}")
