#!/usr/bin/env python3
"""
Quick test runner for basic tests only (Level 0-2).
"""
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests.regression.regression_runner import RegressionTestRunner
import time

def run_basic_tests():
    """Run only Level 0-2 tests."""
    print("=" * 80)
    print("BASIC TESTS ONLY (Level 0-2)")
    print("=" * 80)
    
    runner = RegressionTestRunner(mode="direct")
    runner.load_test_cases()
    
    # Filter to only basic tests (priority 0-2)
    basic_tests = [tc for tc in runner.test_cases if tc.priority <= 2]
    
    print(f"\nFound {len(basic_tests)} basic tests:")
    for tc in basic_tests:
        skip_status = " [WILL SKIP]" if tc.skip else ""
        print(f"  - {tc.id}: {tc.name} (Priority {tc.priority}){skip_status}")
    
    print("\n" + "=" * 80)
    print("EXECUTING TESTS...")
    print("=" * 80 + "\n")
    
    results = []
    for i, tc in enumerate(basic_tests, 1):
        print(f"\n[{i}/{len(basic_tests)}] {tc.name} ({tc.id})")
        print(f"  Priority: {tc.priority}")
        print(f"  Namespace: {tc.namespace}")
        
        if tc.skip:
            print(f"  Status: SKIPPED - {tc.skip_reason}")
            continue
        
        # Execute test in direct mode only
        start = time.time()
        result = runner.execute_test_case(tc, mode="direct")
        duration = time.time() - start
        
        results.append(result)
        
        if result.skipped:
            print(f"  Status: SKIPPED - {result.skip_reason}")
        elif result.passed:
            print(f"  Status: [PASS] ({result.execution_time_ms:.0f}ms)")
        else:
            print(f"  Status: [FAIL] ({result.execution_time_ms:.0f}ms)")
            if result.error:
                print(f"  Error: {result.error[:200]}")
    
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    passed = sum(1 for r in results if r.passed and not r.skipped)
    failed = sum(1 for r in results if not r.passed and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    
    print(f"Total: {len(basic_tests)} tests")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Skipped: {skipped}")
    
    if failed + passed > 0:
        pass_rate = (passed / (failed + passed)) * 100
        print(f"Pass Rate: {pass_rate:.1f}%")
    
    print("=" * 80)
    
    return results

if __name__ == '__main__':
    run_basic_tests()

