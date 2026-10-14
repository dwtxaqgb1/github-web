#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

echo ""
echo "==============================================="
echo "  GitHub 发布工具 - 安装向导"
echo "==============================================="
echo ""

# 读取已有 .env 作为默认值
DEFAULT_PORT=5110
DEFAULT_DATA="$(pwd)/data"
DEFAULT_WORK="$(pwd)/work"
DEFAULT_PASS=admin123
DEFAULT_EXTRA=""

if [ -f .env ]; then
  set -a
  . ./.env 2>/dev/null || true
  set +a
  DEFAULT_PORT=${HOST_PORT:-$DEFAULT_PORT}
  DEFAULT_DATA=${NAS_DATA_DIR:-$DEFAULT_DATA}
  DEFAULT_WORK=${NAS_WORK_DIR:-$DEFAULT_WORK}
  DEFAULT_PASS=${ADMIN_PASSWORD:-$DEFAULT_PASS}
  DEFAULT_EXTRA=${NAS_EXTRA_DIR:-$DEFAULT_EXTRA}
fi

# 检测是否已有部署
EXISTING=0
if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q '^gh-web$'; then
  EXISTING=1
fi
if [ -f .env ]; then
  EXISTING=1
fi

# 菜单
if [ "$EXISTING" = "1" ]; then
  echo "检测到已安装的版本："
  docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q '^gh-web$' && echo "  ✓ 存在容器 gh-web"
  [ -f .env ] && echo "  ✓ 存在配置文件 .env"
  [ -d "$DEFAULT_DATA" ] && echo "  ✓ 存在数据目录 $DEFAULT_DATA"
  echo ""
  echo "请选择操作："
  echo "  1) 全新安装（删除旧容器和所有数据，重新配置）"
  echo "  2) 更新安装（保留数据和配置，只重新构建镜像）"
  echo "  3) 取消"
  read -p "请输入选项 [默认 1]: " MODE
  MODE=${MODE:-1}
else
  MODE=1
  echo "未检测到已安装版本，将进行全新安装。"
  echo ""
fi

if [ "$MODE" = "3" ]; then
  echo "已取消"
  exit 0
fi

# ---- 更新安装 ----
if [ "$MODE" = "2" ]; then
  if [ ! -f .env ]; then
    echo "❌ 更新模式需要已有的 .env 配置文件，未找到。请改用全新安装。"
    exit 1
  fi
  echo ""
  echo "更新安装：保留 .env 和 data/ 目录"
  echo "重新构建镜像并重启容器..."
  docker compose down 2>/dev/null || true
  docker compose up -d --build
  PORT=$(grep '^HOST_PORT=' .env | cut -d= -f2)
  echo ""
  echo "✅ 更新完成！"
  echo "   访问：http://<NAS_IP>:${PORT:-5110}"
  exit 0
fi

# ---- 全新安装 ----
if [ "$EXISTING" = "1" ]; then
  echo ""
  echo "⚠️  全新安装会执行以下操作："
  echo "   1. 停止并删除容器 gh-web"
  echo "   2. 清空数据目录（Token / SSH 密钥 / 密码 都会丢失）"
  echo "   3. 清空工作目录（上传和克隆的临时文件）"
  echo ""
  read -p "确认继续？(y/N) " CONFIRM
  if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
    echo "已取消"
    exit 0
  fi

  echo ""
  echo "正在清理旧部署..."
  docker compose down -v 2>/dev/null || true
  docker rm -f gh-web 2>/dev/null || true
  if [ -d "$DEFAULT_DATA" ]; then
    echo "  清空数据目录: $DEFAULT_DATA"
    rm -rf "${DEFAULT_DATA:?}"/* "${DEFAULT_DATA:?}"/.[!.]* 2>/dev/null || true
  fi
  if [ -d "$DEFAULT_WORK" ]; then
    echo "  清空工作目录: $DEFAULT_WORK"
    rm -rf "${DEFAULT_WORK:?}"/* "${DEFAULT_WORK:?}"/.[!.]* 2>/dev/null || true
  fi
  echo "  ✓ 清理完成"
  echo ""
fi

# 询问新配置
echo "请配置部署参数（直接回车用默认值）："
echo ""
read -p "Web 端口 [$DEFAULT_PORT]: " HOST_PORT
HOST_PORT=${HOST_PORT:-$DEFAULT_PORT}

read -p "数据目录（保存配置/Token/密钥）[$DEFAULT_DATA]: " NAS_DATA_DIR
NAS_DATA_DIR=${NAS_DATA_DIR:-$DEFAULT_DATA}

read -p "工作目录（上传暂存/克隆）[$DEFAULT_WORK]: " NAS_WORK_DIR
NAS_WORK_DIR=${NAS_WORK_DIR:-$DEFAULT_WORK}

echo ""
echo "可选：额外挂载一个 NAS 目录到容器里的 /nas"
echo "  用于把仓库克隆到 NAS 任意位置"
read -p "额外挂载目录（回车跳过）[$DEFAULT_EXTRA]: " NAS_EXTRA_DIR
NAS_EXTRA_DIR=${NAS_EXTRA_DIR:-$DEFAULT_EXTRA}

read -p "登录密码 [$DEFAULT_PASS]: " ADMIN_PASSWORD
ADMIN_PASSWORD=${ADMIN_PASSWORD:-$DEFAULT_PASS}

mkdir -p "$NAS_DATA_DIR" "$NAS_WORK_DIR"
[ -n "$NAS_EXTRA_DIR" ] && mkdir -p "$NAS_EXTRA_DIR"

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

if ! command -v docker >/dev/null 2>&1; then
  echo "❌ 未检测到 docker 命令，请先安装 Docker"
  exit 1
fi

echo ""
read -p "现在构建并启动容器？(Y/n) " GO
GO=${GO:-Y}
if [ "$GO" = "Y" ] || [ "$GO" = "y" ]; then
  docker compose down 2>/dev/null || true
  docker compose up -d --build
  echo ""
  echo "✅ 安装完成！"
  echo "   访问：http://<NAS_IP>:$HOST_PORT"
  echo "   登录密码：$ADMIN_PASSWORD"
else
  echo ""
  echo "已跳过启动。稍后执行："
  echo "  docker compose up -d --build"
fi
