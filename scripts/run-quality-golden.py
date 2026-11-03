"""Run the knowledge-answer golden set and gate the deterministic run on its baseline."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.litellm import (
    LiteLLMModelGateway,
    LiteLLMModelGatewayConfig,
)
from tap.modules.ai.adapters.litellm_catalog import LiteLLMCatalog, ModelRoles
from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
from tap.quality.golden import (
    compare,
    deterministic_golden_gateway,
    format_table,
    load_cases,
    run_golden,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "apps" / "tap-ai-backend" / "tests" / "quality" / "golden"
REAL_TIMEOUT_SECONDS = 60.0


def _real_settings(environ: Mapping[str, str]) -> dict[str, str]:
    missing = [
        name
        for name in ("LITELLM_BASE_URL", "LITELLM_MASTER_KEY")
        if not environ.get(name)
    ]
    if missing:
        raise SystemExit(f"real golden run requires {', '.join(missing)}")
    return {
        "base_url": environ["LITELLM_BASE_URL"],
        "api_key": environ["LITELLM_MASTER_KEY"],
        "chat": environ.get("TAPPER_DEFAULT_CHAT_MODEL") or "qwen-plus",
        "embedding": environ.get("TAPPER_EMBEDDING_MODEL") or "text-embedding-v4",
        "dimension": environ.get("TAPPER_EMBEDDING_DIMENSION") or "1536",
    }


async def _run(args: argparse.Namespace) -> int:
    cases = load_cases(args.cases)
    real = args.real or os.environ.get("TAP_GOLDEN_REAL_MODEL") == "1"
    if real:
        settings = _real_settings(os.environ)
        config = LiteLLMModelGatewayConfig(
            base_url=settings["base_url"],
            api_key=settings["api_key"],
            roles=ModelRoles(settings["chat"], settings["embedding"], None),
            embedding_dimension=int(settings["dimension"]),
            timeout_seconds=REAL_TIMEOUT_SECONDS,
        )
        gateway: LiteLLMModelGateway = LiteLLMModelGateway(
            config,
            scope=VALIDATION_SCOPE,
            redact=_redact,
            catalog=LiteLLMCatalog(
                base_url=settings["base_url"], api_key=settings["api_key"]
            ),
        )
        run = run_golden(
            cases,
            corpus_dir=args.corpus,
            gateway=gateway,
            chat_alias=settings["chat"],
            embedding_alias=settings["embedding"],
            embedding_dimension=config.embedding_dimension,
            timeout_seconds=REAL_TIMEOUT_SECONDS,
        )
    else:
        gateway = deterministic_golden_gateway()
        run = run_golden(cases, corpus_dir=args.corpus, gateway=gateway)
    try:
        report = await run
    finally:
        await gateway.aclose()

    args.report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = args.report_dir / f"golden-{stamp}.json"
    payload = {"model": "real" if real else "deterministic", **report.to_json()}
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    print(format_table(report))
    print(f"report: {report_path}")

    if args.write_baseline is not None:
        args.write_baseline.write_text(
            json.dumps(report.metrics, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            "utf-8",
        )
        print(f"baseline written: {args.write_baseline}")
        return 0
    if real:
        return 0
    regressions = compare(report, json.loads(args.baseline.read_text(encoding="utf-8")))
    for line in regressions:
        print(f"regression: {line}", file=sys.stderr)
    return 1 if regressions else 0


async def _redact(text: str) -> str:
    return await PatternEgressRedactor(max_chars=262144).redact_text(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real", action="store_true", help="use the configured LiteLLM model"
    )
    parser.add_argument("--write-baseline", type=Path, metavar="PATH")
    parser.add_argument(
        "--report-dir", type=Path, default=ROOT / "apps" / "tap-ai-backend" / ".quality"
    )
    parser.add_argument("--cases", type=Path, default=GOLDEN / "kb-golden.v1.jsonl")
    parser.add_argument("--corpus", type=Path, default=GOLDEN / "corpus")
    parser.add_argument("--baseline", type=Path, default=GOLDEN / "baseline.fake.json")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
