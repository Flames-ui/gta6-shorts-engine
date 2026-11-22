"""Test CLI entrypoint."""
import subprocess
import sys
from pathlib import Path


def test_cli_help():
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "main.py"), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "GTA 6 Shorts Generator" in result.stdout
    assert "--llm-provider" in result.stdout
    assert "--mode" in result.stdout
