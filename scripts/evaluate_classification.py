"""Run the fixed offline fake-provider evaluation. No credentials required."""

import argparse
import json
from pathlib import Path

from app.classification.evaluation import evaluate, load_fixtures


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split", choices=["development", "held_out", "all"], default="development"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        corpus, scripts = load_fixtures(
            ROOT / "tests/fixtures/classification/corpus.v1.json",
            ROOT / "tests/fixtures/classification/fake-scripts.v1.json",
        )
        report = evaluate(corpus, scripts, split=args.split)
        serialized = (
            json.dumps(
                report, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False
            )
            + "\n"
        )
        if args.output:
            # Exclusive creation avoids replacing a previous evidence artifact.
            with args.output.open("x", encoding="utf-8", newline="\n") as target:
                target.write(serialized)
        else:
            print(serialized, end="")
    except (ValueError, OSError):
        parser.exit(
            2, "Evaluation failed: invalid fixtures or unavailable output path.\n"
        )


if __name__ == "__main__":
    main()
