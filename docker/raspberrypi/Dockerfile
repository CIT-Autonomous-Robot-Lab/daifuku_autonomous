# ベースとROS依存はダイジェスト・shell.nixで固定する。
ARG NIX_IMAGE=nixos/nix@sha256:b49cb33f3a7e5cf3933ad8774529de0e3807ec5cf54987aa7ccdfb49f2fc55ed
FROM ${NIX_IMAGE} AS nix-bootstrap

FROM nix-bootstrap AS nix-builder
ARG NIX_IMAGE
ARG TARGETARCH
ARG BUILD_JOBS=2
ENV BUILD_JOBS=${BUILD_JOBS}
COPY shell.nix /tmp/nix-env/shell.nix
COPY docker/common/build-nix-environment.sh /tmp/build-nix-environment.sh

FROM nix-builder AS cpu-environment
RUN --mount=type=cache,from=nix-bootstrap,source=/nix,target=/nix,id=daifuku-nix-${TARGETARCH}-${NIX_IMAGE},sharing=locked \
    bash /tmp/build-nix-environment.sh false false

FROM nix-builder AS vulkan-environment
RUN --mount=type=cache,from=nix-bootstrap,source=/nix,target=/nix,id=daifuku-nix-${TARGETARCH}-${NIX_IMAGE},sharing=locked \
    bash /tmp/build-nix-environment.sh false true

FROM scratch AS runtime-base
COPY --chmod=755 docker/common/entrypoint.sh docker/raspberrypi/scripts/build-workspace.sh /usr/local/lib/daifuku/
ENV ROS_DISTRO=humble ROS_WS=/opt/ros_ws
ENV PATH=/bin:/usr/bin:/usr/local/bin
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV GIT_SSL_CAINFO=/etc/ssl/certs/ca-certificates.crt
ENV RMW_IMPLEMENTATION=rmw_fastrtps_cpp
WORKDIR /opt/ros_ws
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]

# GPU依存は明示したターゲットだけに入れる。
FROM runtime-base AS vulkan
COPY --from=vulkan-environment /export/ /

# ターゲット未指定ではGUIを含まないCPUイメージを作る。
FROM runtime-base AS runtime
COPY --from=cpu-environment /export/ /
