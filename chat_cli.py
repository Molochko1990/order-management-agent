import asyncio
import json
import sys

import support_tools
from agent import agent_events


def show_state() -> None:
    changed = {oid: o["status"] for oid, o in support_tools.ORDERS.items() if o != support_tools._FIXTURE[oid]}
    print(f"  [изменённые заказы: {changed or '—'} | возвраты: {support_tools.REFUNDS or '—'}]")


async def main(effort: str) -> None:
    support_tools.reset_state()
    history: list[dict] = []
    print(f"Чат с агентом (effort={effort}). /reset — новый диалог, /quit — выход.")
    while True:
        try:
            text = input("\nВы: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/reset":
            support_tools.reset_state()
            history.clear()
            print("— новый диалог —")
            continue

        history.append({"role": "user", "content": text})
        tools, steps, cost = [], 0, 0.0
        print("Бот: ", end="", flush=True)
        async for kind, data in agent_events(history, effort=effort):
            if kind == "text":
                print(data, end="", flush=True)
            elif kind == "tool":
                tools.append(f"{data['name']}({json.dumps(data['args'], ensure_ascii=False)})")
            elif kind == "usage":
                steps += 1
                cost += data["cost_usd"]
            elif kind == "step":
                history.extend(data)
        print(f"\n  [инструменты: {', '.join(tools) or '—'} | шагов: {steps} | ${cost:.6f}]")
        show_state()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "low"))
