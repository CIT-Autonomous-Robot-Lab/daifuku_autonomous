# config/ 移行の残り — Linux で確かめること

移行そのものは入っている（`deb529c` 以降）。**ここに残っているのは実機か dev
コンテナでしか回せない確認だけ**で、全部通ったらこのファイルを消す。開発ホストは
Windows なので、**`daifuku_config` は一度も colcon で建っていない。**

## 先に install/ を作り直す

```bash
rm -rf install build
```

`git mv` で設定を出したので、両パッケージの share には古い symlink が残る。
リンク切れなので `params._config_files` は飛ばすが、**飛ばしたものは指紋の対象からも
外れる**ので、設定が 1 つ消えたことに気付けない（2026-08-07 に `daifuku_stack` の
share へ `config/robot/joy_teleop.yaml` が居残ったのと同じ形）。掃除ではなく
作り直すこと。

## 通すもの

```bash
docker compose up -d                                          # daifuku_config が建つか
ros2 launch daifuku_stack navigation.launch.py --show-args    # params_dir などの既定が config/stack/ を指すか
ros2 launch daifuku_bringup robot_bringup.launch.py           # 上がるか、config_sentinel が指紋を取れるか
find install -xtype l                                         # 空であること
tools/site.sh map_tsudanuma && tools/site.sh map_19f          # site の読み書きが通るか
colcon test --packages-select daifuku_config daifuku_bringup daifuku_stack daifuku_config_manager
```

`colcon test` で見たいのは `daifuku_config`（新しい `ament_cmake` パッケージ）の
lint_cmake と xmllint。CI の lint ジョブでも同じものが走る。

## 検査が生きていることも見る

`config/overrides/map_19f.yaml` の `daifuku_bringup:` の下にでたらめなノード名を
1 行足して、**起動時に落ちる**こと（移行前と同じメッセージで）。ここが黙って通ると、
`config_root` が親の `config/` を指してしまっている。

Windows で回せる範囲（overrides の全ノード名が新しい木で解決すること、flake8、
全 md の相対リンク）は確認済み。
