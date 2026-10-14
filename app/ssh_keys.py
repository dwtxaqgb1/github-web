import os
import subprocess
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
SSH_DIR = DATA_DIR / "ssh"
KEY_FILE = SSH_DIR / "id_ed25519"
PUB_FILE = SSH_DIR / "id_ed25519.pub"
KNOWN_HOSTS = SSH_DIR / "known_hosts"


def ensure_dir():
    SSH_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(SSH_DIR, 0o700)
    except Exception:
        pass


def has_key():
    return KEY_FILE.exists() and PUB_FILE.exists()


def generate():
    ensure_dir()
    if has_key():
        return False
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "nas-gh-web",
         "-f", str(KEY_FILE)],
        check=True, capture_output=True,
    )
    os.chmod(KEY_FILE, 0o600)
    return True


def public_key():
    if not PUB_FILE.exists():
        return ""
    return PUB_FILE.read_text().strip()


def git_ssh_command():
    if not has_key():
        return None
    return (
        f"ssh -i {KEY_FILE} "
        f"-o IdentitiesOnly=yes "
        f"-o StrictHostKeyChecking=accept-new "
        f"-o UserKnownHostsFile={KNOWN_HOSTS} "
        f"-p 443"
    )


def ssh_url(owner, repo):
    return f"ssh://git@ssh.github.com:443/{owner}/{repo}.git"
