#!/usr/bin/env bash
# Raspberry Pi 4 / 5 のホスト側セットアップ。
#
# create_image.py が cloud-init 経由で /usr/local/sbin/daifuku-provision.sh
# として配置し、初回起動時に一度だけ実行します。すでに動いている機体へ後から
# 適用したり、失敗した箇所をやり直したりする用途でも、そのまま実行できます。
#
#   sudo bash tools/image/provision.sh
#
# 設定は /etc/daifuku/provision.env から読み、環境変数で上書きできます。
#
#   sudo DAIFUKU_SWAP_MB=4096 bash tools/image/provision.sh
#
# ここで面倒を見るのは「コンテナの中に入れられないもの」だけです。
# ナビゲーション一式は docker/raspberrypi のイメージ側にあります。
#
#   * Docker本体とcomposeプラグイン
#   * rtmouseカーネルモジュール（コンテナからはinsmodできない）
#   * DDS向けのカーネルパラメータとFast DDSプロファイルの指定
#   * スワップ（価値反復プランナがPi 4の4GBに収まらないことがある）
#   * 時刻同期、ユーザーの所属グループ、リポジトリの取得
set -Eeuo pipefail

LOG_FILE="${DAIFUKU_LOG_FILE:-/var/log/daifuku-provision.log}"
ENV_FILE="${DAIFUKU_ENV_FILE:-/etc/daifuku/provision.env}"
STATE_DIR=/var/lib/daifuku

if [[ ${EUID} -ne 0 ]]; then
  echo "root権限が必要です: sudo bash $0" >&2
  exit 1
fi

# 環境変数で明示された値のほうが強い。env fileは既定値として読む。
# 最終行に改行がなくても取りこぼさないよう、read の戻り値ではなく key の中身で判定する。
if [[ -f "${ENV_FILE}" ]]; then
  while IFS='=' read -r key value || [[ -n "${key}" ]]; do
    if [[ "${key}" =~ ^DAIFUKU_[A-Z_]+$ && -z "${!key:-}" ]]; then
      printf -v "${key}" '%s' "${value}"
      export "${key?}"
    fi
  done <"${ENV_FILE}"
fi

DAIFUKU_USER="${DAIFUKU_USER:-ubuntu}"
DAIFUKU_MODEL="${DAIFUKU_MODEL:-pi4}"
DAIFUKU_REPO_URL="${DAIFUKU_REPO_URL:-https://github.com/CIT-Autonomous-Robot-Lab/daifuku_autonomous.git}"
DAIFUKU_REPO_REF="${DAIFUKU_REPO_REF:-main}"
DAIFUKU_ROS_DOMAIN_ID="${DAIFUKU_ROS_DOMAIN_ID:-90}"
DAIFUKU_ROBOT_IP="${DAIFUKU_ROBOT_IP:-192.168.1.50}"
DAIFUKU_BUILD_JOBS="${DAIFUKU_BUILD_JOBS:-1}"
DAIFUKU_SWAP_MB="${DAIFUKU_SWAP_MB:-2048}"
DAIFUKU_WITH_RTMOUSE="${DAIFUKU_WITH_RTMOUSE:-1}"
DAIFUKU_BUILD_ON_FIRST_BOOT="${DAIFUKU_BUILD_ON_FIRST_BOOT:-0}"
DAIFUKU_REPO_ARCHIVE="${DAIFUKU_REPO_ARCHIVE:-/boot/firmware/daifuku-repo.tar.gz}"

mkdir -p "${STATE_DIR}" "$(dirname "${LOG_FILE}")"

# ログは端末とファイルの両方へ出す。プロセス置換 (exec > >(tee ...)) だと
# cloud-init が親を回収した時点で tee が刈られ、いちばん見たい失敗直前の行が
# 落ちる。子プロセスをパイプでつないで待つ形にすれば取りこぼさない。
if [[ "${DAIFUKU_TEE:-0}" != "1" ]]; then
  export DAIFUKU_TEE=1
  set -o pipefail
  bash "$0" "$@" 2>&1 | tee -a "${LOG_FILE}"
  exit "${PIPESTATUS[0]}"
fi

FAILED_STEPS=()

step() {
  echo
  echo "==> $*"
}

soft_fail() {
  echo "警告: $* （続行します）" >&2
  FAILED_STEPS+=("$*")
}

HOME_DIR="$(getent passwd "${DAIFUKU_USER}" | cut -d: -f6)"
if [[ -z "${HOME_DIR}" ]]; then
  echo "ユーザー ${DAIFUKU_USER} が存在しません" >&2
  exit 1
fi
WORKSPACE="${DAIFUKU_WORKSPACE:-${HOME_DIR}/daifuku_autonomous}"

as_user() {
  # runuser は既定でrootのHOMEを引き継ぐ。gitが /root/.gitconfig を見に行ったり
  # クローン先の所有者がずれたりするので、HOMEを明示する。
  runuser -u "${DAIFUKU_USER}" -- env "HOME=${HOME_DIR}" "$@"
}

echo "daifuku_autonomous プロビジョニング開始: $(date -Is)"
echo "  model=${DAIFUKU_MODEL} user=${DAIFUKU_USER} workspace=${WORKSPACE}"
echo "  ip=${DAIFUKU_ROBOT_IP} ROS_DOMAIN_ID=${DAIFUKU_ROS_DOMAIN_ID}"

# ---------------------------------------------------------------------------
# apt
# ---------------------------------------------------------------------------

apt_retry() {
  # dpkg のロックは cloud-init の package_update や unattended-upgrades と
  # 取り合いになる。fuser (psmisc) は最小構成に入っていないことがあり、
  # 「コマンドが無いのでロックも無い」と誤判定するので、素直に再試行する。
  local attempt
  for attempt in 1 2 3 4 5 6; do
    if DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 "$@"; then
      return 0
    fi
    echo "  apt-get $1 に失敗 (${attempt}/6)。20秒待って再試行します" >&2
    sleep 20
  done
  return 1
}

apt_install() {
  apt_retry install -y --no-install-recommends "$@"
}

step "ネットワークの疎通を待つ"
NETWORK_OK=0
for _ in $(seq 1 60); do
  if getent hosts archive.ubuntu.com >/dev/null 2>&1; then
    NETWORK_OK=1
    break
  fi
  sleep 5
done

if ((NETWORK_OK == 0)); then
  # ここで止まると以降のaptもgitもDockerも全部失敗する。原因が
  # 「デフォルトルートが無い」であることが多いので、先に大きく出しておく。
  echo
  echo "######################################################################"
  echo "# 名前解決ができません。ネットワーク設定を確認してください。"
  echo "#   ip addr; ip route; cat /etc/netplan/50-cloud-init.yaml"
  echo "# 固定IPでデフォルトゲートウェイが無いとこの状態になります。"
  echo "# 直したあとに次を実行すれば、この続きからやり直せます。"
  echo "#   sudo $0"
  echo "######################################################################"
  echo
  soft_fail "ネットワークに到達できません（apt / git / Docker は失敗します）"
fi

step "aptの更新と共通パッケージの導入"
apt_retry update || soft_fail "apt-get update"
apt_install \
  ca-certificates \
  chrony \
  curl \
  device-tree-compiler \
  git \
  gnupg \
  i2c-tools \
  net-tools \
  python3 \
  rsync \
  tmux \
  || soft_fail "共通パッケージの導入"

# ---------------------------------------------------------------------------
# 時刻同期
# ---------------------------------------------------------------------------

step "時刻同期(chrony)を有効化"
systemctl enable --now chrony 2>/dev/null || systemctl enable --now chronyd 2>/dev/null ||
  soft_fail "chronyの有効化"

# ---------------------------------------------------------------------------
# DDS向けカーネルパラメータ
# ---------------------------------------------------------------------------

step "UDP受信バッファを拡大 (/etc/sysctl.d/60-ros2-dds.conf)"
# 既定値のままだと MID360 の点群と TF で RcvbufErrors が数十万件出て、
# ディスカバリが不安定になる。
cat >/etc/sysctl.d/60-ros2-dds.conf <<'EOF'
# daifuku_autonomous: ROS 2 / Fast DDS 用。tools/image/provision.sh が生成。
net.core.rmem_max = 16777216
net.core.rmem_default = 16777216
net.core.wmem_max = 16777216
net.core.wmem_default = 1048576
net.ipv4.ipfrag_time = 3
net.ipv4.ipfrag_high_thresh = 134217728
EOF
sysctl --system >/dev/null || soft_fail "sysctl --system"

# ---------------------------------------------------------------------------
# スワップ
# ---------------------------------------------------------------------------

if ((DAIFUKU_SWAP_MB > 0)); then
  step "スワップファイルを用意 (${DAIFUKU_SWAP_MB} MB)"
  # 価値反復プランナは広域地図で anon-RSS 2.7GB まで伸び、Pi 4 の 4GB では
  # OOM killer に落とされる。ディスクに余裕がないほうが多いので、空き容量を
  # 確かめてから作る。
  SWAP_FILE=/swapfile
  FREE_MB=$(df --output=avail -m / | tail -n1 | tr -d ' ')
  if [[ -f "${SWAP_FILE}" ]]; then
    echo "${SWAP_FILE} は作成済み"
  elif ((FREE_MB < DAIFUKU_SWAP_MB + 2048)); then
    soft_fail "ディスクの空きが ${FREE_MB} MB しかないためスワップを作りません"
  else
    fallocate -l "${DAIFUKU_SWAP_MB}M" "${SWAP_FILE}" ||
      dd if=/dev/zero of="${SWAP_FILE}" bs=1M count="${DAIFUKU_SWAP_MB}" status=none
    chmod 600 "${SWAP_FILE}"
    mkswap "${SWAP_FILE}" >/dev/null
    swapon "${SWAP_FILE}"
    grep -q "^${SWAP_FILE} " /etc/fstab || echo "${SWAP_FILE} none swap sw 0 0" >>/etc/fstab
  fi
  # SDカードの寿命を削らないよう、本当に足りないときだけ使う。
  echo "vm.swappiness = 10" >/etc/sysctl.d/61-daifuku-swappiness.conf
  sysctl -p /etc/sysctl.d/61-daifuku-swappiness.conf >/dev/null || true
fi

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

step "Dockerを導入"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  echo "docker と compose plugin は導入済み"
else
  if curl -fsSL https://get.docker.com -o /tmp/get-docker.sh && sh /tmp/get-docker.sh; then
    rm -f /tmp/get-docker.sh
  else
    soft_fail "get.docker.com からの導入に失敗。ディストリのパッケージで代替します"
    apt_install docker.io docker-compose-v2 || apt_install docker.io ||
      soft_fail "docker.ioの導入"
  fi
fi
systemctl enable --now docker || soft_fail "dockerの有効化"

if ! docker compose version >/dev/null 2>&1; then
  soft_fail "docker compose プラグインがありません。docker/raspberrypi/ の compose を使えません"
fi

step "${DAIFUKU_USER} をグループへ追加"
for group in docker dialout i2c video plugdev gpio; do
  if getent group "${group}" >/dev/null; then
    usermod -aG "${group}" "${DAIFUKU_USER}"
  fi
done

# ---------------------------------------------------------------------------
# リポジトリ
# ---------------------------------------------------------------------------

step "リポジトリを用意 (${DAIFUKU_REPO_URL} @ ${DAIFUKU_REPO_REF})"
# 認証を求められても対話できないので、その場で失敗させてスナップショットへ倒す。
export GIT_TERMINAL_PROMPT=0
export GIT_ASKPASS=/bin/true

if [[ -d "${WORKSPACE}/.git" ]]; then
  as_user git -C "${WORKSPACE}" fetch --depth 1 origin "${DAIFUKU_REPO_REF}" &&
    as_user git -C "${WORKSPACE}" checkout FETCH_HEAD ||
    soft_fail "既存ワークスペースの更新"
elif [[ ! -d "${WORKSPACE}" ]]; then
  # ブートパーティションのマウント先はUbuntuなら /boot/firmware だが、
  # 環境によっては /boot 直下のこともある。両方見る。
  if [[ ! -f "${DAIFUKU_REPO_ARCHIVE}" && -f "/boot/$(basename "${DAIFUKU_REPO_ARCHIVE}")" ]]; then
    DAIFUKU_REPO_ARCHIVE="/boot/$(basename "${DAIFUKU_REPO_ARCHIVE}")"
  fi

  if as_user git clone --depth 1 --branch "${DAIFUKU_REPO_REF}" \
    "${DAIFUKU_REPO_URL}" "${WORKSPACE}"; then
    echo "  git cloneで取得しました (${DAIFUKU_REPO_REF})"
  elif [[ -n "${DAIFUKU_REPO_ARCHIVE:-}" && -f "${DAIFUKU_REPO_ARCHIVE}" ]]; then
    # 非公開リポジトリやオフラインではcloneできない。create_image.py が
    # ブートパーティションへ置いたスナップショットを展開する。gitの履歴は
    # 付かないので、あとで clone し直したい場合は手で入れ替える。
    echo "  git cloneに失敗したので ${DAIFUKU_REPO_ARCHIVE} を展開します"
    rm -rf "${WORKSPACE}"
    as_user mkdir -p "${WORKSPACE}"
    if tar -xzf "${DAIFUKU_REPO_ARCHIVE}" -C "${WORKSPACE}"; then
      chown -R "${DAIFUKU_USER}:${DAIFUKU_USER}" "${WORKSPACE}"
      echo "  展開しました: ${DAIFUKU_REPO_REF} = ${DAIFUKU_REPO_ARCHIVE_COMMIT:-不明}"
      echo "  （gitの履歴は付きません）"
    else
      soft_fail "スナップショットの展開"
    fi
  else
    soft_fail "リポジトリを取得できません（git cloneが失敗し、スナップショットもありません）"
  fi
fi

WHITELIST="${WORKSPACE}/docker/raspberrypi/fastdds_udp_whitelist.xml"

if [[ -f "${WHITELIST}" && -n "${DAIFUKU_ROBOT_IP}" ]]; then
  step "Fast DDS whitelist のIPを ${DAIFUKU_ROBOT_IP} に合わせる"
  # whitelistにはロボットLANのIPが直接書いてある。--ip を変えたのに XML が
  # 192.168.1.50 のままだと、ホストとコンテナのどちらもロケータを広告できず
  # 通信が静かに止まる。
  if grep -q '192\.168\.1\.50' "${WHITELIST}" && [[ "${DAIFUKU_ROBOT_IP}" != "192.168.1.50" ]]; then
    sed -i "s/192\.168\.1\.50/${DAIFUKU_ROBOT_IP}/g" "${WHITELIST}"
    echo "  書き換えました（git diff に出ます）"
  else
    echo "  変更は不要"
  fi
fi

step "compose用の .env とシェル環境を書く"
ENV_TARGET="${WORKSPACE}/docker/raspberrypi/.env"
if [[ -d "$(dirname "${ENV_TARGET}")" ]]; then
  cat >"${ENV_TARGET}" <<EOF
# tools/image/provision.sh が生成。docker compose が読み込む。
ROS_DOMAIN_ID=${DAIFUKU_ROS_DOMAIN_ID}
BUILD_JOBS=${DAIFUKU_BUILD_JOBS}
EOF
  chown "${DAIFUKU_USER}:${DAIFUKU_USER}" "${ENV_TARGET}"
fi

# ホスト側のROSプロセス（ros2 CLIなど）もコンテナと同じプロファイルを指す
# 必要がある。片方だけがSHMを使う状態になると通信が成立しない。
BASHRC="${HOME_DIR}/.bashrc"
BEGIN_MARK='# >>> daifuku_autonomous >>>'
END_MARK='# <<< daifuku_autonomous <<<'
BLOCK=$(
  cat <<EOF
${BEGIN_MARK}
# tools/image/provision.sh が管理するブロック。
export ROS_DOMAIN_ID=${DAIFUKU_ROS_DOMAIN_ID}
export ROS_LOCALHOST_ONLY=0
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export FASTRTPS_DEFAULT_PROFILES_FILE=${WHITELIST}
export DAIFUKU_WS=${WORKSPACE}
alias daifuku='cd \${DAIFUKU_WS}'
${END_MARK}
EOF
)
touch "${BASHRC}"
if grep -qF "${BEGIN_MARK}" "${BASHRC}"; then
  python3 - "${BASHRC}" "${BLOCK}" <<'PY'
import sys, re
path, block = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
pattern = re.compile(re.escape("# >>> daifuku_autonomous >>>") + r".*?"
                     + re.escape("# <<< daifuku_autonomous <<<") + r"\n?", re.DOTALL)
open(path, "w", encoding="utf-8").write(pattern.sub(block + "\n", text))
PY
else
  printf '\n%s\n' "${BLOCK}" >>"${BASHRC}"
fi
chown "${DAIFUKU_USER}:${DAIFUKU_USER}" "${BASHRC}"

# ---------------------------------------------------------------------------
# rtmouse カーネルモジュール
# ---------------------------------------------------------------------------

if [[ "${DAIFUKU_WITH_RTMOUSE}" == "1" ]]; then
  step "rtmouseカーネルモジュールを導入"
  if [[ "${DAIFUKU_MODEL}" == "pi5" ]]; then
    echo "警告: rt-net/RaspberryPiMouse は Raspberry Pi 5 を公式サポートしていません" >&2
  fi
  # コンテナからは insmod できないので、ここだけはホストの責務。
  # config.txt 側の設定（i2c_arm / spi / anyspi オーバレイ / i2c_baudrate）は
  # create_image.py が書き込み済み。
  if apt_install build-essential "linux-headers-$(uname -r)"; then
    RTMOUSE_SRC=/opt/RaspberryPiMouse
    if [[ -d "${RTMOUSE_SRC}/.git" ]]; then
      git -C "${RTMOUSE_SRC}" pull --ff-only || true
    else
      git clone --depth 1 https://github.com/rt-net/RaspberryPiMouse.git "${RTMOUSE_SRC}" ||
        soft_fail "RaspberryPiMouseの取得"
    fi
    if [[ -x "${RTMOUSE_SRC}/utils/build_install.bash" ]]; then
      # set_configs.bash は config.txt を書き換えて再起動を求めるスクリプト。
      # 同じ内容を create_image.py が先に入れてあるので走らせない。
      (cd "${RTMOUSE_SRC}/utils" && ./build_install.bash) ||
        soft_fail "rtmouseのビルド/導入"
      echo rtmouse >/etc/modules-load.d/rtmouse.conf
    else
      soft_fail "build_install.bash が見つかりません"
    fi
  else
    soft_fail "linux-headers-$(uname -r) を導入できずrtmouseをビルドできません"
  fi
fi

# ---------------------------------------------------------------------------
# 自前の本体ドライバ用の権限
# ---------------------------------------------------------------------------
# raspicat_driver (robot_bringup.launch.py の driver:=original) は PWM (sysfs) と
# gpiochip と I2C をユーザ空間から直接叩く。コンテナは uid/gid 1000 で走り補助
# グループを持たないため、所有権をホスト側で渡しておく。
#
# 機種によらず入れる。Pi 5 では必須（rtmouse が動かないので公式実装を選べない）、
# Pi 4 では driver:=original を選んだときだけ効く（選ばなければ使われないだけ）。
# 詳細は docs/setup/raspberry-pi-4.md と raspberry-pi-5.md。

step "自前ドライバ用のudevルールを導入"
RULES_SRC="${WORKSPACE}/tools/image/udev/99-daifuku-raspicat.rules"
if [[ -f "${RULES_SRC}" ]]; then
  install -m 0644 "${RULES_SRC}" /etc/udev/rules.d/99-daifuku-raspicat.rules &&
    udevadm control --reload &&
    udevadm trigger --subsystem-match=pwm --subsystem-match=gpio \
      --subsystem-match=i2c-dev ||
    soft_fail "udevルールの導入"
  # 旧名。残っていると同じ内容が二重に効く。
  rm -f /etc/udev/rules.d/99-daifuku-pi5.rules
else
  soft_fail "${RULES_SRC} が見つかりません（リポジトリの取得に失敗している）"
fi

# Pi 5 だけ。RP1 の clk_pwm0 は親クロックが選ばれておらずレート 0 のままなので、
# pwm-2chan を当てただけでは period の書き込みが EINVAL で弾かれる。親を xosc に
# 名指しするオーバレイを足す（理由は tools/image/overlays/daifuku-pwm-clk.dts）。
# config.txt 側の dtoverlay= 行は create_image.py が書く。
#
# .dtbo は**ここで作る**。dtc はホストにあるとは限らない（開発ホストは Windows）
# ので、リポジトリには .dts だけを置き、コンパイルは機体でやる。効き始めるのは
# プロビジョニング後の再起動から（udev ルールと同じ）。
#
# config.txt の dtoverlay= 行も**ここで足す**。create_image.py に書かせると、初回
# 起動のあいだだけ「実体の無いオーバレイを指す config.txt」ができてしまう。
# ファームウェアがそれを飛ばすのか止まるのかは確かめていないので、.dtbo を置いた
# 直後に足して、行とファイルが別々に存在する瞬間を作らない。
if [[ "${DAIFUKU_MODEL}" == "pi5" ]]; then
  step "PWM親クロックのDTオーバレイを導入"
  DTS_SRC="${WORKSPACE}/tools/image/overlays/daifuku-pwm-clk.dts"
  BOOT_DIR="/boot/firmware"
  [[ -d "${BOOT_DIR}/overlays" ]] || BOOT_DIR="/boot"
  if [[ -f "${DTS_SRC}" ]]; then
    # -@ が要る（&pwm0 / &rp1_clocks / &clk_xosc のラベル参照を __fixups__ として
    # 残さないと、オーバレイの適用時に解決できない）。
    if dtc -@ -I dts -O dtb -o /tmp/daifuku-pwm-clk.dtbo "${DTS_SRC}" &&
      install -m 0644 /tmp/daifuku-pwm-clk.dtbo "${BOOT_DIR}/overlays/daifuku-pwm-clk.dtbo"; then
      if ! grep -q '^dtoverlay=daifuku-pwm-clk' "${BOOT_DIR}/config.txt"; then
        # pwm-2chan の直後に置く（順序が逆でも当たるが、読む側が追えるように）。
        if grep -q '^dtoverlay=pwm-2chan' "${BOOT_DIR}/config.txt"; then
          sed -i '/^dtoverlay=pwm-2chan/a dtoverlay=daifuku-pwm-clk' "${BOOT_DIR}/config.txt"
        else
          echo 'dtoverlay=daifuku-pwm-clk' >>"${BOOT_DIR}/config.txt"
        fi
        echo "  ${BOOT_DIR}/config.txt に dtoverlay=daifuku-pwm-clk を足しました"
      fi
    else
      soft_fail "PWM親クロックのオーバレイのコンパイル"
    fi
    rm -f /tmp/daifuku-pwm-clk.dtbo
  else
    soft_fail "${DTS_SRC} が見つかりません（リポジトリの取得に失敗している）"
  fi
fi

# ---------------------------------------------------------------------------
# Dockerイメージ
# ---------------------------------------------------------------------------

# 本体ドライバの選択。rtmouseを載せたPi 4だけが公式実装(raspimouse)を使える。
# Pi 5とrtmouse無しのPi 4は自前実装(original)。取り違えるとノードが起動時に
# 落ちる/拒否するので、ここで機種から決めてしまう。
if [[ "${DAIFUKU_MODEL}" != "pi5" && "${DAIFUKU_WITH_RTMOUSE}" == "1" ]]; then
  COMPOSE_ENTRY="docker/raspberrypi/compose.rt.yaml"
else
  COMPOSE_ENTRY="docker/raspberrypi/compose.original.yaml"
fi
COMPOSE_FILE="${WORKSPACE}/${COMPOSE_ENTRY}"

# リポジトリルートの .env に置いておくと、以後 `docker compose` を -f 無しで
# 叩ける（Composeが読むのはカレントディレクトリの .env なので、WORKSPACEから
# 実行すること）。人が書き換えた .env は上書きしない。
if [[ -d "${WORKSPACE}" && ! -f "${WORKSPACE}/.env" ]]; then
  step ".env を作成（COMPOSE_FILE=${COMPOSE_ENTRY}）"
  if [[ -f "${WORKSPACE}/.env.example" ]]; then
    sed "s|^COMPOSE_FILE=.*|COMPOSE_FILE=${COMPOSE_ENTRY}|" \
      "${WORKSPACE}/.env.example" >"${WORKSPACE}/.env"
  else
    echo "COMPOSE_FILE=${COMPOSE_ENTRY}" >"${WORKSPACE}/.env"
  fi
  chown "${DAIFUKU_USER}:${DAIFUKU_USER}" "${WORKSPACE}/.env"
fi

if [[ "${DAIFUKU_BUILD_ON_FIRST_BOOT}" == "1" && -f "${COMPOSE_FILE}" ]]; then
  step "Dockerイメージをビルド（Pi 4では数時間かかります）"
  docker compose -f "${COMPOSE_FILE}" build \
    --build-arg BUILD_JOBS="${DAIFUKU_BUILD_JOBS}" ||
    soft_fail "docker compose build"
fi

# ---------------------------------------------------------------------------
# 仕上げ
# ---------------------------------------------------------------------------

date -Is >"${STATE_DIR}/provisioned"

echo
echo "======================================================================"
if ((${#FAILED_STEPS[@]} > 0)); then
  echo "完了（未了の手順あり）: $(date -Is)"
  for item in "${FAILED_STEPS[@]}"; do
    echo "  - ${item}"
  done
  echo "  ログ: ${LOG_FILE}"
else
  echo "完了: $(date -Is)"
fi
echo
echo "次の手順:"
echo "  1. 再起動する（グループとカーネルモジュールの反映）"
echo "         sudo reboot"
if [[ "${DAIFUKU_BUILD_ON_FIRST_BOOT}" != "1" ]]; then
  echo "  2. Dockerイメージをビルドする（Pi 4で数時間）"
  echo "         cd ${WORKSPACE}"
  echo "         docker compose build --build-arg BUILD_JOBS=${DAIFUKU_BUILD_JOBS}"
  echo "  3. 起動する"
else
  echo "  2. 起動する"
fi
echo "         cd ${WORKSPACE}"
echo "         docker compose up -d   # -f は要らない (.env の COMPOSE_FILE=${COMPOSE_ENTRY})"
echo "======================================================================"
