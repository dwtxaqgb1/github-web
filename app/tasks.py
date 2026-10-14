import os
import json
import threading
import uuid
import traceback
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
LOG_DIR = DATA_DIR / "logs"
TASKS_FILE = DATA_DIR / "tasks.json"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()
_global_lock = threading.Lock()


def _load():
    if not TASKS_FILE.exists():
        return {}
    try:
        data = json.loads(TASKS_FILE.read_text())
        for t in data.values():
            t.setdefault("extras", {})
            t.setdefault("step", "")
            if t.get("status") in ("running", "pending"):
                t["status"] = "interrupted"
                t["result"] = "服务重启，任务被中断"
                t["ended"] = datetime.now().isoformat(timespec="seconds")
        return data
    except Exception:
        return {}


_tasks = _load()


def _save():
    try:
        TASKS_FILE.write_text(json.dumps(_tasks, indent=2, ensure_ascii=False))
    except Exception:
        pass


def create_task(name):
    tid = uuid.uuid4().hex[:12]
    with _lock:
        _tasks[tid] = {
            "id": tid,
            "name": name,
            "status": "pending",
            "created": datetime.now().isoformat(timespec="seconds"),
            "started": None,
            "ended": None,
            "result": "",
            "extras": {},
            "step": "",
            "log_file": str(LOG_DIR / f"{tid}.log"),
        }
        _save()
    return tid


def get_task(tid):
    with _lock:
        t = _tasks.get(tid)
        return dict(t) if t else None


def list_tasks():
    with _lock:
        return sorted(_tasks.values(), key=lambda x: x["created"], reverse=True)


def delete_task(tid):
    with _lock:
        _tasks.pop(tid, None)
        _save()


def _update(tid, **kw):
    with _lock:
        if tid in _tasks:
            _tasks[tid].update(kw)
            _save()


def set_extras(tid, extras):
    with _lock:
        if tid in _tasks:
            _tasks[tid]["extras"] = extras or {}
            _save()



def set_step(tid, step):
    with _lock:
        if tid in _tasks:
            _tasks[tid]["step"] = step or ""
            _save()


def append_log(tid, msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    with _lock:
        t = _tasks.get(tid)
        if not t:
            return
        lf = t["log_file"]
    try:
        with open(lf, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_log(tid, tail=400):
    with _lock:
        t = _tasks.get(tid)
        if not t:
            return ""
        lf = t["log_file"]
    if not os.path.exists(lf):
        return ""
    with open(lf, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    return "".join(lines[-tail:])


def start_task(name, fn, task_id=None):
    tid = task_id or create_task(name)

    def runner():
        with _global_lock:
            _update(tid, status="running",
                    started=datetime.now().isoformat(timespec="seconds"))
            append_log(tid, f"任务开始: {name}")
            try:
                fn(log=lambda m: append_log(tid, m),
                   set_extras=lambda d: set_extras(tid, d),
                   set_step=lambda s: set_step(tid, s))
                _update(tid, status="success", result="OK")
                append_log(tid, "✓ 任务成功")
            except Exception as e:
                _update(tid, status="failed", result=str(e))
                append_log(tid, f"✗ 任务失败: {e}")
                append_log(tid, traceback.format_exc())
            finally:
                _update(tid, ended=datetime.now().isoformat(timespec="seconds"))

    threading.Thread(target=runner, daemon=True).start()
    return tid
