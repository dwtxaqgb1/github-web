import os
import re
import json
import shutil
import subprocess
from pathlib import Path
from functools import wraps
from datetime import timedelta

from flask import (
    Flask, request, session, redirect, url_for,
    render_template, jsonify
)

from app import store
from app import gh_ops
from app import tasks as taskmod
from app import ssh_keys
from app import profiles
from app import file_templates
from app import repo_ops
from app import cache_store
from app import fork_sync
from app import login_guard
from app import readme_gen

WORK_DIR = Path(os.environ.get("WORK_DIR", "/work"))
UPLOAD_DIR = WORK_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

app = Flask(__name__)
app.secret_key = store.get_session_key()
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024
app.permanent_session_lifetime = timedelta(hours=12)

store.init_store(os.environ.get("ADMIN_PASSWORD", "admin123"))

# 启动 fork 自动同步调度器
def _fs_sync(owner, repo, token):
    return repo_ops.sync_fork(token, owner, repo)

def _fs_token():
    return store.get_token()

def _fs_log(msg):
    try:
        import sys
        print("[fork_sync] " + msg, file=sys.stderr, flush=True)
    except Exception:
        pass

fork_sync.start_scheduler(_fs_sync, _fs_token, _fs_log)


@app.after_request
def add_no_cache_headers(response):
    ct = response.headers.get("Content-Type", "")
    if "text/html" in ct or "application/json" in ct:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper


def _owner_from_form():
    cfg = store.get_config()
    return (request.form.get("owner")
            or cfg.get("gh_owner")
            or cfg.get("github_username")
            or "").strip()


@app.route("/login", methods=["GET", "POST"])
def login():
    err = None
    ip = login_guard.get_client_ip(request)

    allowed, remain_sec, remain_tries = login_guard.check(ip)
    if not allowed:
        m, s2 = divmod(remain_sec, 60)
        err = f"登录失败次数过多，请 {m} 分 {s2} 秒后重试"
        return render_template("login.html", err=err, locked=True)

    if request.method == "POST":
        if store.verify_password(request.form.get("password", "")):
            login_guard.record_success(ip)
            session.permanent = True
            session["logged_in"] = True
            return redirect(url_for("index"))
        remain = login_guard.record_fail(ip)
        if remain <= 0:
            err = f"失败次数过多，账户锁定 {login_guard.LOCK_MINUTES} 分钟"
        else:
            err = f"密码错误（还剩 {remain} 次机会）"

    changed = request.args.get("changed") == "1"
    return render_template("login.html", err=err, changed=changed)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    cfg = store.get_config()
    recent = taskmod.list_tasks()[:8]
    return render_template("index.html",
                           username=cfg.get("github_username", ""),
                           owner=cfg.get("gh_owner", ""),
                           has_token=bool(cfg.get("gh_token_enc")),
                           recent=recent)


@app.route("/tasks")
@login_required
def tasks():
    return render_template("tasks.html", tasks=taskmod.list_tasks())


@app.route("/setup", methods=["GET", "POST"])
@login_required
def setup():
    msg = err = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "generate_ssh":
            created = ssh_keys.generate()
            msg = "SSH 密钥已生成" if created else "SSH 密钥已存在，未重新生成"
        elif action == "github":
            token = request.form.get("token", "").strip()
            username = request.form.get("username", "").strip()
            owner = request.form.get("owner", "").strip()
            accel_url = request.form.get("accel_url", "").strip()
            if accel_url and not accel_url.startswith(("http://", "https://")):
                accel_url = "https://" + accel_url
            try:
                if token:
                    env = os.environ.copy()
                    env["GH_TOKEN"] = token
                    r = subprocess.run(
                        ["gh", "api", "user", "-q", ".login"],
                        env=env, capture_output=True, text=True, timeout=20)
                    if r.returncode != 0:
                        raise RuntimeError((r.stderr or "Token 无效").strip())
                    gh_user = r.stdout.strip()
                    if not username:
                        username = gh_user
                store.set_github(
                    token=token or None,
                    username=username,
                    owner=owner or username,
                )
                store.set_accel_url(accel_url)
                msg = "GitHub 设置已保存"
            except Exception as e:
                err = f"保存失败: {e}"
        elif action == "password":
            old = request.form.get("old", "")
            new = request.form.get("new", "")
            new2 = request.form.get("new2", "")
            if not store.verify_password(old):
                err = "原密码错误"
            elif len(new) < 6:
                err = "新密码至少 6 位"
            elif new != new2:
                err = "两次密码不一致"
            else:
                store.set_password(new)
                session.clear()
                return redirect(url_for("login", changed="1"))
    cfg = store.get_config()
    return render_template("setup.html",
                           username=cfg.get("github_username", ""),
                           owner=cfg.get("gh_owner", ""),
                           has_token=bool(cfg.get("gh_token_enc")),
                           accel_url=cfg.get("release_accel_url", ""),
                           ssh_pubkey=ssh_keys.public_key(),
                           ssh_ready=ssh_keys.has_key(),
                           msg=msg, err=err)


@app.route("/api/publish", methods=["POST"])
@login_required
def api_publish():
    if not store.get_token():
        return jsonify({"error": "未配置 Token，请先到设置页"}), 400

    repo_name = (request.form.get("repo_name") or "").strip()
    if not repo_name:
        return jsonify({"error": "缺少项目名称"}), 400
    if not REPO_NAME_RE.match(repo_name):
        return jsonify({"error": "项目名称只允许字母、数字、- _ ."}), 400

    owner = _owner_from_form()
    if not owner:
        return jsonify({"error": "缺少 GitHub 用户名"}), 400

    visibility = request.form.get("visibility", "private")
    if visibility not in ("private", "public", "internal"):
        visibility = "private"
    description = (request.form.get("description") or "").strip()
    branch = (request.form.get("branch") or "main").strip() or "main"
    readme = request.form.get("readme", "")
    tag = (request.form.get("tag") or "").strip()
    release_notes = request.form.get("release_notes", "")
    deploy_pages = request.form.get("deploy_pages") == "on"
    tpl_gitignore = (request.form.get("tpl_gitignore") or "none").strip()
    tpl_license = (request.form.get("tpl_license") or "none").strip()
    tpl_actions = (request.form.get("tpl_actions") or "none").strip()
    readme_auto = request.form.get("readme_auto") == "on"

    src_file = request.files.get("source_file")
    if not src_file or not src_file.filename:
        return jsonify({"error": "请上传源码包 (zip / tar.gz)"}), 400

    tid = taskmod.create_task(f"发布 {owner}/{repo_name}")
    tdir = UPLOAD_DIR / tid
    tdir.mkdir(parents=True, exist_ok=True)

    src_path = tdir / src_file.filename
    src_file.save(str(src_path))

    asset_paths = []
    for af in request.files.getlist("assets"):
        if af and af.filename:
            p = tdir / af.filename
            af.save(str(p))
            asset_paths.append(str(p))

    cfg_now = store.get_config()
    accel_base = (cfg_now.get("release_accel_url") or "").rstrip("/")

    def job(log, set_extras, set_step):
        token = store.get_token()
        set_step("create_repo")
        log(f"目标: {owner}/{repo_name} (visibility={visibility})")
        extract_dir = tdir / "src"
        extract_dir.mkdir(exist_ok=True)
        log(f"解压 {src_path.name}")
        gh_ops.extract_archive(str(src_path), str(extract_dir))
        src_root = gh_ops.flatten_if_single_dir(str(extract_dir))
        log(f"源码根目录: {src_root}")

        # B3: 若用户没写 README，尝试从 zip 里读
        final_readme = readme
        if not final_readme.strip() and readme_auto:
            for cand in ("README.md", "readme.md", "Readme.md", "README.MD", "README"):
                rp = Path(src_root) / cand
                if rp.exists() and rp.is_file():
                    try:
                        final_readme = rp.read_text(encoding="utf-8", errors="replace")
                        log(f"自动读取 zip 里的 {cand} 作为 README（{len(final_readme)} 字符）")
                    except Exception as e:
                        log(f"读取 {cand} 失败: {e}")
                    break
            else:
                log("zip 里没有 README，跳过")

        if final_readme.strip():
            (Path(src_root) / "README.md").write_text(final_readme, encoding="utf-8")
            log("已写入 README.md")

        # B6: 应用发布模板
        cfg_tpl = store.get_config()
        author = cfg_tpl.get("github_username", "") or owner
        try:
            written = file_templates.apply_templates(
                str(src_root),
                gitignore=tpl_gitignore,
                license_=tpl_license,
                actions=tpl_actions,
                author=author,
            )
            if written:
                log("已应用模板: " + ", ".join(written))
        except Exception as e:
            log(f"应用模板失败: {e}")

        extras = {"repo_url": f"https://github.com/{owner}/{repo_name}"}

        gh_ops.create_repo(log, token, owner, repo_name, visibility, description)
        set_step("push_source")
        gh_ops.push_source(log, token, owner, repo_name, src_root,
                           branch=branch, message=f"update {tag}".strip())

        if tag:
            set_step("create_release")
            gh_ops.create_release(log, token, owner, repo_name,
                                  tag, tag, release_notes, asset_paths or None)
            extras["release_url"] = f"https://github.com/{owner}/{repo_name}/releases/tag/{tag}"
            if asset_paths:
                assets = []
                for a in asset_paths:
                    name = Path(a).name
                    orig = (f"https://github.com/{owner}/{repo_name}"
                            f"/releases/download/{tag}/{name}")
                    item = {"name": name, "url": orig}
                    if accel_base:
                        item["accel"] = f"{accel_base}/{orig}"
                    assets.append(item)
                extras["assets"] = assets

        if deploy_pages:
            set_step("deploy_pages")
            log("部署 GitHub Pages ...")
            gh_ops.deploy_pages(log, token, owner, repo_name, str(src_root))
            extras["pages_url"] = f"https://{owner}.github.io/{repo_name}/"

        set_step("done")
        set_extras(extras)

        # 保存本次配置供下次自动填充
        try:
            profiles.upsert(repo_name, {
                "visibility": visibility,
                "description": description,
                "branch": branch,
                "tag": tag,
                "deploy_pages": deploy_pages,
                "tpl_gitignore": tpl_gitignore,
                "tpl_license": tpl_license,
                "tpl_actions": tpl_actions,
                "readme_auto": readme_auto,
            })
        except Exception:
            pass

    taskmod.start_task(f"发布 {owner}/{repo_name}", job, task_id=tid)
    return jsonify({"task_id": tid})


@app.route("/api/pages", methods=["POST"])
@login_required
def api_pages():
    if not store.get_token():
        return jsonify({"error": "未配置 Token"}), 400

    repo_name = (request.form.get("repo_name") or "").strip()
    if not repo_name or not REPO_NAME_RE.match(repo_name):
        return jsonify({"error": "项目名称不合法"}), 400

    owner = _owner_from_form()
    if not owner:
        return jsonify({"error": "缺少 GitHub 用户名"}), 400

    f = request.files.get("pages_file")
    if not f or not f.filename:
        return jsonify({"error": "请上传发布页压缩包"}), 400

    tid = taskmod.create_task(f"发布页 {owner}/{repo_name}")
    tdir = UPLOAD_DIR / tid
    tdir.mkdir(parents=True, exist_ok=True)
    p = tdir / f.filename
    f.save(str(p))

    def job(log, set_extras, set_step):
        token = store.get_token()
        set_step("deploy_pages")
        extract_dir = tdir / "src"
        extract_dir.mkdir(exist_ok=True)
        log("解压发布页 ...")
        gh_ops.extract_archive(str(p), str(extract_dir))
        src_root = gh_ops.flatten_if_single_dir(str(extract_dir))
        log(f"Pages 根目录: {src_root}")
        gh_ops.create_repo(log, token, owner, repo_name, "public", "")
        gh_ops.deploy_pages(log, token, owner, repo_name, str(src_root))
        set_step("done")
        set_extras({
            "repo_url": f"https://github.com/{owner}/{repo_name}",
            "pages_url": f"https://{owner}.github.io/{repo_name}/",
        })

    taskmod.start_task(f"发布页 {owner}/{repo_name}", job, task_id=tid)
    return jsonify({"task_id": tid})


@app.route("/api/task/<tid>")
@login_required
def api_task(tid):
    t = taskmod.get_task(tid)
    if not t:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify({"task": t, "log": taskmod.read_log(tid, tail=300)})


@app.route("/api/tasks")
@login_required
def api_tasks():
    return jsonify({"tasks": taskmod.list_tasks()[:100]})


@app.route("/api/tasks/delete/<tid>", methods=["POST"])
@login_required
def api_task_delete(tid):
    t = taskmod.get_task(tid)
    if not t:
        return jsonify({"error": "任务不存在"}), 404
    if t["status"] in ("running", "pending"):
        return jsonify({"error": "任务运行中，不能删除"}), 400
    try:
        os.remove(t["log_file"])
    except Exception:
        pass
    d = UPLOAD_DIR / tid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    taskmod.delete_task(tid)
    return jsonify({"ok": True})



@app.route("/repos")
@login_required
def repos_page():
    cfg = store.get_config()
    return render_template("repos.html",
                           owner=cfg.get("gh_owner") or cfg.get("github_username", ""),
                           has_token=bool(cfg.get("gh_token_enc")))


@app.route("/api/repos")
@login_required
def api_repos():
    if not store.get_token():
        return jsonify({"error": "未配置 Token"}), 400
    cfg = store.get_config()
    owner = cfg.get("gh_owner") or cfg.get("github_username", "")
    username = cfg.get("github_username", "")
    if not owner:
        return jsonify({"error": "未配置 GitHub 用户名"}), 400

    use_cache = request.args.get("use_cache", "1") != "0"
    cache_key = f"repos_mine_{owner}"

    if use_cache:
        cached = cache_store.get(cache_key)
        if cached is not None:
            return jsonify({"owner": owner, "repos": cached, "from_cache": True})

    token = store.get_token()
    try:
        repos = repo_ops.list_repos(token, owner, username=username)
        cache_store.set(cache_key, repos)
        return jsonify({"owner": owner, "repos": repos, "from_cache": False})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/delete", methods=["POST"])
@login_required
def api_repos_delete():
    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()
    name = (data.get("name") or "").strip()
    if not owner or not name:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400
    try:
        repo_ops.delete_repo(token, owner, name)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/rename", methods=["POST"])
@login_required
def api_repos_rename():
    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()
    name = (data.get("name") or "").strip()
    new_name = (data.get("new_name") or "").strip()
    if not owner or not name or not new_name:
        return jsonify({"error": "缺少参数"}), 400
    if not REPO_NAME_RE.match(new_name):
        return jsonify({"error": "新名称只允许字母、数字、- _ ."}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400
    try:
        repo_ops.rename_repo(token, owner, name, new_name)
        return jsonify({"ok": True, "new_name": new_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/transfer", methods=["POST"])
@login_required
def api_repos_transfer():
    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()
    name = (data.get("name") or "").strip()
    new_owner = (data.get("new_owner") or "").strip()
    if not owner or not name or not new_owner:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400
    try:
        repo_ops.transfer_repo(token, owner, name, new_owner)
        return jsonify({"ok": True, "new_owner": new_owner})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/archive", methods=["POST"])
@login_required
def api_repos_archive():
    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()
    name = (data.get("name") or "").strip()
    archived = bool(data.get("archived", True))
    if not owner or not name:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400
    try:
        repo_ops.set_archive(token, owner, name, archived)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/api/repos/download")
@login_required
def api_repo_download():
    """用 Token 代理下载 GitHub 上的文件（私有/公开都能下）"""
    import urllib.request
    import urllib.error
    from urllib.parse import quote
    from flask import Response

    owner = (request.args.get("owner") or "").strip()
    repo = (request.args.get("repo") or "").strip()
    path = (request.args.get("path") or "").strip()
    ref = (request.args.get("ref") or "").strip()
    if not owner or not repo or not path:
        return jsonify({"error": "缺少参数"}), 400

    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    if ref:
        url += f"?ref={quote(ref)}"

    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github.raw")
    req.add_header("User-Agent", "gh-web-tool")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        msg = body.decode("utf-8", errors="replace")[:300] or f"HTTP {e.code}"
        return jsonify({"error": msg}), e.code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    filename = path.split("/")[-1]
    encoded = quote(filename, safe="")
    return Response(
        data,
        mimetype="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
            "Content-Length": str(len(data)),
            "Cache-Control": "no-store",
        },
    )




@app.route("/api/repos/info")
@login_required
def api_repo_info():
    owner = (request.args.get("owner") or "").strip()
    repo = (request.args.get("repo") or "").strip()
    if not owner or not repo:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400
    try:
        rc, out, err = repo_ops._gh(token, ["api", f"/repos/{owner}/{repo}"])
        data = json.loads(out)
        return jsonify({
            "name": data.get("name", repo),
            "owner": (data.get("owner") or {}).get("login", owner),
            "description": data.get("description") or "",
            "private": bool(data.get("private")),
            "branch": data.get("default_branch", "main"),
            "url": data.get("html_url", ""),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500



def _resolve_clone_path(user_input, default_rel):
    """解析用户输入的克隆路径，返回 (container_path, host_path, ok, err)"""
    host_work = os.environ.get("HOST_WORK_DIR", "/work")
    host_nas = (os.environ.get("HOST_NAS_DIR") or "").strip()
    nas_enabled = bool(host_nas)

    s = (user_input or "").strip()
    if not s:
        s = default_rel

    if "\\" in s:
        return None, None, False, "路径不能包含反斜杠"
    parts = [x for x in s.split("/") if x]
    for x in parts:
        if x in (".", ".."):
            return None, None, False, "路径不能包含 . 或 .."

    if s.startswith("/nas/"):
        if not nas_enabled:
            return None, None, False, "未配置额外挂载目录（NAS_EXTRA_DIR），/nas 不可用"
        rel = s[len("/nas/"):].strip("/")
        if not rel:
            return None, None, False, "/nas 后面的路径不能为空"
        return "/nas/" + rel, os.path.join(host_nas, rel), True, ""

    if s.startswith("/work/"):
        rel = s[len("/work/"):].strip("/")
        if not rel:
            return None, None, False, "/work 后面的路径不能为空"
        return "/work/" + rel, os.path.join(host_work, rel), True, ""

    if s.startswith("/"):
        return None, None, False, "只支持以 /work/ 或 /nas/ 开头的绝对路径，或相对路径（默认放 /work 下）"

    # 相对路径 → /work 下
    rel = s.strip("/")
    if not rel:
        return None, None, False, "路径不能为空"
    return "/work/" + rel, os.path.join(host_work, rel), True, ""


def parse_repo_url(s):
    """解析 GitHub 仓库地址，返回 (owner, name) 或 (None, None)"""
    s = (s or "").strip()
    if not s:
        return None, None
    s = re.sub(r"\.git$", "", s)
    # https://github.com/owner/repo
    m = re.match(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/?#\s]+)", s)
    if m:
        return m.group(1), m.group(2)
    # git@github.com:owner/repo
    m = re.match(r"^git@github\.com:([^/]+)/([^/?#\s]+)", s)
    if m:
        return m.group(1), m.group(2)
    # ssh://git@github.com/owner/repo
    m = re.match(r"^ssh://git@github\.com/([^/]+)/([^/?#\s]+)", s)
    if m:
        return m.group(1), m.group(2)
    # owner/repo
    m = re.match(r"^([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)$", s)
    if m:
        return m.group(1), m.group(2)
    return None, None


@app.route("/api/git/fork", methods=["POST"])
@login_required
def api_git_fork():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    org = (data.get("org") or "").strip() or None
    owner, name = parse_repo_url(url)
    if not owner or not name:
        return jsonify({"error": "无法识别仓库地址，请填 https://github.com/owner/repo 或 owner/repo"}), 400

    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400

    cfg = store.get_config()
    my_owner = cfg.get("gh_owner") or cfg.get("github_username", "")
    if not my_owner:
        return jsonify({"error": "未配置 GitHub 用户名"}), 400

    if owner == my_owner and not org:
        return jsonify({"error": f"这已经是你自己的仓库（{owner}/{name}），无需 fork"}), 400

    def job(log, set_extras, set_step):
        set_step("create_repo")
        log(f"Fork 源仓库: {owner}/{name}")
        log(f"Fork 到: {my_owner}")

        # 先检查目标仓库是否已存在
        rc, out, err = repo_ops._gh(
            token, ["api", f"/repos/{my_owner}/{name}", "-q", ".fork"],
            check=False, log=log)
        if rc == 0:
            is_fork = out.strip() == "true"
            log(f"目标仓库已存在（fork={is_fork}），跳过创建，直接返回")
        else:
            log("调用 GitHub Fork API ...")
            api_path = f"/repos/{owner}/{name}/forks"
            args = ["api", "-X", "POST", api_path]
            if org:
                args += ["-f", f"organization={org}"]
            rc2, out2, err2 = repo_ops._gh(token, args, log=log)
            if rc2 != 0:
                raise RuntimeError((err2 or "Fork 失败").strip())

        set_step("done")
        extras = {
            "action": "fork",
            "source": f"{owner}/{name}",
            "target": f"{my_owner}/{name}",
            "repo_url": f"https://github.com/{my_owner}/{name}",
            "source_url": f"https://github.com/{owner}/{name}",
        }
        set_extras(extras)
        log(f"完成，仓库地址: https://github.com/{my_owner}/{name}")
        log("提示：仓库刚创建可能需要几秒同步，稍等可到「项目」页看到它")

    tid = taskmod.create_task(f"Fork {owner}/{name} → {my_owner}")
    taskmod.start_task(f"Fork {owner}/{name} → {my_owner}", job, task_id=tid)
    return jsonify({"task_id": tid, "source": f"{owner}/{name}", "target": f"{my_owner}/{name}"})


@app.route("/api/repos/clone", methods=["POST"])
@login_required
def api_repo_clone():
    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()
    name = (data.get("name") or "").strip()
    branch = (data.get("branch") or "").strip() or None
    user_path = data.get("subpath") or ""
    if not owner or not name:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400

    default_rel = f"clones/{owner}/{name}"
    container_path, host_path, ok, err = _resolve_clone_path(user_path, default_rel)
    if not ok:
        return jsonify({"error": err}), 400

    dest = Path(container_path)

    def job(log, set_extras, set_step):
        set_step("push_source")
        log(f"目标: {owner}/{name}")
        log(f"克隆到: {container_path}")
        log(f"NAS 路径: {host_path}")
        action = repo_ops.clone_repo(log, token, owner, name, str(dest), branch)
        set_step("done")
        set_extras({
            "action": "clone",
            "local_path": str(dest),
            "host_path": host_path,
            "repo_url": f"https://github.com/{owner}/{name}",
        })
        log(f"完成，NAS 路径: {host_path}")

    tid = taskmod.create_task(f"克隆 {owner}/{name}")
    taskmod.start_task(f"克隆 {owner}/{name}", job, task_id=tid)
    return jsonify({"task_id": tid, "host_path": host_path})


@app.route("/api/system/info")
@login_required
def api_system_info():
    host_work = os.environ.get("HOST_WORK_DIR", "/work")
    host_nas = (os.environ.get("HOST_NAS_DIR") or "").strip()
    return jsonify({
        "work_dir": host_work,
        "nas_dir": host_nas,
        "nas_enabled": bool(host_nas),
    })


@app.route("/api/repos/sync", methods=["POST"])
@login_required
def api_repo_sync():
    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()
    name = (data.get("name") or "").strip()
    if not owner or not name:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400

    key = f"{owner}/{name}"

    def job(log, set_extras, set_step):
        set_step("push_source")
        log(f"同步 fork: {key}")
        ok, msg = repo_ops.sync_fork(token, owner, name, log=log)
        fork_sync.mark_synced(key, ok, msg)
        if not ok:
            raise RuntimeError(msg)
        set_step("done")
        set_extras({
            "action": "sync",
            "repo_url": f"https://github.com/{owner}/{name}",
            "message": msg,
        })
        log(f"同步完成: {msg}")

    tid = taskmod.create_task(f"同步 {key}")
    taskmod.start_task(f"同步 {key}", job, task_id=tid)
    return jsonify({"task_id": tid})


@app.route("/api/repos/sync_config", methods=["GET", "POST"])
@login_required
def api_repo_sync_config():
    if request.method == "GET":
        owner = (request.args.get("owner") or "").strip()
        name = (request.args.get("name") or "").strip()
        if not owner or not name:
            return jsonify({"error": "缺少参数"}), 400
        cfg = fork_sync.get(f"{owner}/{name}")
        return jsonify({"config": cfg, "intervals": list(fork_sync.INTERVALS.keys())})

    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()
    name = (data.get("name") or "").strip()
    enabled = bool(data.get("enabled"))
    interval = (data.get("interval") or "1d").strip()
    if not owner or not name:
        return jsonify({"error": "缺少参数"}), 400
    if interval not in fork_sync.INTERVALS:
        return jsonify({"error": "不支持的时间间隔"}), 400
    key = f"{owner}/{name}"
    cfg = {
        "enabled": enabled,
        "interval": interval,
        "interval_hours": fork_sync.INTERVALS[interval],
    }
    fork_sync.upsert(key, cfg)
    return jsonify({"ok": True, "config": fork_sync.get(key)})


@app.route("/api/repos/sync_status")
@login_required
def api_repo_sync_status():
    return jsonify({"all": fork_sync.get_all()})


@app.route("/api/repos/releases")
@login_required
def api_repo_releases():
    owner = (request.args.get("owner") or "").strip()
    repo = (request.args.get("repo") or "").strip()
    if not owner or not repo:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400
    try:
        releases = repo_ops.list_releases(token, owner, repo)
        cfg = store.get_config()
        accel = (cfg.get("release_accel_url") or "").rstrip("/")
        return jsonify({"releases": releases, "accel_base": accel})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/rollback", methods=["POST"])
@login_required
def api_repo_rollback():
    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()
    name = (data.get("name") or "").strip()
    tag = (data.get("tag") or "").strip()
    branch = (data.get("branch") or "main").strip() or "main"
    if not owner or not name or not tag:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400

    def job(log, set_extras, set_step):
        set_step("push_source")
        log(f"回滚 {owner}/{name} 到 {tag}（分支 {branch}）")
        repo_ops.rollback_to_tag(log, token, owner, name, tag, branch)
        set_step("done")
        set_extras({
            "action": "rollback",
            "repo_url": f"https://github.com/{owner}/{name}",
            "rollback_tag": tag,
        })

    tid = taskmod.create_task(f"回滚 {owner}/{name} 到 {tag}")
    taskmod.start_task(f"回滚 {owner}/{name} 到 {tag}", job, task_id=tid)
    return jsonify({"task_id": tid})


def check_token_scopes(token):
    """探测 Token 权限，返回 dict"""
    import urllib.request
    import urllib.error
    result = {
        "valid": False,
        "scopes": [],
        "user": "",
        "errors": [],
    }
    try:
        req = urllib.request.Request("https://api.github.com/user")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("User-Agent", "gh-web-tool")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            result["valid"] = True
            result["user"] = data.get("login", "")
            raw_scopes = resp.headers.get("X-OAuth-Scopes", "")
            result["scopes"] = [s.strip() for s in raw_scopes.split(",") if s.strip()]
            # Fine-grained Token 会返回 X-Accepted-OAuth-Scopes 而不是 X-OAuth-Scopes
            if not result["scopes"]:
                acc = resp.headers.get("X-Accepted-OAuth-Scopes", "")
                if acc:
                    result["scopes"] = ["fine-grained"]
    except urllib.error.HTTPError as e:
        if e.code == 401:
            result["errors"].append("Token 无效或已过期（401）")
        else:
            result["errors"].append(f"HTTP {e.code}: {e.reason}")
    except Exception as e:
        result["errors"].append(str(e))
    return result


@app.route("/api/setup/check_token", methods=["POST"])
@login_required
def api_setup_check_token():
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify({"error": "缺少 token"}), 400
    info = check_token_scopes(token)
    return jsonify(info)


@app.route("/api/profiles")
@login_required
def api_profiles():
    return jsonify({"profiles": profiles.get_all()})


@app.route("/api/precheck", methods=["POST"])
@login_required
def api_precheck():
    data = request.get_json(silent=True) or {}
    repo_name = (data.get("repo_name") or "").strip()
    visibility = (data.get("visibility") or "private").strip()
    deploy_pages = bool(data.get("deploy_pages"))
    source_size = int(data.get("source_size") or 0)
    asset_size = int(data.get("asset_size") or 0)

    errors = []
    warnings = []

    if not repo_name:
        errors.append("项目名称不能为空")
    elif not REPO_NAME_RE.match(repo_name):
        errors.append("项目名称只允许字母、数字、- _ .")

    if deploy_pages and visibility == "private":
        warnings.append({
            "code": "pages_private",
            "msg": "勾选了 GitHub Pages，但可见性是 private，免费账户不支持 Pages，建议改为 public。"
        })

    MAX = 2 * 1024 * 1024 * 1024
    LARGE = 100 * 1024 * 1024
    if source_size > MAX:
        errors.append(f"源码包 {source_size/1024/1024/1024:.2f} GB，超过 GitHub 上限")
    elif source_size > LARGE:
        warnings.append({
            "code": "large_zip",
            "msg": f"源码包 {source_size/1024/1024:.0f} MB，Git 单文件上限 100MB，可能失败"
        })
    if asset_size > MAX:
        errors.append(f"单个发布资产 {asset_size/1024/1024/1024:.2f} GB，超过 2GB 上限")

    token = store.get_token()
    if not token:
        errors.append("未配置 Token")
    else:
        cfg = store.get_config()
        owner = cfg.get("gh_owner") or cfg.get("github_username", "")
        if owner and repo_name and REPO_NAME_RE.match(repo_name):
            try:
                env = os.environ.copy()
                env["GH_TOKEN"] = token
                r = subprocess.run(
                    ["gh", "api", f"/repos/{owner}/{repo_name}", "-q", ".name"],
                    env=env, capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    warnings.append({
                        "code": "repo_exists",
                        "msg": f"仓库 {owner}/{repo_name} 已存在，本次将覆盖更新（不会删除历史 Release）"
                    })
            except Exception:
                pass

    return jsonify({"errors": errors, "warnings": warnings})


@app.route("/api/repos/save_file", methods=["POST"])
@login_required
def api_repo_save_file():
    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()
    repo = (data.get("repo") or "").strip()
    path = (data.get("path") or "").strip()
    content = data.get("content", "")
    message = (data.get("message") or "").strip() or f"update {path}"
    sha = (data.get("sha") or "").strip()

    if not owner or not repo or not path:
        return jsonify({"error": "缺少参数"}), 400
    if isinstance(content, str) is False:
        return jsonify({"error": "content 必须是字符串"}), 400

    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400

    try:
        result = repo_ops.update_file(token, owner, repo, path, content, message, sha)
        commit = (result.get("commit") or {}) if isinstance(result, dict) else {}
        return jsonify({
            "ok": True,
            "commit_sha": commit.get("sha", ""),
            "commit_url": commit.get("html_url", ""),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/restore_file", methods=["POST"])
@login_required
def api_repo_restore_file():
    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()
    repo = (data.get("repo") or "").strip()
    path = (data.get("path") or "").strip()
    sha = (data.get("sha") or "").strip()
    if not owner or not repo or not path or not sha:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400

    try:
        # 1. 取那个 commit 下的文件内容
        old = repo_ops.get_file(token, owner, repo, path, ref=sha)
        content = old.get("content", "")
        # 2. 取当前文件的最新 sha（用于更新）
        cur = repo_ops.get_file(token, owner, repo, path)
        cur_sha = cur.get("sha", "")
        # 3. 提交更新
        msg = f"restore {path} to {sha[:7]}"
        result = repo_ops.update_file(token, owner, repo, path, content, msg, cur_sha)
        commit = (result.get("commit") or {}) if isinstance(result, dict) else {}
        return jsonify({
            "ok": True,
            "commit_sha": commit.get("sha", ""),
            "commit_url": commit.get("html_url", ""),
            "restored_sha": sha,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/file_history")
@login_required
def api_repo_file_history():
    owner = (request.args.get("owner") or "").strip()
    repo = (request.args.get("repo") or "").strip()
    path = (request.args.get("path") or "").strip()
    limit = request.args.get("limit", "30")
    try:
        limit = min(int(limit), 100)
    except Exception:
        limit = 30
    if not owner or not repo or not path:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400
    try:
        items = repo_ops.list_file_commits(token, owner, repo, path, limit)
        return jsonify({"commits": items})
    except Exception as e:
        msg = str(e)
        if "empty" in msg.lower() or "404" in msg:
            return jsonify({"commits": [], "empty_repo": True})
        return jsonify({"error": msg}), 500


@app.route("/api/repos/file_diff")
@login_required
def api_repo_file_diff():
    owner = (request.args.get("owner") or "").strip()
    repo = (request.args.get("repo") or "").strip()
    path = (request.args.get("path") or "").strip()
    sha = (request.args.get("sha") or "").strip()
    if not owner or not repo or not path or not sha:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400
    try:
        diff = repo_ops.get_commit_file_diff(token, owner, repo, sha, path)
        if diff is None:
            return jsonify({"error": "该提交未修改此文件，或改动过大无法显示"}), 404
        return jsonify(diff)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/compare")
@login_required
def api_repo_compare():
    owner = (request.args.get("owner") or "").strip()
    repo = (request.args.get("repo") or "").strip()
    base = (request.args.get("base") or "").strip()
    head = (request.args.get("head") or "").strip()
    if not owner or not repo or not base or not head:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400
    try:
        result = repo_ops.compare_refs(token, owner, repo, base, head)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/generate_readme", methods=["POST"])
@login_required
def api_repo_generate_readme():
    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()
    repo = (data.get("repo") or "").strip()
    description = (data.get("description") or "").strip()
    branch = (data.get("branch") or "").strip() or None

    if not owner or not repo:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400

    try:
        items = repo_ops.list_contents(token, owner, repo, "", ref=branch)
    except Exception as e:
        return jsonify({"error": f"读取仓库文件失败: {e}"}), 500

    files = [it.get("name", "") for it in items if it.get("type") == "file"]

    # 尝试从仓库信息里取描述
    if not description:
        try:
            rc, out, err = repo_ops._gh(token, ["api", f"/repos/{owner}/{repo}"],
                                        check=False)
            if rc == 0 and out.strip():
                info = json.loads(out)
                description = info.get("description") or ""
        except Exception:
            pass

    markdown = readme_gen.generate(repo, owner, description, files)

    # 顺便探测一下当前是否已有 README，返回其 sha
    readme_sha = ""
    readme_exists = False
    for fname in ("README.md", "readme.md", "Readme.md", "README.MD", "README"):
        if fname in files:
            try:
                cur = repo_ops.get_file(token, owner, repo, fname)
                readme_sha = cur.get("sha", "")
                readme_exists = True
            except Exception:
                pass
            break

    return jsonify({
        "markdown": markdown,
        "files_count": len(files),
        "readme_exists": readme_exists,
        "readme_sha": readme_sha,
        "readme_name": "README.md",
    })


@app.route("/api/repos/setup_build", methods=["POST"])
@login_required
def api_repo_setup_build():
    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()
    repo = (data.get("repo") or "").strip()
    branch = (data.get("branch") or "main").strip() or "main"

    if not owner or not repo:
        return jsonify({"error": "缺少参数"}), 400

    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400

    result = {
        "owner": owner,
        "repo": repo,
        "steps": [],
        "actions_url": f"https://github.com/{owner}/{repo}/actions",
    }

    try:
        items = repo_ops.list_contents(token, owner, repo, "", ref=branch)
        names = [it.get("name", "") for it in items]
        if "Dockerfile" not in names:
            result["steps"].append({"ok": False, "msg": "仓库根目录没有 Dockerfile，无法构建镜像"})
            return jsonify(result)
        result["steps"].append({"ok": True, "msg": "找到 Dockerfile"})
    except Exception as e:
        result["steps"].append({"ok": False, "msg": f"读取仓库失败: {e}"})
        return jsonify(result)

    wf_path = ".github/workflows/docker-publish.yml"
    wf_exists = False
    try:
        for it in repo_ops.list_contents(token, owner, repo, ".github/workflows", ref=branch):
            if it.get("name") == "docker-publish.yml":
                wf_exists = True
                break
    except Exception:
        pass

    if not wf_exists:
        try:
            repo_ops.ensure_workflow_file(
                token, owner, repo, wf_path, file_templates.ACTIONS_PUSH_GHCR,
                message="add docker publish workflow",
            )
            result["steps"].append({"ok": True, "msg": "已生成 docker-publish.yml"})
        except Exception as e:
            result["steps"].append({"ok": False, "msg": f"生成 workflow 失败: {e}"})
            return jsonify(result)
    else:
        result["steps"].append({"ok": True, "msg": "已存在 docker-publish.yml"})

    try:
        cur = repo_ops.get_workflow_permission(token, owner, repo)
        if cur == "write":
            result["steps"].append({"ok": True, "msg": "Actions 写权限已开启"})
        else:
            ok, msg = repo_ops.set_workflow_permission(token, owner, repo)
            if ok:
                result["steps"].append({"ok": True, "msg": "已开启 Actions 写权限"})
            else:
                result["steps"].append({
                    "ok": False,
                    "msg": f"无法自动开启写权限（{msg}）",
                    "hint": f"请去 GitHub 网页手动设置：https://github.com/{owner}/{repo}/settings/actions → Workflow permissions → Read and write permissions",
                })
    except Exception as e:
        result["steps"].append({
            "ok": False,
            "msg": f"权限检查出错: {e}",
            "hint": f"请去 GitHub 网页手动设置：https://github.com/{owner}/{repo}/settings/actions",
        })

    result["ok"] = True
    return jsonify(result)


@app.route("/api/repos/set_package_public", methods=["POST"])
@login_required
def api_repo_set_package_public():
    data = request.get_json(silent=True) or {}
    package_name = (data.get("package_name") or "").strip()
    if not package_name:
        return jsonify({"error": "缺少 package_name"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400
    try:
        cur = repo_ops.get_package_visibility(token, "", package_name)
        if cur == "public":
            return jsonify({"ok": True, "message": "已经是公开的", "visibility": "public"})
        ok, msg = repo_ops.set_package_visibility(token, "", package_name, "public")
        if ok:
            return jsonify({"ok": True, "message": msg, "visibility": "public"})
        return jsonify({"error": msg}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/build_history")
@login_required
def api_repo_build_history():
    owner = (request.args.get("owner") or "").strip()
    repo = (request.args.get("repo") or "").strip()
    limit = request.args.get("limit", "10")
    try:
        limit = min(int(limit), 50)
    except Exception:
        limit = 10
    if not owner or not repo:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400
    try:
        runs = repo_ops.list_workflow_runs(token, owner, repo, limit)
        return jsonify({"runs": runs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/package_visibility")
@login_required
def api_repo_package_visibility():
    package_name = (request.args.get("package_name") or "").strip()
    if not package_name:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400
    try:
        vis = repo_ops.get_package_visibility(token, "", package_name)
        return jsonify({"visibility": vis})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/starred_list")
@login_required
def api_repo_starred_list():
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400
    use_cache = request.args.get("use_cache", "1") != "0"
    cfg = store.get_config()
    owner = cfg.get("gh_owner") or cfg.get("github_username", "")
    cache_key = f"starred_list_{owner}"
    if use_cache:
        cached = cache_store.get(cache_key)
        if cached is not None:
            return jsonify({"starred": cached, "from_cache": True})
    try:
        starred = repo_ops.list_starred(token)
        data = sorted(starred)
        cache_store.set(cache_key, data)
        return jsonify({"starred": data, "from_cache": False})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/toggle_star", methods=["POST"])
@login_required
def api_repo_toggle_star():
    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()
    repo = (data.get("repo") or "").strip()
    action = (data.get("action") or "").strip()
    if not owner or not repo or action not in ("star", "unstar"):
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400
    try:
        if action == "star":
            ok, msg = repo_ops.star_repo(token, owner, repo)
        else:
            ok, msg = repo_ops.unstar_repo(token, owner, repo)
        if ok:
            return jsonify({"ok": True, "starred": action == "star", "message": msg})
        return jsonify({"error": msg}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/starred_repos")
@login_required
def api_repo_starred_repos():
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400

    use_cache = request.args.get("use_cache", "1") != "0"
    cfg = store.get_config()
    owner = cfg.get("gh_owner") or cfg.get("github_username", "")
    cache_key = f"repos_starred_{owner}"

    if use_cache:
        cached = cache_store.get(cache_key)
        if cached is not None:
            return jsonify({"repos": cached, "from_cache": True})

    try:
        repos = repo_ops.list_starred_repos(token)
        cache_store.set(cache_key, repos)
        return jsonify({"repos": repos, "from_cache": False})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/upload_file", methods=["POST"])
@login_required
def api_repo_upload_file():
    owner = (request.form.get("owner") or "").strip()
    repo = (request.form.get("repo") or "").strip()
    dir_path = (request.form.get("dir") or "").strip().strip("/")
    overwrite = request.form.get("overwrite") == "1"
    commit_msg = (request.form.get("message") or "").strip()

    if not owner or not repo:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "没有选择文件"}), 400

    results = []
    MAX = 25 * 1024 * 1024  # 25 MB

    for f in files:
        fname = f.filename or ""
        if not fname:
            continue
        fname = fname.replace("\\", "/").split("/")[-1]
        if not fname:
            continue

        data = f.read()
        size = len(data)

        if size > MAX:
            results.append({
                "name": fname, "ok": False,
                "msg": f"文件 {size/1024/1024:.1f} MB 超过 25MB 上限，已跳过"
            })
            continue

        path = (dir_path + "/" + fname) if dir_path else fname

        # 检查是否已存在
        existing_sha = ""
        try:
            rc, out, err = repo_ops._gh(token, ["api", f"/repos/{owner}/{repo}/contents/{path}"],
                                        check=False)
            if rc == 0 and out.strip():
                try:
                    d = json.loads(out)
                    existing_sha = d.get("sha", "")
                except Exception:
                    pass
        except Exception:
            pass

        if existing_sha and not overwrite:
            results.append({
                "name": fname, "ok": False, "skipped": True,
                "msg": "已存在，跳过"
            })
            continue

        msg = commit_msg or (f"upload {fname}" if not existing_sha else f"update {fname}")
        try:
            repo_ops.upload_file_binary(token, owner, repo, path, data, msg, existing_sha or None)
            results.append({
                "name": fname, "ok": True, "path": path,
                "msg": "已更新" if existing_sha else "已上传"
            })
        except Exception as e:
            results.append({"name": fname, "ok": False, "msg": str(e)[:200]})

    return jsonify({"results": results})


@app.route("/api/repos/delete_file", methods=["POST"])
@login_required
def api_repo_delete_file():
    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()
    repo = (data.get("repo") or "").strip()
    path = (data.get("path") or "").strip()
    sha = (data.get("sha") or "").strip()

    if not owner or not repo or not path:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400

    try:
        result = repo_ops.delete_file(token, owner, repo, path, f"delete {path}", sha or None)
        commit = (result.get("commit") or {}) if isinstance(result, dict) else {}
        return jsonify({
            "ok": True,
            "commit_sha": commit.get("sha", ""),
            "commit_url": commit.get("html_url", ""),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/repos/contents")
@login_required
def api_repo_contents():
    owner = (request.args.get("owner") or "").strip()
    repo = (request.args.get("repo") or "").strip()
    path = (request.args.get("path") or "").strip()
    if not owner or not repo:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400
    cfg = store.get_config()
    accel_base = (cfg.get("release_accel_url") or "").rstrip("/")
    try:
        items = repo_ops.list_contents(token, owner, repo, path)
        return jsonify({"owner": owner, "repo": repo, "path": path,
                        "items": items, "accel_base": accel_base})
    except Exception as e:
        msg = str(e)
        if "empty" in msg.lower() or "HTTP 404" in msg:
            return jsonify({"empty_repo": True, "owner": owner,
                            "repo": repo, "path": path, "items": [],
                            "accel_base": accel_base})
        return jsonify({"error": msg}), 500


@app.route("/api/repos/file")
@login_required
def api_repo_file():
    owner = (request.args.get("owner") or "").strip()
    repo = (request.args.get("repo") or "").strip()
    path = (request.args.get("path") or "").strip()
    if not owner or not repo or not path:
        return jsonify({"error": "缺少参数"}), 400
    token = store.get_token()
    if not token:
        return jsonify({"error": "未配置 Token"}), 400
    try:
        data = repo_ops.get_file(token, owner, repo, path)
        return jsonify(data)
    except Exception as e:
        msg = str(e)
        if "empty" in msg.lower() or "HTTP 404" in msg:
            return jsonify({"empty_repo": True})
        return jsonify({"error": msg}), 500


@app.route("/api/cleanup", methods=["POST"])
@login_required
def api_cleanup():
    running_ids = {t["id"] for t in taskmod.list_tasks()
                   if t["status"] in ("running", "pending")}
    removed = 0
    freed = 0
    for d in UPLOAD_DIR.iterdir():
        if not d.is_dir():
            continue
        if d.name in running_ids:
            continue
        for f in d.rglob("*"):
            if f.is_file():
                try:
                    freed += f.stat().st_size
                except Exception:
                    pass
        shutil.rmtree(d, ignore_errors=True)
        removed += 1
    return jsonify({"removed": removed, "freed_mb": round(freed / 1024 / 1024, 2)})
