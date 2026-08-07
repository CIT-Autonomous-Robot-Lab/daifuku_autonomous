# 利用ガイド

セットアップ済みのRaspberry Pi Catで地図作成と自律移動を行います。初回は次の順で進めてください。

1. [地図を作成する](mapping.md)
2. [保存した地図で自律移動する](navigation.md)
3. [確認コマンドと終了方法を把握する](operations.md)

調整や仕組みを確認する場合:

- [操作パネル（rqt）](control-panel.md)
- [ゲームパッドで操作する](joystick.md)
- [設定リファレンス](configuration.md)
- [構成とパッケージ](architecture.md)
- [走行を記録して再生する](recording.md)
- [トラブルシューティング](troubleshooting.md)

## 起動前の安全確認

- 周囲に人や障害物がなく、走行区域を確保している
- 緊急停止スイッチをすぐ操作できる
- 速度指令（遠隔操作は`/cmd_vel_teleop`）の並進・旋回方向が機体と一致する
- オドメトリとTFに大きな飛びや遅延がない
- LiDARの障害物が正しい位置に表示される
- 地図と実環境が一致する

## コマンドの読み替え

各ページはネイティブ環境の`ros2 ...`を基本形として記載します。軽量Docker環境では先頭に次を付けます。RVizは既定で起動しないため（`use_rviz`の既定は`false`）、追加の指定は要りません。

```bash
docker compose exec ros2 \
  /ros_entrypoint.sh ros2 ...
```

**`docker compose`は必ずリポジトリルートで実行します。** どのComposeファイルを使うかは
リポジトリルートの`.env`（`COMPOSE_FILE`）が持っているので、別のディレクトリからだと
`no configuration file provided`で止まります。`.env`が無ければ`.env.example`から
作ってください（[`docker/raspberrypi/README.md`](../../docker/raspberrypi/README.md#起動)）。

コマンドが長くなる場合は、先にコンテナのシェルへ入ったほうが扱いやすくなります。

```bash
bash docker/raspberrypi/tools/shell.sh
```

モーター電源、遠隔操作、状態確認は`docker/raspberrypi/tools/control.sh`にまとめています。

```bash
bash docker/raspberrypi/tools/control.sh status
bash docker/raspberrypi/tools/control.sh teleop keyboard
```

一覧は[日常操作と確認](operations.md#controlshで操作する)を参照してください。
