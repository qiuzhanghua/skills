#!/usr/bin/env bash
# TDP 插件备份：本地 tdp-scripts -> U 盘。
# 用法: backup.sh [--dry-run] <plugin>|--all
#
# 版本规则：
#   源只有 1 个版本 -> 与目标最高版本比较，源更高才替换，否则跳过
#   源有多个版本   -> 全量复制（覆盖目标该插件的文件）
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
    -h|--help) echo "usage: backup.sh [--dry-run] <plugin>|--all"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) [ -z "$PLUGIN" ] && PLUGIN="$arg" || { echo "too many arguments" >&2; exit 2; } ;;
  esac
done
if [ "$ALL" = 1 ] && [ -n "$PLUGIN" ]; then
  echo "--all 与插件名不能同时使用" >&2; exit 2
fi
if [ "$ALL" = 0 ] && [ -z "$PLUGIN" ]; then
  echo "usage: backup.sh [--dry-run] <plugin>|--all" >&2; exit 2
fi

valid_name() { case "$1" in ''|*[!A-Za-z0-9._+-]*) return 1 ;; esac; }

# <name>_<version>_<os>_... -> <name>；版本以数字开头，名字里可以有下划线
derive_name() {
  local base="$1"
  [[ "$base" =~ ^(.+?)_[0-9][^_]*_ ]] && printf '%s\n' "${BASH_REMATCH[1]}"
}

# 从文件名里取版本：优先 vX.Y.Z，其次 X.Y.Z
version_of_name() {
  local base="$1"
  if [[ "$base" =~ v([0-9]+(\.[0-9]+)+) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"; return 0
  fi
  if [[ "$base" =~ ([0-9]+(\.[0-9]+)+) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"; return 0
  fi
  return 1
}

# 源版本是否高于目标（严格大于）
version_gt() {
  local a="${1#v}" b="${2#v}"
  local -a A B
  local IFS='.'; A=($a); B=($b)
  local i n=${#A[@]}
  [ ${#B[@]} -gt $n ] && n=${#B[@]}
  for ((i = 0; i < n; i++)); do
    local x="${A[i]:-0}" y="${B[i]:-0}"
    x="${x%%[!0-9]*}"; y="${y%%[!0-9]*}"
    x="${x:-0}"; y="${y:-0}"
    if ((10#$x > 10#$y)); then return 0; fi
    if ((10#$x < 10#$y)); then return 1; fi
  done
  return 1
}

max_version() {
  local best="" v
  while IFS= read -r v; do
    [ -n "$v" ] || continue
    if [ -z "$best" ] || version_gt "$v" "$best"; then best="$v"; fi
  done
  printf '%s\n' "$best"
}

versions_of_paths() {
  local p
  for p in "$@"; do
    [ -f "$p" ] || continue
    version_of_name "$(basename -- "$p")" || true
  done | sort -u
}

# 是否需要替换：源单版本时目标更高/相同则跳过；源多版本或无版本信息则复制
should_replace() {
  local src_list="$1" dst_list="$2"
  local src_n src_one dst_max
  src_n=$(printf '%s\n' "$src_list" | sed '/^$/d' | wc -l)
  [ "$src_n" -le 1 ] || return 0
  src_one=$(printf '%s\n' "$src_list" | sed '/^$/d' | head -1)
  dst_max=$(printf '%s\n' "$dst_list" | sed '/^$/d' | max_version)
  [ -z "$dst_max" ] && return 0
  [ -z "$src_one" ] && return 0
  version_gt "$src_one" "$dst_max"
}

list_local_plugins() {
  local d f name
  {
    for d in "$LOCAL_ROOT"/origin/*/; do
      name="$(basename -- "$d")"
      valid_name "$name" && printf '%s\n' "$name"
    done
    for f in "$LOCAL_ROOT"/plugin/* "$LOCAL_ROOT"/bin/*; do
      [ -f "$f" ] || continue
      name="$(derive_name "$(basename -- "$f")")" || continue
      [ -n "$name" ] && valid_name "$name" && printf '%s\n' "$name"
    done
  } | sort -u
}

copy_section() {
  local kind="$1" plugin="$2"
  local files=("$LOCAL_ROOT/$kind/${plugin}_"*)
  [ ${#files[@]} -gt 0 ] || return 1
  local dstdir="$USB_ROOT/$kind"
  local dstfiles=("$dstdir/${plugin}_"*)

  local src_list dst_list
  src_list=$(versions_of_paths "${files[@]}")
  dst_list=$(versions_of_paths "${dstfiles[@]}")
  if ! should_replace "$src_list" "$dst_list"; then
    printf '  %s 跳过：源 %s <= 目标 %s\n' "$kind" \
      "$(printf '%s\n' "$src_list" | sed '/^$/d' | max_version)" \
      "$(printf '%s\n' "$dst_list" | sed '/^$/d' | max_version)"
    return 0
  fi

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

backup_one() {
  local plugin="$1" found=0 origin_n=0 plugin_n=0 bin_n=0
  printf 'plugin %s\n' "$plugin"

  local src="$LOCAL_ROOT/origin/$plugin"
  if [ -d "$src" ]; then
    found=1
    local dst="$USB_ROOT/origin/$plugin"
    local src_files=() dst_files=() p
    while IFS= read -r p; do src_files+=("$p"); done < <(find "$src" -type f 2>/dev/null)
    while IFS= read -r p; do dst_files+=("$p"); done < <(find "$dst" -type f 2>/dev/null)
    local src_list dst_list
    src_list=$(versions_of_paths "${src_files[@]}")
    dst_list=$(versions_of_paths "${dst_files[@]}")
    if ! should_replace "$src_list" "$dst_list"; then
      printf '  origin 跳过：源 %s <= 目标 %s\n' \
        "$(printf '%s\n' "$src_list" | sed '/^$/d' | max_version)" \
        "$(printf '%s\n' "$dst_list" | sed '/^$/d' | max_version)"
    else
      printf '  origin -> %s\n' "$dst"
      if [ "$DRY" = 0 ]; then
        mkdir -p -- "$USB_ROOT/origin"
        local tmp="$dst.new"
        rm -rf -- "$tmp"
        cp -r -- "$src" "$tmp"
        diff -r -- "$src" "$tmp" >/dev/null || { echo "verify failed: origin" >&2; return 1; }
        rm -rf -- "$dst"
        mv -- "$tmp" "$dst"
      fi
      origin_n=$(find "$src" -type f | wc -l)
    fi
  fi

  local kind
  for kind in plugin bin; do
    if copy_section "$kind" "$plugin"; then
      found=1
      if [ "$kind" = plugin ]; then
        plugin_n=$(ls -1 "$LOCAL_ROOT/plugin/${plugin}_"* 2>/dev/null | wc -l)
      else
        bin_n=$(ls -1 "$LOCAL_ROOT/bin/${plugin}_"* 2>/dev/null | wc -l)
      fi
    fi
  done

  [ "$found" = 1 ] || { echo "  本地没有这个插件的文件" >&2; return 1; }
  printf '  完成：origin %s，plugin %s，bin %s\n' "$origin_n" "$plugin_n" "$bin_n"
}

LOCAL_ROOT="${TDP_LOCAL_ROOT:-$HOME/space/tdp-scripts}"
USB_ROOT="$("$SCRIPT_DIR/usb-root.sh")" || exit 1
printf 'local root: %s\nusb root  : %s\n' "$LOCAL_ROOT" "$USB_ROOT"
[ "$DRY" = 1 ] && echo "dry-run   : 不写入"

if [ "$ALL" = 1 ]; then
  mapfile -t plugins < <(list_local_plugins)
  [ ${#plugins[@]} -gt 0 ] || { echo "本地没有找到任何插件" >&2; exit 1; }
  printf '插件数    : %d\n' "${#plugins[@]}"
  failed=0
  for plugin in "${plugins[@]}"; do
    backup_one "$plugin" || { echo "  备份失败: $plugin" >&2; failed=$((failed+1)); }
  done
  [ "$DRY" = 0 ] && sync
  printf '完成：%d 个插件，失败 %d\n' "${#plugins[@]}" "$failed"
  [ "$failed" = 0 ]
else
  backup_one "$PLUGIN" || exit 1
  [ "$DRY" = 0 ] && sync
fi
