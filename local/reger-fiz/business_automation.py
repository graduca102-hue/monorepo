"""ADB/uiautomator2 bootstrap: proxy, Hermes fingerprint and Instagram."""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import uiautomator2 as u2

from main import find_adb_executable


ROOT = Path(__file__).resolve().parent
PROXY_FILE = ROOT / "proxy.txt"
APK_CACHE = ROOT / "apk" / "phone_cache"
INSTAGRAM = "com.instagram.android"
FINGERPRINTER = "ru.hermes_automation.fingerprinter"
REGENERATE_ID = f"{FINGERPRINTER}:id/btnRegenerate"


def adb(adb_exe: str, serial: str | None, *args: str, timeout: int = 30, check: bool = False):
    cmd = [adb_exe]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check)


def online_devices(adb_exe: str) -> list[tuple[str, str]]:
    result = adb(adb_exe, None, "devices", "-l", timeout=10)
    devices = []
    for line in result.stdout.splitlines()[1:]:
        if not re.search(r"\sdevice(?:\s|$)", line):
            continue
        serial = line.split()[0]
        model_match = re.search(r"model:(\S+)", line)
        model = model_match.group(1) if model_match else "Android"
        devices.append((serial, model))
    return devices


def choose_device(devices: list[tuple[str, str]]) -> str:
    print("\nПодключённые ADB-устройства:\n")
    for number, (serial, model) in enumerate(devices, 1):
        print(f"  {number}. {model:18} {serial}")
    while True:
        raw = input("\nВыберите телефон (номер): ").strip()
        try:
            return devices[int(raw) - 1][0]
        except (ValueError, IndexError):
            print("Введите номер из списка.")


def set_portrait(adb_exe: str, serial: str):
    adb(adb_exe, serial, "shell", "settings", "put", "system", "accelerometer_rotation", "0")
    adb(adb_exe, serial, "shell", "settings", "put", "system", "user_rotation", "0")
    print("[OK] Портретная ориентация зафиксирована для всех приложений")


def read_proxy() -> tuple[str, str | None] | None:
    if not PROXY_FILE.is_file():
        return None
    for raw in PROXY_FILE.read_text(encoding="utf-8-sig").splitlines():
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        parsed = urlsplit(value if "://" in value else f"http://{value}")
        if parsed.hostname and parsed.port:
            auth = None
            if parsed.username:
                auth = parsed.username + (f":{parsed.password}" if parsed.password else "")
            return f"{parsed.hostname}:{parsed.port}", auth
        print(f"[WARN] Некорректная строка proxy.txt: {value}")
    return None


def enable_proxy(adb_exe: str, serial: str):
    proxy = read_proxy()
    if not proxy:
        print("[SKIP] Прокси не найден — настройки телефона не меняю")
        return
    endpoint, auth = proxy
    result = adb(adb_exe, serial, "shell", "settings", "put", "global", "http_proxy", endpoint)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "не удалось включить прокси")
    print(f"[OK] Системный прокси включён: {endpoint}")
    if auth:
        print("[WARN] Android global proxy не поддерживает логин/пароль; применён host:port")


def package_paths(adb_exe: str, serial: str, package: str) -> list[str]:
    result = adb(adb_exe, serial, "shell", "pm", "path", package)
    return [line.removeprefix("package:").strip() for line in result.stdout.splitlines() if line.startswith("package:")]


def find_apk_source(adb_exe: str, preferred: str, package: str) -> tuple[str, list[str]]:
    candidates = [preferred] + [s for s, _ in online_devices(adb_exe) if s != preferred]
    for serial in candidates:
        paths = package_paths(adb_exe, serial, package)
        if paths:
            return serial, paths
    raise RuntimeError(f"APK пакета {package} не найден ни на одном доступном телефоне")


def pull_package(adb_exe: str, preferred: str, package: str) -> list[Path]:
    source, remote_paths = find_apk_source(adb_exe, preferred, package)
    target_dir = APK_CACHE / package
    target_dir.mkdir(parents=True, exist_ok=True)
    local_paths = []
    for index, remote in enumerate(remote_paths):
        remote_name = Path(remote).name
        local = target_dir / (remote_name if remote_name not in {p.name for p in local_paths} else f"{index}_{remote_name}")
        result = adb(adb_exe, source, "pull", remote, str(local), timeout=180)
        if result.returncode or not local.is_file():
            raise RuntimeError(f"Не удалось извлечь {remote}: {result.stderr.strip()}")
        local_paths.append(local)
    print(f"[OK] {package}: извлечено {len(local_paths)} APK с {source}")
    return local_paths


def install_package(adb_exe: str, serial: str, apk_files: list[Path], package: str):
    command = "install-multiple" if len(apk_files) > 1 else "install"
    result = adb(adb_exe, serial, command, "-r", "-d", *map(str, apk_files), timeout=600)
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode or "Success" not in output:
        raise RuntimeError(f"Установка {package} не удалась: {output}")
    print(f"[OK] {package} установлен")


def install_instagram_worker(adb_exe: str, serial: str, state: dict):
    try:
        files = pull_package(adb_exe, serial, INSTAGRAM)
        install_package(adb_exe, serial, files, INSTAGRAM)
        state["ok"] = True
    except Exception as exc:
        state["error"] = str(exc)


def ensure_fingerprinter(adb_exe: str, serial: str):
    if package_paths(adb_exe, serial, FINGERPRINTER):
        print("[OK] Hermes Fingerprinter уже установлен")
        return
    files = pull_package(adb_exe, serial, FINGERPRINTER)
    install_package(adb_exe, serial, files, FINGERPRINTER)


def regenerate_fingerprint(serial: str) -> bool:
    device = u2.connect(serial)
    device.app_start(FINGERPRINTER, stop=True, wait=True)
    device(scrollable=True).scroll.toEnd(max_swipes=20)

    button = device(resourceId=REGENERATE_ID)
    if not button.wait(timeout=10):
        button = device.xpath(f'//*[@resource-id="{REGENERATE_ID}"]')
        if not button.wait(timeout=5):
            raise RuntimeError("Кнопка REGENERATE PROFILE не найдена")
    button.click()

    yes = device(textMatches="(?i)^YES$")
    if not yes.wait(timeout=8):
        yes = device(resourceId="android:id/button1")
    if not yes.wait(timeout=3):
        raise RuntimeError("Кнопка подтверждения YES не найдена")
    yes.click()

    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        message = device.toast.get_message(wait_timeout=2, default="") or ""
        if "profile regenerated successfully" in message.lower():
            print(f"[OK] Fingerprint подтверждён: {message}")
            return True
        if device(textContains="Profile regenerated successfully").exists:
            print("[OK] Fingerprint подтверждён UI-сообщением")
            return True
    print("[FAIL] Сообщение об успешной регенерации не появилось")
    return False


def wait_for_instagram(adb_exe: str, serial: str, worker: threading.Thread, state: dict):
    print("[WAIT] Ожидаю завершения установки Instagram (проверка каждую секунду)...")
    while worker.is_alive() or not package_paths(adb_exe, serial, INSTAGRAM):
        if not worker.is_alive() and state.get("error"):
            raise RuntimeError(state["error"])
        time.sleep(1)
    worker.join()
    if state.get("error"):
        raise RuntimeError(state["error"])


def main() -> int:
    adb_exe = find_adb_executable()
    if not adb_exe:
        print("ADB не найден. Установите Android platform-tools.")
        return 1
    devices = online_devices(adb_exe)
    if not devices:
        print("Нет доступных ADB-устройств со статусом device.")
        return 1
    serial = choose_device(devices)
    print(f"\n[START] Телефон: {serial}")
    try:
        set_portrait(adb_exe, serial)
        enable_proxy(adb_exe, serial)

        instagram_state: dict = {}
        instagram_thread = threading.Thread(
            target=install_instagram_worker,
            args=(adb_exe, serial, instagram_state),
            name="instagram-installer",
            daemon=True,
        )
        instagram_thread.start()

        ensure_fingerprinter(adb_exe, serial)
        fingerprint_ok = regenerate_fingerprint(serial)
        print(f"fingerprint = {fingerprint_ok}")
        if not fingerprint_ok:
            return 2

        wait_for_instagram(adb_exe, serial, instagram_thread, instagram_state)
        u2.connect(serial).app_start(INSTAGRAM, wait=True)
        print("[DONE] Instagram установлен и открыт")
        return 0
    except KeyboardInterrupt:
        print("\nОстановлено пользователем")
        return 130
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
