#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

echo ""
echo "==============================================="
echo "  GitHub 发布工具 - 安装向导"
echo "==============================================="
echo ""

# 1. 端口
read -p "Web 端口 [5110]: " HOST_PORT
HOST_PORT=${HOST_PORT:-5110}

# 2. 数据目录
DEFAULT_DATA="$(pwd)/data"
read -p "数据目录（保存配置/Token/密钥） [$DEFAULT_DATA]: " NAS_DATA_DIR
NAS_DATA_DIR=${NAS_DATA_DIR:-$DEFAULT_DATA}

# 3. 工作目录
DEFAULT_WORK="$(pwd)/work"
read -p "工作目录（上传暂存/克隆） [$DEFAULT_WORK]: " NAS_WORK_DIR
NAS_WORK_DIR=${NAS_WORK_DIR:-$DEFAULT_WORK}

# 4. 额外挂载（可选）
echo ""
echo "可选：额外挂载一个 NAS 目录到容器里的 /nas"
echo "  - 填了之后，克隆功能可以往任意 NAS 路径写文件"
echo "  - 例如填 /vol1/1000/我的文件，网页上就能填 /nas/xxx/yyy"
echo "  - 直接回车跳过（只用 /work）"
read -p "额外挂载目录: " NAS_EXTRA_DIR

# 5. 密码
read -p "登录密码 [admin123]: " ADMIN_PASSWORD
ADMIN_PASSWORD=${ADMIN_PASSWORD:-admin123}

# 6. 创建目录
mkdir -p "$NAS_DATA_DIR" "$NAS_WORK_DIR"
[ -n "$NAS_EXTRA_DIR" ] && mkdir -p "$NAS_EXTRA_DIR"

# 7. 写 .env
cat > .env << ENVEOF
HOST_PORT=$HOST_PORT
NAS_DATA_DIR=$NAS_DATA_DIR
NAS_WORK_DIR=$NAS_WORK_DIR
NAS_EXTRA_DIR=$NAS_EXTRA_DIR
ADMIN_PASSWORD=$ADMIN_PASSWORD
ENVEOF

echo ""
echo "✅ 已生成 .env："
echo "---"
cat .env
echo "---"

# 8. 检查 docker
if ! command -v docker >/dev/null 2>&1; then
  echo "❌ 未检测到 docker 命令，请先安装 Docker"
  exit 1
fi

# 9. 构建启动
echo ""
read -p "现在构建并启动容器？(Y/n) " GO
GO=${GO:-Y}
if [ "$GO" = "Y" ] || [ "$GO" = "y" ]; then
  docker compose down 2>/dev/null || true
  docker compose up -d --build --force-recreate
  echo ""
  echo "✅ 完成！"
  echo "   访问：http://<NAS_IP>:$HOST_PORT"
  echo "   初始密码：$ADMIN_PASSWORD"
else
  echo ""
  echo "已跳过启动。稍后执行以下命令启动："
  echo "  docker compose up -d --build --force-recreate"
fi
