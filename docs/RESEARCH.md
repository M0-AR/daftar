# Research log: best practices checked against Daftar (2026-11-09)

Rule followed: no change without (1) an external source and (2) a local measurement or a failing test
that the change fixes. Rejected ideas are listed too, with the evidence against them.

Retrieval numbers come from `scripts/eval_retrieval.py` (27 labelled questions: an English contract,
English research notes, two Arabic documents, one vocalized). Live numbers come from the local Ollama
(qwen2.5:1.5b, qwen2:0.5b) on this machine.

## Adopted

| # | Finding (source) | Evidence here | Change |
|---|---|---|---|
| 1 | SQLite FTS5 `unicode61` treats every character outside `L* N* Co` as a separator, so combining marks (Arabic harakat, Mn) split words ([SQLite FTS5 docs](https://www.sqlite.org/fts5.html)) | Vocalized كَتَبَ was indexed as ك/ت/ب; **0 of 4** Arabic questions answered | `textnorm.py`: NFKC + Arabic normalization (strip harakat/tatweel, unify أإآٱ→ا, ى→ي, ة→ه) before indexing and querying; index migrated (schema v2) |
| 2 | Arabic IR normalization + Light10 stemming ([Larkey et al., SIGIR 2002](https://ciir.cs.umass.edu/pubfiles/ir-249.pdf); [UMass TREC-10](https://trec.nist.gov/pubs/trec10/papers/UMass_TREC10_Final.pdf); [arabic-normalize](https://github.com/AbdeldjalilChougui/arabic-normalize)) | With 500-char blocks: recall@4 0.963 → **1.000**, MRR 0.920 → 0.957 (ablation) | Light10-style prefix/suffix stripping for Arabic tokens |
| 3 | NFKC folds ligatures and Arabic presentation forms from PDF text layers ([PDF parser comparisons 2026](https://www.nutrient.io/blog/best-python-pdf-libraries/)) | `ﻣﺮﺎﺑﺤﺔ` → `مرابحة`, `ﬁ` → `fi` (test) | Applied to search and to extracted PDF text; line-break hyphenation re-joined |
| 4 | Fuse BM25 and vectors with Reciprocal Rank Fusion (k=60); don't add scores of different scales ([denser.ai 2026](https://denser.ai/blog/hybrid-search-for-rag/), [dataaspirant](https://dataaspirant.com/blog/hybrid-search/)) | Score-weighted re-sorting **never beat RRF** (MRR 0.963 vs 0.981 at 1000 chars) | Order always by RRF; "Improve relevance" now only drops weak passages, never reorders |
| 5 | Recursive splitting (paragraph → line → sentence → word) is the recommended default ([PremAI 2026](https://www.premai.io/blog/rag-chunking-strategies-the-2026-benchmark-guide/), [Firecrawl](https://www.firecrawl.dev/blog/best-chunking-strategies-rag)) | 1000 chars: MRR 0.981 → **1.000**; 500 chars: recall@2 0.963 → 1.000; 0 blocks end mid-sentence | Boundary priority in `chunk_pages` (Latin + Arabic punctuation) |
| 6 | Ollama silently drops the start of prompts longer than `num_ctx` ([Ollama FAQ](https://docs.ollama.com/faq), [particula.tech](https://particula.tech/blog/ollama-num-ctx-silent-prompt-truncation), [HN/Aider](https://news.ycombinator.com/item?id=42833427)) | Measured: num_ctx 2048 → model read **2048 of ~6000** tokens | `fit_prompt`: per-script token estimate, num_ctx from {4K, 8K, 16K, 32K} ≤ the model's real context length (`/api/show`), trims history then weakest passages |
| 7 | Token cost differs by script | Measured chars/token: English 4.85, plain Arabic 2.42, **vocalized Arabic 1.16** | Estimate counts Arabic chars as 1 token, others as 1/3.5 (never under-counts any measured text) |
| 8 | Local HTTP servers MUST validate `Origin` and return 403 when present and invalid ([MCP transports spec](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports)) | Failing test with `Origin: https://evil.example` | Origin must equal our own page; Host loopback check and X-Daftar header kept |
| 9 | `BaseHTTPMiddleware` hides client disconnects from streaming responses; use pure ASGI middleware ([Starlette PR #1715](https://github.com/Kludex/starlette/pull/1715), [#2620](https://github.com/Kludex/starlette/pull/2620), [quern #208](https://github.com/quern-dev/quern/issues/208)) | Minimal app: cleanup after disconnect in 0.1 s; with BaseHTTPMiddleware **never** (15 s). Live: Stop lost the partial answer | Guard rewritten as pure ASGI; generator closed on disconnect → Ollama stream closed, partial answer saved and marked stopped (live: 0.2 s) |
| 10 | Stream uploads in chunks with a running size limit ([fast.io](https://fast.io/resources/fastapi-upload-file-size-limit/), [upload benchmark](https://github.com/fedirz/fastapi-file-upload-benchmark)) | Uploads were read fully into memory, no limit | 1 MB chunks to a temp file, SHA-256 while streaming, 200 MB per-file cap, no leftovers (test) |
| 11 | OWASP LLM01: limit the model's role, require citations, validate output with deterministic code ([OWASP LLM01:2025](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)); documents can impersonate chat-template control tokens ([arXiv 2606.09005](https://arxiv.org/pdf/2606.09005)) | Test: passage with `<end_of_turn><start_of_turn>user …` | Chat-template control tokens removed from passages and file names; citation chips only for existing sources |
| 12 | Model choice matters for injection resistance | Plain injection attack: qwen2:0.5b obeyed **6/6**, qwen2.5:1.5b **0/6** | Fallback picks the *largest* installed chat model, not the first alphabetically |
| 13 | Ollama `/api/show` reports `<arch>.context_length` and `capabilities` (checked locally: `qwen2.context_length: 32768`, `['completion','tools']`) | An embedder without "embed" in its name (e.g. all-minilm) was offered as a chat model | Models classified by capability (name heuristic only for old Ollama); metadata cached per digest |
| 14 | nomic-embed-text needs `search_query:` / `search_document:` prefixes; Ollama does not add them ([model card](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5)) | Already implemented | Kept; embedding requests now also set `num_ctx` from the embedder's context length (Ollama default window is smaller) |

## Considered and rejected (with evidence)

| Idea (source) | Why not |
|---|---|
| Spotlighting with delimiters around passages ([Hines et al., arXiv 2403.14720](https://arxiv.org/pdf/2403.14720)) | Measured on qwen2.5:1.5b: injection success went **0/6 → 6/6** with randomized delimiters. The paper itself advises against delimiting alone. |
| Datamarking (replace spaces with ^) | No gain over the current prompt (0/6 for both) and it alters the text a 1B model must read; not adopted. |
| Cross-encoder reranker | Ollama has no official rerank endpoint ([ollama #16076](https://github.com/ollama/ollama/issues/16076)); generate-based workarounds give uniform scores. Revisit when Ollama ships `/api/rerank`. |
| "Lost in the middle" edge ordering ([arXiv 2410.05983](https://arxiv.org/pdf/2410.05983)) | Paper reports negligible gains for small retrieval sets; Daftar sends ≤ 6 passages by default. Best passage stays first, question last. |
| Larger default blocks (400–512 tokens ≈ 1600–2000 chars) | Eval: 1000 = 1600 = 2400 chars here, 500 slightly worse. 1000 chars (~250 tokens, 15% overlap) is inside the researched 200–512 token range and keeps prompts small for 1B models. |
| FTS5 `porter` stemmer | English-only (SQLite docs); the prefix rule already handles English variants and Light10 handles Arabic. |
| PyMuPDF instead of pypdf | Faster, but AGPL-3.0; pypdf scored F1 ≈ 0.95–0.97 on legal/financial/manual documents ([arXiv 2410.09871](https://arxiv.org/html/2410.09871v1)). Kept pypdf + NFKC/de-hyphenation clean-up. |

## Still to verify when the models are installed
- `gemma3:1b`: rerun `pytest -m live` and the injection script; confirm the control-token filter matters for Gemma's `<start_of_turn>` template.
- `nomic-embed-text`: `python scripts/eval_retrieval.py --embed` to measure hybrid search on the same set.
