import os
import json
import threading
from pathlib import Path
from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash, check_password_hash

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_FILE = DATA_DIR / "config.json"
SECRET_FILE = DATA_DIR / ".secret_key"

_lock = threading.Lock()
_cache = None

DEFAULT_CONFIG = {
    "password_hash": "",
    "gh_token_enc": "",
    "github_username": "",
    "gh_owner": "",
    "release_accel_url": "",
}


def _load_secret_key():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SECRET_FILE.exists():
        return SECRET_FILE.read_bytes()
    key = Fernet.generate_key()
    SECRET_FILE.write_bytes(key)
    try:
        os.chmod(SECRET_FILE, 0o600)
    except Exception:
        pass
    return key


_fernet = Fernet(_load_secret_key())


def init_store(admin_password="admin123"):
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_FILE.exists():
            cfg = dict(DEFAULT_CONFIG)
            cfg["password_hash"] = generate_password_hash(admin_password)
            CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
        else:
            try:
                cfg = json.loads(CONFIG_FILE.read_text())
            except Exception:
                cfg = {}
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def _read():
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CONFIG_FILE.read_text())
        except Exception:
            _cache = dict(DEFAULT_CONFIG)
    return _cache


def _write(cfg):
    global _cache
    _cache = cfg
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def get_config():
    with _lock:
        return dict(_read())


def verify_password(password):
    with _lock:
        h = _read().get("password_hash", "")
    if not h:
        return False
    return check_password_hash(h, password)


def set_password(new_password):
    with _lock:
        cfg = _read()
        cfg["password_hash"] = generate_password_hash(new_password)
        _write(cfg)


def set_github(token=None, username=None, owner=None):
    with _lock:
        cfg = _read()
        if token:
            cfg["gh_token_enc"] = _fernet.encrypt(token.encode()).decode()
        if username is not None:
            cfg["github_username"] = username
        if owner is not None:
            cfg["gh_owner"] = owner
        _write(cfg)


def set_accel_url(url):
    with _lock:
        cfg = _read()
        cfg["release_accel_url"] = (url or "").strip()
        _write(cfg)


def clear_token():
    with _lock:
        cfg = _read()
        cfg["gh_token_enc"] = ""
        _write(cfg)


def get_token():
    with _lock:
        enc = _read().get("gh_token_enc", "")
    if not enc:
        return ""
    try:
        return _fernet.decrypt(enc.encode()).decode()
    except Exception:
        return ""


def get_session_key():
    return _fernet._signing_key
