import math
import re
import sqlite3
from collections import Counter
from pathlib import Path

import numpy as np
import pymupdf

INCOMING = Path(__file__).parent / "data" / "incoming"
INDEX_DB = Path(__file__).parent / "docs_index.db"
DOC_TITLES = {
    "reglament-garantii.pdf": "Регламент гарантии",
    "pravila-loyalnosti.pdf": "Правила программы лояльности",
    "spravochnik-pokupatelya.pdf": "Справочник покупателя",
}
DOC_FILES = list(DOC_TITLES)

STOP = set("а бы в во для до же за и из или к как ко ли на не но о об от по при с со то у что это я мне мой".split())
K_RRF = 60


def load_pages(folder: Path = INCOMING, files: list[str] = DOC_FILES) -> list[dict]:
    pages = []
    for name in files:
        with pymupdf.open(folder / name) as doc:
            for i, page in enumerate(doc):
                blocks = [" ".join(b[4].split()) for b in page.get_text("blocks")]
                pages.append({"file": name, "page": i + 1, "text": page.get_text(),
                              "paragraphs": [b for b in blocks if b]})
    return pages


def is_heading(paragraph: str) -> bool:
    return (len(paragraph.split()) <= 8 and not paragraph.endswith((".", ":", ";"))
            and not any(ch.isdigit() for ch in paragraph))


def is_chapter(paragraph: str) -> bool:
    return bool(re.match(r"Глава \d+\.", paragraph)) and not paragraph[-1].isdigit()


def chunk_pages(pages: list[dict], size: int = 120, overlap: int = 20) -> list[dict]:
    chunks, chapter, section, last_file = [], "", "", None
    for p in pages:
        if p["file"] != last_file:
            chapter, section, last_file = "", "", p["file"]
        carry, current = [], []

        def close():
            if current:
                header = "\n".join(h for h in (chapter, section) if h)
                text = "\n".join(carry + current)
                chunks.append({"file": p["file"], "page": p["page"], "chapter": chapter, "section": section,
                               "text": f"{header}\n{text}" if header else text})

        for par in p["paragraphs"]:
            if is_chapter(par):
                close()
                chapter, section, carry, current = par, "", [], []
                continue
            if is_heading(par):
                close()
                section, carry, current = par, [], []
                continue
            if current and sum(len(c.split()) for c in carry + current) + len(par.split()) > size:
                close()
                tail = " ".join(" ".join(carry + current).split()[-overlap:])
                carry, current = ([tail] if overlap else []), []
            current.append(par)
        close()
    return chunks


def embed(texts: list[str], dimensions: int = 512) -> np.ndarray:
    from common import EMBED_MODEL, client

    resp = client.embeddings.create(model=EMBED_MODEL, input=texts, dimensions=dimensions)
    return np.array([d.embedding for d in resp.data], dtype=np.float32)


def build_index(chunks: list[dict], path: Path = INDEX_DB) -> None:
    db = sqlite3.connect(path)
    db.executescript("DROP TABLE IF EXISTS chunks;"
                     "CREATE TABLE chunks (file TEXT, page INTEGER, section TEXT, text TEXT, vector BLOB);")
    with db:
        for i in range(0, len(chunks), 64):
            batch = chunks[i:i + 64]
            vectors = embed([f"{DOC_TITLES[c['file']]}\n{c['text']}" for c in batch])
            db.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                           [(c["file"], c["page"], c["section"], c["text"], v.tobytes()) for c, v in zip(batch, vectors)])
    db.close()


def load_index(path: Path = INDEX_DB) -> tuple[list[dict], np.ndarray]:
    db = sqlite3.connect(path)
    rows = db.execute("SELECT file, page, section, text, vector FROM chunks").fetchall()
    db.close()
    chunks = [{"file": f, "page": p, "section": s, "text": t} for f, p, s, t, _ in rows]
    return chunks, np.vstack([np.frombuffer(r[4], dtype=np.float32) for r in rows])


def vector_search(query: str, chunks: list[dict], matrix: np.ndarray, k: int = 5) -> list[dict]:
    scores = embed([query])[0] @ matrix.T
    return [{**chunks[i], "score": float(scores[i])} for i in np.argsort(-scores)[:k]]


# --- Гибридный поиск: BM25 + вектор, слияние через RRF (урок 07, раздел 3.2) ---------------------------


def terms(text: str) -> list[str]:
    words = [w for w in re.findall(r"\w+", text.lower()) if w not in STOP]
    return [w if re.search(r"[0-9a-z_]", w) else w[:6] for w in words]


class BM25:
    def __init__(self, texts: list[str], k1: float = 1.5, b: float = 0.75):
        self.docs = [terms(t) for t in texts]
        self.k1, self.b = k1, b
        self.lengths = np.array([len(d) for d in self.docs], dtype=np.float32)
        self.avg = float(self.lengths.mean())
        self.freqs = [Counter(d) for d in self.docs]
        seen = Counter(w for d in self.docs for w in set(d))
        n = len(self.docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in seen.items()}

    def scores(self, query: str) -> np.ndarray:
        result = np.zeros(len(self.docs), dtype=np.float32)
        for word in terms(query):
            if word not in self.idf:
                continue
            freq = np.array([f[word] for f in self.freqs], dtype=np.float32)
            norm = freq + self.k1 * (1 - self.b + self.b * self.lengths / self.avg)
            result += self.idf[word] * freq * (self.k1 + 1) / norm
        return result

    def top(self, query: str, n: int = 20) -> list[int]:
        scores = self.scores(query)
        return [int(i) for i in np.argsort(-scores)[:n] if scores[i] > 0]


def build_bm25(chunks: list[dict]) -> BM25:
    return BM25([f"{DOC_TITLES[c['file']]}\n{c['text']}" for c in chunks])


def rank_vector(query: str, matrix: np.ndarray, top: int = 20) -> list[int]:
    return [int(i) for i in np.argsort(-(embed([query])[0] @ matrix.T))[:top]]


def rrf(*rankings: list[int], k: int = K_RRF) -> list[int]:
    score: Counter = Counter()
    for ranking in rankings:
        for position, index in enumerate(ranking, 1):
            score[index] += 1 / (k + position)
    return [i for i, _ in score.most_common()]


def hybrid_search(query: str, chunks: list[dict], matrix: np.ndarray, bm25: BM25, k: int = 5) -> list[dict]:
    return [chunks[i] for i in rrf(rank_vector(query, matrix), bm25.top(query))[:k]]


# --- Поиск для агента: инструмент search_docs ----------------------------------------------------------

_INDEX: tuple | None = None


def source_label(chunk: dict) -> str:
    title = DOC_TITLES[chunk["file"]]
    return f"{title[0].lower()}{title[1:]}, с. {chunk['page']}"


def search_docs(query: str, k: int = 4) -> list[dict]:
    global _INDEX
    if _INDEX is None:
        chunks, matrix = load_index()
        _INDEX = (chunks, matrix, build_bm25(chunks))
    chunks, matrix, bm25 = _INDEX
    return [{"source": source_label(c), "text": c["text"]} for c in hybrid_search(query, chunks, matrix, bm25, k)]


if __name__ == "__main__":
    pages = load_pages()
    chunks = chunk_pages(pages)
    print(f"страниц: {len(pages)}, чанков: {len(chunks)}")
    build_index(chunks)
    print(f"индекс записан в {INDEX_DB.name}")

    chunks, matrix = load_index()
    bm25 = build_bm25(chunks)
    for query in ("сколько длится гарантия на электронику", "когда сгорают бонусы", "что значит E05"):
        print(f"\n{query}")
        for name, found in (("вектор", vector_search(query, chunks, matrix, k=3)),
                            ("гибрид", hybrid_search(query, chunks, matrix, bm25, k=3))):
            print(f"  {name}:")
            for c in found:
                print(f"    {DOC_TITLES[c['file']]}, с. {c['page']} / {c['section']}")
