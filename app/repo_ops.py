from pathlib import Path
import os
import json
import base64
import subprocess
from app.gh_ops import _sanitize, run_cmd

from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))


def _fmt_utc(s):
    """把 GitHub 返回的 UTC 时间转成北京时间字符串"""
    if not s:
        return ""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        dt = dt.astimezone(CST)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(s)[:19].replace("T", " ")



def _gh(token, args, check=True, log=None):
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    cmd = ["gh"] + args
    if log:
        log("$ " + _sanitize(" ".join(cmd)))
    p = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if log:
        if p.stdout:
            log(_sanitize(p.stdout.rstrip()))
        if p.stderr:
            log(_sanitize(p.stderr.rstrip()))
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "gh api 失败").strip())
    return p.returncode, p.stdout, p.stderr


def _fmt_time(s):
    return _fmt_utc(s)


def list_repos(token, owner, username="", per_page=100, log=None):
    # 自己的仓库用 /user/repos（含私有），别人的用 /users/{owner}/repos（仅公开）
    if owner == username:
        endpoint = f"/user/repos?per_page={per_page}&sort=updated&affiliation=owner"
    else:
        endpoint = f"/users/{owner}/repos?per_page={per_page}&sort=updated"
    rc, out, err = _gh(token, ["api", endpoint], log=log)
    data = json.loads(out) if out.strip() else []
    if isinstance(data, dict):
        data = data.get("items", []) if "items" in data else [data]
    result = []
    for r in data:
        result.append({
            "name": r.get("name", ""),
            "owner": (r.get("owner") or {}).get("login", owner),
            "description": r.get("description") or "",
            "private": bool(r.get("private")),
            "archived": bool(r.get("archived")),
            "fork": bool(r.get("fork")),
            "updated": _fmt_time(r.get("updated_at", "")),
            "pushed": _fmt_time(r.get("pushed_at", "")),
            "url": r.get("html_url", ""),
            "branch": r.get("default_branch", "main"),
            "language": r.get("language") or "",
            "stars": r.get("stargazers_count", 0),
            "forks": r.get("forks_count", 0),
            "size_kb": r.get("size", 0),
        })
    return result


def delete_repo(token, owner, repo, log=None):
    _gh(token, ["api", "-X", "DELETE", f"/repos/{owner}/{repo}"], log=log)


def rename_repo(token, owner, repo, new_name, log=None):
    _gh(token, ["api", "-X", "PATCH", f"/repos/{owner}/{repo}",
                "-f", f"name={new_name}"], log=log)


def transfer_repo(token, owner, repo, new_owner, log=None):
    _gh(token, ["api", "-X", "POST", f"/repos/{owner}/{repo}/transfer",
                "-f", f"new_owner={new_owner}"], log=log)


def set_archive(token, owner, repo, archived=True, log=None):
    flag = "true" if archived else "false"
    _gh(token, ["api", "-X", "PATCH", f"/repos/{owner}/{repo}",
                "-F", f"archived={flag}"], log=log)


def list_contents(token, owner, repo, path="", ref=None, log=None):
    from urllib.parse import quote
    p = path.strip("/")
    url = f"/repos/{owner}/{repo}/contents/{quote(p)}"
    if ref:
        url += f"?ref={quote(ref)}"
    rc, out, err = _gh(token, ["api", url], log=log)
    data = json.loads(out) if out.strip() else []
    if isinstance(data, dict):
        return [{
            "name": data.get("name", ""),
            "path": data.get("path", ""),
            "type": data.get("type", "file"),
            "size": data.get("size", 0),
        }]
    result = []
    for it in data:
        result.append({
            "name": it.get("name", ""),
            "path": it.get("path", ""),
            "type": it.get("type", "file"),
            "size": it.get("size", 0),
        })
    result.sort(key=lambda x: (0 if x["type"] == "dir" else 1, x["name"].lower()))
    return result


def get_file(token, owner, repo, path, ref=None, log=None):
    from urllib.parse import quote
    p = path.strip("/")
    url = f"/repos/{owner}/{repo}/contents/{quote(p)}"
    if ref:
        url += f"?ref={quote(ref)}"
    rc, out, err = _gh(token, ["api", url], log=log)
    data = json.loads(out) if out.strip() else {}
    if data.get("type") != "file":
        raise RuntimeError("不是文件")
    content_b64 = (data.get("content") or "").replace("\n", "")
    if not content_b64:
        raise RuntimeError("文件内容为空")
    try:
        content = base64.b64decode(content_b64).decode("utf-8", errors="replace")
    except Exception:
        raise RuntimeError("无法解码文件内容")
    return {
        "name": data.get("name", ""),
        "path": data.get("path", ""),
        "size": data.get("size", 0),
        "sha": data.get("sha", ""),
        "content": content,
    }


def clone_repo(log, token, owner, name, dest, branch=None):
    """clone 或 pull 到 dest"""
    from app import ssh_keys

    dest_path = Path(dest)
    ssh_cmd = ssh_keys.git_ssh_command()
    if ssh_cmd:
        remote_url = ssh_keys.ssh_url(owner, name)
        env_extra = {"GIT_SSH_COMMAND": ssh_cmd}
    else:
        remote_url = f"https://x-access-token:{token}@github.com/{owner}/{name}.git"
        env_extra = {}

    if dest_path.exists() and (dest_path / ".git").exists():
        log(f"目录已存在，执行 git pull: {dest}")
        run_cmd(log, ["git", "fetch", "--all", "--tags", "--prune"],
                cwd=str(dest_path), env=env_extra)
        run_cmd(log, ["git", "pull", "--ff-only"],
                cwd=str(dest_path), env=env_extra, check=False)
        return "updated"

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone"]
    if branch:
        cmd += ["-b", branch]
    cmd += [remote_url, str(dest_path)]
    run_cmd(log, cmd, env=env_extra)
    return "cloned"


def list_releases(token, owner, repo, log=None):
    rc, out, err = _gh(token, ["api", f"/repos/{owner}/{repo}/releases?per_page=100"], log=log)
    data = json.loads(out) if out.strip() else []
    if not isinstance(data, list):
        return []
    result = []
    for r in data:
        assets = []
        for a in (r.get("assets") or []):
            assets.append({
                "name": a.get("name", ""),
                "size": a.get("size", 0),
                "url": a.get("browser_download_url", ""),
                "downloads": a.get("download_count", 0),
            })
        result.append({
            "tag": r.get("tag_name", ""),
            "name": r.get("name", ""),
            "published": _fmt_utc(r.get("published_at") or r.get("created_at") or ""),
            "prerelease": bool(r.get("prerelease")),
            "draft": bool(r.get("draft")),
            "body": r.get("body", "") or "",
            "url": r.get("html_url", ""),
            "assets": assets,
        })
    return result


def rollback_to_tag(log, token, owner, name, tag, branch="main"):
    """把 tag 的代码推回 branch（覆盖历史）"""
    import tempfile
    import shutil
    from app import ssh_keys

    ssh_cmd = ssh_keys.git_ssh_command()
    if ssh_cmd:
        remote_url = ssh_keys.ssh_url(owner, name)
        env_extra = {"GIT_SSH_COMMAND": ssh_cmd}
    else:
        remote_url = f"https://x-access-token:{token}@github.com/{owner}/{name}.git"
        env_extra = {}

    tmp = tempfile.mkdtemp(prefix="rollback-")
    try:
        log("克隆仓库到临时目录 ...")
        run_cmd(log, ["git", "clone", remote_url, tmp], env=env_extra)
        run_cmd(log, ["git", "config", "user.name", "nas-bot"], cwd=tmp)
        run_cmd(log, ["git", "config", "user.email", "nas-bot@local"], cwd=tmp)
        log(f"切换到 tag: {tag}")
        run_cmd(log, ["git", "checkout", f"tags/{tag}"], cwd=tmp)
        log(f"创建分支 {branch} 指向该 tag")
        run_cmd(log, ["git", "checkout", "-B", branch], cwd=tmp)
        log(f"强制推送到 origin/{branch}（覆盖远端）")
        run_cmd(log, ["git", "push", "-f", "origin", branch], cwd=tmp, env=env_extra)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def sync_fork(token, owner, repo, branch=None, log=None):
    """同步 fork，返回 (ok, message)"""
    # 先检查是不是真 fork
    rc, out, err = _gh(token, ["api", f"/repos/{owner}/{repo}"], check=False, log=log)
    if rc != 0:
        return False, f"获取仓库信息失败: {(err or out).strip()}"
    try:
        info = json.loads(out)
    except Exception:
        return False, "解析仓库信息失败"

    if not info.get("fork"):
        parent = info.get("parent") or {}
        parent_full = parent.get("full_name") or ""
        return False, (
            f"仓库 {owner}/{repo} 不是 GitHub 意义上的 fork，无法自动同步。\n"
            f"如果你想要同步上游代码，需要：\n"
            f"  1. 删掉这个仓库（用「项目」页的「删除」按钮）\n"
            f"  2. 用「Fork 别人的仓库」按钮，真正 fork 一次\n"
            f"  3. 之后再点「同步」就能用了"
        )

    parent = info.get("parent") or {}
    parent_full = parent.get("full_name") or ""
    default_branch = info.get("default_branch") or "main"

    args = ["api", "-X", "POST", f"/repos/{owner}/{repo}/merge-upstream"]
    args += ["-f", f"branch={branch or default_branch}"]
    rc, out, err = _gh(token, args, check=False, log=log)

    if rc == 0:
        try:
            data = json.loads(out) if out.strip() else {}
            mt = data.get("merge_type") or data.get("message") or "synced"
            return True, f"已同步（{mt}，来自 {parent_full}）"
        except Exception:
            return True, "已同步"

    combined = ((err or "") + " " + (out or "")).lower()
    if ("no new commits" in combined or "already up to date" in combined
            or "up-to-date" in combined):
        return True, f"已是最新（{parent_full}）"

    msg = (err or out or "同步失败").strip()
    # 冲突时友好提示
    if "conflict" in combined or "merge conflict" in combined:
        msg = "合并冲突，需要手动处理：\n去 GitHub 网页打开仓库，手动解决冲突后再点「同步」"
    return False, msg


def update_file(token, owner, repo, path, content, message, sha=None, log=None):
    """创建或更新文件，返回 commit 信息"""
    import base64

    # 没传 sha 时先查
    if not sha:
        rc, out, err = _gh(token, ["api", f"/repos/{owner}/{repo}/contents/{path}"],
                           check=False, log=log)
        if rc == 0 and out.strip():
            try:
                data = json.loads(out)
                sha = data.get("sha", "")
            except Exception:
                pass

    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    args = ["api", "-X", "PUT", f"/repos/{owner}/{repo}/contents/{path}",
            "-f", f"message={message}",
            "-f", f"content={b64}"]
    if sha:
        args += ["-f", f"sha={sha}"]
    rc, out, err = _gh(token, args, log=log)
    try:
        return json.loads(out)
    except Exception:
        return {"raw": out}


def list_file_commits(token, owner, repo, path, limit=30, log=None):
    """列出某文件的提交历史"""
    from urllib.parse import quote
    url = (f"/repos/{owner}/{repo}/commits"
           f"?path={quote(path)}&per_page={limit}")
    rc, out, err = _gh(token, ["api", url], log=log, check=False)
    if rc != 0:
        msg = (err or out or "").strip()
        if "404" in msg and "empty" in msg.lower():
            return []
        # 空仓库
        if "Git Repository is empty" in msg:
            return []
        raise RuntimeError(msg or f"拉取提交历史失败 (exit {rc})")
    data = json.loads(out) if out.strip() else []
    if not isinstance(data, list):
        return []
    result = []
    for c in data:
        commit = c.get("commit") or {}
        author = commit.get("author") or {}
        result.append({
            "sha": c.get("sha", ""),
            "short": c.get("sha", "")[:7],
            "message": (commit.get("message") or "").split("\n")[0],
            "author": author.get("name", ""),
            "date": _fmt_utc(author.get("date", "")),
        })
    return result


def get_commit_file_diff(token, owner, repo, sha, path, log=None):
    """获取某个 commit 里指定文件的 diff"""
    rc, out, err = _gh(token, ["api", f"/repos/{owner}/{repo}/commits/{sha}"], log=log)
    data = json.loads(out)
    commit = data.get("commit") or {}
    for f in (data.get("files") or []):
        if f.get("filename") == path:
            return {
                "filename": f.get("filename", ""),
                "status": f.get("status", ""),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "patch": f.get("patch", ""),
                "message": commit.get("message", ""),
                "sha": sha,
                "short": sha[:7],
            }
    return None


def compare_refs(token, owner, repo, base, head, log=None):
    """对比两个 ref（tag / commit / 分支）"""
    rc, out, err = _gh(token, ["api", f"/repos/{owner}/{repo}/compare/{base}...{head}"], log=log)
    data = json.loads(out)
    files = []
    for f in (data.get("files") or []):
        files.append({
            "filename": f.get("filename", ""),
            "status": f.get("status", ""),
            "additions": f.get("additions", 0),
            "deletions": f.get("deletions", 0),
            "patch": f.get("patch", ""),
        })
    return {
        "status": data.get("status", ""),
        "ahead_by": data.get("ahead_by", 0),
        "behind_by": data.get("behind_by", 0),
        "total_commits": data.get("total_commits", 0),
        "files": files,
    }

