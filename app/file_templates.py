"""发布模板：.gitignore / LICENSE / GitHub Actions"""

GITIGNORE = {
    "none": "",
    "generic": """# 通用
*.log
*.tmp
*.bak
.DS_Store
Thumbs.db
.idea/
.vscode/
""",
    "node": """# Node
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*
.pnpm-debug.log*
dist/
build/
.next/
.nuxt/
.cache/
.env
.env.local
""",
    "python": """# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
dist/
*.egg-info/
.venv/
venv/
env/
.env
.pytest_cache/
.mypy_cache/
.ruff_cache/
""",
    "go": """# Go
*.exe
*.exe~
*.dll
*.so
*.dylib
*.test
*.out
vendor/
go.work
""",
    "java": """# Java
*.class
*.jar
*.war
target/
.gradle/
build/
.idea/
*.iml
""",
    "docker": """# Docker
*.log
.env
docker-compose.override.yml
""",
}

LICENSE_MIT = """MIT License

Copyright (c) {year} {author}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

LICENSE_APACHE = """                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

Copyright {year} {author}

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

LICENSE_GPL3 = """                    GNU GENERAL PUBLIC LICENSE
                       Version 3, 29 June 2007

Copyright (C) {year} {author}

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

LICENSES = {
    "none": ("", ""),
    "mit": ("LICENSE", LICENSE_MIT),
    "apache": ("LICENSE", LICENSE_APACHE),
    "gpl3": ("LICENSE", LICENSE_GPL3),
}

ACTIONS_DOCKER = """name: Build Docker Image

on:
  push:
    branches: [main]
    tags: ['v*']
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build
        run: |
          if [ -f Dockerfile ]; then
            docker build -t test-build .
          else
            echo "No Dockerfile, skip"
          fi
"""

ACTIONS_TEST_PYTHON = """name: Python Test

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install
        run: |
          if [ -f requirements.txt ]; then
            pip install -r requirements.txt
          fi
      - name: Test
        run: |
          if [ -f pytest.ini ] || [ -d tests ]; then
            pip install pytest
            pytest -v
          else
            echo "No tests found"
          fi
"""

ACTIONS_TEST_NODE = """name: Node Test

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - name: Install
        run: |
          if [ -f package.json ]; then
            npm ci || npm install
          fi
      - name: Test
        run: |
          if [ -f package.json ]; then
            npm test --if-present
          fi
"""

ACTIONS_PUSH_GHCR = """name: Build and Push Docker Image

on:
  push:
    branches: [main]
    tags: ['v*']
  workflow_dispatch:

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=ref,event=branch
            type=ref,event=tag
            type=raw,value=latest,enable={{is_default_branch}}

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
"""

ACTIONS = {
    "none": None,
    "docker": ("docker-build.yml", ACTIONS_DOCKER),
    "test-python": ("python-test.yml", ACTIONS_TEST_PYTHON),
    "test-node": ("node-test.yml", ACTIONS_TEST_NODE),
    "push-ghcr": ("docker-publish.yml", ACTIONS_PUSH_GHCR),
}


def apply_templates(src_root, gitignore="none", license_="none",
                    actions="none", author="", year=None):
    """把模板文件写入源码根目录，返回写入的文件名列表"""
    import os
    from datetime import datetime

    written = []

    # .gitignore
    gi = GITIGNORE.get(gitignore, "")
    if gi:
        path = os.path.join(src_root, ".gitignore")
        with open(path, "w", encoding="utf-8") as f:
            f.write(gi)
        written.append(".gitignore")

    # LICENSE
    lic = LICENSES.get(license_)
    if lic and lic[1]:
        name, content = lic
        if year is None:
            year = datetime.now().year
        path = os.path.join(src_root, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content.format(year=year, author=author or "Unknown"))
        written.append(name)

    # GitHub Actions
    act = ACTIONS.get(actions)
    if act:
        name, content = act
        wf_dir = os.path.join(src_root, ".github", "workflows")
        os.makedirs(wf_dir, exist_ok=True)
        path = os.path.join(wf_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        written.append(f".github/workflows/{name}")

    return written
