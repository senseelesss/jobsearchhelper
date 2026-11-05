import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def run_isolated(code, tmp_path):
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
        "TELEGRAM_BOT_TOKEN": "synthetic-token",
        "TELEGRAM_CHAT_ID": "42",
    }
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


BLOCK_AI = """
import importlib.abc
import sys
class BlockAI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'openai', 'pydantic', 'pydantic_core'}:
            raise AssertionError('Unnecessary AI import: ' + fullname)
sys.meta_path.insert(0, BlockAI())
"""


def test_telegram_check_does_not_import_ai_or_make_unexpected_requests(tmp_path):
    code = (
        BLOCK_AI
        + """
import json
import urllib.request
class Response:
    def __init__(self, result): self.result = result
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps({'ok': True, 'result': self.result}).encode()
calls = []
def request(req, timeout):
    method = req.full_url.rsplit('/', 1)[-1]
    assert method in {'getMe', 'getChat'}
    calls.append(method)
    return Response({'is_bot': True} if method == 'getMe' else {'id': 42})
urllib.request.urlopen = request
from job_radar.cli import main
sys.argv = ['job-radar', 'telegram-check']
main()
assert calls == ['getMe', 'getChat']
"""
    )
    result = run_isolated(code, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "verified" in result.stdout
    assert "synthetic-token" not in result.stdout + result.stderr


def test_daily_import_does_not_load_ai_sdk_or_schema_models(tmp_path):
    result = run_isolated(BLOCK_AI + "\nimport job_radar.daily\nprint('ready')\n", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ready"


def test_launcher_works_from_another_directory_without_activating_venv(tmp_path):
    env = {
        **os.environ,
        "JOB_RADAR_VENV": str(Path(sys.executable).parent.parent),
        "JOB_RADAR_CACHE_DIR": str(tmp_path / "cache"),
    }
    result = subprocess.run(
        [str(ROOT / "bin/job-radar"), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "telegram-check" in result.stdout
