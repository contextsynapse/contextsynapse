# Contributing to ContextSynapse

Thank you for your interest in contributing to ContextSynapse!

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/contextsynapse.git`
3. Install in development mode: `pip install -e ".[dev]"`
4. Copy `.env.example` to `.env` and fill in your values
5. Run tests: `pytest tests/ -v`

## Development Setup

```bash
# Create a virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install with dev dependencies
pip install -e ".[dev]"

# Start the server
python -m uvicorn contextsynapse.api.api:app --reload --port 8000

# Run tests
pytest tests/ -v

# Lint
ruff check contextsynapse/
```

## Building a Vertical Plugin

ContextSynapse supports vertical plugins via Python entry points. See [Plugin Development Guide](docs/plugins.md) for details.

```python
from contextsynapse.plugins import VerticalPlugin, Sensor

class MyVertical(VerticalPlugin):
    name = "my_domain"
    version = "0.1.0"

    def sensors(self):
        return [MySensor()]
```

Register in your `pyproject.toml`:

```toml
[project.entry-points."contextsynapse.plugins"]
my_domain = "my_package.plugin:MyVertical"
```

## Code Standards

- Python 3.10+
- Use type hints
- Follow existing code patterns
- Write tests for new features
- No hardcoded secrets or API keys — use environment variables

## Pull Request Process

1. Create a feature branch from `main`
2. Make your changes
3. Add/update tests
4. Run the test suite
5. Submit a PR with a clear description

## Architecture

```
contextsynapse/
├── core/       # Graph storage engine
├── aiql/       # Query language (AIQL)
├── api/        # FastAPI REST API
├── mcp/        # MCP server for Claude/Copilot
├── context/    # LLM context building (ContextHub)
├── storage/    # Storage backends (CSR, Redis, LMDB)
├── pipelines/  # Data ingestion pipelines
├── plugins/    # Plugin system for verticals
├── vector/     # Vector DB integration
└── search/     # Graph-enhanced search
```

## License

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.
