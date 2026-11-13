from daftar import hardware


def test_linux_ram_from_meminfo():
    text = "MemTotal:       65843124 kB\nMemFree: 1 kB\n"
    assert hardware.linux_ram_gb(text) == 62.8
    assert hardware.linux_ram_gb("") is None


def test_nvidia_parser():
    out = "NVIDIA GeForce RTX 3090, 24576\nNVIDIA GeForce RTX 4090, 24564\n"
    assert hardware.parse_nvidia(out) == {"kind": "nvidia", "name": "NVIDIA GeForce RTX 3090", "vram_gb": 24.0}
    assert hardware.parse_nvidia("not a gpu\n") is None
    assert hardware.parse_nvidia("") is None


def test_fit_levels():
    assert hardware.fit(1.3, 32, 24)["level"] == "gpu"
    assert hardware.fit(1.3, 8, None)["level"] == "cpu"
    assert hardware.fit(1.3, 3, None)["level"] == "tight"
    assert hardware.fit(1.3, 2, None)["level"] == "too_big"
    assert hardware.fit(1.3, None, None)["level"] == "unknown"
