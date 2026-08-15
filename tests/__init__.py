# Package marker. Six test modules import shared fixtures with
# `from tests.conftest import ...`, which only resolves when this directory is
# an importable package. Without it a bare `pytest` — what CI runs — dies at
# collection with `No module named 'tests'`, while `python -m pytest` passes
# because that form puts the working directory on sys.path.
