## What

<!-- One sentence: what does this PR do? -->

## Why

<!-- What problem does it solve? Link to issue if applicable. -->

## How

<!-- Brief description of the approach. -->

## Checklist

- [ ] Tests added/updated for new functionality
- [ ] `pytest tests/unit/ -x` passes locally
- [ ] No PII, secrets, or credentials in the diff
- [ ] No vertical-specific code added to `contextsynapse/` (use `plugins/` or `verticals/`)
- [ ] Plugin imports in core are wrapped in `try/except ImportError`
- [ ] Updated docs/README if adding new features or config

## Type

- [ ] Bug fix
- [ ] New feature
- [ ] Performance improvement
- [ ] Refactor (no behavior change)
- [ ] Documentation
- [ ] Tests
