# Shipping Daftar to paying users on Windows, macOS and Linux

Goal: a buyer downloads one installer, double-clicks it, and is asking questions about their documents
within minutes, with no terminal, no second program to install, and no security warnings.
Researched 2026-11-10; every decision below cites a source or a local measurement.

## 1. The engine: bundle llama.cpp instead of requiring Ollama

**Why.** Requiring a separate Ollama install is the largest source of support pain:
"requires a newer version of Ollama" (HTTP 412) is reported as the most common Ollama failure, minimum
versions per model are undocumented, and updated clients often keep talking to an old server
([ollama #14431](https://github.com/ollama/ollama/issues/14431), [#13411](https://github.com/ollama/ollama/issues/13411),
[#18414](https://github.com/ollama/ollama/issues/18414)). On this machine Ollama 0.23.2 silently ran CPU-only,
could not run Qwen3.5 or PaddleOCR-VL, and crashed on an image (docs/RESEARCH.md #19, #20, #22).
The products users already trust ship the engine inside the app: Jan bundles llama.cpp (CUDA 13 + Vulkan),
LM Studio ships a llama.cpp runtime, and AnythingLLM replaced its bundled Ollama with llama.cpp
([Jan docs](https://www.jan.ai/docs/desktop/local-engine/llama-cpp), [promptquorum](https://www.promptquorum.com/local-llms/local-llm-one-click-installers)).

**What.** Bundle the official `llama-server` (MIT) and run it as a private child process on a loopback port,
exactly like Daftar's own server. It provides chat, embeddings, **reranking** (`/v1/rerank`, which Ollama lacks)
and vision via `--mmproj` ([llama-server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)).
Single-user speed is equal or 5-15% better than Ollama ([InventiveHQ](https://inventivehq.com/blog/ollama-vs-llama-cpp-vs-lm-studio-benchmark)).

| Platform | Build to bundle | Size (b12176) | GPUs covered |
|---|---|---|---|
| Windows x64 | `win-vulkan-x64` + `win-cpu-x64` fallback | 36 MB + 24 MB | NVIDIA, AMD, Intel via Vulkan; any CPU |
| macOS Apple Silicon | `macos-arm64` | 16 MB | Metal |
| macOS Intel | `macos-x64` | 12 MB | CPU only |
| Linux x64 | `ubuntu-vulkan-x64` + `ubuntu-x64` fallback | 34 MB + 22 MB | NVIDIA, AMD, Intel via Vulkan |

CUDA builds are faster on NVIDIA but cost 163 MB + 407 MB runtime per install; offer them later as an optional
"NVIDIA speed pack". Lesson from Jan's bug ([jan #9091](https://github.com/janhq/jan/issues/9091)): never pick a
backend from a table; probe it (`llama-server --list-devices`) and fall back to CPU if the GPU backend fails to load.
Keep Ollama support as an "advanced: use my own Ollama/llama-server" option, as Jan does.

## 2. Models: download on first run, sized to the machine, commercially licensed

- **Licences for a paid product.** Qwen3 / Qwen3.5 / Qwen3-Embedding and PaddleOCR-VL are Apache 2.0; Gemma 4 is
  Apache 2.0; **Gemma 1-3 (our current default gemma3:1b) use the custom Gemma Terms**, which require passing the use
  restrictions down to every customer ([promise.legal](https://blog.promise.legal/open-weight-ai-license-trap-startups/),
  [Gemma 4 Apache](https://www.mindstudio.ai/blog/what-is-gemma-4-apache-2-license-commercial-ai-deployment),
  [Qwen3 Apache](https://nanobase.ai/answers/open-weight-models/qwen-3-commercial-license),
  [PaddleOCR-VL licence](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6/blob/main/LICENSE)).
  → Default to Apache-2.0 models; choose between Qwen3.5-2B and Gemma 4 E2B with `scripts/eval_answers.py`.
- **Size by hardware.** Detect RAM and VRAM; recommend a model that fits weights + KV cache, because spilling to
  system RAM can be ~30x slower ([promptquorum VRAM tiers](https://www.promptquorum.com/local-llms)). Rough tiers:
  ≤8 GB RAM / no GPU → 1-2B; 4-6 GB VRAM → 3-4B Q4; 8-12 GB → 7-14B Q4; 24 GB → up to ~30B Q4. Show a green/yellow/red
  "fits" badge like LM Studio.
- **Downloads.** Resumable, SHA-256 verified, with a mirror fallback (Jan added a backup CDN after GitHub failures,
  [Jan v0.6.10](https://www.jan.ai/changelog/2025-09-18-auto-optimize-vision-imports)). A "use files I already have"
  button imports GGUFs from disk (offline installs, slow connections).
- **Default set:** chat (~1.3 GB) + search Qwen3-Embedding-0.6B (639 MB) + OCR PaddleOCR-VL (optional, ~1 GB).

## 3. Installers, signing and the window

| | Windows | macOS | Linux |
|---|---|---|---|
| Package | Inno Setup installer (per-user, no admin) | signed + notarized `.dmg` | AppImage (+ `.deb`) |
| Web view | WebView2: present on Windows 11 and most Windows 10; check the registry and run Microsoft's 2 MB bootstrapper only if missing ([Microsoft](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)) | WKWebView, built in | WebKitGTK is the fragile one: use the system webkit2gtk-4.1 ("thin AppImage") and open in the browser if it is missing ([pywebview #1818](https://github.com/r0x0r/pywebview/discussions/1818), [briefcase #1029](https://github.com/beeware/briefcase/issues/1029)) |
| Signing | Azure Artifact Signing, $9.99/month; EV certificates no longer skip SmartScreen, reputation builds per publisher ([Microsoft](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation), [gdgsoft](https://www.gdgsoft.com/faq/azure-trusted-signing-cost-effective-exe-code-signing)) | Apple Developer ID, $99/year; hardened runtime + entitlements `allow-unsigned-executable-memory` (PyInstaller needs it), sign inside-out, notarize, staple ([dev.to](https://dev.to/shipyourpythonapp/shipping-a-python-desktop-app-to-real-users-everything-that-breaks-after-pyinstaller-succeeds-118f), [pyinstaller #4629](https://github.com/pyinstaller/pyinstaller/issues/4629)) | none needed; publish SHA-256 sums |

Without signing, Windows shows "Windows protected your PC" and macOS refuses to open the app, the two most
common reasons buyers uninstall.

**Updates:** tufup (TUF-signed, patch-based updates for PyInstaller apps; PyUpdater is discontinued)
([tufup](https://github.com/dennisvang/tufup)). Always ask before updating, and keep the previous engine
version for rollback (Jan users complained when they could not roll back, [jan #9177](https://github.com/janhq/jan/issues/9177)).

## 4. First-run experience (the 5-minute promise)

1. Welcome: what Daftar does, that nothing leaves the computer.
2. Hardware check (automatic): CPU, RAM, GPU + backend that actually loads; plain-language result.
3. "Recommended for this computer": one button downloads the fitted set, with progress, pause and resume; or
   "I have model files" to import.
4. Smoke test: one tiny question to the model; show the speed. If it fails, fall back (GPU → CPU) and say so.
5. Create the first collection with drag and drop.
Every failure screen states the cause and one action ("Your graphics driver is too old for GPU mode; Daftar is
using the processor. Update the driver to make it about 10x faster").

## 5. Plan and status

| Phase | Work | Verifiable here? | Status |
|---|---|---|---|
| 0 | CPU warning, model fallbacks, `--self-test`, CI matrix, smoke test | yes | done (51c9b05) |
| 1a | Engine choice: Ollama or any OpenAI-compatible app (LM Studio, Jan, llama.cpp, vLLM, LocalAI, KoboldCpp) with detection | yes | done |
| 1b | Bundled `llama-server` (Vulkan/Metal/CPU) started by Daftar itself, using the 1a connector; probe + fallback | Linux yes; Win/Mac via CI | proposed |
| 2 | First-run wizard: hardware detection, fitted model download/import, smoke test | yes | proposed |
| 3 | Reranking with llama-server `/v1/rerank`, kept only if `eval_answers.py` improves | yes | proposed |
| 4 | Installers: Inno Setup (+WebView2 check), notarized DMG, AppImage/.deb; signing in CI | needs accounts | needs decision |
| 5 | Auto-update with tufup | yes | proposed |

**Decisions needed from the owner:** Apple Developer account ($99/yr) and Azure Artifact Signing ($9.99/mo,
needs a business or self-employed identity in US/CA/EU/UK); a GitHub (or other) repo for CI builds;
where installers and model mirrors are hosted.
