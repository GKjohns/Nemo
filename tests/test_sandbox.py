from __future__ import annotations

from nemo.executor.sandbox import PythonSession


def test_basic_execution_captures_result() -> None:
    session = PythonSession()
    result = session.execute("_result = 1 + 1")
    assert result.error is None
    assert result.result == 2


def test_session_persists_dataframes() -> None:
    session = PythonSession()
    first = session.execute(
        "import pandas as pd\n"
        "df = pd.DataFrame({'a': [1, 2, 3], 'b': [4, 5, 6]})\n"
        "_result = int(df['a'].sum())"
    )
    assert first.error is None
    assert first.result == 6

    second = session.execute("_result = int(df['b'].sum())")
    assert second.error is None
    assert second.result == 15


def test_timeout_enforced() -> None:
    session = PythonSession(timeout_seconds=1)
    result = session.execute("while True:\n    pass")
    assert result.error is not None
    assert "timed out" in result.error.lower()


def test_import_restrictions_block_os() -> None:
    session = PythonSession()
    result = session.execute("import os\n_result = os.system('echo no')")
    assert result.error is not None
    assert "not allowed" in result.error.lower()


def test_allowed_imports_succeed() -> None:
    session = PythonSession()
    result = session.execute(
        "import pandas as pd\n"
        "import scipy.stats as stats\n"
        "df = pd.DataFrame({'x': [1, 2, 3]})\n"
        "_result = float(stats.ttest_1samp(df['x'], popmean=2).statistic)"
    )
    assert result.error is None
    assert isinstance(result.result, float)


def test_output_capture() -> None:
    session = PythonSession()
    result = session.execute("print('hello')\n_result = 1")
    assert result.error is None
    assert "hello" in result.stdout


def test_error_handling_returns_clean_error() -> None:
    session = PythonSession()
    syntax = session.execute("if True print('nope')")
    assert syntax.error is not None
    assert "syntaxerror" in syntax.error.lower()

    runtime = session.execute("_result = 1 / 0")
    assert runtime.error is not None
    assert "zerodivisionerror" in runtime.error.lower()


def test_memory_estimation_reports_dataframe_usage() -> None:
    session = PythonSession()
    result = session.execute(
        "import pandas as pd\n"
        "df = pd.DataFrame({'a': list(range(1000)), 'b': list(range(1000))})\n"
        "_result = df.shape[0]"
    )
    assert result.error is None
    assert result.memory_mb > 0
