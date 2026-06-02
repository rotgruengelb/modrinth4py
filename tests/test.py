from pathlib import Path

from modrinth4py.core import greet

ASSETS = Path(__file__).parent / "assets"
OUTPUT_DIR = Path(__file__).parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def test_greeting(tmp_path):
    assert greet("Tester", False) == "Hello Tester"


def test_greeting_exited(tmp_path):
    assert greet("Tester", True) == "Hello Tester!"
