"""
Run benchmark from command line:
    python -m contextsynapse.benchmark
    python -m contextsynapse.benchmark --model gpt-4o-mini --verbose
    python -m contextsynapse.benchmark --model gpt-4o --output results.json
"""

import argparse
import json
import sys

from .runner import BenchmarkRunner
from .reporter import BenchmarkReporter


def main():
    parser = argparse.ArgumentParser(
        description="AIContextDB Cost Savings Benchmark",
        epilog="Measures token savings from shared context vs independent agents.",
    )
    parser.add_argument("--model", default="gpt-4o-mini", help="LLM model to test (default: gpt-4o-mini)")
    parser.add_argument("--provider", default="", help="LLM provider (auto-detected if empty)")
    parser.add_argument("--workload", action="append", help="Specific workload(s) to run (default: all)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show progress")
    parser.add_argument("--output", "-o", help="Save JSON results to file")
    parser.add_argument("--markdown", action="store_true", help="Output markdown instead of text")

    args = parser.parse_args()

    runner = BenchmarkRunner(
        model=args.model,
        provider=args.provider,
        workloads=args.workload,
        verbose=args.verbose,
    )

    print(f"Running AIContextDB Cost Savings Benchmark (model: {args.model})...")
    print()

    try:
        results = runner.run()
    except Exception as e:
        print(f"Benchmark failed: {e}", file=sys.stderr)
        sys.exit(1)

    if "error" in results:
        print(f"Error: {results['error']}", file=sys.stderr)
        sys.exit(1)

    # Output
    if args.markdown:
        print(BenchmarkReporter.generate_markdown(results))
    else:
        print(BenchmarkReporter.generate_text_report(results))

    # Save JSON if requested
    if args.output:
        with open(args.output, "w") as f:
            f.write(BenchmarkReporter.generate_json_report(results))
        print(f"\nJSON results saved to: {args.output}")


if __name__ == "__main__":
    main()
