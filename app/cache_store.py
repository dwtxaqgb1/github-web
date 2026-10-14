"""服务端缓存：列表数据写到 /data/cache/ 下，所有客户端共享"""
import os
import json
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

TTL_SECONDS = 24 * 3600  # 24 小时过期


def _path(key):
    safe = "".join(c for c in key if c.isalnum() or c in "-_")
    return CACHE_DIR / f"{safe}.json"


def get(key):
    p = _path(key)
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text())
    except Exception:
        return None
    if not obj or "ts" not in obj or "data" not in obj:
        return None
    if time.time() - obj["ts"] > TTL_SECONDS:
        return None
    return obj["data"]


def get_meta(key):
    p = _path(key)
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text())
    except Exception:
        return None
    return {"ts": obj.get("ts"), "count": len(obj.get("data") or [])}


def set(key, data):
    p = _path(key)
    obj = {"ts": time.time(), "data": data}
    try:
        p.write_text(json.dumps(obj, ensure_ascii=False))
        return True
    except Exception:
        return False


def clear(key):
    p = _path(key)
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass
