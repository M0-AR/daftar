import json
import os
import subprocess
import sys
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


def test_models_never_offer_an_embedding_file_as_chat(tmp_path):
    (tmp_path / "Qwen3-Embedding-0.6B-Q8_0.gguf").write_bytes(b"x" * 100)
    chat = tmp_path / "Qwen3.5-2B-Q4_K_M.gguf"
    chat.write_bytes(b"x" * 10)

    assert ls.find_models(roots=[tmp_path]) == [chat.resolve()]


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX loader variable")
def test_loader_path_prefers_the_program_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/stale/libs")

    env = ls._library_path(tmp_path / "llama-server")

    assert env["LD_LIBRARY_PATH"].split(os.pathsep)[0] == str(tmp_path)
    assert "/stale/libs" in env["LD_LIBRARY_PATH"]


def test_stale_pid_file_is_removed_when_its_process_is_gone(tmp_path):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    pid_file = tmp_path / "llama-server.pid"
    pid_file.write_text(json.dumps({"pid": dead.pid, "parent": os.getpid()}))
    server = ls.LlamaServer([Path("/no/llama-server")], tmp_path / "m.gguf", tmp_path)

    server._kill_stale()

    assert not pid_file.exists()


def test_stale_pid_file_of_a_running_instance_is_left_alone(tmp_path):
    if os.getppid() == 0:
        pytest.skip("no parent process to act as the other Daftar instance")
    pid_file = tmp_path / "llama-server.pid"
    pid_file.write_text(json.dumps({"pid": 1, "parent": os.getppid()}))
    server = ls.LlamaServer([Path("/no/llama-server")], tmp_path / "m.gguf", tmp_path)

    server._kill_stale()
    server._clear_pid()

    assert pid_file.exists()


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
