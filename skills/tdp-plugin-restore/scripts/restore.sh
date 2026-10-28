#!/usr/bin/env bash
# TDP 插件恢复：U 盘 -> 本地 tdp-scripts。
# 用法: restore.sh [--dry-run] <plugin>|--all
set -euo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DRY=0
ALL=0
PLUGIN=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --all) ALL=1 ;;
    -h|--help) echo "usage: restore.sh [--dry-run] <plugin>|--all"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) [ -z "$PLUGIN" ] && PLUGIN="$arg" || { echo "too many arguments" >&2; exit 2; } ;;
  esac
done
if [ "$ALL" = 1 ] && [ -n "$PLUGIN" ]; then
  echo "--all 与插件名不能同时使用" >&2; exit 2
fi
if [ "$ALL" = 0 ] && [ -z "$PLUGIN" ]; then
  echo "usage: restore.sh [--dry-run] <plugin>|--all" >&2; exit 2
fi

valid_name() { case "$1" in ''|*[!A-Za-z0-9._+-]*) return 1 ;; esac; }

# <name>_<version>_<os>_... -> <name>；版本以数字开头，名字里可以有下划线
derive_name() {
  local base="$1"
  [[ "$base" =~ ^(.+?)_[0-9][^_]*_ ]] && printf '%s\n' "${BASH_REMATCH[1]}"
}

list_usb_plugins() {
  local d f name
  {
    for d in "$USB_ROOT"/origin/*/; do
      name="$(basename -- "$d")"
      valid_name "$name" && printf '%s\n' "$name"
    done
    for f in "$USB_ROOT"/plugin/* "$USB_ROOT"/bin/*; do
      [ -f "$f" ] || continue
      name="$(derive_name "$(basename -- "$f")")" || continue
      [ -n "$name" ] && valid_name "$name" && printf '%s\n' "$name"
    done
  } | sort -u
}

copy_section() {
  local kind="$1" plugin="$2"
  local files=("$USB_ROOT/$kind/${plugin}_"*)
  [ ${#files[@]} -gt 0 ] || return 1
  local dstdir="$LOCAL_ROOT/$kind"
  printf '  %s -> %s (%d files)\n' "$kind" "$dstdir" "${#files[@]}"
  if [ "$DRY" = 0 ]; then
    mkdir -p -- "$dstdir"
    local f
    for f in "${files[@]}"; do
      cp -p -- "$f" "$dstdir/.tdpnew-$(basename -- "$f")"
    done
    for f in "${files[@]}"; do
      cmp -s -- "$f" "$dstdir/.tdpnew-$(basename -- "$f")" \
        || { echo "verify failed: $f" >&2; return 1; }
    done
    find "$dstdir" -maxdepth 1 -type f -name "${plugin}_*" -delete
    for f in "${files[@]}"; do
      mv -f -- "$dstdir/.tdpnew-$(basename -- "$f")" "$dstdir/$(basename -- "$f")"
    done
  fi
  return 0
}

restore_one() {
  local plugin="$1" found=0 origin_n=0 plugin_n=0 bin_n=0
  printf 'plugin %s\n' "$plugin"

  local src="$USB_ROOT/origin/$plugin"
  if [ -d "$src" ]; then
    found=1
    local dst="$LOCAL_ROOT/origin/$plugin"
    printf '  origin -> %s\n' "$dst"
    if [ "$DRY" = 0 ]; then
      mkdir -p -- "$LOCAL_ROOT/origin"
      local tmp="$dst.new"
      rm -rf -- "$tmp"
      cp -r -- "$src" "$tmp"
      diff -r -- "$src" "$tmp" >/dev/null || { echo "verify failed: origin" >&2; return 1; }
      rm -rf -- "$dst"
      mv -- "$tmp" "$dst"
    fi
    origin_n=$(find "$src" -type f | wc -l)
  fi

  local kind
  for kind in plugin bin; do
    if copy_section "$kind" "$plugin"; then
      found=1
      if [ "$kind" = plugin ]; then
        plugin_n=$(ls -1 "$USB_ROOT/plugin/${plugin}_"* 2>/dev/null | wc -l)
      else
        bin_n=$(ls -1 "$USB_ROOT/bin/${plugin}_"* 2>/dev/null | wc -l)
      fi
    fi
  done

  [ "$found" = 1 ] || { echo "  U 盘上没有这个插件的文件" >&2; return 1; }
  printf '  完成：origin %s，plugin %s，bin %s\n' "$origin_n" "$plugin_n" "$bin_n"
}

LOCAL_ROOT="${TDP_LOCAL_ROOT:-$HOME/space/tdp-scripts}"
USB_ROOT="$("$SCRIPT_DIR/usb-root.sh")" || exit 1
printf 'usb root  : %s\nlocal root: %s\n' "$USB_ROOT" "$LOCAL_ROOT"
[ "$DRY" = 1 ] && echo "dry-run   : 不写入"

if [ "$ALL" = 1 ]; then
  mapfile -t plugins < <(list_usb_plugins)
  [ ${#plugins[@]} -gt 0 ] || { echo "U 盘上没有找到任何插件" >&2; exit 1; }
  printf '插件数    : %d\n' "${#plugins[@]}"
  failed=0
  for plugin in "${plugins[@]}"; do
    restore_one "$plugin" || { echo "  恢复失败: $plugin" >&2; failed=$((failed+1)); }
  done
  [ "$DRY" = 0 ] && sync
  printf '完成：%d 个插件，失败 %d\n' "${#plugins[@]}" "$failed"
  [ "$failed" = 0 ]
else
  restore_one "$PLUGIN" || exit 1
  [ "$DRY" = 0 ] && sync
fi
