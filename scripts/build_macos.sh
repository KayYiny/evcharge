#!/usr/bin/env bash
# 在 macOS 上把项目打成可双击运行的 .app 与 .dmg
#
# 用法：
#   ./scripts/build_macos.sh              # 打 .app + .dmg
#   ./scripts/build_macos.sh --app-only   # 只打 .app
#   ./scripts/build_macos.sh --lean       # 额外排除一批用不到的模块，包更小、启动略快
#   ./scripts/build_macos.sh --dry-run    # 只打印将要执行的命令，不真构建
#
# 启动慢的说明：macOS 上 `flet pack` 只支持 onefile 模式（它明确拒绝 --onedir），
# 每次启动都要把包内 Python 解压到临时目录，所以首次启动天然偏慢。
# 已做三项优化：启动页给出反馈、matplotlib 延迟导入、字体缓存落到数据目录复用。
# 若要彻底消除解包耗时，改用 `flet build macos`（需 Flutter/Xcode）。
#
# 产物：dist/充电监控.app、dist/充电监控-<版本>.dmg
#
# 说明：PyInstaller/flet pack 不是交叉编译器，macOS 包必须在 macOS 上打。

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
APP_NAME="充电监控"
BUILD_ENV=".venv-build"

APP_ONLY=0
DRY_RUN=0
LEAN=0
for arg in "$@"; do
  case "$arg" in
    --app-only) APP_ONLY=1 ;;
    --dry-run)  DRY_RUN=1 ;;
    --lean)     LEAN=1 ;;
    --onedir)   echo "⚠ macOS 上的 flet pack 不支持 --onedir（会直接报错），已忽略。" >&2 ;;
    -h|--help)  sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "未知参数：${arg}（可用：--app-only / --lean / --dry-run）" >&2; exit 2 ;;
  esac
done

VERSION="$(python3 -c 'import re,pathlib;print(re.search(r"version\s*=\s*\"([^\"]+)\"", pathlib.Path("pyproject.toml").read_text()).group(1))')"

echo "==> 项目：$ROOT"
echo "==> 版本：$VERSION"

# 0) 环境自检：macOS 默认 bash 3.2 对 set -u + 空数组有坑，这里提示一下
if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
  echo "==> 使用 bash ${BASH_VERSION}（macOS 自带 3.2，脚本已做兼容处理）"
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 找不到 python3，请先安装 Python 3.10+" >&2
  exit 1
fi

# 1) 构建用独立虚拟环境（不污染开发环境）
if [ ! -x "$BUILD_ENV/bin/python" ]; then
  echo "==> 创建构建虚拟环境 $BUILD_ENV"
  python3 -m venv "$BUILD_ENV"
fi
PY="$BUILD_ENV/bin/python"
FLET="$BUILD_ENV/bin/flet"

# 依赖安装对网络较敏感，放宽超时并允许重试
PIP_OPTS=(--timeout 60 --retries 5)

# 与开发环境用同一个 Flet 版本：这样 flet pack 会复用
# ~/.flet/client/flet-desktop-full-<版本>/ 里已缓存的桌面客户端，不必再下载上百 MB。
detect_flet_version() {
  if [ -n "${FLET_VERSION:-}" ]; then
    echo "$FLET_VERSION"
    return
  fi
  for env_dir in .venv .venv-build; do
    if [ -x "$env_dir/bin/python" ]; then
      local ver
      ver="$("$env_dir/bin/python" -c 'import flet;print(flet.__version__)' 2>/dev/null || true)"
      if [ -n "$ver" ]; then
        echo "$ver"
        return
      fi
    fi
  done
}

FLET_VER="$(detect_flet_version)"
if [ -n "$FLET_VER" ]; then
  echo "==> Flet 版本：${FLET_VER}（可用 FLET_VERSION=x.y.z 覆盖）"
else
  echo "==> 未检测到本地 Flet 版本，将安装依赖里声明的版本"
fi

install_deps() {
  echo "==> 安装项目依赖（网络慢时会自动重试）"
  "$PY" -m pip install --upgrade pip "${PIP_OPTS[@]}" >/dev/null
  if [ -n "$FLET_VER" ]; then
    "$PY" -m pip install -e . "flet==$FLET_VER" "${PIP_OPTS[@]}"
  else
    "$PY" -m pip install -e . "${PIP_OPTS[@]}"
  fi
  "$PY" -m pip install pyinstaller "${PIP_OPTS[@]}"
}

NEED_INSTALL=0
if [ ! -x "$FLET" ] || ! "$PY" -c "import evcharge" >/dev/null 2>&1; then
  NEED_INSTALL=1
elif [ -n "$FLET_VER" ] && [ "$("$PY" -c 'import flet;print(flet.__version__)' 2>/dev/null)" != "$FLET_VER" ]; then
  echo "==> 构建环境里的 Flet 版本与开发环境不一致，需要对齐"
  NEED_INSTALL=1
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "==> [dry-run] 跳过依赖安装"
elif [ "$NEED_INSTALL" -eq 1 ]; then
  install_deps
else
  echo "==> 依赖已就绪，跳过安装"
fi

# flet pack 打包时需要 Flet 桌面客户端；已缓存就不用联网下载
if [ -n "$FLET_VER" ] && [ -d "$HOME/.flet/client/flet-desktop-full-$FLET_VER" ]; then
  echo "==> 复用已缓存的桌面客户端：~/.flet/client/flet-desktop-full-$FLET_VER"
else
  echo "==> 首次构建需下载 Flet 桌面客户端（较大），请保持网络畅通"
  echo "    若下载失败：把 FLET_VERSION 设成你本机已缓存客户端的版本号后重试"
fi

if [ ! -x "$FLET" ]; then
  echo "❌ 构建环境里没有 flet 命令，请删掉 $BUILD_ENV 后重试" >&2
  exit 1
fi

# 2) 图标：优先用 assets/icon.icns；只有 icon.png 时先用它生成 icns
ICON_ARGS=()
if [ ! -f "assets/icon.icns" ] && [ -f "assets/icon.png" ]; then
  if [ "$DRY_RUN" -eq 0 ]; then
    echo "==> 由 assets/icon.png 生成 macOS 图标"
    "$PY" scripts/make_icon.py || true
  fi
fi
if [ -f "assets/icon.icns" ]; then
  ICON_ARGS=(--icon "assets/icon.icns")
  echo "==> 使用图标：assets/icon.icns"
else
  echo "==> 没有可用图标（assets/icon.icns 或 icon.png），将使用 Flet 默认图标"
fi

# 3) 打包：.app 内含 Python 运行时，目标机器无需装 Python
PACK_ARGS=(
  pack "src/evcharge/cli.py"
  --name "$APP_NAME"
  --product-name "$APP_NAME"
  --product-version "$VERSION"
  --bundle-id "com.example.evcharge"
  --add-data "assets:assets"
  --hidden-import "matplotlib.backends.backend_agg"
  --hidden-import "evcharge.presentation.pages.login_page"
  --yes
)

# 可选：排除一批本项目用不到的模块，减小体积、缩短 onefile 解包时间
if [ "$LEAN" -eq 1 ]; then
  # 只排除明确用不到的 GUI/交互环境；unittest、sqlite3 等仍可能被依赖，不要动
  for module in tkinter matplotlib.backends.backend_qt5agg \
                matplotlib.backends.backend_tkagg IPython jupyter; do
    PACK_ARGS+=("--pyinstaller-build-args=--exclude-module=$module")
  done
fi
# 注意：bash 3.2 在 set -u 下展开空数组会报 unbound variable，
# 必须写成 ${ARR[@]+"${ARR[@]}"} 这种「存在才展开」的形式。
if [ "${#ICON_ARGS[@]}" -gt 0 ]; then
  PACK_ARGS+=("${ICON_ARGS[@]}")
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "==> [dry-run] 将执行："
  echo "    $FLET ${PACK_ARGS[*]}"
  exit 0
fi

# PyInstaller 默认把缓存写到 ~/Library/Application Support（某些环境不可写），
# 统一改到项目内，构建可复现、也不污染用户目录。
export PYINSTALLER_CONFIG_DIR="$ROOT/build/pyinstaller"
mkdir -p "$PYINSTALLER_CONFIG_DIR"

echo "==> flet pack 开始（首次较慢，需要编译依赖）"
"$FLET" "${PACK_ARGS[@]}"

# 3.1) flet pack 不会把版本写进 Info.plist（默认 0.0.0），这里补一下，
#      让「关于本程序」显示正确版本号
APP_PLIST="dist/${APP_NAME}.app/Contents/Info.plist"
if [ -f "$APP_PLIST" ]; then
  plutil -replace CFBundleShortVersionString -string "$VERSION" "$APP_PLIST" 2>/dev/null \
    && echo "==> 已写入版本号：$VERSION"
  plutil -replace CFBundleVersion -string "$VERSION" "$APP_PLIST" 2>/dev/null || true
fi

# 4) 打 DMG（拖进「应用程序」即可安装）
# 新系统推荐 `diskutil image create from`；老系统退回 hdiutil。
# 失败不算构建失败：.app 已经可用，这里只给出替代方案。
if [ "$APP_ONLY" -eq 0 ]; then
  DMG_PATH="dist/${APP_NAME}-${VERSION}.dmg"
  rm -rf "$DMG_PATH"
  STAGE_DIR="build/dmg"
  rm -rf "$STAGE_DIR"
  mkdir -p "$STAGE_DIR"
  cp -R "dist/${APP_NAME}.app" "$STAGE_DIR/"
  ln -sf /Applications "$STAGE_DIR/Applications"

  echo "==> 生成 DMG"
  DMG_OK=0
  if diskutil image create from --format UDZO --volumeName "${APP_NAME}" \
       "$STAGE_DIR" "$DMG_PATH" >/dev/null 2>&1; then
    DMG_OK=1
  elif hdiutil create -volname "${APP_NAME}" -srcfolder "$STAGE_DIR" \
         -ov -format UDZO "$DMG_PATH" >/dev/null 2>&1; then
    DMG_OK=1
  fi

  if [ "$DMG_OK" -eq 1 ] && [ -f "$DMG_PATH" ]; then
    echo "==> DMG 完成：$DMG_PATH"
  else
    echo "⚠ DMG 生成失败（不影响 .app 使用）。可以手动执行："
    echo "    mkdir -p build/dmg && cp -R \"dist/${APP_NAME}.app\" build/dmg/"
    echo "    hdiutil create -volname \"${APP_NAME}\" -srcfolder build/dmg -ov -format UDZO \"$DMG_PATH\""
    echo "  或安装 create-dmg 后：brew install create-dmg && create-dmg \"dist/${APP_NAME}.app\" dist"
  fi
fi

echo
echo "完成："
ls -lh dist | sed 's/^/  /'
echo
echo "提示：未签名的 .app 在别人电脑上可能被 Gatekeeper 拦；"
echo "      本机可执行：xattr -cr \"dist/${APP_NAME}.app\" 再打开。"
