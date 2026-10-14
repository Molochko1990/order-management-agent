"""Шаг 7: собирает 20 ответов агента для ручной разметки и проверки судьи.

15 ответов даёт рабочий промпт agent_v2.md на всех 15 вопросах из questions.json.
Ещё 5 даёт agent_v2_loose.md: плохая версия для проверки судьи. В ней нет правила «только по найденному»
и запрета додумывать, а вместо них есть просьба дополнять ответ общими знаниями. Без этой просьбы модель
и так отвечала по фрагментам, и плохих ответов не получалось. Вопросы для неё: три без ответа и два,
где легко добавить общие советы. Так среди двадцати появятся плохие.

Для каждого ответа сохраняются фрагменты, которые вернул search_docs: судья будет сверять ответ именно с ними.
Запуск из папки проекта: python eval/collect_answers.py. Результат: eval/answers.json.
"""
import asyncio
import json
import sys
from pathlib import Path
import support_tools
from agent import agent_events

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

QUESTIONS = ROOT / "eval" / "questions.json"
ANSWERS = ROOT / "eval" / "answers.json"
PROMPTS = {"v2": ROOT / "prompts" / "agent_v2.md", "v2_loose": ROOT / "prompts" / "agent_v2_loose.md"}
LOOSE_IDS = ["q13", "q14", "q15", "q09", "q12"]


def load_prompt(version: str) -> str:
    return PROMPTS[version].read_text(encoding="utf-8").replace("{today}", support_tools.TODAY.isoformat())


async def ask(question: str, system: str) -> dict:
    support_tools.reset_state()
    text, tools, calls, fragments = [], [], {}, []
    async for kind, data in agent_events([{"role": "user", "content": question}], system=system):
        if kind == "text":
            text.append(data)
        elif kind == "tool":
            tools.append(data["name"])
        elif kind == "step":
            for item in data:
                if item.get("type") == "function_call":
                    calls[item["call_id"]] = item["name"]
                elif item.get("type") == "function_call_output" and calls.get(item["call_id"]) == "search_docs":
                    fragments.extend(json.loads(item["output"]))
    return {"tools": tools, "fragments": fragments, "answer": "".join(text).strip()}


async def main() -> None:
    questions = {q["id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))}
    plan = [(qid, "v2") for qid in questions] + [(qid, "v2_loose") for qid in LOOSE_IDS]
    prompts = {v: load_prompt(v) for v in PROMPTS}

    answers = []
    for n, (qid, version) in enumerate(plan, 1):
        q = questions[qid]
        result = await ask(q["question"], prompts[version])
        answers.append({"id": f"a{n:02d}", "question_id": qid, "prompt": version, "question": q["question"],
                        **result, "human_label": None, "human_note": ""})
        print(f"a{n:02d} [{version:8}] {qid}: {result['answer'][:110]}")

    ANSWERS.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nзаписано {len(answers)} ответов в {ANSWERS.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
