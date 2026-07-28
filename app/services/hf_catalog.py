"""Which models the world actually runs locally, and which of them fit this card.

The local-model panel offered five tags written by hand. They are good tags, but
the list ages the day a new model lands, and it cannot know that the best build
for a 12 GB card today is a quantisation-aware 12B published on Hugging Face —
which is exactly what this machine ended up running, downloaded by hand.

So the catalogue is read from Hugging Face instead: ``/api/models?filter=gguf``
sorted by downloads, then each candidate's ``.gguf`` files for the quantisation
levels it ships. Public, unauthenticated, no inference, and cached — the same
contract as the OpenRouter health lookup in :mod:`app.services.model_stats`.

Ollama stays the only runtime: what this module produces is a ``ollama pull
hf.co/<repo>:<QUANT>`` command, not a download.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.log import get_logger
from app.providers.model_selector import SCORING_UNFIT_MARKERS, infer_size_b
from app.services.local_models import quality_penalty, quant_of, vram_needed_gb

log = get_logger(__name__)

HF_MODELS_URL = "https://huggingface.co/api/models"
HF_MODEL_URL = "https://huggingface.co/api/models/{repo}"

#: Long enough that browsing the panel twice costs one fetch, short enough that
#: a model published this week shows up this week.
_CACHE_TTL_SECONDS = 24 * 3600
_cache: dict[str, Any] = {"fetched_at": 0.0, "data": None}

#: Below this the model is a toy for scoring work — the same floor the cloud
#: ranking applies (``SCORING_MIN_SIZE_B``) would exclude everything that fits a
#: consumer card, so this is deliberately lower: a local 4B is a legitimate
#: choice on an 8 GB laptop, a 0.5B is not.
MIN_PARAMS_B = 4

#: Not text completers, whatever their download count. The HF "gguf" filter
#: returns embedding and speech models at the very top (mxbai-embed,
#: embeddinggemma), and one text-to-speech model was picked to write JSON seven
#: times in a real scan before the cloud ranking learned the same lesson.
_EXTRA_UNFIT = ("embed", "rerank", "whisper", "tts", "speech", "voice", "clip", "sd-", "flux")


def _is_text_model(repo_id: str) -> bool:
    name = repo_id.lower()
    return not any(marker in name for marker in (*SCORING_UNFIT_MARKERS, *_EXTRA_UNFIT))


def _get_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "JobFinder"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read())


_QUANT_FILE_RE = re.compile(r"([A-Za-z0-9_.\-]+)\.gguf$", re.IGNORECASE)

#: The quantisation marker inside a GGUF filename, which is also the tag Ollama
#: resolves. Matched explicitly rather than "whatever follows the last dash":
#: that heuristic turned "Gemmable-4-12B-MTP-GGUF/…-mtp.gguf" into a pull tag of
#: ":mtp", which is not a build of anything.
_TAG_RE = re.compile(r"((?:UD-)?(?:I?Q\d(?:_[A-Za-z0-9]+)*|BF16|FP?16|FP?32|F32))", re.IGNORECASE)


def _quants_of(repo_id: str, timeout: float) -> list[dict[str, str]]:
    """Builds a repo ships: ``{"level": <class>, "tag": <what Ollama wants>}``.

    The two differ on purpose. ``level`` is the cost/quality class this app
    reasons with (a ``UD-Q4_K_XL`` file and a ``qat`` file are both int4-ish);
    ``tag`` is the string the file itself is named after, which is what
    ``ollama pull hf.co/repo:TAG`` resolves — hand it a normalised label and the
    pull fails with "manifest unknown".
    """
    try:
        data = _get_json(HF_MODEL_URL.format(repo=urllib.parse.quote(repo_id)), timeout)
    except Exception as exc:
        log.debug("hf files unavailable for %s: %s", repo_id, exc)
        return []
    seen: dict[str, dict[str, str]] = {}
    for sibling in data.get("siblings") or []:
        filename = str(sibling.get("rfilename") or "")
        if not filename.lower().endswith(".gguf"):
            continue
        # mmproj-* files are the vision projector, not a build of the model.
        if filename.rsplit("/", 1)[-1].lower().startswith("mmproj"):
            continue
        match = _QUANT_FILE_RE.search(filename)
        if not match:
            continue
        stem = match.group(1)
        # Ollama's tag is the quantisation marker inside the filename: a file
        # named "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf" is pulled as ":UD-Q4_K_XL".
        tag_match = _TAG_RE.search(stem)
        if not tag_match:
            continue  # a .gguf that names no build (merged shard, adapter…)
        seen.setdefault(quant_of(stem), {"level": quant_of(stem), "tag": tag_match.group(1)})
    return list(seen.values())


def fetch_catalog(limit: int = 40, timeout: float = 8.0) -> list[dict[str, Any]]:
    """Popular GGUF text models, with the sizes and quantisations they ship.

    Never raises: on any failure the caller keeps its hand-written list, exactly
    as before this module existed.
    """
    now = time.time()
    cached = _cache.get("data")
    if cached is not None and now - float(_cache["fetched_at"]) < _CACHE_TTL_SECONDS:
        return list(cached)

    params = urllib.parse.urlencode(
        {"filter": "gguf", "sort": "downloads", "direction": -1, "limit": max(1, min(limit, 100))}
    )
    try:
        rows = _get_json(f"{HF_MODELS_URL}?{params}", timeout)
    except Exception as exc:
        log.info("hugging face catalogue unavailable: %s", exc)
        return list(cached or [])

    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        repo_id = str(row.get("id") or "")
        if not repo_id or not _is_text_model(repo_id):
            continue
        params_b = infer_size_b(repo_id)
        if params_b < MIN_PARAMS_B:
            continue  # includes the 0 case: a repo whose name states no size
        out.append(
            {
                "repo": repo_id,
                "params_b": params_b,
                "downloads": int(row.get("downloads") or 0),
                "quants": [],
            }
        )
    _cache["fetched_at"] = now
    _cache["data"] = out
    return list(out)


def fits_this_machine(
    catalog: list[dict[str, Any]], vram_gb: float, timeout: float = 6.0, top: int = 6
) -> list[dict[str, Any]]:
    """The catalogue entries this card can run, with the pull command to use.

    The quantisation is chosen, not assumed: the best-quality build that still
    fits the card. Asking Hugging Face for a repo's files costs one request, so
    only the most promising ``top`` are inspected.
    """
    if vram_gb <= 0:
        return []
    picks: list[dict[str, Any]] = []
    for entry in sorted(catalog, key=lambda e: (-e["params_b"], -e["downloads"])):
        if len(picks) >= top:
            break
        params_b = int(entry["params_b"])
        # Cheapest common build first: if even that does not fit, nothing will.
        if vram_needed_gb(params_b, "Q4_K_M") > vram_gb:
            continue
        quants = entry.get("quants") or _quants_of(entry["repo"], timeout)
        entry["quants"] = quants
        affordable = [
            build for build in quants if vram_needed_gb(params_b, build["level"]) <= vram_gb
        ]
        if not affordable:
            continue  # the repo ships nothing this card can hold
        # Best quality among the ones that fit (penalty is negative, 0 = intact).
        best = max(affordable, key=lambda build: quality_penalty(build["level"]))
        picks.append(
            {
                "repo": entry["repo"],
                "params_b": params_b,
                "downloads": entry["downloads"],
                "quant": best["level"],
                "vram_gb": vram_needed_gb(params_b, best["level"]),
                "quality_penalty": quality_penalty(best["level"]),
                # Ollama pulls straight from Hugging Face; no file handling here.
                # The tag is the file's own suffix, not our normalised label.
                "pull": f"hf.co/{entry['repo']}:{best['tag']}",
            }
        )
    return picks
