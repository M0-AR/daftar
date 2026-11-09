"""Retrieval evaluation: recall@k and MRR on a small labelled corpus (English + vocalized Arabic).

    python scripts/eval_retrieval.py              # keyword-only (no Ollama needed)
    python scripts/eval_retrieval.py --embed      # also semantic, needs the search model in Ollama

A question counts as answered when one of the top-k passages contains its `expect` text
(compared after normalization, so Arabic diacritics do not matter).
"""
import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daftar.ingest import chunk_pages  # noqa: E402
from daftar.rag import Retriever  # noqa: E402
from daftar.store import Store  # noqa: E402

CONTRACT = """MASTER SERVICES AGREEMENT

This Master Services Agreement is made between Northwind Traders Ltd ("Client") and Brightline Consulting LLC ("Supplier").

1. Term
The agreement starts on 1 February 2026 and continues for an initial period of twenty-four months. It renews automatically for successive twelve-month periods unless either party gives notice of non-renewal at least sixty days before the end of the current period.

2. Services
The Supplier will provide data engineering, dashboard development and monthly reporting services as described in each Statement of Work. Each Statement of Work must be signed by both parties before work begins.

3. Fees and Payment
The Client pays a fixed monthly fee of 18,400 USD. The Supplier invoices on the first business day of each month. Invoices are payable within forty-five days of receipt. Late payments accrue interest at 1.5 percent per month on the outstanding amount.

4. Expenses
Travel expenses are reimbursed at cost only if the Client approved them in writing in advance. Expenses above 2,000 USD per trip require approval by the Client's finance director.

5. Confidentiality
Each party keeps the other party's confidential information secret and uses it only to perform this agreement. This obligation survives for five years after termination.

6. Intellectual Property
All deliverables created specifically for the Client become the Client's property once paid for in full. The Supplier keeps ownership of its pre-existing tools and libraries and grants the Client a perpetual licence to use them within the deliverables.

7. Liability
Neither party is liable for indirect or consequential losses. Each party's total liability is capped at the fees paid in the twelve months before the claim arose.

8. Termination
Either party may terminate this agreement for convenience with ninety days written notice. Either party may terminate immediately if the other party commits a material breach and fails to remedy it within fifteen days of written notice.

9. Data Protection
The Supplier processes personal data only on the Client's documented instructions and notifies the Client of any personal data breach within forty-eight hours of becoming aware of it.

10. Governing Law
This agreement is governed by the laws of England and Wales, and the courts of London have exclusive jurisdiction.
"""

RESEARCH = """Sleep and memory: literature notes

Study A (Walker et al., 2021) followed 120 university students for four weeks. Students who slept at least eight hours recalled 23 percent more word pairs than students who slept five hours or less. The effect was strongest for material learned in the evening.

Study B (Rasch, 2023) used polysomnography to compare sleep stages. Slow-wave deep sleep was linked to the consolidation of declarative facts, while REM sleep supported emotional and procedural memory. Participants deprived of REM sleep made 31 percent more errors on a motor sequence task.

Study C (Okafor, 2024) tested short daytime naps. A 20-minute nap improved alertness for about three hours but did not improve long-term retention. Naps longer than 60 minutes caused grogginess, called sleep inertia, for up to 30 minutes.

Caffeine: three of the reviewed studies excluded participants who drank more than 400 milligrams of caffeine per day, because caffeine delays sleep onset by roughly 40 minutes.

Limitations: most samples were young adults, and sleep was self-reported in two of the five studies, which may overestimate total sleep time.
"""

ARABIC = """عَقْدُ الْمُرَابَحَةِ

الْمُرَابَحَةُ هِيَ بَيْعُ السِّلْعَةِ بِثَمَنِ شِرَائِهَا مَعَ رِبْحٍ مَعْلُومٍ مُتَّفَقٍ عَلَيْهِ بَيْنَ الْبَائِعِ وَالْمُشْتَرِي.

يَجِبُ أَنْ يَمْتَلِكَ الْمَصْرِفُ السِّلْعَةَ وَيَقْبِضَهَا قَبْلَ بَيْعِهَا لِلْعَمِيلِ، وَلَا يَجُوزُ بَيْعُ مَا لَا يَمْلِكُهُ.

يُسَدِّدُ الْعَمِيلُ الثَّمَنَ عَلَى أَقْسَاطٍ شَهْرِيَّةٍ لِمُدَّةِ ثَلَاثِينَ شَهْرًا، وَلَا تُزَادُ الْأَقْسَاطُ عِنْدَ التَّأَخُّرِ فِي السَّدَادِ.

يَحِقُّ لِلْمَصْرِفِ طَلَبُ ضَمَانَاتٍ مِثْلَ الرَّهْنِ أَوِ الْكَفَالَةِ لِضَمَانِ سَدَادِ الدَّيْنِ.
"""

IJARA = """الإجارة المنتهية بالتمليك

تعريفها: عقد يؤجر فيه المصرف أصلا مملوكا له إلى العميل مقابل أجرة دورية معلومة، مع وعد بنقل ملكية الأصل في نهاية المدة.

الصيانة: يتحمل المؤجر صيانة الأصل الأساسية التي يتوقف عليها الانتفاع، أما الصيانة التشغيلية الدورية فيتحملها المستأجر.

التأمين: يكون تأمين الأصل على المؤجر لأنه المالك، ويجوز أن يدخل تكلفته في حساب الأجرة.

الهلاك: إذا هلك الأصل هلاكا كليا دون تعد من المستأجر انفسخت الإجارة وسقطت الأجرة عن المدة الباقية.

نقل الملكية: تنتقل الملكية في آخر المدة بعقد هبة أو بيع بثمن رمزي، ولا يجوز أن يكون ذلك شرطا في عقد الإجارة نفسه.
"""

DOCS = {"ijara_ar.txt": IJARA, "contract.txt": CONTRACT, "sleep_notes.txt": RESEARCH, "murabaha_ar.txt": ARABIC}

QUESTIONS = [
    ("How much notice is needed to end the contract for convenience?", "ninety days written notice"),
    ("What happens if a party commits a material breach?", "fails to remedy it within fifteen days"),
    ("When are invoices due?", "payable within forty-five days"),
    ("What is the interest on late payments?", "1.5 percent per month"),
    ("What is the monthly fee?", "18,400 USD"),
    ("Who approves expensive trips?", "finance director"),
    ("How long does confidentiality last after the agreement ends?", "five years after termination"),
    ("Who owns the deliverables?", "become the Client's property"),
    ("What is the cap on liability?", "capped at the fees paid"),
    ("How fast must a data breach be reported?", "forty-eight hours"),
    ("Which country's law applies?", "England and Wales"),
    ("How long is the initial term?", "twenty-four months"),
    ("When must non-renewal notice be given?", "sixty days before"),
    ("How much better did students who slept eight hours remember?", "23 percent more"),
    ("Which sleep stage helps procedural memory?", "REM sleep supported"),
    ("Do short naps improve long-term retention?", "did not improve long-term retention"),
    ("What is sleep inertia?", "called sleep inertia"),
    ("How much does caffeine delay falling asleep?", "roughly 40 minutes"),
    ("What are the limitations of the studies?", "self-reported"),
    ("ما هي المرابحة؟", "بيع السلعة بثمن شرائها"),
    ("هل يجب أن يملك المصرف السلعة قبل بيعها؟", "يمتلك المصرف السلعة"),
    ("كم مدة الأقساط الشهرية؟", "ثلاثين شهرا"),
    ("ما الضمانات التي يطلبها المصرف؟", "الرهن او الكفالة"),
    ("من يتحمل صيانات الأصول الأساسية؟", "يتحمل المؤجر صيانه الاصل"),
    ("على من يكون التأمين؟", "تامين الاصل علي المؤجر"),
    ("ماذا يحدث عند هلاك الأصل؟", "انفسخت الاجاره"),
    ("كيف تنتقل الملكيات في نهاية الإجارة؟", "بعقد هبه او بيع"),
]


def normalize(text: str) -> str:
    """Comparison-only normalization (independent of the code under test)."""
    import re
    import unicodedata
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub("[\u064B-\u065F\u0670\u0640]", "", text)
    text = re.sub("[\u0622\u0623\u0625\u0671]", "\u0627", text)
    return text.replace("\u0629", "\u0647").replace("\u0649", "\u064a")


def build(size: int, overlap: int, embed_fn=None):
    tmp = tempfile.TemporaryDirectory()
    store = Store(Path(tmp.name) / "eval.db")
    store.run("INSERT INTO collections(name, created) VALUES ('eval', 0)")
    for i, (name, text) in enumerate(DOCS.items(), 1):
        store.run("INSERT INTO documents(name, ext, size, sha256, created) VALUES (?,?,?,?,0)", (name, ".txt", len(text), str(i)))
        eid = store.run("INSERT INTO entries(document_id, collection_id, status, created, embed_model) VALUES (?,1,'ready',0,?)",
                        (i, "eval-embed" if embed_fn else None))
        chunks = chunk_pages([(1, text)], size, overlap)
        ids = store.replace_chunks(eid, chunks)
        if embed_fn:
            store.set_embeddings(ids, embed_fn([t for _, t in chunks], query=False))
    return tmp, store


def evaluate(size=1600, overlap=240, k=4, rerank=True, embed_fn=None, verbose=False):
    tmp, store = build(size, overlap, embed_fn)
    retriever = Retriever(store)
    hits, rr, misses = 0, 0.0, []
    for q, expect in QUESTIONS:
        vec = embed_fn([q], query=True)[0] if embed_fn else None
        results, _ = retriever.search(q, 1, k, 24, vec, "eval-embed" if embed_fn else None, rerank)
        want = normalize(expect)
        rank = next((i for i, r in enumerate(results, 1) if want in normalize(r["text"])), None)
        if rank:
            hits += 1
            rr += 1 / rank
        else:
            misses.append(q)
    tmp.cleanup()
    n = len(QUESTIONS)
    if verbose and misses:
        print("   missed:", *misses, sep="\n     ")
    return {"recall@k": round(hits / n, 3), "mrr": round(rr / n, 3), "n": n}


def ollama_embedder(model="nomic-embed-text"):
    from daftar.ollama import Ollama, prefixes
    client = Ollama("http://127.0.0.1:11434")
    q_prefix, d_prefix = prefixes(model)
    return lambda texts, query: client.embed(model, [(q_prefix if query else d_prefix) + t for t in texts])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--embed", action="store_true")
    ap.add_argument("-k", type=int, default=4)
    args = ap.parse_args()
    embed = ollama_embedder() if args.embed else None
    t = time.time()
    for size, overlap in ((500, 75), (1000, 150), (1600, 240), (2400, 360)):
        for rerank in (False, True):
            r = evaluate(size, overlap, args.k, rerank, embed, verbose=(size, rerank) == (1600, True))
            print(f"chunk {size:>4}/{overlap:<3} rerank={str(rerank):5} recall@{args.k}={r['recall@k']:.3f}  MRR={r['mrr']:.3f}")
    print(f"({len(QUESTIONS)} questions, {time.time() - t:.1f}s)")
