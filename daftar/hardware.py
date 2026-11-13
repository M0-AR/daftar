"""What this computer can run: memory, CPU and a GPU that is actually present.

Standard library only; parsers are separate from the probes so they can be tested without hardware.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


def linux_ram_gb(meminfo: str) -> float | None:
    for line in meminfo.splitlines():
        if line.startswith("MemTotal:"):
            return round(int(line.split()[1]) * 1024 / 2**30, 1)
    return None


def parse_nvidia(output: str) -> dict | None:
    """First GPU from `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits`."""
    for line in output.splitlines():
        name, _, vram = line.partition(",")
        name, vram = name.strip(), vram.strip()
        if name and vram.isdigit():
            return {"kind": "nvidia", "name": name, "vram_gb": round(int(vram) / 1024, 1)}
    return None


def _windows_ram_gb() -> float | None:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return round(status.ullTotalPhys / 2**30, 1)


def ram_gb() -> float | None:
    try:
        if sys.platform == "win32":
            return _windows_ram_gb()
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
            return round(int(out.stdout.strip()) / 2**30, 1)
        return linux_ram_gb(Path("/proc/meminfo").read_text())
    except Exception:
        return None


def _amd_gpu() -> dict | None:
    try:
        for path in sorted(Path("/sys/class/drm").glob("card[0-9]*/device/mem_info_vram_total")):
            vram = int(path.read_text().strip())
            if vram:
                return {"kind": "amd", "name": "AMD graphics", "vram_gb": round(vram / 2**30, 1)}
    except Exception:
        pass
    return None


def gpu_info(ram: float | None = None) -> dict:
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=10)
            gpu = parse_nvidia(out.stdout)
            if gpu:
                return gpu
        except Exception:
            pass
    if sys.platform == "darwin" and os.uname().machine == "arm64":
        return {"kind": "apple", "name": "Apple Silicon", "vram_gb": ram}
    return _amd_gpu() or {"kind": "none", "name": None, "vram_gb": None}


@lru_cache(maxsize=1)
def summary() -> dict:
    ram = ram_gb()
    return {"ram_gb": ram, "cpu_count": os.cpu_count() or 1, "gpu": gpu_info(ram), "platform": sys.platform}


def fit(model_gb: float, ram: float | None, vram: float | None) -> dict:
    """Leave room for the app, the KV cache and the rest of the desktop; spilling to RAM is ~30x slower."""
    if not ram and not vram:
        return {"level": "unknown", "label": "Memory not detected", "note": "The model may still run; try it."}
    need = model_gb * 1.5
    if vram and need <= vram * 0.75:
        return {"level": "gpu", "label": "Fits your graphics card",
                "note": f"Uses the {vram:g} GB GPU; answers are fast."}
    if ram and need <= ram * 0.5:
        return {"level": "cpu", "label": "Runs on the processor",
                "note": f"Fits in {ram:g} GB memory, but answers are slower without a GPU."}
    if ram and need <= ram * 0.8:
        return {"level": "tight", "label": "Tight fit", "note": "Long documents may slow it down."}
    return {"level": "too_big", "label": "Too large for this computer", "note": "Choose a smaller model."}
