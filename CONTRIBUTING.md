# Contributing to MoA Gateway Pro

Thank you for your interest in contributing! This document describes our
engineering standards, development workflow, and how to submit changes.

## Engineering standards

We follow strict software engineering practices:

1. **Test-driven development** — every new feature ships with unit tests (≥20 cases per module)
2. **End-to-end coverage** — every server endpoint exercised by `test_full_e2e.py` AND `test_deep_e2e.py`
3. **Type hints** — all public APIs have full type annotations
4. **Docstrings** — module-level + public function/class docstrings
5. **No stubs** — production code never ships placeholder/mock logic
6. **Lint clean** — `ruff check` passes
7. **Type checked** — `mypy` runs (non-blocking during gradual migration)
8. **Tracked security audit** — CI runs credential scanner on every PR

## Development setup

```bash
# Clone
git clone https://github.com/Nurburgring-Zhang/moa-gateway-pro.git
cd moa-gateway-pro

# Virtual env
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/macOS

# Install with dev dependencies
pip install -e ".[dev]"

# Pre-commit hooks
pre-commit install
```

## Running tests

```bash
# Unit tests (fast)
pytest moa_gateway/capability/tests/ -q

# Unit tests with coverage
pytest moa_gateway/capability/tests/ --cov=moa_gateway --cov-report=term-missing

# E2E (basic, ~30s)
python scripts/test_full_e2e.py

# E2E (deep, ~5min, 76 endpoints)
python scripts/test_deep_e2e.py

# Security regression
python scripts/test_security_regression.py
```

## Code style

- Python 3.10+
- `ruff` for linting and formatting (configured in `pyproject.toml`)
- `mypy` for type checking (configured in `pyproject.toml`)
- Line length 100
- Type hints on all public APIs
- Docstrings on all modules and public symbols

## Submitting changes

1. Fork the repo
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Make your changes with tests
4. Run the full test suite locally:
   ```bash
   pytest moa_gateway/capability/tests/ -q
   python scripts/test_full_e2e.py
   python scripts/test_deep_e2e.py
   ```
5. Run lint: `ruff check --fix moa_gateway/`
6. Commit with conventional commit format: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`
7. Push and open a Pull Request

## Commit message format

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types:
- `feat` — new feature
- `fix` — bug fix
- `docs` — documentation
- `test` — tests
- `refactor` — code refactor (no behavior change)
- `perf` — performance improvement
- `chore` — tooling, deps, build

## Reporting bugs

Use GitHub Issues. Include:
- MoA Gateway Pro version
- Python version
- OS
- Steps to reproduce
- Expected vs actual behavior
- Server logs (with secrets redacted)

## Security issues

**Do not** file public issues for security vulnerabilities. Email
`Mavis@moa.dev` privately with details.

## License

By contributing, you agree that your contributions will be licensed under
the project's Apache 2.0 license.
