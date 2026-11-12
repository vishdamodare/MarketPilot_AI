"""Thin OpenAI wrapper. Returns None when no key is configured so every caller has a deterministic fallback."""
import logging

from ..config import get_settings

log = logging.getLogger("llm")


def enabled() -> bool:
    return bool(get_settings().openai_api_key)


def client():
    from openai import OpenAI
    return OpenAI(api_key=get_settings().openai_api_key, timeout=45)


def complete(system: str, user: str, max_tokens: int = 300) -> str | None:
    if not enabled():
        return None
    try:
        r = client().chat.completions.create(model=get_settings().openai_model, max_tokens=max_tokens, temperature=0,
                                             messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return (r.choices[0].message.content or "").strip() or None
    except Exception as e:  # never let an LLM outage break a financial workflow
        log.warning("llm call failed: %s", type(e).__name__)
        return None
