import tempfile
from pathlib import Path

import pytest

from daftar import llama_server as ls
from daftar.llama_server import LlamaClient


def test_models_skip_projectors_and_prefer_qwen(tmp_path):
    (tmp_path / "mmproj-Qwen3.5-2B-F16.gguf").write_bytes(b"x")
    (tmp_path / "SmolVLM-256M-Instruct-Q8_0.gguf").write_bytes(b"x" * 10)
    (tmp_path / "gemma-3-1b-it-Q4_K_M.gguf").write_bytes(b"x" * 20)
    qwen = tmp_path / "Qwen3.5-2B-Q4_K_M.gguf"
    qwen.write_bytes(b"x" * 30)

    found = ls.find_models(roots=[tmp_path])

    assert found[0] == qwen.resolve()
    assert all("mmproj" not in p.name for p in found)


def test_binaries_try_gpu_builds_before_cpu(tmp_path):
    for name in ("cpu", "vulkan"):
        (tmp_path / name).mkdir()
        binary = tmp_path / name / "llama-server"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
    (tmp_path / "notes.txt").write_text("not a program")

    found = ls.find_binaries(roots=[tmp_path])

    assert [p.parent.name for p in found] == ["vulkan", "cpu"]


def test_explicit_binary_stays_first(tmp_path):
    cpu, gpu = tmp_path / "cpu", tmp_path / "vulkan"
    for folder in (cpu, gpu):
        folder.mkdir()
        (folder / "llama-server").write_text("#!/bin/sh\n")
        (folder / "llama-server").chmod(0o755)

    found = ls.find_binaries(str(cpu / "llama-server"), roots=[tmp_path])

    assert found[0] == (cpu / "llama-server").resolve()


def test_command_stays_private_and_single_slot(tmp_path):
    model = tmp_path / "Qwen3.5-2B-Q4_K_M.gguf"
    cmd = ls.command(Path("/opt/llama/llama-server"), model, 18080)

    assert cmd[:3] == ["/opt/llama/llama-server", "-m", str(model)]
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--parallel") + 1] == "1"
    assert cmd[-2:] == ["--alias", "Qwen3.5-2B-Q4_K_M"]


def test_metadata_needs_no_running_server(tmp_path):
    model = tmp_path / "Qwen3.5-2B-Q4_K_M.gguf"
    model.write_bytes(b"x" * 5)
    server = ls.LlamaServer([Path("/does/not/exist/llama-server")], model, tmp_path)
    client = LlamaClient(server)

    assert client.version() == "llama.cpp (built-in)"
    assert client.model_list()[0]["name"] == "Qwen3.5-2B-Q4_K_M"
    assert client.model_list()[0]["size"] == 5
    assert client.show("anything")["capabilities"] == ["completion"]
    with pytest.raises(Exception):
        client.embed("anything", ["text"])


@pytest.mark.live
def test_live_builtin_engine_answers():
    binaries, models = ls.find_binaries(), ls.find_models()
    if not binaries or not models:
        pytest.skip("no llama-server program or .gguf model found")
    server = ls.LlamaServer(binaries, models[0], Path(tempfile.mkdtemp(prefix="daftar-llama-")))
    try:
        answer = "".join(LlamaClient(server).chat(
            "", [{"role": "user", "content": "Reply with the single word: OK"}], 0.0, 1024, think=False)).strip()
        assert "ok" in answer.lower(), answer
        assert server.running()
    finally:
        server.stop()
