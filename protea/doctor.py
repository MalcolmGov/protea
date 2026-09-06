"""`protea doctor` — report what this machine can and cannot do. Every check is a real probe."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(name, ok, detail))

    def to_dict(self) -> dict:
        return {"checks": [asdict(c) for c in self.checks]}


def _gpu_detail() -> tuple[bool, str]:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return False, "nvidia-smi not found (no NVIDIA GPU or driver on this host)"
    try:
        out = subprocess.run(
            [smi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"nvidia-smi failed: {exc}"
    lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    return (bool(lines), "; ".join(lines) or (out.stderr.strip() or "no GPU reported"))


def _cuda_detail() -> tuple[bool, str]:
    try:
        import torch  # type: ignore
    except ImportError:
        return False, "torch not installed (install the [train] or [serve] extra where GPUs live)"
    return bool(
        torch.cuda.is_available()
    ), f"torch {torch.__version__}, cuda={torch.version.cuda}, available={torch.cuda.is_available()}"


def _memory_gb() -> float | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return round(pages * page_size / 1e9, 1)
    except (ValueError, OSError, AttributeError):
        return None


def run(cwd: str | None = None) -> DoctorReport:
    r = DoctorReport()
    r.add("python", sys.version_info >= (3, 11), f"{platform.python_version()} ({sys.executable})")
    r.add("platform", True, f"{platform.system()} {platform.release()} {platform.machine()}")
    ok, detail = _gpu_detail()
    r.add("gpu", ok, detail)
    ok, detail = _cuda_detail()
    r.add("cuda", ok, detail)
    mem = _memory_gb()
    r.add("memory", mem is not None and mem >= 8, f"{mem} GB" if mem is not None else "unknown")
    usage = shutil.disk_usage(cwd or os.getcwd())
    free_gb = round(usage.free / 1e9, 1)
    r.add("disk", free_gb >= 20, f"{free_gb} GB free (20 GB+ recommended for a base model download)")
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "HF_TOKEN"):
        r.add(f"env:{name}", bool(os.environ.get(name)), "set" if os.environ.get(name) else "not set")
    r.add(
        "inference_server",
        bool(os.environ.get("PROTEA_INFERENCE_URL")),
        os.environ.get("PROTEA_INFERENCE_URL") or "PROTEA_INFERENCE_URL not set (planned: vLLM endpoint)",
    )
    return r
