#!/usr/bin/env bash
# 種 02 インフラ / 項 02 ネットワーク。**ROS も Docker も要らない** (読むのは
# /proc/sys と /proc/net/snmp だけ)。
#
# 狙いは 1 つ。**大きなトピックが遅いのは DDS ではなく IP の再組み立てが溢れて
# いるから**、という切り分けをここで済ませておくこと。/map 22.5MiB が 95 秒
# かかっていたのが ipfrag_high_thresh を上げて 4.1 秒になった。溢れは UDP の
# カウンタには出ないので、素で見ていると「DDS が遅い」に見える。
#
# 値は tools/image/provision.sh が /etc/sysctl.d/60-ros2-dds.conf に置くもの。
# **判定は全部 WARN 止まり。** 受信側の PC は素のままのことが多く、そちらは
# 機体の健全性ではない (それでも遅さの原因になるので黙らせはしない)。

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

section 0202 "ネットワーク"

if [[ ! -r /proc/sys/net/core/rmem_max ]]; then
  skip "カーネルパラメータ" "/proc/sys を読めない (Windows の開発ホストなど)"
  finish 0
fi

# sysctl_at_least パス 最低値 — provision.sh が置く値と突き合わせる。
sysctl_at_least() {
  local v
  v="$(cat "$1" 2>/dev/null)"
  [[ -n "${v}" ]] || {
    echo "読めない"
    return 1
  }
  echo "${v} (推奨 $2 以上)"
  ((v >= $2))
}

item_warn "net.core.rmem_max が 16MB 以上" \
  sysctl_at_least /proc/sys/net/core/rmem_max 16777216
item_warn "net.core.rmem_default が 16MB 以上" \
  sysctl_at_least /proc/sys/net/core/rmem_default 16777216
item_warn "net.ipv4.ipfrag_high_thresh が 128MB 以上" \
  sysctl_at_least /proc/sys/net/ipv4/ipfrag_high_thresh 134217728
on_fail && diagnose "再組み立てのバッファが既定のまま" \
  "/map や大きな点群の受信だけが極端に遅いか|IP の再組み立てが溢れている。**UDP のカウンタには出ない**ので DDS が遅いように見える|sudo sysctl -w net.ipv4.ipfrag_high_thresh=134217728。恒久化は /etc/sysctl.d/60-ros2-dds.conf" \
  "この機械は受信側の PC か|機体は provision.sh で入っているが、受信側は素のまま|同じ値を受信側にも入れる (tools/image/README.md)"

# 症状そのもの。溢れた数が増えていれば、上の推奨値が入っていても足りていない。
check_reasm_fails() {
  local reqds fails
  reqds="$(snmp_counter Ip ReasmReqds)"
  fails="$(snmp_counter Ip ReasmFails)"
  [[ -n "${fails}" ]] || {
    echo "/proc/net/snmp を読めない"
    return 1
  }
  echo "ReasmReqds ${reqds:-?} / ReasmFails ${fails}"
  ((fails == 0))
}
item_warn "IP の再組み立てに失敗が無い" check_reasm_fails

# 既定のままだと MID360 の点群と TF で RcvbufErrors が数十万件出て、
# ディスカバリそのものが不安定になる。
check_udp_rcvbuf_errors() {
  local e
  e="$(snmp_counter Udp RcvbufErrors)"
  [[ -n "${e}" ]] || {
    echo "/proc/net/snmp を読めない"
    return 1
  }
  echo "RcvbufErrors ${e}"
  ((e == 0))
}
item_warn "UDP の受信バッファが溢れていない" check_udp_rcvbuf_errors

finish
