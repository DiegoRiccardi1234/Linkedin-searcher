"""Scan pipeline internals, split out of the single 2100-line module.

Import order mirrors the pipeline: vocabulary -> scraping adapters -> row
cleaning -> prompts -> heuristic scorer -> deterministic checks. The public
entry points stay on ``app.services.scanner_service``, which re-exports what
callers and tests already import.
"""
