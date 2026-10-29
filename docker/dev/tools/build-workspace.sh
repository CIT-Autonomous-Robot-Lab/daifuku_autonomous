#!/usr/bin/env bash
# 開発コンテナのワークスペースビルド。**パッケージは名前で選ばない** — src/ にある
# ものは全部建てるので、パッケージを足してもこのファイルは直さなくてよい。
set -eo pipefail

WS="${AUTONOMOUS_WS:-/workspaces/daifuku_autonomous}"
BUILD_JOBS="${BUILD_JOBS:-2}"
REPOS_FILE="${WS}/daifuku_autonomous.repos"

cd "${WS}"
source /opt/ros/humble/setup.bash
source /opt/ros2_rust_ws/install/local_setup.bash
source /opt/raspicat2/install/setup.bash

mkdir -p src

# 外部パッケージの取得。**ネットワークがあれば毎回 vcs import する** (--skip-existing
# は付けないので、repos の version が変わればチェックアウトも動く。version が
# ブランチ名のときはローカルのブランチを checkout するだけで上流へは進めない)。
# 無ければ呼ばない (既存リポジトリにも git fetch が走って落ちる)。
mapfile -t repo_entries < <(python3 - "${REPOS_FILE}" <<'PY'
import sys
import yaml

with open(sys.argv[1], 'rb') as f:
    repos = yaml.safe_load(f)['repositories'] or {}
for path, spec in repos.items():
    print(f"{path}\t{spec.get('url', '')}")
PY
)
if [[ ${#repo_entries[@]} -eq 0 ]]; then
  echo "daifuku_autonomous.repos を読めませんでした" >&2
  exit 1
fi
repo_paths=("${repo_entries[@]%%$'\t'*}")
probe_url="${repo_entries[0]#*$'\t'}"

export GIT_TERMINAL_PROMPT=0
online=false
if timeout 15 git ls-remote -q --exit-code "${probe_url}" HEAD >/dev/null 2>&1; then
  online=true
  echo "ネットワークに繋がっているので vcs import します"
  vcs import . < "${REPOS_FILE}" \
    || echo "警告: vcs import が失敗しました。手元のチェックアウトのまま建てます" >&2
else
  echo "ネットワークに繋がらないので vcs import と rosdep を省略します"
fi

missing=()
for repo_path in "${repo_paths[@]}"; do
  [[ -n "$(ls -A "${WS}/${repo_path}" 2>/dev/null)" ]] || missing+=("${repo_path}")
done
if [[ ${#missing[@]} -ne 0 ]]; then
  printf '取得できませんでした (ネットワークが要ります): %s\n' "${missing[*]}" >&2
  exit 1
fi

# livox_ros_driver2 は ROS 2 用の manifest と launch を上流で別名に置いている。
cp src/livox_ros_driver2/package_ROS2.xml src/livox_ros_driver2/package.xml
rm -rf src/livox_ros_driver2/launch
# Windows/Podman のバインドマウントは一部の Unix タイムスタンプを設定できない。
mkdir -p src/livox_ros_driver2/launch
while IFS= read -r -d '' launch_file; do
  target="src/livox_ros_driver2/launch/${launch_file##*/}"
  dd if="${launch_file}" of="${target}" status=none
done < <(find src/livox_ros_driver2/launch_ROS2 -maxdepth 1 -type f -print0)

# イメージは apt の索引を消してある。rosdep がソース固有のパッケージ
# (libaprutil1-dev など) を見つけることがあるので、入れる前に索引を取り直す。
if [[ "${online}" == true ]]; then
  apt-get update
  rosdep install \
    --from-paths src \
    --ignore-src \
    --rosdistro humble \
    -r -y
fi

# CMake/Python/Rust を同じビルドに載せ、colcon に依存順を任せる。
pkg_list="$(colcon list --base-paths src)"
awk '$1 == "vi_planner" && $3 ~ /cargo/ {found=1} END {exit !found}' <<<"${pkg_list}" || {
  printf 'vi_planner is missing from colcon list\n' >&2
  exit 1
}
bash /usr/local/lib/daifuku/workspace-layout.sh prepare <<<"${pkg_list}"

colcon build \
  --base-paths src \
  --symlink-install \
  --parallel-workers "${BUILD_JOBS}" \
  --cargo-args --release \
  --cmake-args \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DROS_EDITION=ROS2 \
    -DDISTRO_ROS=humble

bash /usr/local/lib/daifuku/workspace-layout.sh commit

printf '\nBuild complete. Refresh this shell with:\n  source %s/install/setup.bash\n' "${WS}"
