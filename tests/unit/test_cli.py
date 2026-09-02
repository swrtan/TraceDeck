import subprocess
import sys
import os
from pathlib import Path


def test_module_help_does_not_create_data_directory(tmp_path):
    data_dir = tmp_path / "data"
    environment = os.environ.copy()
    source_root = Path(__file__).resolve().parents[2] / "src"
    environment["PYTHONPATH"] = str(source_root)
    environment["TRACEDECK_DATA_DIR"] = str(data_dir)
    result = subprocess.run(
        [sys.executable, "-m", "tracedeck", "--help"],
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()
    assert not data_dir.exists()
