"""
Cost Savings Benchmark
=======================
Proves the value of shared context by measuring token usage
with and without AIContextDB.

Usage:
    python -m contextsynapse.benchmark
    python -m contextsynapse.benchmark --model gpt-4o-mini --verbose
"""

from .runner import BenchmarkRunner
from .reporter import BenchmarkReporter

__all__ = ["BenchmarkRunner", "BenchmarkReporter"]
