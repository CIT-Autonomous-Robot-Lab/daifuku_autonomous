#!/usr/bin/env python3
"""Raspberry Pi 4 / 5 用のSDカードイメージを作成してセットアップする。

ベースイメージ（Ubuntu Server arm64 preinstalled）を取得してSDカードへ書き込み、
ブートパーティションへcloud-initの設定を注入します。初回起動時に
``provision.sh``（同ディレクトリ）が走り、Docker・DDSチューニング・
rtmouseカーネルモジュール・本リポジトリの取得までを済ませます。

Windows / Linux / macOSで動きます。標準ライブラリのみを使用します。

  # SDカードの候補を一覧する
  python3 tools/image/create_image.py devices

  # 取得 → 書き込み → 設定注入 をまとめて実行（要 管理者 / root）
  python3 tools/image/create_image.py all --model pi4 --device 2 \\
      --ssh-key ~/.ssh/id_ed25519.pub

  # 既に焼いてあるカードへ設定だけ入れ直す
  python3 tools/image/create_image.py configure --model pi4 --boot-dir E:\\

サブコマンド:
  devices    書き込み先候補のディスクを一覧する
  fetch      ベースイメージを取得・検証・展開してキャッシュへ置く
  flash      キャッシュのイメージをディスクへ書き込む
  configure  ブートパーティションへcloud-initとconfig.txtを書く
  all        fetch → flash → configure

本体ドライバとconfig.txtの関係:
  rtmouseを入れるか（既定・Pi 4のみ）で、config.txtに入るオーバレイが変わります。
  入れる場合は公式実装（driver:=raspimouse）でPWMはrtmouseの直書き、入れない
  場合（--no-rtmouse、およびPi 5）は自前実装（driver:=original）が
  ハードウェアPWMを使うのでpwm-2chanオーバレイが入ります。両方を同時に動かす
  ことはできません。

Raspberry Pi 5について:
  Ubuntu 22.04はPi 5に対応しません（--model pi5 は既定でUbuntu 24.04）。
  またRaspberry Pi Catのrtmouseカーネルモジュールも公式にはPi 5非対応です。
  --model pi5 では既定でrtmouseの導入を行いません。ナビゲーション本体は
  Dockerコンテナ側なのでPi 5でも動きます。
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import gzip
import hashlib
import json
import lzma
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]

if sys.version_info < (3, 8):
    raise SystemExit("Python 3.8以上が必要です")


def _use_utf8_console() -> None:
    """日本語のメッセージがcp932/cp1252のコンソールで落ちないようにする。"""
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:  # noqa: BLE001 - リダイレクト時などは失敗してよい
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


_use_utf8_console()

# Ubuntu 22.04 does not boot on a Raspberry Pi 5 (Canonical will not backport
# the Pi 5 platform support to jammy).  Pi 4 stays on 22.04 because the native
# ROS 2 Humble escape hatch in tools/setup/ needs jammy.
DEFAULT_RELEASE = {"pi4": "22.04", "pi5": "24.04"}

CDIMAGE_BASE = "https://cdimage.ubuntu.com/releases/{release}/release/"
IMAGE_PATTERN = re.compile(
    r"^ubuntu-(\d+\.\d+(?:\.\d+)?)-preinstalled-server-arm64\+raspi\.img\.xz$"
)

DEFAULT_REPO_URL = "https://github.com/CIT-Autonomous-Robot-Lab/daifuku_autonomous.git"
# ブートパーティションに置くリポジトリのスナップショット。Piから見た位置。
REPO_ARCHIVE_NAME = "daifuku-repo.tar.gz"
REPO_ARCHIVE_ON_PI = f"/boot/firmware/{REPO_ARCHIVE_NAME}"
DEFAULT_ROBOT_IP = "192.168.1.50"
DEFAULT_ROS_DOMAIN_ID = 90

BEGIN_MARK = "# >>> daifuku_autonomous >>>"
END_MARK = "# <<< daifuku_autonomous <<<"

SECTOR = 512
CHUNK = 4 * 1024 * 1024

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


# --------------------------------------------------------------------------
# 出力とプロセス実行
# --------------------------------------------------------------------------


def log(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"警告: {msg}", file=sys.stderr, flush=True)


def die(msg: str, code: int = 1) -> "None":
    print(f"エラー: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def run(cmd, check=True, capture=True):
    """外部コマンドを実行する。cmdはリスト。"""
    proc = subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        die(f"コマンドが失敗しました: {' '.join(map(str, cmd))}\n{detail}")
    return proc


def powershell(script: str, check=True):
    """PowerShellを実行して標準出力を返す（Windows専用）。"""
    proc = run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        check=check,
    )
    return proc.stdout


def human(size: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


class Progress:
    """1行を書き換えながら進捗を出す。"""

    def __init__(self, label: str, total: int | None):
        self.label = label
        self.total = total
        self.done = 0
        self.started = time.monotonic()
        self.last = 0.0

    def advance(self, n: int) -> None:
        self.done += n
        now = time.monotonic()
        if now - self.last < 0.5:
            return
        self.last = now
        self._render(now)

    def _render(self, now: float) -> None:
        elapsed = max(now - self.started, 1e-6)
        rate = self.done / elapsed
        if self.total:
            pct = 100.0 * self.done / self.total
            text = (
                f"    {self.label}: {pct:5.1f}%  "
                f"{human(self.done)} / {human(self.total)}  {human(rate)}/s"
            )
        else:
            text = f"    {self.label}: {human(self.done)}  {human(rate)}/s"
        print(text.ljust(78), end="\r", file=sys.stderr, flush=True)

    def close(self) -> None:
        self._render(time.monotonic())
        print(file=sys.stderr, flush=True)


def require_privileges() -> None:
    """ディスクへの書き込みには管理者権限が要る。"""
    if IS_WINDOWS:
        try:
            elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001 - 判定できなければ後続の書き込みで落ちる
            elevated = False
        if not elevated:
            die("管理者権限が必要です。管理者としてPowerShellを開き直してください。")
    else:
        if os.geteuid() != 0:
            die("root権限が必要です。sudoを付けて実行してください。")


# --------------------------------------------------------------------------
# fetch: ベースイメージの取得
# --------------------------------------------------------------------------


def http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "daifuku-image/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def resolve_base_image(release: str) -> tuple[str, str, str]:
    """SHA256SUMSから最新のpreinstalled-serverイメージを選ぶ。

    ポイントリリース（22.04.5 / 24.04.4 ...）はURLに埋まっているので、
    ファイル名を決め打ちすると数か月で腐る。チェックサム一覧から拾えば
    ファイル名の解決と完全性の検証を同時に済ませられる。
    """
    base = CDIMAGE_BASE.format(release=release)
    log(f"ベースイメージを検索: {base}SHA256SUMS")
    try:
        sums = http_get(base + "SHA256SUMS").decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        die(f"SHA256SUMSを取得できませんでした: {exc}")

    candidates = []
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        digest, name = parts[0], parts[1].lstrip("*")
        match = IMAGE_PATTERN.match(name)
        if match:
            version = tuple(int(x) for x in match.group(1).split("."))
            candidates.append((version, name, digest))

    if not candidates:
        die(f"Ubuntu {release} にarm64+raspiのpreinstalled-serverイメージがありません")

    version, name, digest = max(candidates)
    log(f"選択: {name}")
    return base + name, name, digest


def sha256_of(path: Path, label: str) -> str:
    digest = hashlib.sha256()
    progress = Progress(label, path.stat().st_size)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
            progress.advance(len(block))
    progress.close()
    return digest.hexdigest()


def download(url: str, dest: Path) -> None:
    partial = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "daifuku-image/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0) or None
        progress = Progress("ダウンロード", total)
        with partial.open("wb") as handle:
            for block in iter(lambda: response.read(CHUNK), b""):
                handle.write(block)
                progress.advance(len(block))
        progress.close()
    partial.replace(dest)


def decompress(archive: Path, dest: Path) -> None:
    partial = dest.with_suffix(dest.suffix + ".part")
    progress = Progress("展開", None)
    with lzma.open(archive, "rb") as src, partial.open("wb") as dst:
        for block in iter(lambda: src.read(CHUNK), b""):
            dst.write(block)
            progress.advance(len(block))
    progress.close()
    partial.replace(dest)


def cache_dir(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
    elif IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = Path(base) / "daifuku_autonomous" / "images"
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        path = Path(base) / "daifuku_autonomous" / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def fetch_image(release: str, cache: Path, force: bool = False) -> Path:
    url, name, digest = resolve_base_image(release)
    archive = cache / name
    image = cache / name[: -len(".xz")]

    if image.exists() and not force:
        log(f"展開済みイメージを再利用: {image}")
        return image

    if archive.exists() and not force:
        log(f"ダウンロード済み: {archive}")
    else:
        log(f"ダウンロード: {url}")
        download(url, archive)

    log("SHA256を検証")
    actual = sha256_of(archive, "検証")
    if actual != digest:
        archive.unlink(missing_ok=True)
        die(
            "SHA256が一致しません。ダウンロードが壊れています。\n"
            f"  期待値: {digest}\n  実際:   {actual}"
        )

    log(f"展開: {image}")
    decompress(archive, image)
    log(f"イメージ: {image} ({human(image.stat().st_size)})")
    return image


# --------------------------------------------------------------------------
# devices: 書き込み先の列挙
# --------------------------------------------------------------------------


class Device:
    def __init__(self, ident, path, model, size, removable, system):
        self.id = str(ident)
        self.path = path
        self.model = model or "(不明)"
        self.size = int(size or 0)
        self.removable = bool(removable)
        self.system = bool(system)

    def describe(self) -> str:
        flags = []
        flags.append("リムーバブル" if self.removable else "内蔵")
        if self.system:
            flags.append("システムディスク")
        return f"  [{self.id}] {self.path}  {human(self.size):>10}  {self.model}  ({', '.join(flags)})"


def list_devices() -> list[Device]:
    if IS_WINDOWS:
        return _list_devices_windows()
    if IS_MACOS:
        return _list_devices_macos()
    if IS_LINUX:
        return _list_devices_linux()
    die(f"未対応のプラットフォームです: {sys.platform}")
    return []


def _list_devices_windows() -> list[Device]:
    out = powershell(
        "Get-Disk | Select-Object Number,FriendlyName,Size,BusType,IsSystem,IsBoot "
        "| ConvertTo-Json -Compress -Depth 3"
    )
    try:
        data = json.loads(out or "[]")
    except json.JSONDecodeError:
        die("Get-Diskの出力を解釈できませんでした")
        return []
    if isinstance(data, dict):
        data = [data]

    devices = []
    for disk in data:
        bus = (disk.get("BusType") or "").strip()
        # BusTypeは数値で返ることがある。7=USB, 8=RAID, 11=SD, 12=MMC。
        removable = bus in ("USB", "SD", "MMC", "7", "11", "12", 7, 11, 12)
        devices.append(
            Device(
                ident=disk.get("Number"),
                path=f"\\\\.\\PhysicalDrive{disk.get('Number')}",
                model=disk.get("FriendlyName"),
                size=disk.get("Size"),
                removable=removable,
                system=bool(disk.get("IsSystem")) or bool(disk.get("IsBoot")),
            )
        )
    return devices


def _list_devices_linux() -> list[Device]:
    # HOTPLUG列は比較的新しいutil-linuxにしかない。無ければRMだけで判断する。
    columns = "NAME,PATH,SIZE,MODEL,RM,TYPE,HOTPLUG"
    proc = run(["lsblk", "-J", "-b", "-d", "-o", columns], check=False)
    if proc.returncode != 0:
        proc = run(["lsblk", "-J", "-b", "-d", "-o", "NAME,PATH,SIZE,MODEL,RM,TYPE"])
    data = json.loads(proc.stdout or '{"blockdevices": []}')
    root_disk = _linux_root_device()

    devices = []
    for disk in data.get("blockdevices", []):
        if disk.get("type") != "disk":
            continue
        name = disk.get("name") or ""
        devices.append(
            Device(
                ident=name,
                path=disk.get("path") or f"/dev/{name}",
                model=(disk.get("model") or "").strip(),
                size=disk.get("size"),
                removable=bool(disk.get("rm")) or bool(disk.get("hotplug")),
                system=root_disk is not None and root_disk == name,
            )
        )
    return devices


def _linux_root_device() -> str | None:
    proc = run(["findmnt", "-n", "-o", "SOURCE", "/"], check=False)
    if proc.returncode != 0:
        return None
    source = (proc.stdout or "").strip()
    if not source.startswith("/dev/"):
        return None
    proc = run(["lsblk", "-no", "PKNAME", source], check=False)
    parent = (proc.stdout or "").strip().splitlines()
    return parent[0] if parent else source[len("/dev/") :]


def _list_devices_macos() -> list[Device]:
    out = run(["diskutil", "list", "-plist", "physical"], capture=True).stdout
    data = plistlib.loads(out.encode())
    devices = []
    for name in data.get("WholeDisks", []):
        info_out = run(["diskutil", "info", "-plist", name]).stdout
        info = plistlib.loads(info_out.encode())
        devices.append(
            Device(
                ident=name,
                path=f"/dev/{name}",
                model=info.get("MediaName"),
                size=info.get("TotalSize"),
                removable=bool(info.get("Removable") or info.get("RemovableMediaOrExternalDevice")),
                system=bool(info.get("SystemImage")) or info.get("MountPoint") == "/",
            )
        )
    return devices


def resolve_device(spec: str) -> Device:
    devices = list_devices()
    for device in devices:
        if spec == device.id or spec == device.path or spec == f"/dev/{device.id}":
            return device
    # Windowsは "2" でも "\\.\PhysicalDrive2" でも指定できるようにする。
    if IS_WINDOWS and spec.isdigit():
        for device in devices:
            if device.id == spec:
                return device
    die(
        f"ディスク '{spec}' が見つかりません。次のコマンドで候補を確認してください。\n"
        f"  python3 {Path(__file__).name} devices"
    )
    return devices[0]


# --------------------------------------------------------------------------
# flash: ディスクへの書き込み
# --------------------------------------------------------------------------


def confirm(device: Device, image: Path, assume_yes: bool) -> None:
    print()
    print("次のディスクの内容をすべて消去して書き込みます。")
    print(device.describe())
    print(f"  イメージ: {image} ({human(image.stat().st_size)})")
    print()
    if assume_yes:
        return
    answer = input("続行するには 'yes' と入力してください: ").strip()
    if answer != "yes":
        die("中止しました", code=130)


def check_device_safety(device: Device, max_size_gb: int, force: bool) -> None:
    if device.system and not force:
        die(f"{device.path} はシステムディスクです。書き込みません。")
    if not device.removable and not force:
        die(
            f"{device.path} はリムーバブルディスクとして認識されていません。"
            "SDカードリーダーによっては内蔵扱いになります。意図した相手であれば "
            "--force を付けてください。"
        )
    limit = max_size_gb * 1024**3
    if device.size > limit and not force:
        die(
            f"{device.path} は {human(device.size)} で上限 {max_size_gb} GB を超えます。"
            "外付けHDDなどを誤って指定していないか確認し、意図通りなら --force か "
            "--max-size-gb を指定してください。"
        )


def flash(image: Path, device: Device, assume_yes: bool, dry_run: bool) -> None:
    if not image.is_file():
        die(f"イメージがありません: {image}")
    if device.size and image.stat().st_size > device.size:
        die(
            f"イメージ({human(image.stat().st_size)})が"
            f"{device.path}の容量({human(device.size)})を超えています"
        )
    confirm(device, image, assume_yes)
    if dry_run:
        log("--dry-run のため書き込みは行いません")
        return

    if IS_WINDOWS:
        _flash_windows(image, device)
    elif IS_MACOS:
        _flash_macos(image, device)
    else:
        _flash_linux(image, device)
    log("書き込み完了")


def _write_raw(image: Path, target: str) -> None:
    """イメージをブロックデバイスへ生書きする。

    Windowsの \\\\.\\PhysicalDriveN とmacOSの /dev/rdiskN はセクタ境界でしか
    書けないので、最終ブロックはゼロ詰めして揃える。
    """
    total = image.stat().st_size
    progress = Progress("書き込み", total)
    with image.open("rb") as src, open(target, "rb+", buffering=0) as dst:
        while True:
            block = src.read(CHUNK)
            if not block:
                break
            if len(block) % SECTOR:
                block += b"\0" * (SECTOR - len(block) % SECTOR)
            dst.write(block)
            progress.advance(len(block))
        dst.flush()
        os.fsync(dst.fileno())
    progress.close()


def _windows_drive_letters(number: int) -> list[str]:
    """ディスク上のマウント済みボリュームのドライブレターを返す。"""
    out = powershell(
        f"$ErrorActionPreference='SilentlyContinue';"
        f"Get-Partition -DiskNumber {number} | "
        f"Where-Object DriveLetter | ForEach-Object {{ $_.DriveLetter }}",
        check=False,
    )
    return [line.strip() for line in (out or "").splitlines() if line.strip()]


def _windows_dismount_volumes(letters: list[str]) -> list[int]:
    """ボリュームをロックして外し、ハンドルを開いたまま返す。

    Windowsはマウント中のボリュームに属するセクタへの生書きを拒否するので、
    書く前にボリュームを外す必要がある。`Set-Disk -IsOffline` は
    リムーバブルメディアには使えず（"Removable media cannot be set to
    offline"）、SDカードリーダー越しのカードはまさにこれに当たる
    （check_device_safetyがリムーバブルであることを要求しているので、
    既定の経路では必ずこちらに来る）。

    ハンドルを閉じると再マウントされ、書き込みの途中から弾かれる。
    そのため書き終わるまで開いたままにする。
    """
    GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
    FILE_SHARE_READ, FILE_SHARE_WRITE = 0x00000001, 0x00000002
    OPEN_EXISTING = 3
    FSCTL_LOCK_VOLUME = 0x00090018
    FSCTL_DISMOUNT_VOLUME = 0x00090020
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    kernel32.DeviceIoControl.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
    ]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    handles: list[int] = []
    for letter in letters:
        handle = kernel32.CreateFileW(
            f"\\\\.\\{letter}:",
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if not handle or handle == INVALID_HANDLE_VALUE:
            _windows_close_handles(handles)
            die(f"ボリューム {letter}: を開けません: {ctypes.WinError()}")
        handles.append(handle)
        returned = ctypes.c_uint32(0)
        # ロックは他のプロセス（エクスプローラ、ウイルス対策）がハンドルを
        # 持っていると失敗する。ディスマウントだけでも書けるので警告に留める。
        if not kernel32.DeviceIoControl(
            handle, FSCTL_LOCK_VOLUME, None, 0, None, 0, ctypes.byref(returned), None
        ):
            warn(f"ボリューム {letter}: をロックできません（続行します）: {ctypes.WinError()}")
        if not kernel32.DeviceIoControl(
            handle, FSCTL_DISMOUNT_VOLUME, None, 0, None, 0, ctypes.byref(returned), None
        ):
            error = ctypes.WinError()
            _windows_close_handles(handles)
            die(
                f"ボリューム {letter}: を外せません: {error}\n"
                f"エクスプローラなどでカードを開いていないか確認してください。"
            )
        log(f"ボリューム {letter}: を外しました")
    return handles


def _windows_close_handles(handles: list[int]) -> None:
    for handle in handles:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


def _flash_windows(image: Path, device: Device) -> None:
    number = device.id
    powershell(f"Set-Disk -Number {number} -IsReadOnly $false", check=False)
    handles = _windows_dismount_volumes(_windows_drive_letters(number))
    try:
        _write_raw(image, device.path)
    finally:
        _windows_close_handles(handles)
    # 書いたパーティションテーブルをOSに読み直させる（configureが
    # ブートパーティションを探せるようにする）。
    powershell(f"Update-Disk -Number {number}", check=False)
    time.sleep(3)


def _flash_linux(image: Path, device: Device) -> None:
    out = run(["lsblk", "-nlo", "MOUNTPOINT", device.path], check=False).stdout or ""
    for mountpoint in filter(None, (line.strip() for line in out.splitlines())):
        log(f"アンマウント: {mountpoint}")
        run(["umount", mountpoint], check=False)
    _write_raw(image, device.path)
    run(["blockdev", "--rereadpt", device.path], check=False)
    run(["udevadm", "settle"], check=False)
    time.sleep(2)


def _flash_macos(image: Path, device: Device) -> None:
    log(f"アンマウント: {device.path}")
    run(["diskutil", "unmountDisk", device.path])
    raw = device.path.replace("/dev/disk", "/dev/rdisk")
    _write_raw(image, raw)
    run(["diskutil", "mountDisk", device.path], check=False)
    time.sleep(2)


# --------------------------------------------------------------------------
# configure: ブートパーティションへの設定注入
# --------------------------------------------------------------------------


def looks_like_boot_dir(path: Path) -> bool:
    try:
        return (path / "config.txt").is_file() and (path / "cmdline.txt").is_file()
    except OSError:
        return False


def find_boot_dir(timeout: float = 60.0) -> Path:
    """system-bootパーティションのマウント先を探す。

    書き込み直後はOSがパーティションテーブルを読み直すまで見えないので、
    しばらくポーリングする。
    """
    deadline = time.monotonic() + timeout
    log("ブートパーティションを探しています")
    while True:
        for candidate in _boot_dir_candidates():
            if looks_like_boot_dir(candidate):
                log(f"ブートパーティション: {candidate}")
                return candidate
        if time.monotonic() >= deadline:
            break
        time.sleep(2)
    die(
        "ブートパーティション（config.txtのあるFATパーティション）が見つかりません。\n"
        "カードを挿し直してから、次のように場所を指定して configure を実行してください。\n"
        "  python3 tools/image/create_image.py configure --boot-dir <パス>"
    )
    return Path()


def _boot_dir_candidates():
    if IS_WINDOWS:
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            yield Path(f"{letter}:\\")
        return
    if IS_MACOS:
        volumes = Path("/Volumes")
        if volumes.is_dir():
            yield from volumes.iterdir()
        return
    for root in (Path("/media"), Path("/run/media"), Path("/mnt")):
        if not root.is_dir():
            continue
        yield root
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            yield entry
            try:
                yield from (child for child in entry.iterdir() if child.is_dir())
            except OSError:
                continue


def mount_boot_partition_linux(device: Device) -> tuple[Path, bool]:
    """Linuxでは自動マウントに頼らず、書き込んだデバイスの第1パーティションを
    名指しでマウントする。

    自動マウント済みのディレクトリを探しに行くと、別のカードが刺さっている
    ときに関係ないほうを設定してしまう。
    """
    partition = f"{device.path}p1" if re.search(r"\d$", device.path) else f"{device.path}1"
    if not Path(partition).exists():
        for candidate in _boot_dir_candidates():
            if looks_like_boot_dir(candidate):
                return candidate, False
        die(f"ブートパーティションが見つかりません: {partition}")

    # 自動マウントされていれば、その場所をそのまま使う。
    out = run(["findmnt", "-n", "-o", "TARGET", partition], check=False).stdout or ""
    mounted = out.strip().splitlines()
    if mounted and looks_like_boot_dir(Path(mounted[0])):
        return Path(mounted[0]), False

    mountpoint = Path("/mnt/daifuku-boot")
    mountpoint.mkdir(parents=True, exist_ok=True)
    log(f"マウント: {partition} -> {mountpoint}")
    run(["mount", partition, str(mountpoint)])
    return mountpoint, True


def read_ssh_keys(paths: list[str]) -> list[str]:
    keys = []
    for spec in paths:
        candidate = Path(spec).expanduser()
        if candidate.is_file():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    keys.append(line)
        elif spec.startswith(("ssh-", "ecdsa-", "sk-")):
            keys.append(spec.strip())
        else:
            die(f"SSH公開鍵として解釈できません: {spec}")
    return keys


def yaml_block(items: list[str], indent: int) -> str:
    pad = " " * indent
    return "\n".join(f"{pad}- {json.dumps(item)}" for item in items)


def render_network_config(args) -> str:
    lines = ["version: 2", "ethernets:", "  eth0:", "    optional: true"]
    if args.ip.lower() == "dhcp":
        lines.append("    dhcp4: true")
    else:
        address = args.ip if "/" in args.ip else f"{args.ip}/24"
        lines.append("    dhcp4: false")
        lines.append(f"    addresses: [{address}]")
        if args.gateway and args.gateway.lower() != "none":
            lines.append("    routes:")
            lines.append("      - to: default")
            lines.append(f"        via: {args.gateway}")
        if args.dns and args.dns.lower() != "none":
            servers = ", ".join(args.dns.split(","))
            lines.append("    nameservers:")
            lines.append(f"      addresses: [{servers}]")

    if args.wifi_ssid:
        # wlan0はマルチホームになりDDSのディスカバリを乱す。ロボットLANは
        # あくまでeth0側で、wlan0は保守用という位置づけ。
        # docker/raspberrypi/fastdds_udp_whitelist.xml がwlan0のロケータを
        # 広告させないための対策になっている。
        lines += [
            "wifis:",
            "  wlan0:",
            "    optional: true",
            "    dhcp4: true",
            "    access-points:",
            f"      {json.dumps(args.wifi_ssid)}:",
        ]
        if args.wifi_password:
            lines.append(f"        password: {json.dumps(args.wifi_password)}")
    return "\n".join(lines) + "\n"


def git_here(*arguments, check=False):
    return run(["git", "-C", str(REPO_ROOT), *arguments], check=check)


def default_repo_ref() -> str:
    """手元でチェックアウト中のブランチを既定の取得元にする。

    `main`を決め打ちにすると、作業ブランチで作ったカードなのに
    `git clone`が通ったときだけ`main`が入る、という食い違いが起きる。
    """
    if not (REPO_ROOT / ".git").exists():
        return "main"
    proc = git_here("rev-parse", "--abbrev-ref", "HEAD")
    name = (proc.stdout or "").strip()
    if proc.returncode == 0 and name and name != "HEAD":
        return name
    return "main"


def write_repo_archive(boot_dir: Path, ref: str, dry_run: bool) -> str | None:
    """手元のリポジトリのスナップショットをブートパーティションへ置く。

    リポジトリが非公開だと、Pi側からの `git clone` は認証を求めて失敗する。
    1.6MB程度なのでブートパーティションに十分収まり、ネットワークも認証情報も
    要らずにリビジョンをそのまま持ち込める。provision.sh は clone を試した
    うえで、駄目ならこれを展開する。

    どちらの経路でも同じリビジョンになるよう、`--repo-ref`と同じrefを固める。
    成功したらコミットハッシュを返す。
    """
    if not (REPO_ROOT / ".git").exists():
        warn(f"{REPO_ROOT} はgitリポジトリではないので、スナップショットを作りません")
        return None

    target_ref = ref
    if git_here("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode != 0:
        warn(f"ref '{ref}' が手元で解決できないので、HEADのスナップショットを作ります")
        target_ref = "HEAD"

    resolved = git_here("rev-parse", "--short", f"{target_ref}^{{commit}}")
    if resolved.returncode != 0:
        warn("git rev-parse に失敗したので、スナップショットを作りません")
        return None
    commit = (resolved.stdout or "").strip()

    dirty = git_here("status", "--porcelain")
    if dirty.returncode == 0 and (dirty.stdout or "").strip():
        warn(
            "作業ツリーに未コミットの変更があります。スナップショットには"
            f"コミット済みの内容（{target_ref} = {commit}）だけが入ります。"
        )

    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "archive", "--format=tar", target_ref],
        check=False,
        stdout=subprocess.PIPE,
    )
    if proc.returncode != 0 or not proc.stdout:
        warn("git archive に失敗したので、スナップショットを作りません")
        return None

    payload = gzip.compress(proc.stdout, 6)
    target = boot_dir / REPO_ARCHIVE_NAME
    log(
        f"リポジトリのスナップショット: {target} "
        f"({human(len(payload))}, {target_ref} = {commit})"
    )
    if not dry_run:
        target.write_bytes(payload)
    return commit


def render_provision_env(args) -> str:
    values = {
        "DAIFUKU_USER": args.user,
        "DAIFUKU_MODEL": args.model,
        "DAIFUKU_REPO_URL": args.repo_url,
        "DAIFUKU_REPO_REF": args.repo_ref,
        "DAIFUKU_ROS_DOMAIN_ID": str(args.ros_domain_id),
        "DAIFUKU_ROBOT_IP": args.ip.split("/")[0] if args.ip.lower() != "dhcp" else "",
        "DAIFUKU_BUILD_JOBS": str(args.build_jobs),
        "DAIFUKU_SWAP_MB": str(args.swap_mb),
        "DAIFUKU_WITH_RTMOUSE": "1" if args.with_rtmouse else "0",
        "DAIFUKU_BUILD_ON_FIRST_BOOT": "1" if args.build_on_first_boot else "0",
        "DAIFUKU_REPO_ARCHIVE": getattr(args, "repo_archive_path", "") or "",
        "DAIFUKU_REPO_ARCHIVE_COMMIT": getattr(args, "repo_archive_commit", "") or "",
    }
    header = "# tools/image/create_image.py が生成。provision.shが読み込む。\n"
    return header + "".join(f"{k}={v}\n" for k, v in values.items())


def render_user_data(args, provision_sh: bytes) -> str:
    keys = read_ssh_keys(args.ssh_key)
    parts = [
        "#cloud-config",
        "# tools/image/create_image.py が生成したファイルです。手で書き換えるより、",
        "# 引数を変えて configure をやり直すほうが確実です。",
        f"hostname: {args.hostname}",
        "manage_etc_hosts: true",
        "",
        "users:",
        f"  - name: {args.user}",
        # compose.yaml の user: "1000:1000" と揃える。Fast DDSの共有メモリは
        # 0644で作られるので、ホストとコンテナのuidが違うと通信が静かに止まる。
        "    uid: 1000",
        "    primary_group: " + args.user,
        "    groups: [adm, sudo, dialout, plugdev, video]",
        "    sudo: 'ALL=(ALL) NOPASSWD:ALL'",
        "    shell: /bin/bash",
        f"    lock_passwd: {'false' if args.password else 'true'}",
    ]
    if args.password:
        parts.append(f"    plain_text_passwd: {json.dumps(args.password)}")
    if keys:
        parts.append("    ssh_authorized_keys:")
        parts.append(yaml_block(keys, 6))
    if not keys and not args.password:
        warn(
            "SSH公開鍵もパスワードも指定されていません。--ssh-key か --password の"
            "どちらかを指定しないとログインできません。"
        )

    parts += [
        "",
        f"ssh_pwauth: {'true' if args.password else 'false'}",
        f"timezone: {args.timezone}",
        "package_update: true",
        "",
        "write_files:",
        # 実際のセットアップはprovision.shが行う。cloud-initは設定値と
        # スクリプト本体を置いて呼ぶだけにして、二重管理を避ける。
        "  - path: /etc/daifuku/provision.env",
        "    permissions: '0644'",
        "    content: |",
        _indent(render_provision_env(args), 6),
        "  - path: /usr/local/sbin/daifuku-provision.sh",
        "    permissions: '0755'",
        "    encoding: b64",
        "    content: " + base64.b64encode(provision_sh).decode("ascii"),
        "",
        "runcmd:",
        "  - [ /usr/local/sbin/daifuku-provision.sh ]",
        "",
        "final_message: "
        "\"daifuku_autonomous: cloud-init done. provision log: /var/log/daifuku-provision.log\"",
        "",
    ]
    return "\n".join(parts)


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line else pad for line in text.rstrip("\n").splitlines())


def render_config_txt_block(model: str, with_rtmouse: bool) -> str:
    """config.txtへ書くブロック。何が要るかは機種ではなく本体ドライバで決まる。

    rtmouseを入れるなら（Pi 4の既定、robot_bringup.launch.pyのdriver:=raspimouse）
    PWMはrtmouseがレジスタ直書きで出すので、カーネルのPWMドライバを同じピンに
    当ててはいけない。入れないなら（--no-rtmouse、またはPi 5）自前ドライバ
    (driver:=original)がステップクロックをハードウェアPWMから出すので、
    pwm-2chanオーバレイが要る。
    """
    lines = [
        BEGIN_MARK,
        "# daifuku_autonomous: tools/image/create_image.py が管理するブロック。",
        "# マーカーの間は configure のたびに置き換わる。",
        "dtparam=i2c_arm=on",
        "dtparam=spi=on",
        # パルスカウンタ(0x10/0x11)はI2Cのタイムアウトに弱い。標準の100kHzより
        # 落として取りこぼしを減らす（rt-netの推奨値）。Pi 5のI2CはRP1の
        # DesignWare系でタイミング生成が違うため、効くかは実機で確認する
        # （docs/setup/raspberry-pi-5.md）。
        "dtparam=i2c_baudrate=62500",
    ]
    if with_rtmouse:
        if model != "pi5":
            lines += [
                "# 本体ドライバは rtmouse + raspimouse (driver:=raspimouse)。",
                "# PWM は rtmouse がレジスタ直書きで出すので pwm オーバレイは入れない。",
            ]
        # kernel 5.16以降のrtmouseはA/D(MCP3204)をanyspiオーバレイで取る。
        lines.append('dtoverlay=anyspi:spi0-0,dev="microchip,mcp3204",speed=1000000')
    # Pi 5では--with-rtmouseを指定できてしまうが、rtmouseはRP1に届かないので
    # 本体ドライバは結局driver:=originalしかない。PWMオーバレイは必ず入れる。
    if not with_rtmouse or model == "pi5":
        if model == "pi5":
            # rtmouseはBCM2711のGPIO/PWM/CLKレジスタをioremapするので、
            # それらがRP1側にあるPi 5では動かない。
            lines.append(
                "# Raspberry Pi 5 では rtmouse は動かない"
                " (rt-net/RaspberryPiMouse は非対応)。"
            )
        lines += [
            "# 本体ドライバは raspicat_driver (driver:=original)。",
            # 左右モータのステップクロックをハードウェアPWMから出す。
            # func=4はpwm-2chan-overlay.dtsが挙げる正当な組み合わせ
            # （PWM0: 12,4(Alt0) / PWM1: 13,4(Alt0)）。Pi 4ではbcm2835のPWM、
            # Pi 5ではbcm2712-rpi.dtsiが`pwm: &pwm0` -> `pwm0: &rp1_pwm0`と
            # 付け替えているのでRP1のPWMに当たる。どちらもGPIO12 -> ch0、
            # GPIO13 -> ch1。
            # ネット上にある`dtoverlay=pwm-pi5`はrpi-6.12.yのoverlays/Makefileに
            # 存在しない（pwm / pwm-2chan / pwm-gpio / pwm-gpio-fan / pwm-ir-tx /
            # pwm-pio / pwm1 だけ）。
            "dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4",
        ]
    lines.append(END_MARK)
    return "\n".join(lines) + "\n"


def patch_marked_block(path: Path, block: str) -> None:
    """マーカーで囲んだブロックを置き換える（無ければ末尾に足す）。"""
    original = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    pattern = re.compile(
        re.escape(BEGIN_MARK) + r".*?" + re.escape(END_MARK) + r"\n?",
        re.DOTALL,
    )
    if pattern.search(original):
        updated = pattern.sub(block, original)
    else:
        separator = "" if original.endswith("\n") or not original else "\n"
        updated = original + separator + "\n" + block
    path.write_text(updated, encoding="utf-8", newline="\n")


def patch_cmdline(path: Path) -> None:
    """Dockerのメモリ制限に必要なcgroupパラメータを足す。"""
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    tokens = text.split()
    for token in ("cgroup_enable=memory", "cgroup_memory=1"):
        if token not in tokens:
            tokens.append(token)
    path.write_text(" ".join(tokens) + "\n", encoding="utf-8", newline="\n")


def configure(boot_dir: Path, args) -> None:
    if not looks_like_boot_dir(boot_dir):
        die(
            f"{boot_dir} はブートパーティションに見えません"
            "（config.txt と cmdline.txt が必要です）"
        )

    provision_sh = (SCRIPT_DIR / "provision.sh").read_bytes()
    # cloud-initのwrite_filesはそのまま書き出す。CRLFで保存されているとPi側で
    # 実行できなくなるので、ここで必ずLFへ揃える。
    provision_sh = provision_sh.replace(b"\r\n", b"\n")

    # user-dataを組み立てる前にスナップショットの成否を確定させる。
    args.repo_archive_path = ""
    args.repo_archive_commit = ""
    if args.repo_archive:
        commit = write_repo_archive(boot_dir, args.repo_ref, args.dry_run)
        if commit:
            args.repo_archive_path = REPO_ARCHIVE_ON_PI
            args.repo_archive_commit = commit

    files = {
        "user-data": render_user_data(args, provision_sh),
        "network-config": render_network_config(args),
        "meta-data": f"instance-id: daifuku-{args.hostname}\nlocal-hostname: {args.hostname}\n",
    }
    for name, content in files.items():
        target = boot_dir / name
        log(f"書き込み: {target}")
        if not args.dry_run:
            target.write_text(content, encoding="utf-8", newline="\n")

    log(f"更新: {boot_dir / 'config.txt'}")
    if not args.dry_run:
        patch_marked_block(
            boot_dir / "config.txt",
            render_config_txt_block(args.model, args.with_rtmouse),
        )
    log(f"更新: {boot_dir / 'cmdline.txt'}")
    if not args.dry_run:
        patch_cmdline(boot_dir / "cmdline.txt")

    log("設定を書き込みました")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        choices=("pi4", "pi5"),
        default="pi4",
        help="対象のRaspberry Pi（既定: pi4）",
    )
    parser.add_argument(
        "--release",
        default=None,
        help="Ubuntuのリリース（既定: pi4は22.04、pi5は24.04）",
    )
    parser.add_argument("--cache-dir", default=None, help="イメージの保存先")
    parser.add_argument("--dry-run", action="store_true", help="書き込まずに動作だけ確認する")


def add_configure_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--hostname", default="raspicat", help="ホスト名（既定: raspicat）")
    parser.add_argument("--user", default="ubuntu", help="作成するユーザー（既定: ubuntu、uid 1000固定）")
    parser.add_argument(
        "--ssh-key",
        action="append",
        default=[],
        metavar="PATH_OR_KEY",
        help="SSH公開鍵のファイルパスまたは鍵そのもの（複数指定可）",
    )
    parser.add_argument("--password", default=None, help="パスワード（指定するとパスワード認証も有効）")
    parser.add_argument(
        "--ip",
        default=DEFAULT_ROBOT_IP,
        help=f"eth0の固定IP。'dhcp'でDHCP（既定: {DEFAULT_ROBOT_IP}）",
    )
    parser.add_argument(
        "--gateway",
        default=None,
        help="デフォルトゲートウェイ（既定: --ipと同一サブネットの.1、'none'で置かない）",
    )
    parser.add_argument(
        "--dns",
        default=None,
        help="DNSサーバー（カンマ区切り、既定: ゲートウェイ、'none'で置かない）",
    )
    parser.add_argument("--wifi-ssid", default=None, help="保守用wlan0のSSID")
    parser.add_argument("--wifi-password", default=None, help="保守用wlan0のパスフレーズ")
    parser.add_argument("--timezone", default="Asia/Tokyo", help="タイムゾーン（既定: Asia/Tokyo）")
    parser.add_argument(
        "--ros-domain-id",
        type=int,
        default=DEFAULT_ROS_DOMAIN_ID,
        help=f"ROS_DOMAIN_ID（既定: {DEFAULT_ROS_DOMAIN_ID}）",
    )
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL, help="取得するリポジトリのURL")
    parser.add_argument(
        "--repo-ref",
        default=None,
        help="チェックアウトするブランチ/タグ（既定: 手元でチェックアウト中のブランチ）",
    )
    parser.add_argument(
        "--no-repo-archive",
        dest="repo_archive",
        action="store_false",
        default=True,
        help="手元のリポジトリのスナップショットをブートパーティションへ置かない"
        "（既定では置き、Pi側のgit cloneが失敗したときの取得元にする）",
    )
    parser.add_argument(
        "--build-jobs",
        type=int,
        default=None,
        help="Dockerビルドの並列数（既定: pi4は1、pi5は2）",
    )
    parser.add_argument(
        "--swap-mb",
        type=int,
        default=None,
        help="スワップファイルのサイズMB。0で作らない（既定: pi4は2048、pi5は0）",
    )
    parser.add_argument(
        "--no-rtmouse",
        dest="with_rtmouse",
        action="store_false",
        default=None,
        help="rtmouseカーネルモジュールを導入しない"
        "（自前ドライバ driver:=original で動かす場合。config.txtにはpwmオーバレイが入る）",
    )
    parser.add_argument(
        "--with-rtmouse",
        dest="with_rtmouse",
        action="store_true",
        default=None,
        help="rtmouseカーネルモジュールを導入する（pi5では非対応。既定はpi4のみ有効）",
    )
    parser.add_argument(
        "--build-on-first-boot",
        action="store_true",
        help="初回起動時にDockerイメージまでビルドする（Pi 4では数時間かかる）",
    )
    parser.add_argument("--boot-dir", default=None, help="ブートパーティションのパス")


def add_flash_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--device", required=True, help="書き込み先（devicesサブコマンドのID）")
    parser.add_argument("-y", "--yes", action="store_true", help="確認を省略する")
    parser.add_argument("--force", action="store_true", help="安全確認を無視する")
    parser.add_argument(
        "--max-size-gb",
        type=int,
        default=128,
        help="書き込みを許すディスクサイズの上限GB（既定: 128）",
    )


def apply_model_defaults(args) -> None:
    if args.release is None:
        args.release = DEFAULT_RELEASE[args.model]
    if getattr(args, "build_jobs", None) is None:
        args.build_jobs = 1 if args.model == "pi4" else 2
    if getattr(args, "swap_mb", None) is None:
        # Pi 4は4GBしかなく、価値反復プランナが広域地図で2.7GB近くまで伸びる。
        args.swap_mb = 2048 if args.model == "pi4" else 0
    if getattr(args, "with_rtmouse", None) is None:
        args.with_rtmouse = args.model == "pi4"
    if getattr(args, "repo_ref", None) is None and hasattr(args, "repo_ref"):
        args.repo_ref = default_repo_ref()
        log(f"リポジトリのref: {args.repo_ref}")
    if args.with_rtmouse and args.model == "pi5":
        warn(
            "rt-net/RaspberryPiMouse は Raspberry Pi 5 を公式サポートしていません。"
            "ビルドに失敗する可能性があります。"
        )
    if args.model == "pi5" and args.release.startswith("22.04"):
        die("Ubuntu 22.04 は Raspberry Pi 5 で起動しません。--release 24.04 以降を指定してください。")
    apply_network_defaults(args)


def apply_network_defaults(args) -> None:
    """固定IPのときのゲートウェイとDNSを補う。

    初回起動のプロビジョニングはapt・git・Dockerの取得でインターネットへ出る。
    固定IPだけを書いてデフォルトルートが無いと、起動はするのに何も入っていない
    Piができあがるので、指定が無ければ同一サブネットの .1 を仮定する。
    """
    if not hasattr(args, "ip") or args.ip.lower() == "dhcp":
        return

    address = args.ip.split("/")[0]
    octets = address.split(".")
    if len(octets) != 4 or not all(o.isdigit() for o in octets):
        die(f"--ip をIPv4アドレスとして解釈できません: {args.ip}")

    if args.gateway is None:
        args.gateway = ".".join(octets[:3] + ["1"])
        warn(
            f"--gateway の指定が無いので {args.gateway} を仮定します。"
            "違う場合は --gateway で指定してください（デフォルトルートを置かないなら "
            "--gateway none）。"
        )
    if args.dns is None and args.gateway.lower() != "none":
        args.dns = args.gateway


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="create_image.py",
        description="Raspberry Pi 4 / 5 用のSDカードイメージを作成してセットアップする",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command")

    devices_parser = subparsers.add_parser("devices", help="書き込み先候補を一覧する")
    devices_parser.set_defaults(command="devices")

    fetch_parser = subparsers.add_parser("fetch", help="ベースイメージを取得する")
    add_common_arguments(fetch_parser)
    fetch_parser.add_argument("--force", action="store_true", help="キャッシュを無視して取り直す")

    flash_parser = subparsers.add_parser("flash", help="イメージを書き込む")
    add_common_arguments(flash_parser)
    add_flash_arguments(flash_parser)
    flash_parser.add_argument("--image", default=None, help="書き込むイメージ（既定: キャッシュ）")

    configure_parser = subparsers.add_parser("configure", help="ブートパーティションを設定する")
    add_common_arguments(configure_parser)
    add_configure_arguments(configure_parser)

    all_parser = subparsers.add_parser("all", help="fetch → flash → configure")
    add_common_arguments(all_parser)
    add_flash_arguments(all_parser)
    add_configure_arguments(all_parser)

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2

    if args.command == "devices":
        devices = list_devices()
        if not devices:
            print("ディスクが見つかりませんでした")
            return 1
        print("書き込み先の候補:")
        for device in devices:
            print(device.describe())
        print()
        print("SDカードのIDを --device に渡してください。")
        return 0

    apply_model_defaults(args)
    cache = cache_dir(args.cache_dir)

    if args.command == "fetch":
        fetch_image(args.release, cache, force=args.force)
        return 0

    if args.command == "configure":
        boot_dir = Path(args.boot_dir).expanduser() if args.boot_dir else find_boot_dir()
        configure(boot_dir, args)
        return 0

    # flash と all はディスクを触るので権限確認から入る。
    require_privileges()
    device = resolve_device(args.device)
    check_device_safety(device, args.max_size_gb, args.force)

    if args.command == "flash":
        image = Path(args.image).expanduser() if args.image else fetch_image(args.release, cache)
        flash(image, device, args.yes, args.dry_run)
        return 0

    # all
    image = fetch_image(args.release, cache)
    flash(image, device, args.yes, args.dry_run)
    if args.dry_run:
        log("--dry-run のため設定注入も行いません")
        return 0

    unmount_after = False
    if args.boot_dir:
        boot_dir = Path(args.boot_dir).expanduser()
    elif IS_LINUX:
        boot_dir, unmount_after = mount_boot_partition_linux(device)
    else:
        boot_dir = find_boot_dir()

    try:
        configure(boot_dir, args)
    finally:
        if unmount_after:
            run(["umount", str(boot_dir)], check=False)
        if IS_MACOS:
            run(["diskutil", "eject", device.path], check=False)

    print()
    log("SDカードの準備ができました")
    print(f"  1. SDカードをRaspberry Pi（{args.model}）に挿して起動する")
    print("  2. 初回起動のプロビジョニングは10〜20分ほどかかる")
    print(f"  3. ssh {args.user}@{args.ip.split('/')[0] if args.ip != 'dhcp' else args.hostname + '.local'}")
    print("  4. 進捗は /var/log/daifuku-provision.log で確認する")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n中断しました", file=sys.stderr)
        sys.exit(130)
