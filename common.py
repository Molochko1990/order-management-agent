import os
import sys
from pathlib import Path

from openai import AsyncOpenAI, OpenAI

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

MODEL = "gpt-5.6-luna"
STRONG_MODEL = "gpt-5.6-terra"
EMBED_MODEL = "text-embedding-3-small"

PRICES = {
    MODEL: (0.20, 0.02, 1.20),
    STRONG_MODEL: (2.00, 0.20, 12.00),
    EMBED_MODEL: (0.02, 0.0, 0.0),
}
CACHE_WRITE = 1.25

if not os.environ.get("OPENAI_API_KEY"):
    print("OPENAI_API_KEY не задан: вызовы модели упадут (положите ключ в .env)", file=sys.stderr)
_KEY = os.environ.get("OPENAI_API_KEY") or "not-set"
client = OpenAI(api_key=_KEY, max_retries=5, timeout=120)
aclient = AsyncOpenAI(api_key=_KEY, max_retries=5, timeout=120)


def usage_dict(usage) -> dict:
    if usage is None:
        return {"input": 0, "cached": 0, "cache_write": 0, "output": 0, "reasoning": 0}
    details_in = getattr(usage, "input_tokens_details", None)
    details_out = getattr(usage, "output_tokens_details", None)
    return {
        "input": usage.input_tokens,
        "cached": getattr(details_in, "cached_tokens", 0) or 0,
        "cache_write": getattr(details_in, "cache_write_tokens", 0) or 0,
        "output": usage.output_tokens,
        "reasoning": getattr(details_out, "reasoning_tokens", 0) or 0,
    }


def cost_usd(u: dict, model: str = MODEL) -> float:
    price_in, price_cached, price_out = PRICES[model]
    ordinary = u["input"] - u["cached"] - u["cache_write"]
    cost_in = ordinary * price_in + u["cached"] * price_cached + u["cache_write"] * price_in * CACHE_WRITE
    return (cost_in + u["output"] * price_out) / 1_000_000


def stop_reason(resp) -> str | None:
    if resp.status == "incomplete":
        reason = getattr(resp.incomplete_details, "reason", None)
        return "length" if reason == "max_output_tokens" else "refusal"
    for item in resp.output:
        if item.type == "message" and any(part.type == "refusal" for part in item.content or []):
            return "refusal"
    return None
