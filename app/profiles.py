import os
import json
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
PROFILE_FILE = DATA_DIR / "repo_profiles.json"

_lock = threading.Lock()
_cache = None


def _load():
    global _cache
    if _cache is None:
        if PROFILE_FILE.exists():
            try:
                _cache = json.loads(PROFILE_FILE.read_text())
            except Exception:
                _cache = {}
        else:
            _cache = {}
    return _cache


def _write(data):
    global _cache
    _cache = data
    try:
        PROFILE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception:
        pass


def get_all():
    with _lock:
        return dict(_load())


def get(repo_name):
    with _lock:
        return dict(_load().get(repo_name, {}))


def upsert(repo_name, cfg):
    with _lock:
        data = _load()
        existing = data.get(repo_name, {})
        existing.update(cfg or {})
        data[repo_name] = existing
        _write(data)
        return dict(existing)


def delete(repo_name):
    with _lock:
        data = _load()
        if repo_name in data:
            del data[repo_name]
            _write(data)
