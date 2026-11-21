"""Test CLI entrypoint."""
import subprocess
import sys


def test_cli_help():
    result = subprocess.run(
        [sys.executable, "main.py", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "GTA 6 Shorts Generator" in result.stdout
    assert "--llm-provider" in result.stdout
    assert "--mode" in result.stdout
