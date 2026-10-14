import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

from common import MODEL, STRONG_MODEL, client, cost_usd, stop_reason, usage_dict
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

ANSWERS = ROOT / "eval" / "answers.json"


class Grounded(BaseModel):
    reasoning: str = Field(description="Какие утверждения ответа есть во фрагментах, а каких нет, в двух-трёх предложениях")
    grounded: bool


JUDGE = """# Роль
Вы проверяете ответы ассистента поддержки интернет-магазина.

# Критерий
Ответ проходит, если каждое его утверждение о магазине, товарах, правилах, сроках, суммах и действиях клиента есть во фрагментах или прямо из них следует.
Ответ не проходит, если хотя бы одно такое утверждение во фрагментах отсутствует, искажено или относится во фрагменте к другому случаю.
- Ответ «Не знаю» проходит. Совет обратиться в поддержку проходит всегда.
- Вежливые фразы и источники в скобках не проверяйте.
- Верно ли утверждение по вашим собственным знаниям, не важно: только фрагменты."""


def judge(a: dict, model: str) -> tuple[Grounded, float]:
    fragments = "\n\n".join(f"<fragment>\n{f['text']}\n</fragment>" for f in a["fragments"]) or "(фрагментов нет)"
    user = f"# Фрагменты\n{fragments}\n\n# Вопрос клиента\n{a['question']}\n\n# Ответ ассистента\n{a['answer']}"
    resp = client.responses.parse(
        model=model,
        instructions=JUDGE,
        input=user,
        text_format=Grounded,
        store=False,
        max_output_tokens=600,
        reasoning={"effort": "none"},
        temperature=0,
    )
    stop = stop_reason(resp)
    if stop:
        raise RuntimeError(stop)
    return resp.output_parsed, cost_usd(usage_dict(resp.usage), model)


def agreement(human: list[bool], verdicts: list[bool]) -> dict:
    pairs = list(zip(human, verdicts))
    return {
        "tp": sum(h and g for h, g in pairs),          # хороший, судья пропустил
        "fn": sum(h and not g for h, g in pairs),      # хороший, судья забраковал
        "tn": sum(not h and not g for h, g in pairs),  # плохой, судья поймал
        "fp": sum(not h and g for h, g in pairs),      # плохой, судья пропустил
    }


if __name__ == "__main__":
    answers = json.loads(ANSWERS.read_text(encoding="utf-8"))
    if any(a["human_label"] not in ("ok", "bad") for a in answers):
        sys.exit("сначала разметьте все ответы: human_label должен быть ok или bad")
    human = [a["human_label"] == "ok" for a in answers]

    print(f"{'судья':14} {'TPR':>9} {'TNR':>9} {'совпало':>9} {'$':>8}")
    for name, model in (("MODEL", MODEL), ("STRONG_MODEL", STRONG_MODEL)):
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda a: judge(a, model), answers))
        verdicts = [r.grounded for r, _ in results]
        for a, (r, _) in zip(answers, results):
            a.setdefault("judge", {})[name] = {"grounded": r.grounded, "reasoning": r.reasoning}
        m = agreement(human, verdicts)
        good, bad = m["tp"] + m["fn"], m["tn"] + m["fp"]
        print(f"{name:14} {m['tp']:>3} из {good:<3} {m['tn']:>3} из {bad:<3} {m['tp'] + m['tn']:>3} из {len(answers):<3}"
              f" {sum(c for _, c in results):8.4f}")
        for a, h, g in zip(answers, human, verdicts):
            if h != g:
                print(f"   {a['id']} человек {a['human_label']}, судья {'ok' if g else 'bad'}: "
                      f"{a['judge'][name]['reasoning'][:150]}")

    ANSWERS.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nвердикты судей записаны в {ANSWERS.relative_to(ROOT)}, поле judge")
