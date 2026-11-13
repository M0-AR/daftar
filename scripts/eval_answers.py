"""Answer-quality check with a real model: is the answer right, and does it cite a passage?

    python scripts/eval_answers.py qwen3.5:2b           # compares passage formats plain vs json
"""
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_retrieval as ev  # noqa: E402
from daftar.rag import Retriever, build_messages  # noqa: E402

# Any of these (after normalization) in the answer counts as correct.
KEYS = [
    ["ninety days", "90 days"], ["fifteen days", "15 days"], ["forty-five days", "45 days"], ["1.5"],
    ["18,400", "18400"], ["finance director"], ["five years", "5 years"], ["client"], ["fees paid", "twelve months", "12 months"],
    ["forty-eight hours", "48 hours"], ["england"], ["twenty-four months", "24 months"], ["sixty days", "60 days"],
    ["23"], ["rem"], ["not", "no"], ["grogginess", "inertia"], ["40 minutes", "forty minutes"], ["self-reported", "young"],
    ["بيع", "ربح"], ["نعم", "يجب", "يمتلك"], ["ثلاثين", "30"], ["الرهن", "الكفاله", "رهن"],
    ["المؤجر"], ["المؤجر"], ["انفسخت", "تنفسخ", "سقطت", "فسخ"], ["هبه", "بيع"],
]
assert len(KEYS) == len(ev.QUESTIONS)


def ask(model, messages):
    body = {"model": model, "stream": False, "messages": messages, "options": {"num_ctx": 4096, "temperature": 0.2}}
    if os.environ.get("EVAL_THINK") != "on":
        body["think"] = False  # Qwen3.5 thinks by default; hidden reasoning only adds latency in a RAG app
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=300))["message"]["content"]


def main(model):
    tmp, store = ev.build(1000, 150)
    retriever = Retriever(store)
    for quote in ("plain", "json"):
        correct = cited = 0
        wrong = []
        t = time.time()
        for (q, _), keys in zip(ev.QUESTIONS, KEYS):
            sources, _ = retriever.search(q, 1, 4)
            answer = ask(model, build_messages(q, sources, [], "eval", list(ev.DOCS), quote=quote))
            ok = any(ev.normalize(k) in ev.normalize(answer) for k in keys)
            correct += ok
            cited += bool(re.search(r"\[\d{1,2}\]", answer))
            if not ok:
                wrong.append(f"{q} -> {answer[:90]!r}")
        n = len(ev.QUESTIONS)
        print(f"{model} {quote:5}: correct {correct}/{n}, cited {cited}/{n}  ({time.time() - t:.0f}s)", flush=True)
        for w in wrong:
            print("    wrong:", w)
    tmp.cleanup()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "qwen3.5:2b")
