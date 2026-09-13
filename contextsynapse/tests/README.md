# AIQL Test Coverage Suite

This folder contains comprehensive tests for AIQL queries to verify:
- **Coverage**: All query types are tested
- **Efficiency**: Execution time and memory usage
- **Data Storage**: Data is properly stored after queries
- **Data Retrieval**: Queries return correct results
- **API vs Direct**: Both API and direct execution (like Neo4j bolt) methods

## Files

- `comprehensive_aiql_test_suite.py`: Main test suite implementation
- `run_tests.py`: Simple test runner
- `coverage_report.json`: Generated test coverage report (created after running tests)

## Usage

### Run Tests

```bash
# Run all tests
python test_aiql_coverage/run_tests.py

# Or directly
python -m test_aiql_coverage.comprehensive_aiql_test_suite
```

### Test Methods

The suite tests queries via three methods:

1. **Direct Execution** (always available)
   - Like Neo4j Bolt protocol
   - Direct database access
   - No HTTP overhead

2. **API Endpoint** (`/aiql`)
   - HTTP-based execution
   - Requires API server running on port 8000

3. **Tinker Endpoint** (`/tinker`)
   - Direct DB access via HTTP
   - Requires Tinker server running on port 8001

### Test Namespace

All tests use namespace: `test_aiql_coverage`

### Output

After running tests, a `coverage_report.json` file is generated with:
- Test results for each query
- Success/failure rates
- Execution times
- Memory usage
- Data operations (nodes/edges created/retrieved)
- Statistics by method

## Cleanup

After testing is complete, this entire folder can be removed as it contains only temporary test files.
































