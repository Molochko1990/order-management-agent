import asyncio
import json
import sys
from pathlib import Path

import support_tools
from agent import agent_events
from common import MODEL

DIR = Path(__file__).parent / "dialogs"


def short(result_json: str, limit: int = 90) -> str:
    r = json.loads(result_json)
    if "error" in r:
        text = "ошибка: " + r["error"]
    elif "refund_id" in r:
        text = f"{r['refund_id']} на {r['amount_rub']}"
    elif r.get("refund"):
        text = f"{r['status']}, {r['refund']['refund_id']} на {r['refund']['amount_rub']}"
    else:
        text = ", ".join(f"{k}={r[k]}" for k in ("status", "address") if k in r)
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def run_dialog(scenario: dict, effort: str) -> dict:
    support_tools.reset_state()
    history, turns, cost, steps = [], [], 0.0, 0
    for text in scenario["messages"]:
        history.append({"role": "user", "content": text})
        answer, calls = "", []
        async for kind, data in agent_events(history, effort=effort):
            if kind == "text":
                answer += data
            elif kind == "usage":
                cost += data["cost_usd"]
                steps += 1
            elif kind == "step":
                history.extend(data)
                for item in data:
                    if item.get("type") == "function_call":
                        calls.append({"name": item["name"], "args": json.loads(item["arguments"])})
                    elif item.get("type") == "function_call_output":
                        calls[-1]["result"] = short(item["output"])
        turns.append({"user": text, "calls": calls, "answer": answer})

    order = support_tools.ORDERS.get(scenario["order_id"])
    refunds = [r for r in support_tools.REFUNDS if r["order_id"] == scenario["order_id"]]
    exp = scenario["expect"]
    ok = (order["status"] if order else None) == exp["status"] \
        and [r["amount_rub"] for r in refunds] == exp["refunds"] \
        and ("address" not in exp or (order or {}).get("address") == exp["address"]) \
        and len(support_tools.REFUNDS) == len(refunds)
    return {**scenario, "turns": turns, "status_after": order["status"] if order else None,
            "address_after": (order or {}).get("address"), "refunds_after": refunds,
            "cost_usd": cost, "steps": steps, "ok": ok}


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def to_markdown(results: list[dict], effort: str) -> str:
    total = sum(r["cost_usd"] for r in results)
    lines = [
        f"# Десять диалогов (модель {MODEL}, effort={effort})", "",
        f"Верно: **{sum(r['ok'] for r in results)}/{len(results)}**. "
        f"Цена всех диалогов ${total:.6f}, в среднем **${total / len(results):.6f}** за диалог.", "",
        "Каждый диалог начинается с исходного состояния `data/orders.json`. Проверка — по состоянию заказа после диалога.", "",
        "| # | Сценарий | Запрос клиента | Вызовы инструментов | Ответ бота (последний) | status после | Возвраты после | Ожидалось | Верно |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        req = "<br>".join(f"{i}. {cell(t['user'])}" for i, t in enumerate(r["turns"], 1))
        calls = "<br>".join(
            f"{i}. `{c['name']}({cell(json.dumps(c['args'], ensure_ascii=False))})` → {cell(c.get('result', ''))}"
            for i, t in enumerate(r["turns"], 1) for c in t["calls"]) or "—"
        status = r["status_after"] or "нет заказа"
        if r["address_after"]:
            status += f"<br>адрес: {cell(r['address_after'])}"
        refunds = "<br>".join(f"{x['refund_id']}: {x['amount_rub']}" for x in r["refunds_after"]) or "—"
        exp = r["expect"]
        expected = f"{exp['status'] or 'нет заказа'}, возвраты {exp['refunds'] or '—'}"
        lines.append(f"| {r['id']} | {cell(r['title'])} ({r['order_id']}) | {req} | {calls} | "
                     f"{cell(r['turns'][-1]['answer'])} | {status} | {refunds} | {expected} | {'✅' if r['ok'] else '❌'} |")
    return "\n".join(lines) + "\n"


async def main(effort: str) -> None:
    scenarios = json.loads((DIR / "scenarios.json").read_text(encoding="utf-8"))
    results = []
    for s in scenarios:
        r = await run_dialog(s, effort)
        results.append(r)
        print(f"{r['id']:>2}. {'OK ' if r['ok'] else 'FAIL'} {r['title']:<45} status={r['status_after']} "
              f"refunds={[x['amount_rub'] for x in r['refunds_after']]} ${r['cost_usd']:.6f}")
    (DIR / f"results_{effort}.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (DIR / f"dialogs_{effort}.md").write_text(to_markdown(results, effort), encoding="utf-8")
    total = sum(r["cost_usd"] for r in results)
    print(f"\nВерно {sum(r['ok'] for r in results)}/{len(results)}, всего ${total:.6f}, в среднем ${total / len(results):.6f}")
    print(f"Таблица: dialogs/dialogs_{effort}.md")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "low"))
