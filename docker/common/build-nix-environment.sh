#!/usr/bin/env bash
# Nixの取得・ビルド用ファイルを最終イメージへ残さず、実現した依存だけを取り出す。
set -euo pipefail

gui="$1"
vulkan="$2"
profile=/tmp/daifuku-nix-profile
image_root=/export

# ホスト側のビルド終了処理と干渉しないよう、nixbldをコンテナ専用のUID範囲へ移す。
# 非rootのビルドは維持し、/homeless-shelterへのキャッシュ作成も防ぐ。
while IFS=: read -r name password uid gid gecos home login_shell; do
  case "${name}" in
    nixbld[0-9]*) uid="$((uid + 30000))" ;;
  esac
  printf '%s:%s:%s:%s:%s:%s:%s\n' \
    "${name}" "${password}" "${uid}" "${gid}" "${gecos}" "${home}" "${login_shell}"
done </etc/passwd >/tmp/nix-build-passwd
mv /tmp/nix-build-passwd /etc/passwd

export NIX_CONFIG="$(
  printf '%s\n' \
    'experimental-features = nix-command flakes' \
    'sandbox = false' \
    'http2 = false' \
    'extra-substituters = https://attic.iid.ciirc.cvut.cz/ros https://ros.cachix.org https://cache.numtide.com' \
    'extra-trusted-public-keys = ros:JR95vUYsShSqfA1VTYoFt1Nz6uXasm5QrcOsGry9f6Q= ros.cachix.org-1:dSyZxI8geDCJrwgvCOHDoAfOm5sV1wCPjBkKL+38Rvo= niks3.numtide.com-1:DTx8wZduET09hRmMtKdQDxNNthLQETkc/yaX7M4qK0g='
)"

mkdir -p "${image_root}/opt/daifuku"
nix print-dev-env --print-build-logs --file /tmp/nix-env/shell.nix \
  --arg gui "${gui}" --arg vulkan "${vulkan}" \
  --max-jobs "${BUILD_JOBS:-2}" --cores "${BUILD_JOBS:-2}" \
  --profile "${profile}" >"${image_root}/opt/daifuku/nix-env.bash"

# coreutilsなども、このプロファイルと同じ固定バージョンを使う。
source "${image_root}/opt/daifuku/nix-env.bash"
# 同一ヘッダーやライブラリを共有し、コピー先でも出力間のhardlinkを保つ。
nix-store --optimise
mkdir -p "${image_root}"/{bin,etc/ssl/certs,opt/ros/humble,root,run,tmp,usr/local/bin,usr/local/lib/daifuku}
chmod 1777 "${image_root}/tmp"
mkdir -p "${image_root}/nix/store"
mapfile -t store_paths < <(nix-store --query --requisites "${profile}")
# cpを出力ごとに呼ぶと、別の出力と共有するhardlinkが通常ファイルへ戻る。
cp -a "${store_paths[@]}" "${image_root}/nix/store/"
for store_path in "${store_paths[@]}"; do
  if [[ -f "${store_path}/etc/ssl/certs/ca-bundle.crt" ]]; then
    cp "${store_path}/etc/ssl/certs/ca-bundle.crt" \
      "${image_root}/etc/ssl/certs/ca-certificates.crt"
  fi
  if [[ "${vulkan}" == true && -d "${store_path}/share/vulkan/icd.d" ]]; then
    mkdir -p "${image_root}/usr/share/vulkan/icd.d"
    cp -a "${store_path}/share/vulkan/icd.d/." "${image_root}/usr/share/vulkan/icd.d/"
  fi
  # NixのlibGLXはこのパスからMesaを読む。ホストのドライバには依存させない。
  if [[ "${gui}" == true && -f "${store_path}/lib/dri/swrast_dri.so" ]]; then
    ln -s "${store_path}" "${image_root}/run/opengl-driver"
  fi
done
test -s "${image_root}/etc/ssl/certs/ca-certificates.crt"
if [[ "${gui}" == true ]]; then
  mkdir -p "${image_root}/etc/fonts"
  ln -s "${FONTCONFIG_FILE}" "${image_root}/etc/fonts/fonts.conf"
fi

ln -s "$(command -v bash)" "${image_root}/bin/bash"
ln -s bash "${image_root}/bin/sh"
ln -s "$(command -v env)" "${image_root}/bin/env"
ln -s /bin "${image_root}/usr/bin"
# docker execで直接呼ぶCLIも、Nixシェルと同じ優先順で見つかるようにする。
IFS=: read -r -a bin_dirs <<<"${PATH}"
for bin_dir in "${bin_dirs[@]}"; do
  [[ "${bin_dir}" == /nix/store/* ]] || continue
  for program in "${bin_dir}"/*; do
    [[ -x "${program}" && ! -d "${program}" ]] || continue
    if [[ ! -e "${image_root}/usr/local/bin/${program##*/}" ]]; then
      ln -s "${program}" "${image_root}/usr/local/bin/"
    fi
  done
done
ln -s /usr/local/lib/daifuku/entrypoint.sh "${image_root}/ros_entrypoint.sh"
if [[ "${gui}" == true ]]; then
  build_command=build-autonomous
else
  build_command=build-workspace
fi
ln -s /usr/local/lib/daifuku/build-workspace.sh "${image_root}/usr/local/bin/${build_command}"

# 既存のCLI・CIの入口を保つ。ROS通信の呼び出し元指定は上書きしない。
cat >"${image_root}/opt/ros/humble/setup.bash" <<'SETUP'
_daifuku_domain_id="${ROS_DOMAIN_ID:-90}"
_daifuku_localhost_only="${ROS_LOCALHOST_ONLY:-0}"
source /opt/daifuku/nix-env.bash
export ROS_DOMAIN_ID="${_daifuku_domain_id}"
export ROS_LOCALHOST_ONLY="${_daifuku_localhost_only}"
unset _daifuku_domain_id _daifuku_localhost_only
SETUP
printf '%s\n' 'source /opt/ros/humble/setup.bash' >"${image_root}/etc/profile"
printf '%s\n' 'source /opt/ros/humble/setup.bash' >"${image_root}/root/.bashrc"
printf '%s\n' \
  'root:x:0:0:root:/root:/bin/bash' \
  'ros:x:1000:1000:ROS:/tmp:/bin/bash' >"${image_root}/etc/passwd"
printf '%s\n' 'root:x:0:' 'ros:x:1000:' >"${image_root}/etc/group"
printf '%s\n' 'hosts: files dns' >"${image_root}/etc/nsswitch.conf"
printf 'NIX_IMAGE_READY gui=%s vulkan=%s\n' "${gui}" "${vulkan}"
