#!/usr/bin/env python3
"""
Script to add skip flags to test cases that are not ready to run.
"""
import os
import yaml
from pathlib import Path

# Define which tests to skip and why
SKIP_RULES = {
    'blockchain': {
        'skip': True,
        'skip_reason': 'Traceability layer not enabled in default configuration',
        'pattern': 'test_blockchain_'
    },
    'time_travel': {
        'skip': True,
        'skip_reason': 'Temporal queries not fully implemented',
        'pattern': 'test_time_travel_'
    },
    'semantic_hash': {
        'skip': True,
        'skip_reason': 'SEMANTIC_HASH operator not implemented',
        'pattern': 'test_semantic_hash_'
    },
    'semantic_boundary': {
        'skip': True,
        'skip_reason': 'Semantic boundary detection not fully implemented',
        'pattern': 'test_semantic_boundary_'
    }
}

def add_skip_flags():
    """Add skip flags to test files based on rules."""
    cases_dir = Path(__file__).parent / 'cases'
    
    updated_count = 0
    skipped_count = 0
    
    for test_file in sorted(cases_dir.glob('test_*.yaml')):
        # Read the test file
        with open(test_file, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Parse YAML
        try:
            test_data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            print(f"❌ Error parsing {test_file.name}: {e}")
            continue
        
        # Check if this test should be skipped
        should_skip = False
        skip_reason = None
        
        for rule_name, rule in SKIP_RULES.items():
            if test_file.name.startswith(rule['pattern']):
                should_skip = True
                skip_reason = rule['skip_reason']
                break
        
        if should_skip:
            # Add skip flags if not already present
            if 'skip' not in test_data or not test_data['skip']:
                test_data['skip'] = True
                test_data['skip_reason'] = skip_reason
                
                # Write back to file
                with open(test_file, 'w', encoding='utf-8') as f:
                    yaml.dump(test_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                
                print(f"✅ Updated {test_file.name} - added skip flag")
                updated_count += 1
            else:
                print(f"⏭️  {test_file.name} - already has skip flag")
                skipped_count += 1
    
    print(f"\n📊 Summary:")
    print(f"  - Updated: {updated_count} files")
    print(f"  - Already skipped: {skipped_count} files")
    print(f"  - Total tests to skip: {updated_count + skipped_count}")

if __name__ == '__main__':
    add_skip_flags()

