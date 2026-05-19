"""CLI entry point for benchr."""

import argparse
import sys
from pathlib import Path

from benchr.results import (
    execution_result_from_json,
    build_summary_data,
    group_execution_result,
    extract_unique_names,
)
from benchr.output import DefaultSummaryFormatter, compare_and_print


def cli() -> None:
    parser = argparse.ArgumentParser(description="benchr - benchmark comparison tool")
    sub = parser.add_subparsers(dest="command")

    compare_parser = sub.add_parser(
        "compare",
        help="Compare benchmark JSON result files; first file is the baseline",
    )
    compare_parser.add_argument(
        "files", nargs="+", type=str, help="JSON result files to compare"
    )
    compare_parser.add_argument(
        "--metric",
        type=str,
        default=None,
        help="Comma-separated list of metric names to display (e.g. runtime,throughput)",
    )

    args = parser.parse_args()

    if args.command == "compare":
        metrics = set(args.metric.split(",")) if args.metric else None
        files = [Path(f) for f in args.files]
        for f in files:
            if not f.exists():
                print(f"Error: file not found: {f}", file=sys.stderr)
                sys.exit(1)

        names = extract_unique_names(files)
        results = [execution_result_from_json(f.read_text()) for f in files]

        if len(results) == 1:
            data = build_summary_data(results[0], [])
            out = DefaultSummaryFormatter(metrics=metrics).format(data)
            if out:
                print(out)
        else:
            grouped = [group_execution_result(r, n) for r, n in zip(results, names)]
            compare_and_print(grouped, metrics=metrics)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    cli()
