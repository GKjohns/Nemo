# Running Tests

Use this command from the project root to create a virtual environment, install the package in editable mode, install `pytest`, and run the default fast suite (slow tests are excluded by default):

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -e . pytest && .venv/bin/python -m pytest -q
```

## Re-run tests after setup

If `.venv` is already created and dependencies are installed, run the default fast suite:

```bash
.venv/bin/python -m pytest -q
```

## Run slow / E2E tests explicitly

Slow tests are marked and skipped by default. Use one of these commands when you want full validation:

```bash
# Only slow tests (golden/e2e)
.venv/bin/python -m pytest -m slow -o addopts=""

# Entire suite including slow tests
.venv/bin/python -m pytest -o addopts=""
```

## Run a single test file

```bash
.venv/bin/python -m pytest -q tests/test_executor.py
```
