# 取得用ステージを分け、最終イメージにはshell.nixの依存だけを置く。
ARG NIX_IMAGE=nixos/nix@sha256:b49cb33f3a7e5cf3933ad8774529de0e3807ec5cf54987aa7ccdfb49f2fc55ed
FROM ${NIX_IMAGE} AS nix-bootstrap

FROM nix-bootstrap AS nix-environment
ARG NIX_IMAGE
ARG TARGETARCH
ARG BUILD_JOBS=2
ENV BUILD_JOBS=${BUILD_JOBS}
COPY shell.nix /tmp/nix-env/shell.nix
COPY docker/common/build-nix-environment.sh /tmp/build-nix-environment.sh
RUN --mount=type=cache,from=nix-bootstrap,source=/nix,target=/nix,id=daifuku-nix-${TARGETARCH}-${NIX_IMAGE},sharing=locked \
    bash /tmp/build-nix-environment.sh true false

FROM scratch
COPY --from=nix-environment /export/ /
COPY --chmod=755 docker/common/entrypoint.sh docker/dev/tools/build-workspace.sh /usr/local/lib/daifuku/
ENV ROS_DISTRO=humble AUTONOMOUS_WS=/workspaces/daifuku_autonomous
ENV PATH=/bin:/usr/bin:/usr/local/bin
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV GIT_SSL_CAINFO=/etc/ssl/certs/ca-certificates.crt
ENV RMW_IMPLEMENTATION=rmw_fastrtps_cpp
ENV QT_QPA_PLATFORM=xcb
ENV FONTCONFIG_FILE=/etc/fonts/fonts.conf
WORKDIR /workspaces/daifuku_autonomous
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]
