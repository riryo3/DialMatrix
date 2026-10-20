#!/bin/bash
# このプロジェクトの記録（git）を、1 つのファイルにまとめて、別の場所に保存する（インターネットには、何も送らない）。
#   ./backup_repo.sh [保存先のフォルダ]     既定: ~/Documents/Rev-O-mate-記録のバックアップ
# 戻すとき:  git clone その.bundle ファイル 新しいフォルダ
set -e
cd "$(dirname "$0")/.."
DEST="${1:-$HOME/Documents/Rev-O-mate-記録のバックアップ}"
mkdir -p "$DEST"
FILE="$DEST/rev-o-mate_$(date +%Y%m%d-%H%M).bundle"
git bundle create "$FILE" --all
git bundle verify "$FILE" >/dev/null
# 古いものは 10 個まで残す
ls -1t "$DEST"/rev-o-mate_*.bundle 2>/dev/null | tail -n +11 | while read -r old; do rm -f "$old"; done
echo "保存しました: $FILE ($(du -h "$FILE" | cut -f1))"
