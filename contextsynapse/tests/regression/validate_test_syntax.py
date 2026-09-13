#!/usr/bin/env python3
"""
AIQL Test Syntax Validator

Validates all regression test queries against the current AIQL grammar.
Run this BEFORE executing tests to catch syntax errors early.

Purpose: Prevent grammar drift and ensure test consistency.
"""
import sys
from pathlib import Path
import yaml
from typing import Dict, List, Tuple

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from contextsynapse.aiql.aiql_parser import AIQLParser


class TestSyntaxValidator:
    """Validates test queries against AIQL grammar."""
    
    def __init__(self, test_dir: str = "tests/regression/cases"):
        self.test_dir = Path(test_dir)
        self.parser = AIQLParser()
        self.results = {
            'valid': [],
            'invalid': [],
            'skipped': []
        }
    
    def validate_query(self, query: str, test_id: str) -> Tuple[bool, str]:
        """
        Validate a single query.
        
        Returns:
            (is_valid, error_message)
        """
        try:
            # Try to parse the query
            self.parser.parse(query)
            return True, ""
        except Exception as e:
            error_msg = str(e)
            # Extract just the relevant error info
            if "No terminal matches" in error_msg:
                lines = error_msg.split('\n')
                error_msg = lines[0] if lines else error_msg
            return False, error_msg
    
    def validate_all_tests(self) -> Dict:
        """Validate all test files."""
        print("=" * 80)
        print("AIQL TEST SYNTAX VALIDATOR")
        print("=" * 80)
        print(f"Validating tests in: {self.test_dir}")
        print()
        
        test_files = sorted(self.test_dir.glob("test_*.yaml"))
        
        for test_file in test_files:
            try:
                with open(test_file, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                
                test_id = data.get('id', test_file.stem)
                test_name = data.get('name', 'Unknown')
                query = data.get('query', '')
                skip = data.get('skip', False)
                
                # Check if test is intentionally skipped
                if skip:
                    self.results['skipped'].append({
                        'id': test_id,
                        'name': test_name,
                        'file': test_file.name,
                        'reason': data.get('skip_reason', 'Not specified')
                    })
                    continue
                
                # Validate query syntax
                is_valid, error = self.validate_query(query, test_id)
                
                if is_valid:
                    self.results['valid'].append({
                        'id': test_id,
                        'name': test_name,
                        'file': test_file.name
                    })
                    print(f"[VALID] {test_file.name}: {test_name}")
                else:
                    self.results['invalid'].append({
                        'id': test_id,
                        'name': test_name,
                        'file': test_file.name,
                        'query': query[:100] + '...' if len(query) > 100 else query,
                        'error': error
                    })
                    print(f"[INVALID] {test_file.name}: {test_name}")
                    print(f"   Error: {error[:150]}")
                    print()
            
            except Exception as e:
                print(f"[WARNING] {test_file.name}: Error loading file - {e}")
        
        return self.results
    
    def print_summary(self):
        """Print validation summary."""
        print("\n" + "=" * 80)
        print("VALIDATION SUMMARY")
        print("=" * 80)
        
        total = len(self.results['valid']) + len(self.results['invalid']) + len(self.results['skipped'])
        valid = len(self.results['valid'])
        invalid = len(self.results['invalid'])
        skipped = len(self.results['skipped'])
        
        print(f"Total Tests: {total}")
        print(f"[VALID] Valid Syntax: {valid}")
        print(f"[INVALID] Invalid Syntax: {invalid}")
        print(f"[SKIP] Skipped (Intentional): {skipped}")
        
        if invalid > 0:
            print(f"\n[WARNING] {invalid} tests have SYNTAX ERRORS and need fixing!")
            print("\nInvalid Tests:")
            for test in self.results['invalid']:
                print(f"  - {test['file']}: {test['name']}")
                print(f"    ID: {test['id']}")
                print(f"    Error: {test['error'][:100]}")
                print()
        else:
            print("\n[SUCCESS] All runnable tests have valid syntax!")
        
        if valid > 0:
            runnable_pass_rate = (valid / (valid + invalid)) * 100 if (valid + invalid) > 0 else 0
            print(f"\n[STATS] Syntax Validity Rate: {runnable_pass_rate:.1f}% ({valid}/{valid + invalid} runnable tests)")
        
        print("=" * 80)
        
        return invalid == 0
    
    def save_report(self, output_file: str = "syntax_validation_report.json"):
        """Save validation report to JSON."""
        import json
        from datetime import datetime
        
        report = {
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'total': len(self.results['valid']) + len(self.results['invalid']) + len(self.results['skipped']),
                'valid': len(self.results['valid']),
                'invalid': len(self.results['invalid']),
                'skipped': len(self.results['skipped'])
            },
            'results': self.results
        }
        
        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"\n[REPORT] Report saved to: {output_file}")


def main():
    """Main entry point."""
    validator = TestSyntaxValidator()
    validator.validate_all_tests()
    all_valid = validator.print_summary()
    validator.save_report()
    
    # Exit with error code if any tests have syntax errors
    sys.exit(0 if all_valid else 1)


if __name__ == '__main__':
    main()

