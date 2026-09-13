# AIQL Regression Test Cases

This directory contains approved and passed test cases for AIQL queries.

## Test Case Format

Each test case is a **YAML file** (preferred - more readable) or JSON file with the following structure:

**YAML Format (Preferred):**

```yaml
id: test_001
name: Create Node Test
description: Tests basic node creation
query: CREATE NODE TestNode { name: 'test', value: 42 }
namespace: regression_test
expected_success: true
expected_nodes: 1
expected_edges: null
tags:
  - basic
  - create
verify_persistence: true
test_both_modes: true
approved_by: username
notes: Basic node creation test
```

**JSON Format (Also Supported):**

```json
{
  "id": "test_001",
  "name": "Create Node Test",
  "description": "Tests basic node creation",
  "query": "CREATE NODE TestNode { name: 'test', value: 42 }",
  "namespace": "regression_test",
  "expected_success": true,
  "expected_nodes": 1,
  "verify_persistence": true,
  "test_both_modes": true
}
```

## Fields

- `id`: Unique identifier for the test case
- `name`: Human-readable name
- `description`: Description of what the test validates
- `query`: The AIQL query to execute
- `namespace`: Namespace to use for the test
- `expected_success`: Whether the query should succeed (default: true)
- `expected_nodes`: Expected number of nodes in result (optional)
- `expected_edges`: Expected number of edges in result (optional)
- `expected_data_keys`: Expected keys in result.data (optional)
- `tags`: List of tags for categorization
- `verify_persistence`: Whether to verify data persists (default: false)
- `test_both_modes`: Whether to test both API and CLI modes (default: true)
- `approved_by`: Who approved this test case
- `approved_date`: When it was approved (auto-set if not provided)
- `notes`: Additional notes

## Adding New Test Cases

1. Create a new **YAML file** in this directory (preferred - more readable)
2. Use a descriptive filename (e.g., `test_create_node_basic.yaml`)
3. Follow the format above
4. Set `verify_persistence: true` for write operations
5. Set `test_both_modes: true` to ensure consistency
6. Run the regression suite to verify it passes
7. Commit the test case file

## Running Tests

```bash
# Run all tests in both API and CLI modes (recommended)
python run_regression.py --test-both-modes

# Run all tests in direct mode only
python run_regression.py --mode direct

# Run all tests in API mode only (requires server)
python run_regression.py --mode api

# Specify custom test directory
python run_regression.py --test-dir custom/path --test-both-modes
```

## Test Organization

Organize test cases by feature or category:
- `basic_*.json` - Basic operations
- `create_*.json` - CREATE operations
- `select_*.json` - SELECT queries
- `traverse_*.json` - TRAVERSE queries
- `pipeline_*.json` - Pipeline operations
- etc.

