import json
from pathlib import Path

from docs_index import DOC_TITLES, build_bm25, hybrid_search, load_index, vector_search

QUESTIONS = Path(__file__).parent / "eval" / "questions.json"
K = 3


def hit(found: list[dict], pages: list[list]) -> bool:
    gold = {(f, p) for f, p in pages}
    return any((c["file"], c["page"]) in gold for c in found)


if __name__ == "__main__":
    questions = [q for q in json.loads(QUESTIONS.read_text(encoding="utf-8")) if q["pages"]]
    chunks, matrix = load_index()
    bm25 = build_bm25(chunks)

    hits = {"вектор": 0, "гибрид": 0}
    for q in questions:
        found = {"вектор": vector_search(q["question"], chunks, matrix, K),
                 "гибрид": hybrid_search(q["question"], chunks, matrix, bm25, K)}
        marks = []
        for name, chunks_found in found.items():
            ok = hit(chunks_found, q["pages"])
            hits[name] += ok
            marks.append(f"{name} {'+' if ok else '-'}")
        print(f"{q['id']} {' | '.join(marks)}  {q['question']}")
        for name, chunks_found in found.items():
            if not hit(chunks_found, q["pages"]):
                print(f"     промах {name}: " + "; ".join(f"{DOC_TITLES[c['file']]}, с. {c['page']}" for c in chunks_found))

    n = len(questions)
    print(f"\n| Поиск  | recall@{K} |\n|--------|----------|")
    for name, h in hits.items():
        print(f"| {name} | {h} из {n} |")
