"""根据仓库文件结构生成 README.md 草稿"""
from datetime import datetime

LANG_SIGNS = [
    ("Node.js", ["package.json", "yarn.lock", "pnpm-lock.yaml"]),
    ("Python", ["requirements.txt", "setup.py", "pyproject.toml", "Pipfile"]),
    ("Go", ["go.mod", "go.sum"]),
    ("Java", ["pom.xml", "build.gradle", "build.gradle.kts"]),
    ("Rust", ["Cargo.toml"]),
    ("PHP", ["composer.json"]),
    ("Ruby", ["Gemfile"]),
    ("C#/.NET", ["*.csproj", "*.sln"]),
    ("C/C++", ["CMakeLists.txt", "Makefile"]),
]

ENTRY_FILES = [
    "main.py", "app.py", "run.py", "server.py", "manage.py", "__main__.py",
    "index.js", "app.js", "server.js", "main.js", "index.ts", "main.ts",
    "main.go", "main.rs", "Main.java", "index.php",
]

INSTALL_HINTS = {
    "Node.js": "npm install",
    "Python": "pip install -r requirements.txt",
    "Go": "go mod download",
    "Java": "mvn install",
    "Rust": "cargo build",
    "PHP": "composer install",
    "Ruby": "bundle install",
}

RUN_HINTS = {
    "Node.js": "npm start",
    "Python": "python main.py",
    "Go": "go run .",
    "Java": "mvn spring-boot:run",
    "Rust": "cargo run",
    "PHP": "php -S localhost:8000",
    "Ruby": "ruby main.rb",
}


def detect_lang(files):
    names = {f.lower() for f in files}
    for lang, signs in LANG_SIGNS:
        for s in signs:
            if s.startswith("*."):
                ext = s[1:]
                if any(n.endswith(ext) for n in names):
                    return lang
            elif s.lower() in names:
                return lang
    # 靠文件扩展名兜底
    ext_map = {
        ".py": "Python", ".js": "Node.js", ".ts": "Node.js",
        ".go": "Go", ".rs": "Rust", ".java": "Java",
        ".php": "PHP", ".rb": "Ruby", ".cs": "C#/.NET",
        ".c": "C/C++", ".cpp": "C/C++",
    }
    for f in files:
        for ext, lang in ext_map.items():
            if f.lower().endswith(ext):
                return lang
    return ""


def has_dir(files, d):
    d = d.lower()
    return any(f.lower() == d or f.lower().startswith(d + "/") for f in files)


def has_file(files, name):
    name = name.lower()
    return any(f.lower() == name for f in files)


def pick_entry(files):
    names_lower = {f.lower(): f for f in files}
    for e in ENTRY_FILES:
        if e.lower() in names_lower:
            return names_lower[e.lower()]
    return ""


def detect_license(files):
    for f in files:
        if f.lower() == "license" or f.lower().startswith("license."):
            return f
    return ""


def generate(repo_name, owner, description, files, default_branch="main"):
    """返回 markdown 字符串"""
    lang = detect_lang(files)
    entry = pick_entry(files)
    lic = detect_license(files)
    has_docker = has_file(files, "Dockerfile") or has_file(files, "docker-compose.yml")
    has_tests = has_dir(files, "tests") or has_dir(files, "test") or has_dir(files, "__tests__")
    has_gha = has_dir(files, ".github")
    has_gitignore = has_file(files, ".gitignore")
    has_pages = has_file(files, "index.html")

    lines = []
    lines.append(f"# {repo_name}")
    lines.append("")
    if description:
        lines.append(description)
        lines.append("")
    else:
        lines.append("> 一句话介绍这个项目。")
        lines.append("")

    badges = []
    if lang:
        badges.append(f"![语言](https://img.shields.io/badge/语言-{lang.replace(' ', '%20').replace('#', '%23').replace('/', '%2F')}-blue)")
    if lic:
        badges.append(f"![License](https://img.shields.io/badge/license-{lic}-green)")
    if has_docker:
        badges.append("![Docker](https://img.shields.io/badge/Docker-支持-2496ED)")
    if has_gha:
        badges.append("![Actions](https://img.shields.io/badge/CI-GitHub%20Actions-2088FF)")
    if badges:
        lines.append(" ".join(badges))
        lines.append("")

    # 特性
    lines.append("## ✨ 特性")
    lines.append("")
    feats = []
    if lang:
        feats.append(f"基于 **{lang}** 构建")
    if has_docker:
        feats.append("支持 **Docker** 一键部署")
    if has_gha:
        feats.append("集成 **GitHub Actions** 自动构建 / 测试")
    if has_pages:
        feats.append("带静态页面（GitHub Pages 可托管）")
    if has_tests:
        feats.append("包含单元测试")
    if not feats:
        feats.append("待补充")
    for f in feats:
        lines.append(f"- {f}")
    lines.append("")

    # 目录结构（只列根目录文件）
    lines.append("## 📁 目录结构")
    lines.append("")
    lines.append("```")
    lines.append(f"{repo_name}/")
    shown = [f for f in files if "/" not in f][:15]
    shown.sort()
    for i, f in enumerate(shown):
        prefix = "└── " if i == len(shown) - 1 else "├── "
        lines.append(prefix + f)
    if len(files) > len(shown):
        lines.append("└── ...")
    lines.append("```")
    lines.append("")

    # 安装
    if lang and lang in INSTALL_HINTS:
        lines.append("## 🚀 快速开始")
        lines.append("")
        lines.append("### 环境要求")
        lines.append("")
        lines.append(f"- {lang}")
        if has_docker:
            lines.append("- Docker / Docker Compose（可选）")
        lines.append("")
        lines.append("### 安装依赖")
        lines.append("")
        lines.append("```bash")
        lines.append(f"git clone https://github.com/{owner}/{repo_name}.git")
        lines.append(f"cd {repo_name}")
        lines.append(INSTALL_HINTS[lang])
        lines.append("```")
        lines.append("")
        if lang in RUN_HINTS:
            lines.append("### 运行")
            lines.append("")
            lines.append("```bash")
            lines.append(RUN_HINTS[lang])
            lines.append("```")
            lines.append("")
        if has_docker:
            lines.append("### Docker 部署")
            lines.append("")
            lines.append("```bash")
            lines.append("docker compose up -d")
            lines.append("```")
            lines.append("")

    # 使用
    lines.append("## 📖 使用")
    lines.append("")
    if entry:
        lines.append(f"入口文件：`{entry}`")
    else:
        lines.append("详细用法见下方文档 / 代码注释。")
    lines.append("")
    lines.append("```")
    lines.append("（在这里补充使用示例）")
    lines.append("```")
    lines.append("")

    # 测试
    if has_tests:
        lines.append("## 🧪 测试")
        lines.append("")
        lines.append("```bash")
        if lang == "Python":
            lines.append("pytest")
        elif lang == "Node.js":
            lines.append("npm test")
        elif lang == "Go":
            lines.append("go test ./...")
        elif lang == "Rust":
            lines.append("cargo test")
        else:
            lines.append("# 运行测试命令")
        lines.append("```")
        lines.append("")

    # 贡献
    lines.append("## 🤝 贡献")
    lines.append("")
    lines.append("欢迎提交 Issue 和 Pull Request。")
    lines.append("")

    # License
    lines.append("## 📄 License")
    lines.append("")
    if lic:
        lines.append(f"本项目采用 [{lic}]({lic}) 授权。")
    else:
        lines.append("本项目尚未声明 License。")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"_README 由 GitHub 发布工具于 {datetime.now().strftime('%Y-%m-%d')} 生成，请按需修改。_")

    return "\n".join(lines)
