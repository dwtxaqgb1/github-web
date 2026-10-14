"""Fork 仓库自动同步：配置存储 + 后台调度"""
import os
import json
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_FILE = DATA_DIR / "fork_sync.json"

_lock = threading.Lock()
_cache = None
_scheduler_started = False

# 支持的时间间隔（小时）
INTERVALS = {
    "1h": 1,
    "6h": 6,
    "12h": 12,
    "1d": 24,
    "3d": 72,
    "1w": 168,
}


def _load():
    global _cache
    if _cache is None:
        if CONFIG_FILE.exists():
            try:
                _cache = json.loads(CONFIG_FILE.read_text())
            except Exception:
                _cache = {}
        else:
            _cache = {}
    return _cache


def _save():
    try:
        CONFIG_FILE.write_text(json.dumps(_cache, indent=2, ensure_ascii=False))
    except Exception:
        pass


def get_all():
    with _lock:
        return dict(_load())


def get(key):
    with _lock:
        return dict(_load().get(key, {}))


def upsert(key, cfg):
    with _lock:
        data = _load()
        existing = data.get(key, {})
        existing.update(cfg or {})
        data[key] = existing
        _save()
        return dict(existing)


def remove(key):
    with _lock:
        data = _load()
        if key in data:
            del data[key]
            _save()


def mark_synced(key, ok, message=""):
    with _lock:
        data = _load()
        item = data.setdefault(key, {})
        item["last_sync"] = datetime.now().isoformat(timespec="seconds")
        item["last_result"] = "ok" if ok else "err"
        item["last_message"] = (message or "")[:500]
        _save()


def _due(cfg, now):
    if not cfg.get("enabled"):
        return False
    try:
        interval_h = float(cfg.get("interval_hours", 24))
    except Exception:
        interval_h = 24
    last = cfg.get("last_sync")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return True
    return (now - last_dt) >= timedelta(hours=interval_h)


def start_scheduler(sync_fn, token_getter, log_fn=None):
    """sync_fn(owner, repo, token) -> (ok, message)"""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def loop():
        # 启动后先等 30 秒再开始首次扫描，让服务先就绪
        time.sleep(30)
        while True:
            try:
                _tick(sync_fn, token_getter, log_fn)
            except Exception as e:
                if log_fn:
                    log_fn(f"fork_sync scheduler error: {e}")
            time.sleep(60)  # 每分钟检查一次

    threading.Thread(target=loop, daemon=True).start()


def _tick(sync_fn, token_getter, log_fn):
    data = get_all()
    if not data:
        return
    token = token_getter()
    if not token:
        return
    now = datetime.now()
    for key, cfg in list(data.items()):
        if not _due(cfg, now):
            continue
        parts = key.split("/", 1)
        if len(parts) != 2:
            continue
        owner, repo = parts
        try:
            ok, msg = sync_fn(owner, repo, token)
            mark_synced(key, ok, msg)
            if log_fn:
                log_fn(f"auto sync {key}: {'ok' if ok else 'err'} - {str(msg)[:100]}")
        except Exception as e:
            mark_synced(key, False, str(e))
            if log_fn:
                log_fn(f"auto sync {key} exception: {e}")
