"""Can this machine score jobs by itself, and with which model?

Every quota problem this app has is the same problem: a free tier that is shared,
throttled and occasionally gone. A model running on the user's own GPU has none
of that — no 429, no daily cap, no prompt leaving the machine. What it does have
is a hard ceiling: the model must fit in VRAM, and a model that does not fit runs
ten times slower on the CPU, which for a scan of fifty offers is the difference
between minutes and an afternoon.

So the question is never "is local better" but "what fits here". This module
answers it: read the hardware, translate it into a model size, and check what is
already installed. It talks to Ollama rather than downloading GGUF files itself —
Ollama already solves quantisation, licences, resume and disk layout, and it is
an OpenAI-compatible server, which is exactly what ``CustomOpenAIProvider``
speaks. Nothing here ever runs inference.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from app.log import get_logger

log = get_logger(__name__)

#: Default Ollama endpoint. The provider wants the OpenAI-compatible path
#: (``/v1``); the management API (tags, pull) lives at the root.
OLLAMA_HOST = "http://localhost:11434"
OLLAMA_OPENAI_BASE = f"{OLLAMA_HOST}/v1"

#: A quantised (Q4) model needs roughly 0.6 GB of VRAM per billion parameters,
#: plus room for the context window. Rounded up from measurements rather than
#: theory: a 12B Q4 occupies ~7 GB on disk and ~8.5 GB loaded with a 8k context.
_GB_PER_B_Q4 = 0.62
_CONTEXT_OVERHEAD_GB = 1.5


@dataclass
class Hardware:
    gpu_name: str
    vram_gb: float
    ram_gb: float
    cpu: str
    source: str  # how VRAM was measured, so the UI can say when it is a guess


@dataclass
class Recommendation:
    verdict: str  # "good" | "workable" | "cpu_only" | "unknown"
    max_params_b: int
    models: list[dict[str, str]]
    reason: str


def _run(cmd: list[str], timeout: float = 6.0) -> str:
    """Run a short informational command. Never raises: absent tools are normal."""
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("hardware probe %s skipped: %s", cmd[0], exc)
        return ""


def _nvidia_vram() -> tuple[str, float]:
    """(name, VRAM in GB) from nvidia-smi, or ("", 0)."""
    exe = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    raw = _run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader"])
    if not raw:
        return "", 0.0
    first = raw.splitlines()[0]
    name, _, mem = first.partition(",")
    match = re.search(r"(\d+)", mem)
    if not match:
        return name.strip(), 0.0
    return name.strip(), round(int(match.group(1)) / 1024, 1)


def _system_ram_gb() -> float:
    try:
        if platform.system() == "Windows":
            raw = _run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory",
                ],
                timeout=15.0,
            )
            return round(int(raw) / (1024**3), 1) if raw.strip().isdigit() else 0.0
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / (1024**2), 1)
    except (OSError, ValueError) as exc:
        log.debug("RAM probe skipped: %s", exc)
    return 0.0


def detect_hardware() -> Hardware:
    """What this machine has. Zero-cost and best-effort: unknown fields are 0.

    VRAM comes from ``nvidia-smi`` when present. The Windows WMI figure is NOT
    used as a substitute: ``Win32_VideoController.AdapterRAM`` is a 32-bit field
    and saturates at 4 GB, which reported an RTX 5070 (12 GB) as a 4 GB card.
    """
    gpu_name, vram = _nvidia_vram()
    source = "nvidia-smi" if gpu_name else "none"
    if not gpu_name and platform.system() == "Windows":
        gpu_name = _run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_VideoController | "
                "Where-Object { $_.AdapterRAM -gt 0 } | "
                "Select-Object -First 1 -ExpandProperty Name)",
            ],
            timeout=15.0,
        )
        source = "name-only" if gpu_name else "none"
    return Hardware(
        gpu_name=gpu_name or "",
        vram_gb=vram,
        ram_gb=_system_ram_gb(),
        cpu=platform.processor() or platform.machine(),
        source=source,
    )


def _fits(vram_gb: float) -> int:
    """Largest parameter count (in billions) that fits in ``vram_gb`` at Q4."""
    usable = max(0.0, vram_gb - _CONTEXT_OVERHEAD_GB)
    return int(usable / _GB_PER_B_Q4)


#: Ollama tags worth suggesting, smallest first. Deliberately short and all from
#: families that emit clean JSON: scoring is a structured-output job, and the
#: instruct builds of gemma/qwen are what survive it (see the scoring notes in
#: model_selector). Sizes are the parameter counts the tag advertises.
_CANDIDATES: tuple[tuple[int, str, str], ...] = (
    (4, "gemma3:4b", "Il minimo utile: gira ovunque, ma giudica in modo grossolano."),
    (8, "qwen3:8b", "Buon compromesso su schede da 8 GB."),
    (12, "gemma3:12b", "Il punto dolce su una scheda da 12 GB: JSON pulito e veloce."),
    (14, "qwen3:14b", "Come sopra, famiglia diversa: utile come secondo parere."),
    (27, "gemma3:27b", "Qualita' vicina ai modelli cloud, serve una scheda da 24 GB."),
)


def recommend(hardware: Hardware) -> Recommendation:
    """Which local models this machine can actually run, and whether it is worth it."""
    if hardware.vram_gb <= 0:
        return Recommendation(
            verdict="unknown" if hardware.source == "name-only" else "cpu_only",
            max_params_b=0,
            models=[],
            reason=(
                "Nessuna GPU NVIDIA rilevata. Un modello su CPU impiega minuti per "
                "offerta: usalo per provare, non per uno scan."
            ),
        )
    ceiling = _fits(hardware.vram_gb)
    usable = [
        {"tag": tag, "params_b": str(size), "note": note}
        for size, tag, note in _CANDIDATES
        if size <= ceiling
    ]
    if not usable:
        return Recommendation(
            verdict="cpu_only",
            max_params_b=ceiling,
            models=[],
            reason=(
                f"{hardware.vram_gb} GB di VRAM non bastano nemmeno per un modello da 4B "
                "quantizzato. Meglio restare sui provider gratuiti."
            ),
        )
    best = usable[-1]
    verdict = "good" if int(best["params_b"]) >= 12 else "workable"
    return Recommendation(
        verdict=verdict,
        max_params_b=ceiling,
        models=usable,
        reason=(
            f"{hardware.gpu_name} con {hardware.vram_gb} GB regge modelli fino a "
            f"~{ceiling}B quantizzati. Consigliato: {best['tag']}."
        ),
    )


def ollama_status(host: str = OLLAMA_HOST, timeout: float = 3.0) -> dict[str, Any]:
    """Is Ollama running, and what has it already downloaded?

    ``installed`` distinguishes "not running" from "not installed at all": the
    first is a button, the second is a link to the installer.
    """
    installed = bool(shutil.which("ollama")) or bool(
        shutil.which("ollama.exe")
        or _run(["powershell", "-NoProfile", "-Command", "(Get-Command ollama).Source"], 15.0)
    )
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        log.debug("ollama not reachable at %s: %s", host, exc)
        return {"running": False, "installed": installed, "models": [], "host": host}
    models = [
        {
            "name": str(m.get("name") or ""),
            "size_gb": round(int(m.get("size") or 0) / (1024**3), 1),
        }
        for m in (data.get("models") or [])
    ]
    return {"running": True, "installed": True, "models": models, "host": host}


#: Name of the derived model the app scores with. Fixed and deterministic, so
#: choosing another base model overwrites it rather than littering the list.
SCORING_VARIANT = "jobfinder-scorer"

#: Context window for that variant. Ollama defaults to 4096 tokens NO MATTER what
#: ``max_tokens`` asks for, and a scoring prompt is ~2500 tokens of CV plus
#: posting: measured, the reply was cut off at exactly ``total_tokens: 4096``
#: every time, which the app then correctly reported as a truncation and fell
#: back to the keyword estimate. 16k leaves room for a batch of three offers.
SCORING_NUM_CTX = 16384


def ensure_scoring_variant(base_model: str, host: str = OLLAMA_HOST, timeout: float = 60.0) -> str:
    """Create (or refresh) a copy of ``base_model`` with a usable context window.

    Returns the variant's name, or ``base_model`` unchanged when Ollama refuses —
    a smaller window is worse, not fatal, and the truncation guard downstream
    still catches it. The copy is free and instant: Ollama layers parameters over
    the same weights rather than duplicating the file.
    """
    payload = json.dumps(
        {
            "model": SCORING_VARIANT,
            "from": base_model,
            "parameters": {"num_ctx": SCORING_NUM_CTX},
            "stream": False,
        }
    ).encode()
    request = urllib.request.Request(
        f"{host}/api/create", data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            if resp.status == 200:
                return SCORING_VARIANT
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        log.warning("could not widen the context window for %s: %s", base_model, exc)
    return base_model


def pull_events(model: str, host: str = OLLAMA_HOST) -> Any:
    """Yield Ollama's own progress lines while it downloads ``model``.

    Ollama streams newline-delimited JSON; each line is passed through as-is so
    the caller can turn it into an SSE event. Download, resume and checksum are
    Ollama's problem, which is the entire reason for going through it.
    """
    body = json.dumps({"model": model, "stream": True}).encode()
    request = urllib.request.Request(
        f"{host}/api/pull", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=3600) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


_FAMILIES = ("gemma", "qwen", "llama", "mistral", "phi", "deepseek")


def _family_of(name: str) -> str:
    lowered = name.lower()
    return next((f for f in _FAMILIES if f in lowered), "")


def already_usable(models: list[dict[str, Any]], ceiling_b: int) -> list[dict[str, Any]]:
    """Downloaded models that fit this machine, best-first.

    A model already on disk beats a better one that is not: it costs no download
    and it is what the user actually chose. Sizes are read from the tag, which is
    how Ollama names things — "gemma-4-12b-it-GGUF:Q4_K_M" is a 12B.
    """
    from app.providers.model_selector import infer_size_b

    usable = []
    for model in models:
        name = str(model.get("name") or "")
        size = infer_size_b(name)
        if ceiling_b and size > ceiling_b:
            continue  # would spill out of VRAM and crawl on the CPU
        usable.append(
            {
                "name": name,
                "params_b": size,
                "size_gb": model.get("size_gb"),
                "family": _family_of(name),
            }
        )
    # Bigger is better among the ones that fit; a model whose tag states no size
    # sorts last rather than being dropped (it may still be perfectly good).
    return sorted(usable, key=lambda m: m["params_b"] or 0, reverse=True)


def snapshot() -> dict[str, Any]:
    """Everything the settings panel needs, in one call."""
    hardware = detect_hardware()
    recommendation = recommend(hardware)
    status = ollama_status()
    ready = already_usable(status["models"], recommendation.max_params_b)

    # Mark a suggestion as covered when a downloaded model of the same family is
    # at least as big: suggesting gemma3:12b to someone who already has a 12B
    # gemma is asking them to download seven gigabytes for nothing.
    for entry in recommendation.models:
        want_family = _family_of(entry["tag"])
        want_size = int(entry["params_b"])
        entry["installed"] = (
            "1"
            if any(m["family"] == want_family and (m["params_b"] or 0) >= want_size for m in ready)
            else ""
        )

    reco: dict[str, Any] = asdict(recommendation)
    if ready:
        best = ready[0]
        reco["reason"] = (
            f"{hardware.gpu_name} con {hardware.vram_gb} GB regge modelli fino a "
            f"~{recommendation.max_params_b}B quantizzati. Hai gia' {best['name']} "
            f"({best['params_b']}B): puoi usarlo subito, senza scaricare nulla."
        )
    return {
        "hardware": asdict(hardware),
        "recommendation": reco,
        "ollama": status,
        "ready": ready,
        "openai_base_url": OLLAMA_OPENAI_BASE,
    }
