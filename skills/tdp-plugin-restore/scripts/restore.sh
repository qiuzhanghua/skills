#!/usr/bin/env bash
# TDP 插件恢复：U 盘 -> 本地 tdp-scripts。
# 用法: restore.sh [--dry-run] <plugin>
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DRY=0
PLUGIN=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    -h|--help) echo "usage: restore.sh [--dry-run] <plugin>"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) [ -z "$PLUGIN" ] && PLUGIN="$arg" || { echo "too many arguments" >&2; exit 2; } ;;
  esac
done
[ -n "$PLUGIN" ] || { echo "usage: restore.sh [--dry-run] <plugin>" >&2; exit 2; }
case "$PLUGIN" in *[!A-Za-z0-9._+-]*) echo "invalid plugin name: $PLUGIN" >&2; exit 2 ;; esac

LOCAL_ROOT="${TDP_LOCAL_ROOT:-$HOME/space/tdp-scripts}"
USB_ROOT="$("$SCRIPT_DIR/usb-root.sh")" || exit 1

echo "usb root  : $USB_ROOT"
echo "local root: $LOCAL_ROOT"
[ "$DRY" = 1 ] && echo "dry-run   : 不写入"

shopt -s nullglob
found=0 origin_n=0 plugin_n=0 bin_n=0

# origin/<plugin>：先拷到 .new，校验通过后再替换本地
src="$USB_ROOT/origin/$PLUGIN"
if [ -d "$src" ]; then
  found=$((found+1))
  dst="$LOCAL_ROOT/origin/$PLUGIN"
  echo "origin -> $dst"
  if [ "$DRY" = 0 ]; then
    mkdir -p -- "$LOCAL_ROOT/origin"
    tmp="$dst.new"
    rm -rf -- "$tmp"
    cp -r -- "$src" "$tmp"
    diff -r -- "$src" "$tmp" >/dev/null || { echo "verify failed: origin" >&2; exit 1; }
    rm -rf -- "$dst"
    mv -- "$tmp" "$dst"
  fi
  origin_n=$(find "$src" -type f | wc -l)
fi

# plugin/<plugin>_* 与 bin/<plugin>_*：先 staging 再替换本地
for kind in plugin bin; do
  srcdir="$USB_ROOT/$kind"
  files=("$srcdir/${PLUGIN}_"*)
  [ ${#files[@]} -gt 0 ] || continue
  found=$((found+1))
  dstdir="$LOCAL_ROOT/$kind"
  echo "$kind -> $dstdir (${#files[@]} files)"
  if [ "$DRY" = 0 ]; then
    mkdir -p -- "$dstdir"
    for f in "${files[@]}"; do
      cp -p -- "$f" "$dstdir/.tdpnew-$(basename -- "$f")"
    done
    for f in "${files[@]}"; do
      cmp -s -- "$f" "$dstdir/.tdpnew-$(basename -- "$f")" \
        || { echo "verify failed: $f" >&2; exit 1; }
    done
    find "$dstdir" -maxdepth 1 -type f -name "${PLUGIN}_*" -delete
    for f in "${files[@]}"; do
      mv -f -- "$dstdir/.tdpnew-$(basename -- "$f")" "$dstdir/$(basename -- "$f")"
    done
  fi
  if [ "$kind" = plugin ]; then plugin_n=${#files[@]}; else bin_n=${#files[@]}; fi
done

if [ "$found" = 0 ]; then
  echo "U 盘上没有插件 “$PLUGIN” 的文件（origin/plugin/bin）" >&2
  exit 1
fi

[ "$DRY" = 0 ] && sync
echo "完成：origin 文件 $origin_n，plugin $plugin_n，bin $bin_n"
