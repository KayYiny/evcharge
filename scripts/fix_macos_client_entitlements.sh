#!/usr/bin/env bash
# 修复打包版「相册识别」报 ENTITLEMENT_NOT_FOUND 的问题。
#
# 背景：`flet pack` 会把内置 Flet 客户端改名/换图标后重新 ad-hoc 签名，
# 但没带 --entitlements，于是 `com.apple.security.files.user-selected.read-write`
# 丢失，系统文件选择器直接拒绝：
#   PlatformException(ENTITLEMENT_NOT_FOUND, Either the Read-Only or Read-Write
#   entitlement is required for this action.)
#
# 本脚本对「本次构建产出的那份客户端缓存」重新签名，补回权限。
# 构建脚本 build_macos.sh 已自动调用；如果手动清过 ~/.flet/client 缓存，
# 重新打包一次即可（或单独再跑一次本脚本）。
#
# 用法：
#   ./scripts/fix_macos_client_entitlements.sh [版本号]
#   版本号省略时自动读取 .venv/.venv-build 里的 flet 版本。

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PLIST="$ROOT/assets/macos_entitlements.plist"   # 与运行时修复共用同一份

detect_version() {
  if [ -n "${1:-}" ]; then
    echo "$1"
    return
  fi
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

VERSION="$(detect_version "${1:-}")"
if [ -z "$VERSION" ]; then
  echo "❌ 无法确定 Flet 版本，请显式传入，例如：$0 1.1.1" >&2
  exit 1
fi
if [ ! -f "$PLIST" ]; then
  echo "❌ 找不到 $PLIST" >&2
  exit 1
fi

CLIENT_ROOT="$HOME/.flet/client"
if [ ! -d "$CLIENT_ROOT" ]; then
  echo "ℹ 没有 $CLIENT_ROOT（尚未运行过打包版），跳过"
  exit 0
fi

# flet pack 会在缓存里生成 flet-desktop-full-<版本>-<指纹>/ 目录，取最新那个
TARGETS=()
while IFS= read -r dir; do
  [ -n "$dir" ] && TARGETS+=("$dir")
done < <(ls -dt "$CLIENT_ROOT/flet-desktop-full-$VERSION"-*/ 2>/dev/null || true)

if [ "${#TARGETS[@]}" -eq 0 ]; then
  echo "ℹ 未找到 flet-desktop-full-$VERSION-* 的打包缓存，先运行一次打包版程序后重试"
  exit 0
fi

fixed=0
for dir in "${TARGETS[@]}"; do
  for app in "$dir"*.app; do
    [ -d "$app" ] || continue
    echo "==> 重新签名：$app"
    if codesign --force --deep --sign - --entitlements "$PLIST" "$app" 2>/dev/null; then
      got="$(codesign -d --entitlements :- "$app" 2>/dev/null | tr -d '\n' | grep -c 'files.user-selected.read-write' || true)"
      if [ "$got" -ge 1 ]; then
        echo "    ✅ 权限已补回（user-selected.read-write）"
        fixed=$((fixed + 1))
      else
        echo "    ⚠ 签名完成但未读到目标权限，请检查 $PLIST"
      fi
    else
      echo "    ❌ 签名失败（可能客户端正在运行，请先退出程序再试）"
    fi
  done
done

if [ "$fixed" -gt 0 ]; then
  echo "完成：已修复 $fixed 个客户端。重新打开程序后「相册识别」应可用。"
else
  echo "未做修改。若程序正在运行，请先完全退出（⌘Q）后重跑。"
fi
