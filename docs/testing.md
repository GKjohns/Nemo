# Running Tests

Use this command from the project root to create a virtual environment, install the package in editable mode, install `pytest`, and run the full test suite:

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -e . pytest && .venv/bin/python -m pytest -q
```

## Re-run tests after setup

If `.venv` is already created and dependencies are installed, run:

```bash
.venv/bin/python -m pytest -q
```

## Run a single test file

```bash
.venv/bin/python -m pytest -q tests/test_executor.py
```
