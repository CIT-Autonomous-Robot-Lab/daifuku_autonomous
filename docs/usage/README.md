# 利用ガイド

セットアップ済みのRaspberry Pi Catで地図作成と自律移動を行います。初回は次の順で進めてください。

1. [地図を作成する](mapping.md)
2. [保存した地図で自律移動する](navigation.md)
3. [確認コマンドと終了方法を把握する](operations.md)

調整や仕組みを確認する場合:

- [設定リファレンス](configuration.md)
- [構成とパッケージ](architecture.md)
- [トラブルシューティング](troubleshooting.md)

## 起動前の安全確認

- 周囲に人や障害物がなく、走行区域を確保している
- 緊急停止スイッチをすぐ操作できる
- `/cmd_vel`の並進・旋回方向が機体と一致する
- オドメトリとTFに大きな飛びや遅延がない
- LiDARの障害物が正しい位置に表示される
- 地図と実環境が一致する

## コマンドの読み替え

各ページはネイティブ環境の`ros2 ...`を基本形として記載します。軽量Docker環境では先頭に次を付け、RVizを起動しないよう`use_rviz:=false`を追加します。

```bash
docker compose -f docker/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 ...
```

複雑なコマンドは先にコンテナシェルへ入ると扱いやすくなります。

```bash
bash docker/tools/shell.sh
```

モーター電源、遠隔操作、状態確認は`docker/tools/control.sh`にまとめています。

```bash
bash docker/tools/control.sh status
bash docker/tools/control.sh teleop keyboard
```

一覧は[日常操作と確認](operations.md#controlshで操作する)を参照してください。
