"""登录失败限速：同一 IP 连续失败 N 次锁定 M 分钟"""
import os
import json
import threading
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
GUARD_FILE = DATA_DIR / "login_attempts.json"

MAX_FAILS = 5
LOCK_MINUTES = 10

_lock = threading.Lock()
_cache = None


def _load():
    global _cache
    if _cache is None:
        if GUARD_FILE.exists():
            try:
                _cache = json.loads(GUARD_FILE.read_text())
            except Exception:
                _cache = {}
        else:
            _cache = {}
    return _cache


def _save():
    try:
        GUARD_FILE.write_text(json.dumps(_cache, indent=2))
    except Exception:
        pass


def _now():
    return datetime.now()


def check(ip):
    """返回 (allowed, remaining_seconds, remaining_tries)"""
    with _lock:
        data = _load()
        rec = data.get(ip)
        if not rec:
            return True, 0, MAX_FAILS
        # 检查是否锁定期
        locked_until_str = rec.get("locked_until")
        if locked_until_str:
            try:
                locked_until = datetime.fromisoformat(locked_until_str)
                now = _now()
                if now < locked_until:
                    remain = int((locked_until - now).total_seconds())
                    return False, remain, 0
                # 锁定已过期，清空
                del data[ip]
                _save()
            except Exception:
                pass
        # 未锁定，检查剩余次数
        fails = rec.get("fails", 0)
        return True, 0, max(0, MAX_FAILS - fails)


def record_fail(ip):
    """记录一次失败，返回剩余次数（0 表示刚触发锁定）"""
    with _lock:
        data = _load()
        rec = data.get(ip) or {"fails": 0}
        rec["fails"] = rec.get("fails", 0) + 1
        rec["last"] = _now().isoformat(timespec="seconds")
        remain = MAX_FAILS - rec["fails"]
        if remain <= 0:
            rec["locked_until"] = (_now() + timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds")
            rec["fails"] = 0
            remain = 0
        data[ip] = rec
        _save()
        return remain


def record_success(ip):
    """登录成功后清空该 IP 记录"""
    with _lock:
        data = _load()
        if ip in data:
            del data[ip]
            _save()


def get_client_ip(req):
    """从 Flask request 里取客户端 IP，兼容反代"""
    xff = req.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    xri = req.headers.get("X-Real-IP", "")
    if xri:
        return xri.strip()
    return req.remote_addr or "unknown"
