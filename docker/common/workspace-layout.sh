#!/usr/bin/env bash
# 成功したビルドの構成だけを記録し、構成変更時は生成物を作り直す。
set -eo pipefail

if [[ "$1" == commit ]]; then
  mv build/.workspace-layout.pending build/.workspace-layout
  exit
fi

mkdir -p build install
layout="$({
  printf '%s\n' "$PWD"
  cat # stdin は colcon list のパッケージ名・配置・種類。
  find src -type d \( -name .git -o -name target -o -name build \
    -o -name install -o -name log -o -name .venv -o -name __pycache__ \
    -o -name .pytest_cache -o -name .ruff_cache \) -prune -o \
    -type f ! -name '*.pyc' ! -name '*.pyo' \
    \( \( -name CMakeLists.txt -o -name package.xml -o -name setup.py \
      -o -name setup.cfg -o -name Cargo.toml \) -exec sha256sum {} + -o -print \) \
    -o -type l -printf '%p -> %l\n'
} | LC_ALL=C sort | sha256sum)"

if [[ ! -f build/.workspace-layout || "$(< build/.workspace-layout)" != "$layout" ]]; then
  echo "workspace layout: 構成変更を検出したため build/install を再生成します"
  # マウントポイント、取得済みクレート、前回成功時の記録は残す。
  find build -mindepth 1 -maxdepth 1 ! -name .cargo ! -name .workspace-layout \
    -exec rm -rf -- {} +
  find install -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
fi
printf '%s\n' "$layout" > build/.workspace-layout.pending
