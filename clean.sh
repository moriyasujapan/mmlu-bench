#!/bin/sh
# 実行環境（promptfoo 本体・キャッシュ・評価 DB）を消す。
# 測定結果 runs/ と問題セット dataset/ は残す。
set -e
cd "$(dirname "$0")"
rm -rf node_modules .promptfoo .npm-cache package-lock.json
echo "実行環境を削除しました。runs/ と dataset/ は残しています。"
echo "完全に消すには、このディレクトリごと削除してください。"
