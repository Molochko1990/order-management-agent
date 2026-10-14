import json
from pathlib import Path

from common import MODEL, aclient, cost_usd, stop_reason, usage_dict
from support_tools import TODAY, TOOLS, run_tool

PROMPT_FILE = Path(__file__).parent / "prompts" / "agent_v1.md"
MAX_STEPS = 8
FALLBACK = "Не получилось решить обращение, передаю его оператору."


def load_system() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8").replace("{today}", TODAY.isoformat())


SYSTEM = load_system()


def _args(arguments_json: str) -> dict:
    try:
        return json.loads(arguments_json)
    except json.JSONDecodeError:
        return {"_raw": arguments_json}


async def agent_events(history: list[dict], effort: str = "low", max_steps: int = MAX_STEPS, model: str = MODEL):
    items = list(history)
    for _ in range(max_steps):
        stream = await aclient.responses.create(
            model=model, instructions=SYSTEM, input=items, tools=TOOLS, store=False, stream=True,
            reasoning={"effort": effort}, max_output_tokens=2000,
        )
        done, final = {}, None
        async with stream:
            async for event in stream:
                if event.type == "response.output_text.delta":
                    yield "text", event.delta
                elif event.type == "response.output_item.done":
                    done[event.output_index] = event.item
                elif event.type in ("response.completed", "response.incomplete"):
                    final = event.response
        if final is None:
            raise RuntimeError("стрим закончился без response.completed")

        u = usage_dict(final.usage)
        yield "usage", {**u, "cost_usd": cost_usd(u, model)}

        output = [done[i] for i in sorted(done)]
        if stop_reason(final):
            yield "step", [item.to_dict() for item in output if item.type != "function_call"]
            break

        calls = [item for item in output if item.type == "function_call"]
        if not calls:
            yield "step", [item.to_dict() for item in output]
            return

        step = [item.to_dict() for item in output]
        for call in calls:
            yield "tool", {"name": call.name, "args": _args(call.arguments)}
            step.append({"type": "function_call_output", "call_id": call.call_id, "output": run_tool(call.name, call.arguments)})
        items.extend(step)
        yield "step", step

    yield "text", FALLBACK
    yield "step", [{"role": "assistant", "content": FALLBACK}]
