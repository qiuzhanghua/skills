#!/usr/bin/env bash
# 解析 U 盘挂载点。优先 TDP_USB_ROOT，其次按卷标 SanDisk / TDP 查找，
# 最后兼容 Windows 风格的 /mnt/f。
set -euo pipefail

if [ -n "${TDP_USB_ROOT:-}" ]; then
  [ -d "$TDP_USB_ROOT" ] || { echo "TDP_USB_ROOT 不存在: $TDP_USB_ROOT" >&2; exit 1; }
  printf '%s\n' "$TDP_USB_ROOT"
  exit 0
fi

USER_NAME="${USER:-$(id -un)}"
for label in SanDisk TDP; do
  mnt="$(lsblk -Pno LABEL,MOUNTPOINT 2>/dev/null \
    | sed -n "s/^LABEL=\"$label\" MOUNTPOINT=\"\\(.*\\)\"$/\\1/p" \
    | head -1)"
  if [ -n "$mnt" ] && [ -d "$mnt" ]; then
    printf '%s\n' "$mnt"
    exit 0
  fi
  for cand in "/media/$USER_NAME/$label" "/run/media/$USER_NAME/$label" "/Volumes/$label"; do
    if [ -d "$cand" ]; then
      printf '%s\n' "$cand"
      exit 0
    fi
  done
done

for cand in /mnt/f /mnt/usb; do
  if [ -d "$cand/origin" ] || [ -d "$cand/plugin" ] || [ -d "$cand/bin" ]; then
    printf '%s\n' "$cand"
    exit 0
  fi
done

echo "未找到 U 盘：请插入 U 盘，或设置 TDP_USB_ROOT=<挂载点>" >&2
exit 1
