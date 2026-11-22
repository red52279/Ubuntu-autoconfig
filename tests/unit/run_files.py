"""Keep test workspaces in the repository's ignored runs directory."""
from pathlib import Path
import tempfile

RUNS = Path(__file__).resolve().parents[2] / 'runs' / 'tests'


def temporary_directory(prefix='test.'):
    RUNS.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=RUNS, prefix=prefix)
