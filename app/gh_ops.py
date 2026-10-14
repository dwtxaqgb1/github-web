import os
import re
import shutil
import tempfile
import zipfile
import tarfile
import subprocess
from pathlib import Path
from app import ssh_keys

WORK_DIR = Path(os.environ.get("WORK_DIR", "/work"))

_TOKEN_RE = re.compile(r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")


def _sanitize(s):
    return _TOKEN_RE.sub("***REDACTED***", s)


def run_cmd(log, cmd, cwd=None, env=None, check=True):
    cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
    log(f"$ {_sanitize(cmd_str)}")
    e = os.environ.copy()
    if env:
        e.update(env)
    p = subprocess.Popen(
        cmd, cwd=cwd, env=e,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, shell=isinstance(cmd, str),
    )
    if p.stdout:
        for line in p.stdout:
            line = line.rstrip()
            if line:
                log(_sanitize(line))
    p.wait()
    if check and p.returncode != 0:
        raise RuntimeError(f"命令失败 (exit {p.returncode}): {_sanitize(cmd_str)}")
    return p.returncode


def safe_extract_zip(zip_path, dest):
    dest = os.path.realpath(dest)
    with zipfile.ZipFile(zip_path) as z:
        for m in z.namelist():
            target = os.path.realpath(os.path.join(dest, m))
            if not (target == dest or target.startswith(dest + os.sep)):
                raise RuntimeError(f"非法路径: {m}")
        z.extractall(dest)


def safe_extract_tar(tar_path, dest):
    dest = os.path.realpath(dest)
    with tarfile.open(tar_path) as t:
        for m in t.getmembers():
            target = os.path.realpath(os.path.join(dest, m.name))
            if not (target == dest or target.startswith(dest + os.sep)):
                raise RuntimeError(f"非法路径: {m.name}")
        t.extractall(dest)


def extract_archive(archive_path, dest):
    archive_path = str(archive_path)
    if archive_path.endswith(".zip"):
        safe_extract_zip(archive_path, dest)
    elif archive_path.endswith((".tar.gz", ".tgz", ".tar")):
        safe_extract_tar(archive_path, dest)
    else:
        raise RuntimeError("只支持 .zip / .tar.gz / .tgz / .tar")


def flatten_if_single_dir(path):
    p = Path(path)
    items = [x for x in p.iterdir() if x.name not in (".DS_Store", "__MACOSX")]
    if len(items) == 1 and items[0].is_dir():
        return items[0]
    return p


def _setup_remote(log, token, owner, name, repo_dir):
    ssh_cmd = ssh_keys.git_ssh_command()
    if ssh_cmd:
        remote_url = ssh_keys.ssh_url(owner, name)
        env_extra = {"GIT_SSH_COMMAND": ssh_cmd}
        log(f"使用 SSH: {remote_url}")
    else:
        remote_url = f"https://x-access-token:{token}@github.com/{owner}/{name}.git"
        env_extra = {}
        log(f"使用 HTTPS: {_sanitize(remote_url)}")
    subprocess.run(["git", "remote", "remove", "origin"],
                   cwd=repo_dir, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", remote_url],
                   cwd=repo_dir, check=True, capture_output=True)
    return env_extra


def create_repo(log, token, owner, name, visibility="private", description=""):
    env = {"GH_TOKEN": token}
    rc = run_cmd(
        log,
        ["gh", "repo", "view", f"{owner}/{name}", "--json", "name", "-q", ".name"],
        env=env, check=False,
    )
    if rc == 0:
        log(f"仓库已存在: {owner}/{name}")
        return
    cmd = ["gh", "repo", "create", f"{owner}/{name}", f"--{visibility}"]
    if description:
        cmd += ["--description", description]
    run_cmd(log, cmd, env=env)


def push_source(log, token, owner, name, src_dir, branch="main", message="update"):
    src = Path(src_dir)
    if not (src / ".git").exists():
        run_cmd(log, ["git", "init", "-b", branch], cwd=src)
    run_cmd(log, ["git", "config", "user.name", "nas-bot"], cwd=src)
    run_cmd(log, ["git", "config", "user.email", "nas-bot@local"], cwd=src)
    run_cmd(log, ["git", "add", "-A"], cwd=src)
    rc = run_cmd(log, ["git", "diff", "--cached", "--quiet"], cwd=src, check=False)
    if rc != 0:
        run_cmd(log, ["git", "commit", "-m", message], cwd=src)
    else:
        log("无变更需提交")
    env_extra = _setup_remote(log, token, owner, name, src)
    run_cmd(log, ["git", "push", "-u", "origin", branch, "--force"],
            cwd=src, env=env_extra)


def create_release(log, token, owner, name, tag, title, notes, assets=None):
    env = {"GH_TOKEN": token}
    cmd = ["gh", "release", "create", tag,
           "--repo", f"{owner}/{name}",
           "--title", title or tag,
           "--notes", notes or f"Release {tag}"]
    if assets:
        cmd += [str(a) for a in assets]
    run_cmd(log, cmd, env=env)


def deploy_pages(log, token, owner, name, src_dir):
    src = Path(src_dir)
    tmp = Path(tempfile.mkdtemp(prefix="pages-"))
    try:
        for item in src.iterdir():
            if item.name == ".git":
                continue
            dst = tmp / item.name
            if item.is_dir():
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)
        run_cmd(log, ["git", "init", "-b", "gh-pages"], cwd=tmp)
        run_cmd(log, ["git", "config", "user.name", "nas-bot"], cwd=tmp)
        run_cmd(log, ["git", "config", "user.email", "nas-bot@local"], cwd=tmp)
        run_cmd(log, ["git", "add", "-A"], cwd=tmp)
        run_cmd(log, ["git", "commit", "-m", "deploy pages"], cwd=tmp)
        env_extra = _setup_remote(log, token, owner, name, tmp)
        run_cmd(log, ["git", "push", "-u", "origin", "gh-pages", "--force"],
                cwd=tmp, env=env_extra)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    env = {"GH_TOKEN": token}
    rc = run_cmd(
        log,
        ["gh", "api", f"/repos/{owner}/{name}/pages", "-q", ".html_url"],
        env=env, check=False,
    )
    if rc == 0:
        run_cmd(log, ["gh", "api", "-X", "PUT", f"/repos/{owner}/{name}/pages",
                      "-f", "source[branch]=gh-pages", "-f", "source[path]=/"],
                env=env, check=False)
    else:
        run_cmd(log, ["gh", "api", "-X", "POST", f"/repos/{owner}/{name}/pages",
                      "-f", "source[branch]=gh-pages", "-f", "source[path]=/"],
                env=env, check=False)
