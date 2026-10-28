#!/usr/bin/env bash
# 解析 U 盘挂载点。
#   1. TDP_USB_ROOT 显式指定
#   2. 按卷标 SanDisk / TDP 认（挂载表优先，再用 /media/*、/run/media/*、/Volumes 通配）
#   3. 认不到卷标时，如果只有一个可移动挂载点，就用它（不依赖用户名）
#   4. 兼容 Windows 风格的 /mnt/f、/mnt/usb
set -euo pipefail
shopt -s nullglob

if [ -n "${TDP_USB_ROOT:-}" ]; then
  [ -d "$TDP_USB_ROOT" ] || { echo "TDP_USB_ROOT 不存在: $TDP_USB_ROOT" >&2; exit 1; }
  printf '%s\n' "$TDP_USB_ROOT"
  exit 0
fi

mounted_label() {
  lsblk -Pno LABEL,MOUNTPOINT 2>/dev/null \
    | sed -n "s/^LABEL=\"$1\" MOUNTPOINT=\"\\(.*\\)\"$/\\1/p" \
    | head -1
}

for label in SanDisk TDP; do
  mnt="$(mounted_label "$label")"
  if [ -n "$mnt" ] && [ -d "$mnt" ]; then
    printf '%s\n' "$mnt"
    exit 0
  fi
  for cand in /media/*/"$label" /run/media/*/"$label" /Volumes/"$label"; do
    [ -d "$cand" ] && { printf '%s\n' "$cand"; exit 0; }
  done
done

# 卷标不认识：取唯一的可移动挂载点（udisks 通常挂在 /media/<user>/<label>）
candidates=()
for cand in /media/*/*/ /run/media/*/*/; do
  [ -d "$cand" ] && candidates+=("${cand%/}")
done
if [ ${#candidates[@]} -eq 1 ]; then
  printf '%s\n' "${candidates[0]}"
  exit 0
fi

for cand in /mnt/f /mnt/usb; do
  if [ -d "$cand/origin" ] || [ -d "$cand/plugin" ] || [ -d "$cand/bin" ]; then
    printf '%s\n' "$cand"
    exit 0
  fi
done

if [ ${#candidates[@]} -eq 0 ]; then
  echo "未找到 U 盘：请插入 U 盘，或设置 TDP_USB_ROOT=<挂载点>" >&2
else
  echo "找到多个可移动挂载点，请用 TDP_USB_ROOT 指定：" >&2
  printf '  %s\n' "${candidates[@]}" >&2
fi
exit 1
