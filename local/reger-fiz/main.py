import uiautomator2 as u2
import time
import random
import requests
import re
import string
import datetime
import threading
import subprocess
import sys
import shutil
import os
import tempfile
import zipfile
from pathlib import Path

try:
    from uiautomator2.exceptions import UiObjectNotFoundError
except ImportError:
    UiObjectNotFoundError = Exception

try:
    import pyotp
except ImportError:
    pyotp = None

# ========== КОНФИГУРАЦИЯ ==========
ANYMESSAGE_API_URL = "https://api.anymessage.shop"
ANYMESSAGE_TOKEN = ""
SMSBOWER_MAIL_API_URL = "https://smsbower.page/api/mail"
SMSBOWER_TOKEN = ""

MAX_RETRIES = 3
WAIT_SHORT = 0.05
WAIT_LONG = 0.35
WAIT_POLL = 0.08
POLL_FAST = 0.08
POLL_DUMP_MIN_INTERVAL = 0.55
POLL_UI_ONLY = 0.55
POLL_EMAIL_TRANSITION = 0.9
REG_TRANSITION_TIMEOUT = 20
REG_TRANSITION_SETTLE = 0.45
EMAIL_TO_CODE_SETTLE = 2.0
CODE_TO_PASSWORD_SETTLE = 2.0
CLICK_PAUSE = 0.05
ACTION_TIMEOUT = 40
POST_REG_TIMEOUT = 150
CODE_POLL_DELAY = 1.0
CODE_ERROR_WAIT = 4.0
CODE_ERROR_STABLE = 0.6

INSTAGRAM_PKG = "com.instagram.android"
PLAY_STORE_PKG = "com.android.vending"
PLAY_STORE_INSTALL_TIMEOUT = 300
PLAY_STORE_STUCK_DOWNLOAD_SEC = 120
APK_DIR = Path(__file__).resolve().parent / "apk"
APKM_FILENAME = "com.instagram.apkm"
APK_FILENAME = "com.instagram.apk"
INSTALL_METHOD_PLAY_STORE = "play_store"
INSTALL_METHOD_APKM = "apkm"
DEVICE_EMULATOR_PKG = "dev.device.emulator"
REGISTERED_ACCOUNTS_FILE = Path(__file__).resolve().parent / "registered_accounts.txt"
_2FA_SECRET_RE = re.compile(r"(?:[A-Z2-7]{4}\s+){5,}[A-Z2-7]{4}", re.IGNORECASE)


def find_adb_executable():
    """Находит ADB для прямых команд установки, даже если его нет в PATH."""
    candidates = []
    path_adb = shutil.which("adb")
    if path_adb:
        candidates.append(Path(path_adb))

    app_dir = Path(__file__).resolve().parent
    python_dir = Path(sys.executable).resolve().parent
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates.extend(
        [
            app_dir / "adb.exe",
            app_dir / "platform-tools" / "adb.exe",
            python_dir / "Lib" / "site-packages" / "adbutils" / "binaries" / "adb.exe",
            local_app_data / "Android" / "Sdk" / "platform-tools" / "adb.exe",
            Path(r"C:\Program Files\Laixi\tools\platform-tools\adb.exe"),
            Path(r"C:\Program Files (x86)\panda_android\tools\adb.exe"),
        ]
    )
    for candidate in candidates:
        if candidate and candidate.is_file():
            return str(candidate)
    return None

RU_MONTH_SHORT = {
    1: "янв",
    2: "фев",
    3: "мар",
    4: "апр",
    5: "мая",
    6: "июн",
    7: "июл",
    8: "авг",
    9: "сен",
    10: "окт",
    11: "ноя",
    12: "дек",
}


_device_registration_locks = {}
_device_registration_locks_guard = threading.Lock()


def _get_device_registration_lock(device_serial):
    """Один поток на одно устройство — иначе второй поток делает reinstall во время кода."""
    key = device_serial or "__default__"
    with _device_registration_locks_guard:
        if key not in _device_registration_locks:
            _device_registration_locks[key] = threading.Lock()
        return _device_registration_locks[key]


class _BlockedUiObject:
    """Заглушка u2-элемента — используется когда шаг 6b блокирует device."""

    def __init__(self, bot, label):
        self._bot = bot
        self._label = label

    @property
    def exists(self):
        return False

    def __getattr__(self, name):
        if name == "info":
            return {}

        def blocked(*args, **kwargs):
            self._bot._trace_device_blocked(f"{self._label}.{name}", args)
            return _BlockedUiObject(self._bot, f"{self._label}.{name}")

        return blocked

    def __bool__(self):
        return False


class _TracedDevice:
    """
    Обёртка u2: логирует КАЖДЫЙ вызов к телефону + блокирует всё на шаге 6b.
    Так видно точную функцию, которая «дёрнула» Instagram перед вылетом.
    """

    _DIRECT_BLOCK = frozenset({
        "shell",
        "app_start",
        "app_stop",
        "click",
        "press",
        "swipe",
        "send_keys",
        "set_clipboard",
        "app_uninstall",
        "install",
        "unlock",
        "screen_on",
        "screen_off",
        "wake_up",
        "dump_hierarchy",
    })

    def __init__(self, raw_device, bot):
        self._raw = raw_device
        self._bot = bot

    def __call__(self, *args, **kwargs):
        label = repr((args, kwargs))[:90]
        self._bot._trace_device(f"selector{label}", "")
        if self._bot._code_api_wait_active:
            self._bot._trace_device_blocked(f"selector{label}", ())
            return _BlockedUiObject(self._bot, label)
        return self._raw(*args, **kwargs)

    def __getattr__(self, name):
        if name in ("_raw", "_bot"):
            return object.__getattribute__(self, name)
        raw = object.__getattribute__(self, "_raw")
        bot = object.__getattribute__(self, "_bot")
        attr = getattr(raw, name)

        if name == "info":
            bot._trace_device("READ info", "")
            if bot._code_api_wait_active:
                return bot._cached_device_info
            return attr

        if not callable(attr):
            return attr

        def wrapper(*args, **kwargs):
            preview = repr(args[0])[:72] if args else ""
            bot._trace_device(f"{name}({preview})", "")
            if bot._code_api_wait_active:
                bot._trace_device_blocked(name, args)
                return _TracedDevice._blocked_return(name)
            return attr(*args, **kwargs)

        return wrapper

    @staticmethod
    def _blocked_return(method):
        if method == "shell":
            return type("ShellOut", (), {"output": "", "exit_code": 0})()
        if method == "app_current":
            return {}
        if method == "app_list_running":
            return []
        return None


class InstagramAndroidBot:
    def __init__(
        self,
        email_domain="gmail.com",
        anymessage_token=None,
        email_mode="anymessage",
        smsbower_token=None,
        device_serial=None,
        log_callback=None,
        stop_checker=None,
        max_retries=None,
        install_method=INSTALL_METHOD_PLAY_STORE,
        root_enabled=False,
        trace_enabled=False,
    ):
        self.device_serial = device_serial
        self._log_callback = log_callback
        self._stop_checker = stop_checker
        self.max_retries = max_retries if max_retries is not None else MAX_RETRIES
        method = (install_method or INSTALL_METHOD_PLAY_STORE).strip().lower()
        self.install_method = (
            INSTALL_METHOD_APKM if method == INSTALL_METHOD_APKM else INSTALL_METHOD_PLAY_STORE
        )
        self.root_enabled = bool(root_enabled)

        self._log("🔍 Подключаюсь к устройству...")
        try:
            raw_device = (
                u2.connect(device_serial) if device_serial else u2.connect()
            )
        except Exception as exc:
            raise ConnectionError(f"Не удалось подключиться к устройству: {exc}")

        if not raw_device.info:
            raise ConnectionError("Устройство не отвечает (пустой device.info)")

        self._cached_device_info = raw_device.info or {}
        self._raw_device = raw_device
        self.device = _TracedDevice(raw_device, self)

        product = self._cached_device_info.get("productName") or device_serial or "Android"
        self._log(f"✅ Подключено к: {product}")
        try:
            adb_serial = raw_device.serial
            if adb_serial:
                self._log(f"   📱 ADB serial: {adb_serial}")
        except Exception:
            pass

        self.email_domain = email_domain
        self.email_mode = (email_mode or "anymessage").strip().lower()
        self.anymessage_token = anymessage_token or ANYMESSAGE_TOKEN
        self.smsbower_token = smsbower_token or SMSBOWER_TOKEN

        self.email = None
        self.activation_id = None
        self.code = None
        self.password = None
        self.username = None
        self.full_name = None
        self.otp_key = None

        self.code_sent = False
        self.last_identifier = None
        self.invalid_confirmation_code_retry = False
        self.verification_detected = False
        self.code_submitted_to_instagram = False
        self._entry_path = None
        self._onboarding_one_page_done = False
        self._signup_form_design = None
        self.blocked_confirmation_codes = set()
        self.last_submitted_confirmation_code = None
        self.rate_limit_hits_on_email = 0
        self._code_wait_result = None
        self.account_registered = False
        self._screen_cache_text = ""
        self._screen_cache_ts = 0.0
        self._code_api_wait_active = False
        self._trace_seq = 0
        self._trace_enabled = bool(trace_enabled)

        info = self._cached_device_info
        self._screen_w = info.get("displayWidth") or 1080
        self._screen_h = info.get("displayHeight") or 1920

    # ========== ЛОГИ / ПАУЗЫ / TRACE ==========

    def _format_caller_chain(self, skip=2, depth=6):
        import inspect

        parts = []
        for frame in inspect.stack()[skip : skip + depth]:
            parts.append(f"{frame.function}:{frame.lineno}")
        return " ← ".join(parts)

    def _trace_step(self, label, detail=""):
        if not self._trace_enabled:
            return
        import inspect

        self._trace_seq += 1
        frame = inspect.stack()[1]
        extra = f" {detail}" if detail else ""
        self._log(
            f"   [STEP #{self._trace_seq}] {label}{extra} "
            f"← {frame.function}:{frame.lineno}"
        )

    def _trace_device(self, action, preview=""):
        if not self._trace_enabled:
            return
        method = action.split("(")[0].strip().replace("READ ", "")
        is_dangerous = method in _TracedDevice._DIRECT_BLOCK or method in (
            "app_current",
            "app_list_running",
        )
        if not self._code_api_wait_active and not is_dangerous:
            return

        self._trace_seq += 1
        chain = self._format_caller_chain(skip=2, depth=6)
        extra = f" | {preview}" if preview else ""
        self._log(f"   [DEV #{self._trace_seq}] {action}{extra} ← {chain}")

    def _trace_device_blocked(self, action, args):
        if not self._trace_enabled:
            return
        import inspect

        self._trace_seq += 1
        arg_preview = repr(args[0])[:60] if args else ""
        chain = self._format_caller_chain(skip=2, depth=8)
        self._log(
            f"   [DEV-BLOCKED #{self._trace_seq}] ⛔ {action} {arg_preview} "
            f"← {chain}"
        )

    def _trace_api(self, label, detail=""):
        if not self._trace_enabled:
            return
        import inspect

        self._trace_seq += 1
        frame = inspect.stack()[1]
        extra = f" {detail}" if detail else ""
        self._log(
            f"   [API #{self._trace_seq}] {label}{extra} "
            f"← {frame.function}:{frame.lineno}"
        )

    def _log(self, msg):
        line = str(msg)
        if self._log_callback:
            prefix = f"[{self.device_serial}] " if self.device_serial else ""
            self._log_callback(prefix + line)
        else:
            print(line)

    def _should_stop(self):
        if not self._stop_checker:
            return False
        try:
            return bool(self._stop_checker())
        except Exception:
            return False

    def _human_pause(self, lo=0.15, hi=0.3, reason=""):
        delay = random.uniform(lo, hi)
        if reason:
            self._log(f"   ⏳ Пауза {delay:.1f} сек ({reason})")
        time.sleep(delay)

    def _short_pause(self, lo=0.04, hi=0.1, reason=""):
        delay = random.uniform(lo, hi)
        if reason:
            self._log(f"   ⏳ Короткая пауза {delay:.1f} сек ({reason})")
        time.sleep(delay)

    def _begin_code_api_wait(self):
        """
        Шаг 6b: ТОЛЬКО HTTP к API. Ноль вызовов к телефону — даже shell/wakeup.
        На S9 любой adb/u2 во время ожидания кода = вылет Instagram.
        """
        self._code_api_wait_active = True
        self._trace_step(
            "6b START",
            "Instagram заморожен — device полностью заблокирован",
        )

    def _end_code_api_wait(self):
        self._code_api_wait_active = False
        self._trace_step("6b END", "device разблокирован")

    def _device_action_blocked_during_code_wait(self, action):
        if not getattr(self, "_code_api_wait_active", False):
            return False
        self._log(f"   ⛔ {action} отменён — идёт ожидание кода из API (шаг 6b)")
        return True

    def _get_device_registration_lock(self):
        return _get_device_registration_lock(self.device_serial)

    _REGISTRATION_RESUME_SCREENS = frozenset({
        "email",
        "code",
        "password",
        "birthday",
        "full_name",
        "username",
        "name_username",
        "agree",
    })

    _IME_FOREGROUND_PACKAGES = frozenset({
        "com.samsung.android.honeyboard",
        "com.sec.android.inputmethod",
        "com.sec.android.inputmethod.samsung",
        "com.google.android.inputmethod.latin",
        "com.android.inputmethod.latin",
    })

    def _is_instagram_foreground(self):
        try:
            cur = self.device.app_current() or {}
            pkg = (cur.get("package") or "").strip()
            if pkg == INSTAGRAM_PKG:
                return True
            # Samsung S9: при вводе пароля/кода foreground часто у IME, не у Instagram.
            if pkg in self._IME_FOREGROUND_PACKAGES and self._is_instagram_running():
                return True
        except Exception:
            pass
        return False

    def _is_instagram_usable_without_restart(self):
        if self._is_instagram_foreground():
            return True
        if not self._is_instagram_running():
            return False
        return bool(self._instagram_registration_ui_on_screen())

    def _bring_instagram_to_front(self):
        """Мягко вывести Instagram вперёд без kill/restart (важно для Samsung S9)."""
        try:
            self.device.shell(
                f"am start -a android.intent.action.MAIN "
                f"-c android.intent.category.LAUNCHER "
                f"-p {INSTAGRAM_PKG} "
                f"--activity-reorder-to-front"
            )
            time.sleep(0.35)
            return True
        except Exception as exc:
            self._log(f"   ⚠️ bring-to-front: {exc}")
            return False

    def _get_registration_resume_point(self):
        """
        Продолжение БЕЗ reinstall — только post-code после ввода кода.
        Rate-limit / email / code — всегда полный ретрай.
        """
        if not self._is_instagram_running():
            return None
        if not self.code_submitted_to_instagram:
            return None
        if self._ui_has_text(*self._UI_AGREE_MARKERS):
            return "agree"
        if self._ui_has_text(*self._UI_BIRTHDAY_MARKERS):
            return "birthday"
        if self._ui_has_text(*self._UI_USERNAME_MARKERS):
            return "username"
        if self._ui_has_text(*self._UI_FULLNAME_MARKERS) or self._ui_has_text(
            *self._UI_NICKNAME_MARKERS
        ):
            return "full_name"
        if self._ui_has_text(*self._UI_PASSWORD_MARKERS):
            return "password"
        return None

    def _instagram_registration_ui_on_screen(self):
        """u2-only — без dump_hierarchy (S9)."""
        if self._has_code_screen_markers_u2_only():
            return "code"
        if self._ui_has_text(*self._UI_PASSWORD_MARKERS):
            return "password"
        if self._ui_has_text(*self._UI_SIGNUP_EMAIL_SCREEN):
            return "email"
        return None

    def _needs_full_reinstall(self, attempt):
        """Полный ретрай с удалением — rate-limit или первая попытка."""
        if attempt == 1:
            return True
        if self.rate_limit_hits_on_email > 0:
            return True
        return False

    def _is_instagram_running_fast(self):
        """Только pidof — app_list_running() на Samsung может висеть 30–40 сек."""
        try:
            out = (self.device.shell(f"pidof {INSTAGRAM_PKG}").output or "").strip()
            return bool(out)
        except Exception:
            return False

    def _is_instagram_running(self):
        if self._is_instagram_running_fast():
            return True
        try:
            running = self.device.app_list_running() or []
            return INSTAGRAM_PKG in running
        except Exception:
            pass
        return False

    def _ensure_instagram_foreground_2fa(self, reason="2FA"):
        """Быстрый вывод Instagram для 2FA — без тяжёлых проверок регистрации."""
        if self._is_instagram_foreground():
            return True
        self._log(f"   📍 2FA: вывожу Instagram вперёд ({reason})...")
        if self._is_instagram_running_fast():
            self._bring_instagram_to_front()
            time.sleep(0.4)
            if self._is_instagram_foreground():
                return True
            # UI может грузиться — не блокируемся на app_list_running
            return True
        try:
            self.device.app_start(INSTAGRAM_PKG, wait=True, stop=False)
            time.sleep(0.5)
            return True
        except Exception as exc:
            self._log(f"   ❌ 2FA: не удалось открыть Instagram: {exc}")
            return False

    def _ensure_instagram_foreground(self, reason="", gentle=True):
        """
        Вернуть Instagram на экран БЕЗ force-stop и без лишнего app_start.
        На S9 app_start при открытой клавиатуре выглядит как «закрыли и открыли».
        """
        self._trace_step("ensure_foreground", f"reason={reason!r} gentle={gentle}")
        if getattr(self, "_code_api_wait_active", False):
            self._trace_step("ensure_foreground SKIP", "6b active")
            return True

        if self._is_instagram_usable_without_restart():
            return True

        if self._is_instagram_running():
            self._log(
                f"   ℹ️ Instagram в фоне ({reason}) — мягкий вывод вперёд"
            )
            self._bring_instagram_to_front()
            if self._is_instagram_usable_without_restart():
                self._log("   ✅ Instagram снова доступен")
                return True
            if gentle and self._instagram_registration_ui_on_screen():
                self._log(
                    f"   ℹ️ UI регистрации на экране ({reason}) — app_start пропущен"
                )
                return True

        if gentle and self._instagram_registration_ui_on_screen():
            self._log(
                f"   ℹ️ Экран регистрации виден ({reason}) — перезапуск не нужен"
            )
            return True

        if not self._is_instagram_running():
            self._log(f"   ⚠️ Instagram не запущен ({reason}) — холодный старт")
            try:
                self.device.app_start(INSTAGRAM_PKG, wait=True, stop=False)
                time.sleep(WAIT_LONG)
            except Exception as exc:
                self._log(f"   ❌ Не удалось открыть Instagram: {exc}")
                return False
        elif gentle:
            self._log(
                f"   ℹ️ Instagram запущен ({reason}) — app_start пропущен (gentle)"
            )
            return True

        try:
            self.device.app_start(INSTAGRAM_PKG, wait=True, stop=False)
            time.sleep(WAIT_SHORT)
        except Exception as exc:
            self._log(f"   ⚠️ app_start(stop=False): {exc}")

        ok = self._is_instagram_usable_without_restart()
        if ok:
            self._log("   ✅ Instagram на экране")
        return ok

    def _should_retry_registration(self, attempt, max_attempts):
        """Верификация или код уже введён — без повторов на той же почте."""
        if self.verification_detected:
            self._log(
                "\n⛔ Верификация Instagram — повтор отключён, регистрация завершена"
            )
            return False
        if self.code_submitted_to_instagram:
            self._log(
                "\n⛔ Код уже вводился — повтор на той же почте отключён, стоп"
            )
            return False
        if self.account_registered:
            self._log(
                "\n⛔ Аккаунт уже создан — повтор с переустановкой отключён, стоп"
            )
            return False
        return attempt < max_attempts

    # ========== ANYMESSAGE ==========

    def get_anymessage_email(self, site="instagram.com", retry_delay=5, log_every_attempts=6):
        attempt = 0
        wait_started_at = time.time()

        while True:
            attempt += 1
            try:
                self._log(f"   📧 Заказ почты AnyMessage (попытка {attempt})...")
                response = requests.get(
                    f"{ANYMESSAGE_API_URL}/email/order",
                    params={
                        "token": self.anymessage_token,
                        "site": site,
                        "domain": self.email_domain,
                    },
                    timeout=15,
                )
                data = response.json()

                if data.get("status") == "success":
                    email = data.get("email")
                    activation_id = data.get("id")
                    if email and activation_id:
                        if attempt > 1:
                            waited_for = int(time.time() - wait_started_at)
                            self._log(
                                f"   ✅ AnyMessage: почта после {attempt} попыток, ожидание {waited_for} сек"
                            )
                        return email, activation_id

                value_text = str(data.get("value", "")).strip().lower()
                message_text = str(data.get("message", "")).strip().lower()
                no_emails = (
                    value_text == "no emails"
                    or "no emails" in value_text
                    or "no emails" in message_text
                )

                if no_emails:
                    waited_for = int(time.time() - wait_started_at)
                    if attempt == 1 or attempt % max(1, log_every_attempts) == 0:
                        self._log(
                            f"   ⏳ AnyMessage: почт нет, жду... попытка {attempt}, {waited_for} сек"
                        )
                    time.sleep(max(1, retry_delay))
                    continue

                self._log(f"   ❌ Ошибка заказа почты AnyMessage: {data}")
                return None, None

            except Exception as e:
                waited_for = int(time.time() - wait_started_at)
                if attempt == 1 or attempt % max(1, log_every_attempts) == 0:
                    self._log(
                        f"   ⚠️ AnyMessage: ошибка, жду... попытка {attempt}, {waited_for} сек: {e}"
                    )
                time.sleep(max(1, retry_delay))

    def get_anymessage_code(self, max_attempts=60, delay=None, code_min_len=6, code_max_len=6):
        if delay is None:
            delay = CODE_POLL_DELAY
        if not self.activation_id:
            self._log("   ❌ Нет activation_id для получения кода")
            return None

        self._log(
            f"   🔑 Ожидаю код (до {max_attempts} попыток, poll {delay} сек)..."
        )

        for attempt in range(max_attempts):
            self._trace_api(
                "AnyMessage getmessage",
                f"попытка {attempt + 1}/{max_attempts} id={self.activation_id}",
            )
            try:
                url = (
                    f"{ANYMESSAGE_API_URL}/email/getmessage"
                    f"?token={self.anymessage_token}&id={self.activation_id}&preview=0"
                )
                response = requests.get(url, timeout=12.33)
                data = response.json()

                if data.get("status") == "success":
                    msg_body = data.get("value", "")
                    code = self._extract_confirmation_code_from_text(
                        msg_body, code_min_len, code_max_len
                    )
                    if code:
                        self._log(f"   ✅ Получен код: {code} (попытка {attempt + 1})")
                        self._trace_api("AnyMessage OK", f"code={code}")
                        return code
                elif data.get("status") == "error" and data.get("value") == "wait message":
                    pass

                if attempt > 0 and attempt % 10 == 0:
                    self._log(f"   ⏳ Код ещё не пришёл... {attempt}/{max_attempts}")

                if attempt + 1 < max_attempts:
                    time.sleep(delay)

            except Exception as e:
                if attempt == 0 or (attempt + 1) % 5 == 0:
                    self._log(f"   ⚠️ Ошибка получения кода: {e}")
                if attempt + 1 < max_attempts:
                    time.sleep(delay)

        self._log("   ❌ Не удалось получить код подтверждения")
        return None

    # ========== SMSBOWER ==========

    def _get_smsbower_service_candidates(self, service=None, platform="instagram"):
        base = (service or platform or "instagram").strip().lower()
        candidates_map = {
            "instagram": ["instagram", "ig", "instagram.com"],
            "ig": ["ig", "instagram", "instagram.com"],
            "instagram.com": ["instagram.com", "instagram", "ig"],
            "facebook": ["facebook", "fb", "facebook.com"],
            "fb": ["fb", "facebook", "facebook.com"],
            "facebook.com": ["facebook.com", "facebook", "fb"],
        }
        raw_candidates = candidates_map.get(base, [base])
        result = []
        seen = set()
        for item in raw_candidates:
            value = str(item or "").strip()
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result or ["instagram"]

    def _set_smsbower_mail_status(self, mail_id, status):
        if not self.smsbower_token:
            return False, {"error": "empty token"}
        try:
            response = requests.get(
                f"{SMSBOWER_MAIL_API_URL}/setStatus",
                params={
                    "api_key": self.smsbower_token,
                    "id": mail_id,
                    "status": status,
                },
                timeout=15,
            )
            data = response.json()
            return bool(data.get("status") == 1), data
        except Exception as e:
            return False, {"error": str(e)}

    def get_smsbower_email(
        self,
        domain=None,
        service=None,
        platform="instagram",
        retry_delay=5,
        log_every_attempts=6,
    ):
        api_token = self.smsbower_token or ""
        if not api_token:
            self._log("   ❌ SMSBower: API key пустой")
            return None, None

        domain = domain or self.email_domain
        service_candidates = self._get_smsbower_service_candidates(
            service=service, platform=platform
        )
        attempt = 0
        wait_started_at = time.time()

        while True:
            attempt += 1
            last_error = None
            wait_and_retry = False
            for service_code in service_candidates:
                try:
                    self._log(
                        f"   📧 SMSBower: заказ почты (попытка {attempt}, service={service_code})..."
                    )
                    response = requests.get(
                        f"{SMSBOWER_MAIL_API_URL}/getActivation",
                        params={
                            "api_key": api_token,
                            "service": service_code,
                            "domain": domain,
                        },
                        timeout=20,
                    )
                    data = response.json()
                    if data.get("status") == 1:
                        email = str(data.get("mail") or "").strip()
                        mail_id = data.get("mailId")
                        if email and mail_id:
                            if attempt > 1:
                                waited_for = int(time.time() - wait_started_at)
                                self._log(
                                    f"   ✅ SMSBower: почта после {attempt} попыток, "
                                    f"ожидание {waited_for} сек (service={service_code})"
                                )
                            return email, mail_id
                    error_text = str(
                        data.get("error") or data.get("message") or ""
                    ).strip()
                    error_lower = error_text.lower()
                    if "no mails yet" in error_lower:
                        wait_and_retry = True
                        last_error = error_text or "No mails yet"
                        continue
                    if "insufficient balance" in error_lower:
                        self._log(f"   ❌ SMSBower: недостаточно баланса ({error_text})")
                        return None, None
                    if "no such service" in error_lower or "bad service" in error_lower:
                        last_error = error_text or f"Unsupported service {service_code}"
                        continue
                    last_error = error_text or str(data)
                except Exception as e:
                    last_error = str(e)

            if wait_and_retry:
                waited_for = int(time.time() - wait_started_at)
                if attempt == 1 or attempt % max(1, log_every_attempts) == 0:
                    self._log(
                        f"   ⏳ SMSBower: почт нет, жду... попытка {attempt}, {waited_for} сек"
                    )
                time.sleep(max(1, int(retry_delay)))
                continue

            self._log(f"   ❌ SMSBower: не удалось заказать почту ({last_error})")
            return None, None

    def get_smsbower_code(
        self,
        mail_id=None,
        max_attempts=60,
        delay=1,
        code_min_len=6,
        code_max_len=6,
        auto_close=True,
        log_every_attempts=5,
    ):
        api_token = self.smsbower_token or ""
        mail_id = mail_id or self.activation_id
        if not api_token:
            self._log("   ❌ SMSBower: API key пустой")
            return None
        if not mail_id:
            self._log("   ❌ SMSBower: нет mail_id для получения кода")
            return None

        self._log(
            f"   🔑 SMSBower: ожидаю код (до {max_attempts} попыток, каждые {delay} сек)..."
        )

        for attempt in range(max_attempts):
            try:
                response = requests.get(
                    f"{SMSBOWER_MAIL_API_URL}/getCode",
                    params={"api_key": api_token, "mailId": mail_id},
                    timeout=15,
                )
                data = response.json()
                if data.get("status") == 1:
                    raw_code = str(data.get("code") or "").strip()
                    code = (
                        self._extract_confirmation_code_from_text(
                            raw_code, code_min_len, code_max_len
                        )
                        or raw_code
                    )
                    code = str(code or "").strip()
                    if code:
                        self._log(f"   ✅ SMSBower: получен код: {code}")
                        if auto_close:
                            self._set_smsbower_mail_status(mail_id, 3)
                        return code

                error_text = str(data.get("error") or "").strip().lower()
                if (attempt + 1) < max_attempts and (
                    attempt == 0
                    or (attempt + 1) % max(1, int(log_every_attempts)) == 0
                ):
                    if "code has not been received yet" in error_text:
                        self._log(
                            f"   ⏳ SMSBower: код ещё не пришёл... "
                            f"{attempt + 1}/{max_attempts}"
                        )
                    elif error_text:
                        self._log(
                            f"   ⚠️ SMSBower: {error_text} "
                            f"({attempt + 1}/{max_attempts})"
                        )
                time.sleep(delay)
            except Exception as e:
                if attempt == 0 or (attempt + 1) % 5 == 0:
                    self._log(f"   ⚠️ SMSBower: ошибка получения кода: {e}")
                time.sleep(delay)

        self._log("   ❌ SMSBower: не удалось получить код подтверждения")
        return None

    # ========== EMAIL (унифицировано) ==========

    def get_email(self, site="instagram.com"):
        if self.email_mode == "smsbower":
            return self.get_smsbower_email(domain=self.email_domain, platform="instagram")
        return self.get_anymessage_email(site=site)

    def get_confirmation_code(self, max_attempts=60, delay=None):
        if delay is None:
            delay = CODE_POLL_DELAY
        if self.email_mode == "smsbower":
            return self.get_smsbower_code(
                mail_id=self.activation_id,
                max_attempts=60,
                delay=1,
            )
        return self.get_anymessage_code(max_attempts=max_attempts, delay=delay)

    def _extract_confirmation_code_from_text(self, msg_body, code_min_len=6, code_max_len=6):
        if not msg_body:
            return None

        m = re.search(rf"\b(\d{{{code_min_len},{code_max_len}}})\b", msg_body)
        if m:
            return m.group(1)

        m = re.search(rf"\[(\d{{{code_min_len},{code_max_len}}})\]", msg_body)
        if m:
            return m.group(1)

        m = re.search(rf"\d{{{code_min_len//2}}}\s*\d{{{code_min_len//2}}}", msg_body)
        if m:
            s = re.sub(r"\s+", "", m.group(0))
            if code_min_len <= len(s) <= code_max_len:
                return s

        return None

    # ========== ГЕНЕРАЦИЯ ДАННЫХ (как в test.py) ==========

    def generate_password(self):
        lowercase = "".join(random.choices(string.ascii_lowercase, k=4))
        uppercase = "".join(random.choices(string.ascii_uppercase, k=3))
        digits = "".join(random.choices(string.digits, k=2))
        password = list(lowercase + uppercase + digits + "@")
        random.shuffle(password)
        return "".join(password)

    def generate_full_name(self):
        first_names = [
            "Delly", "John", "Jane", "Alex", "Sam", "Chris", "Taylor", "Jordan",
            "Morgan", "Casey", "Emma", "Olivia", "James", "Michael",
        ]
        last_names = [
            "Adof", "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia",
            "Miller", "Davis", "Wilson",
        ]
        return f"{random.choice(first_names)} {random.choice(last_names)}"

    def generate_username_from_email(self):
        email_local = self.email.split("@")[0]
        random_digits = "".join(random.choices(string.digits, k=2))
        return self._sanitize_ig_username(f"{email_local}{random_digits}")

    def _sanitize_ig_username(self, raw):
        if raw is None:
            return ""
        s = str(raw).strip()
        s = s.replace("[]", "").replace("[", "").replace("]", "")
        if "+" in s:
            s = s.split("+", 1)[0]
        s = "".join(ch for ch in s if ord(ch) < 128)
        s = re.sub(r"[^a-zA-Z0-9_.]+", "", s)
        s = s.lower()
        s = re.sub(r"\.{2,}", ".", s).strip("._")
        if len(s) < 3 or not s:
            s = "u" + "".join(random.choices(string.ascii_lowercase + string.digits, k=9))
        if len(s) > 30:
            s = s[:30]
        return s

    def _generate_birth_date(self):
        age = random.randint(20, 40)
        today = datetime.date.today()
        year = today.year - age
        month = random.randint(1, 12)
        day = random.randint(1, 28)
        return datetime.date(year, month, day)

    # ========== ANDROID UI — ДИНАМИЧЕСКИЕ ОЖИДАНИЯ ==========

    def _try_selector(self, **kwargs):
        """Безопасный UiObject: u2 не принимает editable=False и editable=True."""
        try:
            return self.device(**kwargs)
        except ReferenceError:
            return None
        except Exception:
            return None

    def _screen_has_any_text(self, needles, timeout=ACTION_TIMEOUT, poll=WAIT_POLL, label="экран"):
        if isinstance(needles, str):
            needles = [needles]
        self._log(f"   ⏳ Жду {label} (до {timeout} сек)...")
        start = time.time()
        last_log = 0
        while time.time() - start < timeout:
            found = False
            for needle in needles:
                if self._ui_has_text(needle):
                    found = True
                    break
            if not found:
                blob = self._get_screen_text_lower()
                for needle in needles:
                    if blob and needle.lower() in blob:
                        found = True
                        break
            if found:
                self._log(f"   ✅ {label}: найдено ({int(time.time()-start)} сек)")
                return True
            elapsed = int(time.time() - start)
            if elapsed - last_log >= 3:
                self._log(f"   ⏳ {label}: ещё жду... ({elapsed}/{timeout} сек)")
                last_log = elapsed
            time.sleep(poll)
        self._log(f"   ❌ {label}: не появился за {timeout} сек")
        return False

    def _wait_for_edit_text(
        self,
        timeout=ACTION_TIMEOUT,
        password=False,
        hint_variants=None,
        label="поле ввода",
    ):
        self._log(f"   ⏳ Жду {label} (до {timeout} сек)...")
        start = time.time()
        last_log = 0
        while time.time() - start < timeout:
            candidates = []

            if hint_variants:
                if isinstance(hint_variants, str):
                    hint_variants = [hint_variants]
                for hint in hint_variants:
                    el = self.device(className="android.widget.EditText", textContains=hint)
                    if el.exists:
                        candidates.append(el)
                    el = self.device(className="android.widget.EditText", descriptionContains=hint)
                    if el.exists:
                        candidates.append(el)

            if password:
                pwd_el = self._password_edit_field()
                if pwd_el is not None:
                    candidates.append(pwd_el)

            if not candidates:
                xpath_el = self._xpath_first_editable(exclude_password=password)
                if xpath_el is not None:
                    candidates.append(xpath_el)

            if not candidates:
                try:
                    el = self.device(className="android.widget.AutoCompleteTextView")
                    if el.exists:
                        candidates.append(el)
                except Exception:
                    pass

            if not candidates:
                el = self.device(className="android.widget.EditText")
                if el.exists:
                    candidates.append(el)

            for el in candidates:
                try:
                    if not el.exists:
                        continue
                    is_pwd = self._is_password_input(el)
                    if password and not is_pwd:
                        continue
                    if not password and is_pwd:
                        continue
                    self._log(f"   ✅ {label} найдено ({int(time.time()-start)} сек)")
                    return el
                except Exception:
                    continue

            elapsed = int(time.time() - start)
            if elapsed - last_log >= 3:
                self._log(f"   ⏳ {label}: ещё не на экране ({elapsed}/{timeout} сек)")
                last_log = elapsed
            time.sleep(WAIT_POLL)

        self._log(f"   ❌ {label} не найдено за {timeout} сек")
        return None

    def click_by_text(
        self,
        text_variants,
        max_attempts=3,
        scroll=False,
        quiet=False,
        pause_after=None,
    ):
        if isinstance(text_variants, str):
            text_variants = [text_variants]
        pause = CLICK_PAUSE if pause_after is None else pause_after

        for attempt in range(max_attempts):
            for text in text_variants:
                if not quiet:
                    self._log(
                        f"   🔍 Ищу «{text}» (попытка {attempt + 1}/{max_attempts})..."
                    )

                btn = self.device(text=text)
                if not btn.exists:
                    btn = self.device(text=text, className="android.widget.Button")
                if not btn.exists:
                    btn = self.device(textContains=text)

                if not btn.exists and scroll:
                    self.device.swipe(
                        self._screen_w // 2,
                        int(self._screen_h * 0.72),
                        self._screen_w // 2,
                        int(self._screen_h * 0.35),
                        0.25,
                    )
                    time.sleep(0.3)
                    continue

                if btn.exists:
                    if not quiet:
                        self._log(f"   ✅ Нажимаю «{text}»")
                    try:
                        btn.click()
                    except UiObjectNotFoundError:
                        if not quiet:
                            self._log(f"   ⚠️ «{text}» исчезло до клика — повтор")
                        time.sleep(0.25)
                        continue
                    except Exception as exc:
                        if "-32002" in str(exc):
                            if not quiet:
                                self._log(f"   ⚠️ «{text}» исчезло до клика — повтор")
                            time.sleep(0.25)
                            continue
                        raise
                    self._invalidate_screen_cache()
                    if pause:
                        time.sleep(pause)
                    return True

            if scroll:
                self.device.swipe(
                    self._screen_w // 2,
                    int(self._screen_h * 0.72),
                    self._screen_w // 2,
                    int(self._screen_h * 0.35),
                    0.25,
                )
                time.sleep(0.3)

        if not quiet:
            self._log(f"   ❌ Кнопка не найдена: {text_variants}")
        return False

    def _fast_click(self, variants, label="", log=True):
        """Мгновенный клик — минимум adb-запросов, без пауз после."""
        if isinstance(variants, str):
            variants = [variants]
        for text in variants:
            for spec in (
                {"text": text},
                {"textContains": text},
                {"description": text},
                {"descriptionContains": text},
            ):
                try:
                    el = self.device(**spec)
                    if el.exists:
                        if log and label:
                            self._log(f"   🤖 [Post-reg] «{label}»")
                        el.click()
                        self._invalidate_screen_cache()
                        return True
                except Exception:
                    continue
        return False

    def _blob_has_any(self, blob, *needles):
        if not blob:
            return False
        for needle in needles:
            if needle and str(needle).lower() in blob:
                return True
        return False

    def _is_human_verification_screen(self, text=None):
        """Checkpoint «Confirm you're human» — это вериф, НЕ onboarding."""
        if self._ui_has_text(*self._UI_HUMAN_VERIFY_MARKERS):
            return True
        if text is not None:
            return self._blob_has_any(text, *self._UI_HUMAN_VERIFY_TEXT)
        text = self._get_screen_text_lower()
        return self._blob_has_any(text, *self._UI_HUMAN_VERIFY_TEXT)

    def click_next_repeated(self, max_attempts=3, extra_clicks=2, reason=""):
        variants = ["Далее", "Next", "Продолжить", "Continue"]
        if reason:
            self._log(f"   🔘 Next ({reason})...")
        self._short_pause(reason=f"перед Next ({reason})" if reason else "перед Next")

        clicked = False
        for attempt in range(max_attempts):
            if self.click_by_text(variants, max_attempts=1, quiet=attempt > 0):
                clicked = True
                break
            self._log_current_screen_light(
                f"Далее не нажата, попытка {attempt + 1}/{max_attempts}"
            )
            time.sleep(0.6)

        if not clicked:
            self._log("   ❌ Не удалось нажать Далее/Next")
            return False

        for i in range(extra_clicks):
            time.sleep(0.45)
            try:
                self.click_by_text(variants, max_attempts=1, quiet=True)
            except Exception:
                pass
        return True

    def click_next_once(self, reason=""):
        """Один клик «Далее» без повторных нажатий (после имени/username/даты)."""
        variants = ["Далее", "Next", "Продолжить", "Continue"]
        if reason:
            self._log(f"   🔘 Next ({reason})...")
        time.sleep(WAIT_SHORT)
        for attempt in range(3):
            if self.click_by_text(variants, max_attempts=1, quiet=attempt > 0):
                return True
            if attempt < 2:
                self._log_current_screen_light(f"Далее не нажата, попытка {attempt + 1}/3")
            time.sleep(0.25)
        self._log("   ❌ Не удалось нажать Далее/Next")
        return False

    def input_into_field(self, field, text, label="поле", clear_first=True, human_delay=False):
        if not field or not field.exists:
            self._log(f"   ❌ {label}: элемент недоступен")
            return False

        preview = text if len(text) <= 24 else text[:24] + "..."
        self._log(f"   ✏️ Ввожу в {label}: {preview}")

        try:
            field.click()
            time.sleep(0.1)
        except Exception:
            pass

        if clear_first:
            try:
                field.clear_text()
            except Exception:
                pass
            time.sleep(0.08)

        text = str(text)
        if human_delay:
            for ch in text:
                try:
                    self.device.send_keys(ch)
                except Exception:
                    pass
                time.sleep(random.uniform(0.06, 0.14))
        else:
            try:
                field.set_text(text)
            except Exception:
                try:
                    self.device.send_keys(text)
                except Exception:
                    self._log(f"   ❌ {label}: не удалось ввести текст")
                    return False

        time.sleep(0.12)
        self._log(f"   ✅ {label}: ввод выполнен")
        return True

    def paste_into_field(self, field, text, label="поле", clear_first=True):
        """Вставка текста через буфер обмена (надёжно для пароля со спецсимволами)."""
        if not field or not field.exists:
            self._log(f"   ❌ {label}: элемент недоступен")
            return False

        if not self._is_instagram_usable_without_restart():
            self._ensure_instagram_foreground(f"перед вводом {label}", gentle=True)

        text = str(text)
        preview = text if len(text) <= 24 else text[:24] + "..."
        self._log(f"   📋 Вставляю в {label}: {preview}")

        try:
            field.click()
            time.sleep(0.3)
        except Exception:
            pass

        if clear_first:
            try:
                field.clear_text()
            except Exception:
                pass
            time.sleep(0.15)

        pasted = False

        try:
            field.set_text(text)
            time.sleep(0.35)
            pasted = True
            self._log(f"   ✅ {label}: вставка через set_text")
        except Exception:
            pass

        if not pasted:
            try:
                self.device.set_clipboard(text)
                time.sleep(0.15)
                field.click()
                time.sleep(0.15)
                self.device.press(279)
                time.sleep(0.4)
                pasted = True
                self._log(f"   ✅ {label}: вставка через буфер обмена")
            except Exception:
                pass

        if not pasted:
            for needle in ("Вставить", "Paste", "PASTE"):
                btn = self.device(text=needle)
                if btn.exists:
                    try:
                        self.device.set_clipboard(text)
                        time.sleep(0.1)
                        field.long_click()
                        time.sleep(0.4)
                        btn.click()
                        time.sleep(0.35)
                        pasted = True
                        self._log(f"   ✅ {label}: вставка через меню «{needle}»")
                        break
                    except Exception:
                        continue

        if not pasted:
            try:
                field.set_text(text)
                time.sleep(0.35)
                pasted = True
                self._log(f"   ✅ {label}: вставка через set_text (fallback)")
            except Exception:
                pass

        if not pasted:
            try:
                self.device.send_keys(text)
                time.sleep(0.35)
                pasted = True
                self._log(f"   ✅ {label}: вставка через send_keys (fallback)")
            except Exception as exc:
                self._log(f"   ❌ {label}: вставка не удалась ({exc})")
                return False

        return True

    def input_text(self, field_variants, text, clear_first=True, label="поле ввода"):
        field = self._wait_for_edit_text(
            timeout=ACTION_TIMEOUT,
            hint_variants=field_variants,
            label=label,
        )
        if not field:
            return False
        return self.input_into_field(field, text, label=label, clear_first=clear_first)

    def _xpath_first_editable(self, exclude_password=True):
        queries = [
            '//*[@editable="true" and @password="false"]',
            '//*[@class="android.widget.EditText" and @password="false"]',
            '//*[@class="android.widget.EditText"]',
            '//*[@editable="true"]',
        ]
        for query in queries:
            try:
                node = self.device.xpath(query)
                if node.exists:
                    if exclude_password:
                        try:
                            info = node.info
                            if info.get("password") is True:
                                continue
                        except Exception:
                            pass
                    return node
            except Exception:
                continue
        return None

    def _refind_code_input_field(self, allow_focus_tap=False):
        """Свежее поле кода после паузы API — без долгого wait_for_code_input."""
        field = self._find_code_input_field(code_context=True)
        if field:
            return field
        if allow_focus_tap:
            return self._try_focus_code_input()
        return None

    def _try_focus_code_input(self):
        """Тап по полю кода, если u2 не видит EditText сразу."""
        for query in (
            '//*[@class="android.widget.EditText" and @password="false"]',
            '//*[@class="android.widget.EditText"]',
            '//*[@editable="true" and @password="false"]',
            '//*[@editable="true"]',
        ):
            try:
                node = self.device.xpath(query)
                if node.exists:
                    node.click()
                    time.sleep(0.25)
                    found = self._find_code_input_field(code_context=True)
                    if found:
                        return found
                    return node
            except Exception:
                continue

        try:
            self.device.click(self._screen_w // 2, int(self._screen_h * 0.42))
            time.sleep(0.35)
        except Exception:
            pass

        sel = self._try_selector(focused=True)
        if sel and sel.exists:
            return sel
        return self._find_code_input_field(code_context=True)

    def wait_for_code_input(self, timeout=ACTION_TIMEOUT):
        self._log(
            f"   ⏳ Жду поле кода (до {timeout} сек, u2-only)..."
        )
        self._code_wait_result = None
        start = time.monotonic()
        last_log = 0

        while time.monotonic() - start < timeout:
            sec = int(time.monotonic() - start)

            err = self._check_post_email_errors_u2_only()
            if err == "rate_limit":
                self._log(f"   ⚠️ [{sec}/{timeout}] Rate-limit — не код")
                self._code_wait_result = "rate_limit"
                return None

            if not self._has_code_screen_markers_u2_only():
                if sec - last_log >= 5:
                    if self._is_still_signup_email_step_u2_only():
                        self._log(f"   ⏳ [{sec}/{timeout}] Ещё email-экран")
                    else:
                        self._log(f"   ⏳ [{sec}/{timeout}] Жду маркеры кода")
                    last_log = sec
                time.sleep(POLL_UI_ONLY)
                continue

            try:
                el = self._find_code_input_field(code_context=True)
            except Exception:
                el = None

            if not el:
                try:
                    el = self._try_focus_code_input()
                except Exception:
                    el = None

            if el:
                self._log(f"   ✅ [{sec}/{timeout}] Поле кода найдено")
                self._trace_step(
                    "wait_for_code_input OK",
                    f"{sec}с — дальше device только после API",
                )
                return el

            if sec - last_log >= 5:
                self._log(f"   ⏳ [{sec}/{timeout}] Экран кода, жду поле...")
                last_log = sec
            time.sleep(POLL_UI_ONLY)

        if self._check_post_email_errors_u2_only() == "rate_limit":
            self._code_wait_result = "rate_limit"
            return None

        self._log(f"   ❌ Поле кода не появилось за {timeout} сек")
        snap = self._take_poll_snapshot(force=True)
        screen = self._detect_registration_screen(screen_text=snap)
        self._log(f"   📍 Экран при таймауте кода: «{screen}»")
        return None

    def input_confirmation_code(self, field, code):
        """Ввод OTP: set_text без лишних app_start/тапов (важно для Samsung S9)."""
        self._trace_step("input_confirmation_code START", str(code)[:8])
        code = str(code).strip()
        preview = code if len(code) <= 8 else code[:8] + "..."
        self._log(f"   ✏️ Ввожу код подтверждения: {preview}")

        on_code_screen = (
            self._is_instagram_running() and self._has_code_screen_markers()
        )
        if not on_code_screen:
            self._ensure_instagram_foreground("перед вводом кода", gentle=True)
        else:
            self._log("   ℹ️ Экран кода на месте — Instagram не трогаем")

        field = self._refind_code_input_field(allow_focus_tap=False)
        if not field:
            field = self._refind_code_input_field(allow_focus_tap=True)

        if field:
            for attempt, use_click in enumerate((False, True)):
                try:
                    if use_click:
                        field.click()
                        time.sleep(0.12)
                    try:
                        field.clear_text()
                    except Exception:
                        pass
                    field.set_text(code)
                    time.sleep(0.3)
                    self._log(
                        f"   ✅ код: ввод через set_text"
                        f"{' + click' if use_click else ''}"
                    )
                    return True
                except Exception:
                    if attempt == 0:
                        field = self._refind_code_input_field(allow_focus_tap=True)
                        if not field:
                            break
                    continue

        try:
            focused = self._try_selector(focused=True)
            if focused and focused.exists:
                focused.set_text(code)
                time.sleep(0.3)
                self._log("   ✅ код: ввод через focused set_text")
                return True
        except Exception:
            pass

        try:
            self._try_focus_code_input()
            self.device.send_keys(code)
            time.sleep(0.3)
            self._log("   ✅ код: ввод через send_keys (fallback)")
            return True
        except Exception as exc:
            self._log(f"   ❌ код: не удалось ввести ({exc})")
            return False

    # ========== ГАЛОЧКА «ЗАПОМНИТЬ» ==========

    def uncheck_remember_login(self):
        self._log("   🔍 Ищу галочку «Запомнить данные для входа»...")
        self._short_pause(reason="перед поиском галочки")

        selectors = [
            self.device(textContains="Запомнить"),
            self.device(textContains="запомнить"),
            self.device(textContains="Remember"),
            self.device(textContains="remember"),
            self.device(textContains="Сохранить данные"),
            self.device(className="android.widget.CheckBox", checked=True),
            self.device(className="android.widget.CompoundButton", checked=True),
        ]

        for sel in selectors:
            try:
                if not sel.exists:
                    continue
                info = sel.info
                if info.get("checked") is True:
                    self._log("   ✅ Галочка отмечена — снимаю")
                    sel.click()
                    time.sleep(0.4)
                    return True
                text = info.get("text") or info.get("contentDescription") or ""
                if any(k in text.lower() for k in ("запомн", "remember", "сохран")):
                    self._log(f"   ✅ Нажимаю на блок галочки: {text[:40]}")
                    sel.click()
                    time.sleep(0.4)
                    return True
            except Exception:
                continue

        # Клик рядом с текстом через родителя
        for needle in ("Запомнить", "запомнить", "Remember"):
            row = self.device(textContains=needle)
            if row.exists:
                self._log(f"   ✅ Нашёл текст «{needle}» — пробую снять галочку")
                row.click()
                time.sleep(0.4)
                return True

        self._log("   ⚠️ Галочка не найдена или уже снята — продолжаю")
        return False

    # ========== ANDROID DATE PICKER (колёса / свайпы) ==========

    _WHEEL_COL_X = (0.20, 0.50, 0.80)  # день, месяц, год

    def _picker_dialog_open(self):
        return self._ui_has_text("Установите дату", "Установить дату", "Set date")

    def _birthday_screen_ready(self):
        if self._picker_dialog_open():
            return True
        if self._ui_has_text(*self._UI_BIRTHDAY_MARKERS):
            return True
        text = self._get_screen_text_lower()
        return any(
            k in text
            for k in ("дату рождения", "укажите дату", "birthday", "birth date")
        )

    def _month_from_picker_text(self, text):
        if not text:
            return None
        t = str(text).lower().strip().rstrip(".")
        for num, short in RU_MONTH_SHORT.items():
            if t == short or t == f"{short}.":
                return num
        for num, short in sorted(
            RU_MONTH_SHORT.items(), key=lambda x: len(x[1]), reverse=True
        ):
            if t.startswith(short):
                return num
        en = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        for key, num in en.items():
            if t.startswith(key):
                return num
        return None

    def _read_wheel_value(self, picker=None, col_index=None):
        """Читает значение только из своей колонки (без mix-up между колёсами)."""
        if picker is not None:
            return self._read_picker_center_value(picker) or ""
        if col_index is not None:
            vals = self._read_wheel_values_from_hierarchy()
            if vals[col_index]:
                return vals[col_index]
        return ""

    def _is_wheel_oscillating(self, history):
        """Качели только если два РАЗНЫХ значения чередуются (6↔9)."""
        if len(history) < 4:
            return False
        return (
            history[-1] == history[-3]
            and history[-2] == history[-4]
            and history[-1] != history[-2]
        )

    def _is_wheel_stuck(self, history):
        """Одно и то же значение несколько раз подряд — точный свайп не двигает."""
        return len(history) >= 3 and history[-1] == history[-2] == history[-3]

    def _pick_swipe_mode(self, history, diff, step_size, label):
        if self._is_wheel_stuck(history):
            self._log(
                f"   ⚠️ {label}: застряли на {history[-1]} — переключаю на обычный свайп"
            )
            return "normal"
        if self._is_wheel_oscillating(history):
            self._log(
                f"   ⚠️ {label}: качели {history[-2]}↔{history[-1]} — средний свайп"
            )
            return "mid"
        if abs(diff) < max(2, step_size):
            return "fine"
        return "normal"

    def _wheel_swipe(self, picker, col_index, direction, steps=1, mode="normal"):
        for _ in range(max(1, int(steps))):
            if picker is not None:
                self._swipe_picker(picker, direction, mode=mode)
            elif col_index is not None:
                self._swipe_wheel_column(col_index, direction, mode=mode)
        time.sleep(0.09 if mode == "fine" else (0.11 if mode == "mid" else 0.14))

    def _opposite_direction(self, direction):
        return "up" if direction == "down" else "down"

    def _month_diff_shortest(self, cur, target):
        diff = int(target) - int(cur)
        if diff > 6:
            diff -= 12
        if diff < -6:
            diff += 12
        return diff

    def _calibrate_wheel(self, picker, col_index, label=""):
        """
        Калибровка колонки: направление роста + шаг обычного и точного свайпа.
        На Samsung часто 1 свайп = 3 года/месяца.
        """
        val1 = self._read_wheel_value(picker, col_index)
        n1 = int(val1) if val1 and str(val1).isdigit() else None
        m1 = self._month_from_picker_text(val1)

        self._wheel_swipe(picker, col_index, "down", steps=1, mode="normal")
        val2 = self._read_wheel_value(picker, col_index)
        n2 = int(val2) if val2 and str(val2).isdigit() else None
        m2 = self._month_from_picker_text(val2)

        inc = "down"
        step = 1
        if n1 is not None and n2 is not None:
            step = max(1, abs(n2 - n1))
            inc = "down" if n2 > n1 else ("up" if n2 < n1 else "down")
        elif m1 is not None and m2 is not None:
            md = self._month_diff_shortest(m1, m2)
            step = max(1, abs(md))
            inc = "down" if md > 0 else ("up" if md < 0 else "down")

        self._wheel_swipe(
            picker, col_index, self._opposite_direction(inc), steps=1, mode="normal"
        )

        val3 = self._read_wheel_value(picker, col_index)
        n3 = int(val3) if val3 and str(val3).isdigit() else None
        m3 = self._month_from_picker_text(val3)

        self._wheel_swipe(picker, col_index, "down", steps=1, mode="fine")
        val4 = self._read_wheel_value(picker, col_index)
        n4 = int(val4) if val4 and str(val4).isdigit() else None
        m4 = self._month_from_picker_text(val4)

        fine_step = 1
        if n3 is not None and n4 is not None:
            fine_step = max(1, abs(n4 - n3))
        elif m3 is not None and m4 is not None:
            fine_step = max(1, abs(self._month_diff_shortest(m3, m4)))

        self._wheel_swipe(
            picker, col_index, self._opposite_direction(inc), steps=1, mode="fine"
        )

        tag = f" ({label})" if label else ""
        self._log(
            f"   ℹ️ Калибровка{tag}: «{val1}»→«{val2}», шаг={step}, "
            f"точный шаг={fine_step}, рост при «{inc}»"
        )
        return inc, step, fine_step

    def _scroll_wheel_numeric(
        self,
        picker,
        col_index,
        target,
        increases_on,
        step_size,
        fine_step,
        label,
        max_rounds=120,
    ):
        decrease_on = self._opposite_direction(increases_on)
        prev_before_swipe = None
        history = []

        for step in range(max_rounds):
            val = self._read_wheel_value(
                picker, col_index if picker is None else None
            )
            if not val or not str(val).isdigit():
                self._log(f"   📍 {label} сейчас: «{val}» (цель {target})")
                self._wheel_swipe(
                    picker, col_index, decrease_on, steps=1, mode="mid"
                )
                continue

            cur = int(val)
            self._log(f"   📍 {label} сейчас: {cur} (цель {target})")

            if cur == target:
                self._log(f"   ✅ {label}: «{cur}»")
                return True

            history.append(cur)

            if prev_before_swipe is not None:
                if prev_before_swipe < target < cur:
                    self._log(
                        f"   ↩️ {label}: перелёт {prev_before_swipe}→{cur}, средний свайп вниз"
                    )
                    self._wheel_swipe(
                        picker, col_index, decrease_on, steps=1, mode="mid"
                    )
                    prev_before_swipe = None
                    continue
                if prev_before_swipe > target > cur:
                    self._log(
                        f"   ↩️ {label}: перелёт {prev_before_swipe}→{cur}, средний свайп вверх"
                    )
                    self._wheel_swipe(
                        picker, col_index, increases_on, steps=1, mode="mid"
                    )
                    prev_before_swipe = None
                    continue

            diff = target - cur
            mode = self._pick_swipe_mode(history, diff, step_size, label)
            direction = increases_on if diff > 0 else decrease_on
            prev_before_swipe = cur
            self._wheel_swipe(
                picker, col_index, direction, steps=1, mode=mode
            )

        val = self._read_wheel_value(picker, col_index if picker is None else None)
        ok = val and str(val).isdigit() and int(val) == target
        if ok:
            self._log(f"   ✅ {label}: «{val}»")
        else:
            self._log(f"   ⚠️ {label}: не выставлено (осталось «{val}», цель {target})")
        return ok

    def _scroll_wheel_month(
        self,
        picker,
        col_index,
        target_month,
        increases_on,
        step_size,
        fine_step,
        label,
        max_rounds=50,
    ):
        decrease_on = self._opposite_direction(increases_on)
        target_name = RU_MONTH_SHORT.get(target_month, str(target_month))
        prev_before_swipe = None
        history = []

        for step in range(max_rounds):
            val = self._read_wheel_value(
                picker, col_index if picker is None else None
            )
            cur = self._month_from_picker_text(val)
            if cur is None and val and str(val).isdigit() and int(val) > 12:
                self._log(
                    f"   ❌ {label}: колонка показывает день («{val}»), не месяц — "
                    f"перепутаны колонки picker"
                )
                return False
            if cur is None:
                self._log(f"   📍 {label} сейчас: «{val}» (цель {target_name})")
                self._wheel_swipe(
                    picker, col_index, decrease_on, steps=1, mode="mid"
                )
                continue

            self._log(
                f"   📍 {label} сейчас: «{val}» / {cur} (цель {target_name})"
            )
            if cur == target_month:
                self._log(f"   ✅ {label}: «{val}»")
                return True

            history.append(cur)

            if prev_before_swipe is not None:
                if prev_before_swipe < target_month < cur:
                    self._log(
                        f"   ↩️ {label}: перелёт {prev_before_swipe}→{cur}, средний свайп вниз"
                    )
                    self._wheel_swipe(
                        picker, col_index, decrease_on, steps=1, mode="mid"
                    )
                    prev_before_swipe = None
                    continue
                if prev_before_swipe > target_month > cur:
                    self._log(
                        f"   ↩️ {label}: перелёт {prev_before_swipe}→{cur}, средний свайп вверх"
                    )
                    self._wheel_swipe(
                        picker, col_index, increases_on, steps=1, mode="mid"
                    )
                    prev_before_swipe = None
                    continue

            diff = self._month_diff_shortest(cur, target_month)
            mode = self._pick_swipe_mode(history, diff, step_size, label)
            direction = increases_on if diff > 0 else decrease_on
            prev_before_swipe = cur
            self._wheel_swipe(
                picker, col_index, direction, steps=1, mode=mode
            )

        val = self._read_wheel_value(picker, col_index if picker is None else None)
        ok = self._month_from_picker_text(val) == target_month
        if ok:
            self._log(f"   ✅ {label}: «{val}»")
        else:
            self._log(
                f"   ⚠️ {label}: не выставлено (осталось «{val}», цель {target_name})"
            )
        return ok

    def _get_date_picker_columns(self):
        """Возвращает 3 колонки NumberPicker или None."""
        columns = []
        for i in range(6):
            picker = self.device(className="android.widget.NumberPicker", instance=i)
            try:
                if picker.exists:
                    columns.append(picker)
                else:
                    break
            except Exception:
                break

        if len(columns) >= 3:
            columns = self._sort_date_picker_columns(columns[:3])
            return columns

        dp = self.device(className="android.widget.DatePicker")
        if dp.exists:
            columns = []
            for i in range(6):
                picker = dp.child(className="android.widget.NumberPicker", instance=i)
                try:
                    if picker.exists:
                        columns.append(picker)
                    else:
                        break
                except Exception:
                    break
            if len(columns) >= 3:
                columns = self._sort_date_picker_columns(columns[:3])
                return columns

        columns = []
        for i in range(1, 4):
            try:
                node = self.device.xpath(f"(//android.widget.NumberPicker)[{i}]")
                if node.exists:
                    columns.append(node)
            except Exception:
                continue
        if len(columns) >= 3:
            columns = self._sort_date_picker_columns(columns[:3])
            return columns
        return None

    def _picker_center_x(self, picker, fallback_idx=0):
        try:
            b = picker.info.get("bounds") or {}
            if b:
                return (b["left"] + b["right"]) // 2
        except Exception:
            pass
        if 0 <= fallback_idx < len(self._WHEEL_COL_X):
            return int(self._screen_w * self._WHEEL_COL_X[fallback_idx])
        return fallback_idx * (self._screen_w // 3)

    def _sort_date_picker_columns(self, columns):
        indexed = list(enumerate(columns[:3]))
        indexed.sort(key=lambda pair: self._picker_center_x(pair[1], pair[0]))
        return [picker for _, picker in indexed]

    def _classify_wheel_value(self, text):
        if not text:
            return "unknown"
        t = str(text).strip()
        if re.match(r"^\d{4}$", t):
            y = int(t)
            if 1920 <= y <= 2035:
                return "year"
        if self._month_from_picker_text(t):
            return "month"
        if t.isdigit():
            n = int(t)
            if n > 12:
                return "day"
            if 1 <= n <= 12:
                return "small_num"
        return "unknown"

    def _restore_wheel_swipes(self, picker, col_index, count):
        for _ in range(max(0, int(count))):
            self._wheel_swipe(picker, col_index, "up", steps=1, mode="normal")

    def _probe_wheel_column_kind(self, picker, col_index):
        """
        Короткий зонд: колонка дня даёт 13..31, месяц — названия или только 1..12.
        """
        if picker is None and col_index is None:
            return "unknown"

        values = []
        swipes = 0
        for _ in range(7):
            val = self._read_wheel_value(picker, col_index)
            if val:
                values.append(val)
            if val and val.isdigit() and int(val) > 12:
                self._restore_wheel_swipes(picker, col_index, swipes)
                return "day"
            if val and self._month_from_picker_text(val) and not val.isdigit():
                self._restore_wheel_swipes(picker, col_index, swipes)
                return "month"
            self._wheel_swipe(picker, col_index, "down", steps=1, mode="normal")
            swipes += 1

        self._restore_wheel_swipes(picker, col_index, swipes)

        for val in values:
            if val and val.isdigit() and int(val) > 12:
                return "day"
        for val in values:
            if val and self._month_from_picker_text(val):
                return "month"
        return "small_num"

    def _resolve_date_picker_layout(self, columns):
        """
        Определяет day/month/year колонки (Samsung/Instagram: DMY или MDY).
        """
        if columns is None or len(columns) < 3:
            vals = self._read_wheel_values_from_hierarchy()
            if len(vals) >= 3 and any(vals):
                slots = []
                for sort_idx, val in enumerate(vals[:3]):
                    slots.append(
                        {
                            "sort_idx": sort_idx,
                            "picker": None,
                            "val": val or "",
                            "kind": self._classify_wheel_value(val or ""),
                        }
                    )
                roles = {}
                for role, kinds in (
                    ("year", ("year",)),
                    ("month", ("month",)),
                    ("day", ("day",)),
                ):
                    for slot in slots:
                        if slot["kind"] in kinds and role not in roles:
                            roles[role] = slot
                            break
                for slot in slots:
                    if slot in roles.values():
                        continue
                    if slot["kind"] == "small_num":
                        probed = self._probe_wheel_column_kind(None, slot["sort_idx"])
                        if probed == "day" and "day" not in roles:
                            roles["day"] = slot
                        elif probed == "month" and "month" not in roles:
                            roles["month"] = slot
                unresolved = [s for s in slots if s not in roles.values()]
                if "year" in roles and len(unresolved) == 2:
                    left, right = sorted(unresolved, key=lambda s: s["sort_idx"])
                    roles.setdefault("month", left)
                    roles.setdefault("day", right)
                for idx, role in enumerate(("day", "month", "year")):
                    if role not in roles and idx < len(slots):
                        roles[role] = slots[idx]
                order = ["?"] * 3
                for role, slot in roles.items():
                    order[slot["sort_idx"]] = {"day": "D", "month": "M", "year": "Y"}[role]
                self._log(
                    f"   ℹ️ Раскладка picker (координаты, слева→направо): {''.join(order)} | "
                    f"день=«{roles['day']['val']}», "
                    f"месяц=«{roles['month']['val']}», "
                    f"год=«{roles['year']['val']}»"
                )
                return {
                    "day_picker": None,
                    "month_picker": None,
                    "year_picker": None,
                    "day_col": roles["day"]["sort_idx"],
                    "month_col": roles["month"]["sort_idx"],
                    "year_col": roles["year"]["sort_idx"],
                    "use_coords": True,
                }
            return {
                "day_picker": None,
                "month_picker": None,
                "year_picker": None,
                "day_col": 0,
                "month_col": 1,
                "year_col": 2,
                "use_coords": True,
            }

        slots = []
        for sort_idx, picker in enumerate(columns[:3]):
            val = (
                self._read_picker_center_value(picker)
                or self._read_picker_value(picker)
                or ""
            )
            slots.append(
                {
                    "sort_idx": sort_idx,
                    "picker": picker,
                    "val": val,
                    "kind": self._classify_wheel_value(val),
                }
            )

        roles = {}

        for role, kinds in (
            ("year", ("year",)),
            ("month", ("month",)),
            ("day", ("day",)),
        ):
            for slot in slots:
                if slot["kind"] in kinds and role not in roles:
                    roles[role] = slot
                    break

        for slot in slots:
            if slot in roles.values():
                continue
            if "year" in roles and "month" in roles and "day" in roles:
                break
            if slot["kind"] != "small_num":
                continue
            probed = self._probe_wheel_column_kind(slot["picker"], None)
            if probed == "day" and "day" not in roles:
                roles["day"] = slot
            elif probed == "month" and "month" not in roles:
                roles["month"] = slot

        unresolved = [s for s in slots if s not in roles.values()]
        if "year" in roles and len(unresolved) == 2:
            left, right = sorted(unresolved, key=lambda s: s["sort_idx"])
            if "month" not in roles and "day" not in roles:
                roles["month"] = left
                roles["day"] = right
            elif "month" not in roles:
                roles["month"] = left if left not in roles.values() else right
            elif "day" not in roles:
                roles["day"] = left if left not in roles.values() else right

        for idx, role in enumerate(("day", "month", "year")):
            if role not in roles and idx < len(slots):
                roles[role] = slots[idx]

        order = ["?"] * 3
        for role, slot in roles.items():
            letter = {"day": "D", "month": "M", "year": "Y"}.get(role, "?")
            order[slot["sort_idx"]] = letter

        self._log(
            f"   ℹ️ Раскладка picker (слева→направо): {''.join(order)} | "
            f"день=«{roles['day']['val']}», "
            f"месяц=«{roles['month']['val']}», "
            f"год=«{roles['year']['val']}»"
        )

        return {
            "day_picker": roles["day"]["picker"],
            "month_picker": roles["month"]["picker"],
            "year_picker": roles["year"]["picker"],
            "day_col": roles["day"]["sort_idx"],
            "month_col": roles["month"]["sort_idx"],
            "year_col": roles["year"]["sort_idx"],
            "use_coords": False,
        }

    def _read_picker_center_value(self, picker):
        """Читает значение в центре колеса (TextView внутри bounds picker)."""
        try:
            pb = picker.info.get("bounds") or {}
            if not pb:
                center_y = int(self._screen_h * 0.45)
                pl, pr = 0, self._screen_w
            else:
                center_y = (pb["top"] + pb["bottom"]) // 2
                pl, pr = pb["left"], pb["right"]
        except Exception:
            center_y = int(self._screen_h * 0.45)
            pl, pr = 0, self._screen_w

        best_text = ""
        best_dist = 10**9

        for cls in ("android.widget.TextView", "android.widget.EditText"):
            try:
                nodes = picker.child(className=cls)
                if not nodes.exists:
                    continue
                for node in nodes:
                    try:
                        text = (node.get_text() or "").strip()
                        if not text:
                            continue
                        nb = node.info.get("bounds") or {}
                        if not nb:
                            continue
                        nx = (nb["left"] + nb["right"]) // 2
                        if nx < pl - 8 or nx > pr + 8:
                            continue
                        ny = (nb["top"] + nb["bottom"]) // 2
                        dist = abs(ny - center_y)
                        if dist < best_dist:
                            best_dist = dist
                            best_text = text
                    except Exception:
                        continue
            except Exception:
                continue

        return best_text

    def _read_picker_value(self, picker):
        try:
            edit = picker.child(className="android.widget.EditText")
            if edit.exists:
                val = edit.get_text()
                if val:
                    return str(val).strip()
        except Exception:
            pass
        try:
            tvs = picker.child(className="android.widget.TextView")
            if tvs.exists:
                val = tvs.get_text()
                if val:
                    return str(val).strip()
        except Exception:
            pass
        return ""

    def _read_wheel_values_from_hierarchy(self):
        """Читает центральные значения 3 колонок из UI dump (fallback)."""
        values = [None, None, None]
        try:
            xml = self.device.dump_hierarchy(compressed=True)
        except Exception:
            return values

        x1 = int(self._screen_w * 0.33)
        x2 = int(self._screen_w * 0.66)
        center_y = int(self._screen_h * 0.45)
        max_dist = int(self._screen_h * 0.06)
        buckets = [[], [], []]

        patterns = (
            r'text="([^"]+)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*text="([^"]+)"',
        )
        for pattern in patterns:
            for m in re.finditer(pattern, xml):
                if pattern.startswith("text"):
                    text = m.group(1).strip()
                    left, top, right, bottom = map(int, m.group(2, 3, 4, 5))
                else:
                    left, top, right, bottom = map(int, m.group(1, 2, 3, 4))
                    text = m.group(5).strip()
                if not text:
                    continue
                cx = (left + right) // 2
                cy = (top + bottom) // 2
                if cx < x1:
                    col = 0
                elif cx < x2:
                    col = 1
                else:
                    col = 2
                dist = abs(cy - center_y)
                if dist > max_dist:
                    continue
                buckets[col].append((dist, text))

        for i, bucket in enumerate(buckets):
            if bucket:
                bucket.sort(key=lambda x: x[0])
                values[i] = bucket[0][1]
        return values

    def _swipe_picker(self, picker, direction="up", mode="normal"):
        try:
            b = picker.info.get("bounds") or {}
            if not b:
                return False
            cx = (b["left"] + b["right"]) // 2
            height = b["bottom"] - b["top"]
            if mode == "bold":
                if direction == "up":
                    self.device.swipe(cx, b["bottom"] - 15, cx, b["top"] + 15, 0.28)
                else:
                    self.device.swipe(cx, b["top"] + 15, cx, b["bottom"] - 15, 0.28)
            else:
                cy = (b["top"] + b["bottom"]) // 2
                if mode == "fine":
                    dy = max(58, height // 6)
                    duration = 0.17
                elif mode == "mid":
                    dy = max(78, height // 4)
                    duration = 0.19
                else:
                    dy = max(80, height // 2)
                    duration = 0.2
                if direction == "up":
                    self.device.swipe(cx, cy + dy, cx, cy - dy, duration)
                else:
                    self.device.swipe(cx, cy - dy, cx, cy + dy, duration)
            return True
        except Exception:
            return False

    def _swipe_wheel_column(self, col_index, direction="up", mode="normal"):
        """Свайп по координатам колонки (день=0, месяц=1, год=2)."""
        cx = int(self._screen_w * self._WHEEL_COL_X[col_index])
        cy = int(self._screen_h * 0.45)
        if mode == "bold":
            top = int(self._screen_h * 0.30)
            bottom = int(self._screen_h * 0.58)
            if direction == "up":
                self.device.swipe(cx, bottom, cx, top, 0.28)
            else:
                self.device.swipe(cx, top, cx, bottom, 0.28)
        elif mode == "fine":
            dy = int(self._screen_h * 0.058)
            duration = 0.17
        elif mode == "mid":
            dy = int(self._screen_h * 0.085)
            duration = 0.19
        else:
            dy = int(self._screen_h * 0.12)
            duration = 0.2
        if mode != "bold":
            if direction == "up":
                self.device.swipe(cx, cy + dy, cx, cy - dy, duration)
            else:
                self.device.swipe(cx, cy - dy, cx, cy + dy, duration)

    def _open_birthday_picker(self):
        if self._picker_dialog_open():
            self._log("   ✅ Диалог «Установите дату» уже открыт")
            return True

        self._log("   🔍 Открываю выбор даты рождения...")
        triggers = [
            self.device(className="android.widget.EditText"),
            self.device(textContains="дату рождения"),
            self.device(textContains="Дата рождения"),
            self.device(textContains="Birthday"),
            self.device(textContains="Birth date"),
            self.device(textContains="years old"),
            self.device(textContains="лет"),
            self.device(textContains="янв"),
            self.device(textContains="фев"),
            self.device(textContains="мар"),
            self.device(textContains="апр"),
            self.device(textContains="мая"),
            self.device(textContains="июн"),
            self.device(textContains="июл"),
            self.device(textContains="авг"),
            self.device(textContains="сен"),
            self.device(textContains="окт"),
            self.device(textContains="ноя"),
            self.device(textContains="дек"),
        ]
        for trig in triggers:
            try:
                if trig.exists:
                    trig.click()
                    time.sleep(0.45)
                    if self._picker_dialog_open():
                        self._log("   ✅ Диалог выбора даты открыт")
                        return True
            except Exception:
                continue

        cx = self._screen_w // 2
        cy = int(self._screen_h * 0.42)
        self.device.click(cx, cy)
        time.sleep(0.45)
        if self._picker_dialog_open():
            self._log("   ✅ Диалог даты открыт (fallback tap)")
            return True

        self._log("   ❌ Не удалось открыть диалог даты")
        return False

    def _apply_birthday_wheels(self, birth_date, columns=None):
        layout = self._resolve_date_picker_layout(columns)
        use_coords = layout["use_coords"]

        year_picker = None if use_coords else layout["year_picker"]
        month_picker = None if use_coords else layout["month_picker"]
        day_picker = None if use_coords else layout["day_picker"]

        year_col = layout["year_col"]
        month_col = layout["month_col"]
        day_col = layout["day_col"]

        if use_coords:
            self._log("   ℹ️ NumberPicker не найден — свайпы по координатам колонок")

        year_inc, year_step, year_fine = self._calibrate_wheel(
            year_picker, year_col, label="год"
        )

        self._log("   🔄 Выставляю год...")
        if not self._scroll_wheel_numeric(
            year_picker,
            year_col,
            birth_date.year,
            year_inc,
            year_step,
            year_fine,
            label="год",
            max_rounds=120,
        ):
            return False

        month_inc, month_step, month_fine = self._calibrate_wheel(
            month_picker, month_col, label="месяц"
        )

        self._log("   🔄 Выставляю месяц...")
        if not self._scroll_wheel_month(
            month_picker,
            month_col,
            birth_date.month,
            month_inc,
            month_step,
            month_fine,
            label="месяц",
            max_rounds=50,
        ):
            return False

        day_inc, day_step, day_fine = self._calibrate_wheel(
            day_picker, day_col, label="день"
        )

        self._log("   🔄 Выставляю день...")
        if not self._scroll_wheel_numeric(
            day_picker,
            day_col,
            birth_date.day,
            day_inc,
            day_step,
            day_fine,
            label="день",
            max_rounds=40,
        ):
            return False

        return True

    def set_birthday_via_picker(self, birth_date=None):
        birth_date = birth_date or self._generate_birth_date()
        self._log(
            f"   📅 Целевая дата: {birth_date.day:02d}.{birth_date.month:02d}.{birth_date.year}"
        )

        if not self._birthday_screen_ready():
            if not self._screen_has_any_text(
                list(self._UI_BIRTHDAY_MARKERS),
                timeout=ACTION_TIMEOUT,
                label="экран даты рождения",
            ):
                return False

        if not self._picker_dialog_open():
            if not self._open_birthday_picker():
                return False
            if not self._screen_has_any_text(
                ["Установите дату", "Установить дату", "Set date"],
                timeout=8,
                label="диалог «Установите дату»",
            ):
                return False

        columns = self._get_date_picker_columns()
        if columns:
            self._log(f"   ✅ Найдено колонок picker: {len(columns)}")
        else:
            self._log("   ⚠️ NumberPicker не найден, работаю через координаты")

        if not self._apply_birthday_wheels(birth_date, columns):
            return False

        self._human_pause(0.12, 0.22, reason="перед «УСТАНОВИТЬ»")

        if not self.click_by_text(
            ["УСТАНОВИТЬ", "Установить", "OK", "Готово", "SET", "Set"],
            max_attempts=4,
        ):
            self._log("   ❌ Не нажата кнопка УСТАНОВИТЬ")
            return False

        self._log("   ✅ Дата установлена в picker")
        time.sleep(0.25)
        return True

    # ========== ОПРЕДЕЛЕНИЕ ЭКРАНА / ОШИБКИ КОДА (как ix_ios web) ==========

    _CODE_ERROR_KEYWORDS = [
        "wrong", "invalid", "incorrect", "expired",
        "error occurred", "an error", "try again", "please try",
        "account confirmation", "confirmation error",
        "the code you entered", "code is not valid", "code isn't valid",
        "confirmation code you entered is invalid",
        "неверн", "недействит", "истек", "истёк", "не действит",
        "код не", "ошибка", "повторите", "произошла ошибка",
        "не удалось", "не подходит",
    ]

    _INVALID_CODE_NEEDLES = (
        "invalid or has expired",
        "confirmation code you entered is invalid",
        "that code isn't valid",
        "that code is not valid",
        "code isn't valid",
        "code is not valid",
        "incorrect code",
        "wrong code",
        "код недействит",
        "код неверн",
        "код истек",
        "код истёк",
        "неверный код",
    )

    _UI_CODE_MARKERS = (
        "Введите код подтверждения",
        "введите код подтверждения",
        "Код подтверждения",
        "код подтверждения",
        "Введите код",
        "введите код",
        "confirmation code",
        "Confirmation code",
        "Enter confirmation",
        "enter the confirmation",
        "Enter the confirmation code",
        "Enter confirmation code",
        "confirmation code",
        "6-digit code",
        "6 digit code",
        "code we sent",
        "We sent a code",
        "we sent a code",
        "To confirm your account",
        "Verify your email",
        "код из email",
        "код из письма",
        "отправили код",
        "мы отправили",
        "код на",
        "6-значн",
        "6 digit",
    )

    _PASSWORD_SCREEN_LIGHT = (
        "Create a password",
        "Create password",
        "Создайте пароль",
        "Придумайте пароль",
        "create a password",
    )

    _CODE_SCREEN_LIGHT = (
        "confirmation code",
        "Enter confirmation",
        "6-digit code",
        "code we sent",
        "код подтвержд",
    )

    _EMAIL_SCREEN_LIGHT = (
        "What's your email",
        "Enter your email",
        "Email address",
    )

    _RATE_LIMIT_LIGHT = (
        "please wait a few minutes before you try again",
        "wait a few minutes before you try again",
    )

    _UI_PASSWORD_MARKERS = (
        "Создайте пароль",
        "создайте пароль",
        "Create a password",
        "Create password",
        "Придумайте пароль",
        "придумайте пароль",
        "пароль должен",
    )

    _UI_BIRTHDAY_MARKERS = (
        "дату рождения",
        "Дата рождения",
        "Укажите дату",
        "Укажите дату рождения",
        "Birthday",
        "Birth date",
        "Установите дату",
    )

    _UI_FULLNAME_MARKERS = (
        "Полное имя",
        "полное имя",
        "Full name",
        "ваше имя",
        "Your name",
    )

    _UI_NICKNAME_MARKERS = (
        "Nickname",
        "Никнейм",
        "Display name",
        "What should we call you",
        "What's your name",
        "What's your name?",
        "Add your name",
        "Enter your name",
        "Как вас зовут",
    )

    _UI_USERNAME_MARKERS = (
        "Имя пользователя",
        "имя пользователя",
        "Choose a username",
        "Create a username",
        "Pick a username",
        "Add a username",
    )

    _UI_LOGIN_MARKERS = (
        "Log in",
        "Log In",
        "Войти",
        "Forgot password",
        "Forgot password?",
        "Забыли пароль",
    )

    _UI_LOGIN_FIELD_HINTS = (
        "Phone number, username, or email",
        "Username, email or mobile number",
        "Mobile number",
        "phone number, username",
        "username, email",
    )

    _UI_SIGNUP_EMAIL_SCREEN = (
        "What's your email",
        "What’s your email",
        "Enter your email",
        "your email address",
        "Add your email",
        "Email address",
        "Какой у вас адрес",
    )

    _UI_AGREE_MARKERS = (
        "Принимаю",
        "принимаю",
        "I agree",
        "Agree to",
    )

    _UI_EMAIL_MARKERS = (
        "эл. адрес",
        "эл. почт",
        "email",
        "Email",
        "электронн",
        "phone number",
        "номер телефона",
    )

    _UI_EMAIL_SIGNUP_CHOICE = (
        "Sign up with email",
        "Register with email",
        "Зарегистрироваться с эл. адресом",
        "Зарегистрироваться с эл. почтой",
        "Зарегистрироваться с email",
        "Регистрация по эл. адресу",
        "Использовать эл. адрес",
    )

    _UI_CREATE_NEW_ACCOUNT = (
        "Create new account",
        "Create account",
    )

    _UI_GET_STARTED = (
        "Get started",
    )

    _UI_GET_STARTED_ONBOARDING = (
        "Get started on Instagram",
        "Начните работу в Instagram",
    )

    _UI_USE_EMAIL = (
        "Use email",
        "Use Email",
        "Использовать email",
        "Использовать эл. адрес",
        "Использовать эл. почту",
    )

    _UI_STARTUP_AGREE = (
        "Allow all cookies",
        "Accept all",
        "I agree",
        "Agree",
        "Accept",
        "Согласен",
        "Соглашаюсь",
        "Принять все",
        "Принять",
    )

    _UI_STARTUP_CONTINUE = (
        "Continue",
        "Продолжить",
    )

    _UI_HUMAN_VERIFY_MARKERS = (
        "Confirm you're human",
        "Confirm you are human",
        "confirm you're human to use",
        "confirm you are human to use",
        "Confirm that you're human",
        "Подтвердите, что вы человек",
        "Подтвердите что вы человек",
        "подтвердите, что вы человек",
    )

    _UI_HUMAN_VERIFY_TEXT = (
        "confirm you're human",
        "confirm you are human",
        "confirm you're human to use",
        "confirm you are human to use",
        "confirm that you're human",
        "confirm that you are human",
        "verify you're human",
        "подтвердите, что вы человек",
        "подтвердите что вы человек",
        "подтвердите, что вы не робот",
    )

    def _ui_has_text(self, *needles):
        """Проверка текста на экране через селекторы u2 (работает лучше dump TextView)."""
        for needle in needles:
            if not needle:
                continue
            try:
                if self.device(textContains=needle).exists:
                    return True
                if self.device(text=needle).exists:
                    return True
                if self.device(descriptionContains=needle).exists:
                    return True
            except Exception:
                continue
        return False

    def _ui_has_any_light(self, *needles):
        """
        Минимум adb-запросов — один textContains на фразу, стоп на первом совпадении.
        Для S9: частый _ui_has_text с десятками фраз вешает Instagram.
        """
        for needle in needles:
            if not needle:
                continue
            try:
                if self.device(textContains=needle).exists:
                    return True
            except Exception:
                continue
        return False

    def _is_password_input(self, el):
        try:
            return el.info.get("password") is True
        except Exception:
            return False

    def _password_edit_field(self):
        """Поле пароля: password=True в u2, иначе xpath."""
        try:
            el = self.device(className="android.widget.EditText", password=True)
            if el.exists:
                return el
        except (ReferenceError, Exception):
            pass
        try:
            node = self.device.xpath('//*[@password="true"]')
            if node.exists:
                return node
        except Exception:
            pass
        return None

    def _invalidate_screen_cache(self):
        self._screen_cache_text = ""
        self._screen_cache_ts = 0.0

    def _take_poll_snapshot(self, force=False):
        """
        Один dump_hierarchy на итерацию poll-цикла.
        S9 «вылетает» если dump_hierarchy дёргать каждые 0.08 сек (см. _wait_after_email_next).
        """
        now = time.time()
        if (
            not force
            and self._screen_cache_text
            and (now - self._screen_cache_ts) < POLL_DUMP_MIN_INTERVAL
        ):
            return self._screen_cache_text

        self._trace_step("poll_snapshot", f"dump_hierarchy (интервал {POLL_DUMP_MIN_INTERVAL}с)")
        self._invalidate_screen_cache()
        return self._get_screen_text_lower(force=True, max_age=POLL_DUMP_MIN_INTERVAL)

    def _get_screen_text_lower(self, force=False, max_age=0.35):
        now = time.time()
        if (
            not force
            and self._screen_cache_text
            and (now - self._screen_cache_ts) < max_age
        ):
            return self._screen_cache_text

        if (
            not force
            and self._screen_cache_text
            and (now - self._screen_cache_ts) < POLL_DUMP_MIN_INTERVAL
        ):
            return self._screen_cache_text

        chunks = []
        try:
            xml = self.device.dump_hierarchy(compressed=True)
            for val in re.findall(r'(?:text|content-desc)="([^"]*)"', xml):
                val = val.strip()
                if val:
                    chunks.append(val)
        except Exception:
            pass

        if not chunks:
            for sel in (
                self.device(className="android.widget.TextView"),
                self.device(className="android.widget.Button"),
                self.device(className="android.widget.EditText"),
            ):
                try:
                    if not sel.exists:
                        continue
                    for node in sel:
                        try:
                            t = node.get_text()
                            if t:
                                chunks.append(t)
                        except Exception:
                            continue
                except Exception:
                    continue

        self._screen_cache_text = " ".join(chunks).lower()
        self._screen_cache_ts = now
        return self._screen_cache_text

    def _text_has_any(self, needles):
        if isinstance(needles, str):
            needles = [needles]
        if self._ui_has_text(*needles):
            return True
        text = self._get_screen_text_lower()
        return any(n.lower() in text for n in needles)

    def _has_password_field(self):
        """Реальное поле пароля. OTP-код часто тоже password=true — не путать."""
        if self._looks_like_code_screen():
            return False
        if not self._looks_like_password_screen():
            return False
        pwd_el = self._password_edit_field()
        return pwd_el is not None and pwd_el.exists

    def _has_plain_edit_field(self):
        try:
            if self.device(className="android.widget.EditText").exists:
                return True
        except Exception:
            pass
        return self._xpath_first_editable(exclude_password=True) is not None

    def _has_code_screen_light(self):
        return self._ui_has_any_light(*self._CODE_SCREEN_LIGHT)

    def _has_email_screen_light(self):
        return self._ui_has_any_light(*self._EMAIL_SCREEN_LIGHT, "Email")

    def _has_rate_limit_light(self):
        return self._ui_has_any_light(*self._RATE_LIMIT_LIGHT)

    def _has_password_screen_light(self):
        if self._has_code_screen_light():
            return False
        return self._ui_has_any_light(*self._PASSWORD_SCREEN_LIGHT)

    def _detect_registration_screen_light(self):
        """Текущий экран — только u2, без dump_hierarchy (S9)."""
        if self._ui_has_any_light(*self._UI_AGREE_MARKERS):
            return "agree"
        if self._is_combined_name_username_screen():
            return "name_username"
        if self._ui_has_any_light(*self._UI_USERNAME_MARKERS):
            return "username"
        if self._ui_has_any_light(
            *self._UI_FULLNAME_MARKERS
        ) or self._ui_has_any_light(*self._UI_NICKNAME_MARKERS):
            return "full_name"
        if self._ui_has_any_light(*self._UI_BIRTHDAY_MARKERS):
            return "birthday"
        if self._has_code_screen_light():
            return "code"
        if self._has_password_screen_light():
            return "password"
        if self._has_email_screen_light():
            return "email"
        return "unknown"

    def _check_code_page_error_light(self):
        """Ошибка на экране кода — только u2-селекторы."""
        for kw in self._CODE_ERROR_KEYWORDS:
            if self._ui_has_any_light(kw):
                return kw
        return ""

    def _has_code_screen_markers_u2_only(self):
        """Экран OTP — только u2-селекторы, без dump_hierarchy (S9)."""
        priority = (
            "confirmation code",
            "Confirmation code",
            "Enter confirmation",
            "enter the confirmation",
            "Enter the confirmation code",
            "6-digit code",
            "code we sent",
            "We sent a code",
            "код подтвержд",
            "введите код",
            "отправили код",
        )
        if self._ui_has_text(*priority):
            return True
        return self._ui_has_text(*self._UI_CODE_MARKERS)

    def _check_rate_limit_error_u2_only(self):
        """Только полная фраза Instagram — короткие совпадения дают ложный rate-limit."""
        return self._ui_has_text(
            "please wait a few minutes before you try again",
            "wait a few minutes before you try again",
            "подождите несколько минут, прежде чем повторить",
            "подождите несколько минут",
        )

    def _check_post_email_errors_u2_only(self):
        if self._check_rate_limit_error_u2_only():
            return "rate_limit"
        for kw in (
            "something went wrong",
            "registration error",
            "ошибка регистрации",
            "please try again later",
            "please wait a few minutes before you try again",
        ):
            if self._ui_has_text(kw):
                err_l = kw.lower()
                if "wait a few minutes" in err_l or "try again later" in err_l:
                    return "rate_limit"
                return "error"
        return None

    def _is_still_signup_email_step_u2_only(self):
        if self._has_code_screen_markers_u2_only():
            return False
        if self._check_rate_limit_error_u2_only():
            return True
        if self._ui_has_text(*self._UI_SIGNUP_EMAIL_SCREEN):
            return True
        if self._ui_has_text(
            "Sign up with mobile number",
            "I already have an account",
            "No one will see this on your profile",
        ):
            return True
        if self._ui_has_text("Email") and self._has_plain_edit_field():
            return True
        return False

    def _has_code_screen_markers(self, screen_text=None):
        """Проверка экрана OTP — без лишних dump_hierarchy в poll-циклах."""
        if self._ui_has_text(*self._UI_CODE_MARKERS):
            return True
        quick = (
            "confirmation code",
            "6-digit code",
            "code we sent",
            "код подтвержд",
            "введите код",
        )
        if self._ui_has_text(*quick):
            return True

        phrases = (
            "confirmation code",
            "enter the confirmation",
            "enter confirmation code",
            "6-digit code",
            "6 digit code",
            "code we sent",
            "we sent a code",
            "to confirm your account",
            "verify your email",
            "enter the code",
            "код подтвержд",
            "введите код",
            "отправили код",
        )
        if screen_text is not None:
            return any(p in screen_text for p in phrases)

        text = self._get_screen_text_lower(max_age=POLL_DUMP_MIN_INTERVAL)
        return any(p in text for p in phrases)

    def _is_still_signup_email_step(self, screen_text=None):
        """Всё ещё шаг email (не OTP). Код-экран имеет приоритет."""
        if self._has_code_screen_markers(screen_text=screen_text):
            return False
        if self._check_rate_limit_error(screen_text=screen_text):
            return True
        if self._ui_has_text(*self._UI_SIGNUP_EMAIL_SCREEN):
            return True
        if self._ui_has_text(
            "Sign up with mobile number",
            "I already have an account",
            "No one will see this on your profile",
        ):
            return True
        if self._ui_has_text("Email") and self._has_plain_edit_field():
            return True
        return False

    def _is_confirmed_code_screen(self):
        """Экран OTP: есть маркеры кода и нет rate-limit."""
        if self._check_rate_limit_error():
            return False
        return self._has_code_screen_markers()

    def _looks_like_code_screen(self, screen_text=None):
        if self._check_rate_limit_error(screen_text=screen_text):
            return False
        return self._has_code_screen_markers(screen_text=screen_text)

    def _is_login_screen(self, screen_text=None):
        """Экран входа (НЕ регистрация) — как auth form в test.py."""
        has_login = self._ui_has_text(*self._UI_LOGIN_MARKERS[:3])
        has_forgot = self._ui_has_text("Forgot password", "Forgot password?", "Забыли пароль")
        has_create = self._ui_has_text(*self._UI_CREATE_NEW_ACCOUNT)

        if has_login and (has_create or has_forgot):
            return True

        text = screen_text
        if text is None:
            text = self._get_screen_text_lower(max_age=POLL_DUMP_MIN_INTERVAL)
        if has_login and any(h.lower() in text for h in self._UI_LOGIN_FIELD_HINTS):
            return True

        if has_create and has_login:
            return True

        return False

    def _is_signup_email_screen(self, screen_text=None):
        """Реальный экран ввода email при регистрации (не поле входа)."""
        if self._is_login_screen(screen_text=screen_text):
            return False
        if self._ui_has_text(*self._UI_SIGNUP_EMAIL_SCREEN):
            return True
        if self._ui_has_text(*self._UI_EMAIL_SIGNUP_CHOICE):
            return False
        if (
            self._has_plain_edit_field()
            and self._ui_has_text("Email")
            and not self._ui_has_text("Log in", "Log In", "Войти")
            and getattr(self, "_entry_path", None) in ("create_account", "get_started")
        ):
            return True
        return False

    def _is_onboarding_one_page_screen(self, screen_text=None):
        """
        Новый дизайн формы: после Create new account / Get started —
        Mobile/Use email + пароль + дата на одном экране.
        """
        if self._is_login_screen(screen_text=screen_text):
            return False
        if self._looks_like_code_screen(screen_text=screen_text):
            return False
        if self._ui_has_text(*self._UI_CREATE_NEW_ACCOUNT) and self._ui_has_text(
            "Log in", "Log In", "Войти"
        ):
            return False
        if not self._ui_has_text(*self._UI_USE_EMAIL):
            return False

        has_pwd = (
            self._ui_has_text("Password", "Пароль")
            or self._has_password_field()
        )
        has_bday = self._ui_has_text(*self._UI_BIRTHDAY_MARKERS)
        has_mobile = self._ui_has_text(
            "Mobile number", "Номер мобильного", "Phone number", "Mobile Number"
        )

        if has_pwd and has_bday:
            return True
        if has_mobile and (has_pwd or has_bday):
            return True
        if self._ui_has_text(*self._UI_GET_STARTED_ONBOARDING) and has_pwd:
            return True
        return False

    def _is_classic_signup_email_form(self, screen_text=None):
        """Старый дизайн: отдельный экран email (без пароля/даты на той же странице)."""
        if self._is_onboarding_one_page_screen(screen_text=screen_text):
            return False
        if self._ui_has_text(*self._UI_EMAIL_SIGNUP_CHOICE):
            return True
        if self._ui_has_text(*self._UI_SIGNUP_EMAIL_SCREEN):
            return True
        if self._is_signup_email_screen(screen_text=screen_text):
            return True
        if (
            self._entry_path in ("create_account", "get_started")
            and self._ui_has_text("Email")
            and self._has_plain_edit_field()
            and not self._has_password_field()
        ):
            return True
        return False

    def _detect_signup_form_design(self, timeout=ACTION_TIMEOUT):
        """После входа в регистрацию — classic (по шагам) или one-page."""
        self._log("\n🔍 Определяю дизайн формы регистрации (classic / one-page)...")
        self._signup_form_design = None
        start = time.monotonic()
        last_log = 0

        while time.monotonic() - start < timeout:
            elapsed = int(time.monotonic() - start)

            if self._is_onboarding_one_page_screen():
                self._signup_form_design = "one_page"
                self._entry_path = "onboarding_one_page"
                self._log(f"   📐 Дизайн: ONE-PAGE (Use email + пароль + дата) [{elapsed}с]")
                return "one_page"

            if self._is_classic_signup_email_form():
                self._signup_form_design = "classic"
                self._log(f"   📐 Дизайн: CLASSIC (email по шагам) [{elapsed}с]")
                return "classic"

            if self._ui_has_text(*self._UI_EMAIL_SIGNUP_CHOICE):
                self._log("   📍 Classic: Sign up with email...")
                if self._click_sign_up_with_email():
                    time.sleep(0.8)
                    continue

            if elapsed - last_log >= 4:
                self._log(f"   ⏳ Жду форму регистрации... ({elapsed}/{timeout}с)")
                last_log = elapsed
            time.sleep(0.4)

        if self._is_onboarding_one_page_screen():
            self._signup_form_design = "one_page"
            self._entry_path = "onboarding_one_page"
            self._log("   📐 Дизайн: ONE-PAGE (по таймауту)")
            return "one_page"

        self._signup_form_design = "classic"
        self._log("   📐 Дизайн: CLASSIC (fallback)")
        return "classic"

    def _first_non_password_edit(self):
        try:
            edits = self.device(className="android.widget.EditText")
            if edits.exists:
                for el in edits:
                    try:
                        if not self._is_password_input(el):
                            return el
                    except Exception:
                        continue
        except Exception:
            pass
        return self._xpath_first_editable(exclude_password=True)

    def _click_use_email_on_onboarding(self):
        self._log("   📍 Нажимаю «Use email»...")
        if self.click_by_text(list(self._UI_USE_EMAIL), max_attempts=4):
            time.sleep(0.6)
            return True
        return self._fast_click(list(self._UI_USE_EMAIL), label="Use email")

    def _complete_onboarding_one_page_signup(self):
        """Use email → email → password → birthday → Next (новый one-page UI)."""
        self._log("   📋 One-page: Use email → email → пароль → дата → Next")

        if self._ui_has_text(*self._UI_USE_EMAIL):
            if not self._click_use_email_on_onboarding():
                self._log("   ❌ Не удалось нажать Use email")
                return False
            time.sleep(0.5)

        email_field = self._first_non_password_edit()
        if email_field:
            if not self.input_into_field(email_field, self.email, label="email"):
                return False
        elif not self.input_text(
            ["Email", "email", "Mobile number", "эл. адрес"],
            self.email,
            label="email",
        ):
            return False

        self.uncheck_remember_login()
        if not self.password:
            self.password = self.generate_password()
        self._log(f"   🔑 Пароль: {self.password}")

        pwd_field = self._password_edit_field()
        if not pwd_field or not pwd_field.exists:
            self._log("   ❌ Поле пароля не найдено на one-page экране")
            return False
        if not self.input_into_field(pwd_field, self.password, label="пароль"):
            return False

        if not self.set_birthday_via_picker():
            return False

        if not self.click_next_once(reason="one-page onboarding"):
            return False

        self._onboarding_one_page_done = True
        self._log("   ✅ One-page onboarding заполнен, жду код с почты")
        return True

    def _looks_like_username_registration_screen(self, screen_text=None):
        if self._is_login_screen(screen_text=screen_text):
            return False
        if self._is_combined_name_username_screen():
            return False
        if self._ui_has_text(*self._UI_USERNAME_MARKERS):
            return True
        text = screen_text
        if text is None:
            text = self._get_screen_text_lower(max_age=POLL_DUMP_MIN_INTERVAL)
        return "choose a username" in text or "create a username" in text

    def _looks_like_password_screen(self, screen_text=None):
        """Только явный экран создания пароля, не OTP с password=true."""
        if self._looks_like_code_screen(screen_text=screen_text):
            return False
        if self._ui_has_text(*self._UI_PASSWORD_MARKERS):
            return True
        text = screen_text
        if text is None:
            text = self._get_screen_text_lower(max_age=POLL_DUMP_MIN_INTERVAL)
        return any(
            k in text
            for k in (
                "создайте пароль",
                "create a password",
                "create password",
                "придумайте пароль",
            )
        )

    def _detect_registration_screen(self, screen_text=None):
        """
        Определяет текущий экран регистрации.
        Код подтверждения — приоритетнее пароля (OTP поле часто password=true).
        """
        if self._ui_has_text(*self._UI_AGREE_MARKERS):
            return "agree"

        if self._is_combined_name_username_screen():
            return "name_username"

        if self._looks_like_username_registration_screen(screen_text=screen_text):
            return "username"

        if self._ui_has_text(*self._UI_FULLNAME_MARKERS) or self._ui_has_text(
            *self._UI_NICKNAME_MARKERS
        ):
            return "full_name"

        if self._ui_has_text(*self._UI_BIRTHDAY_MARKERS):
            return "birthday"

        if self._looks_like_code_screen(screen_text=screen_text):
            return "code"

        if self._looks_like_password_screen(screen_text=screen_text):
            return "password"

        if self._is_signup_email_screen(screen_text=screen_text):
            return "email"

        if (
            not self._is_login_screen(screen_text=screen_text)
            and self._ui_has_text(*self._UI_EMAIL_MARKERS)
            and self._has_plain_edit_field()
            and getattr(self, "_entry_path", None)
        ):
            return "email"

        text = screen_text
        if text is None:
            text = self._get_screen_text_lower(max_age=POLL_DUMP_MIN_INTERVAL)
        if self._has_plain_edit_field() and self._blob_has_any(text, "код", "code", "подтвержд"):
            return "code"

        return "unknown"

    def _find_code_input_field(self, code_context=None):
        """Ищет поле ввода кода. На экране OTP принимаем и password=true."""
        if code_context is None:
            code_context = self._looks_like_code_screen()

        for spec in (
            {"className": "android.widget.EditText"},
            {"className": "android.widget.AutoCompleteTextView"},
            {"focused": True, "className": "android.widget.EditText"},
            {"className": "android.widget.EditText", "textContains": "код"},
            {"className": "android.widget.EditText", "textContains": "code"},
            {"className": "android.widget.EditText", "descriptionContains": "код"},
            {"className": "android.widget.EditText", "descriptionContains": "code"},
        ):
            sel = self._try_selector(**spec)
            if sel and sel.exists:
                if code_context or not self._is_password_input(sel):
                    return sel

        if code_context:
            for query in (
                '//*[@class="android.widget.EditText"]',
                '//*[@editable="true"]',
            ):
                try:
                    node = self.device.xpath(query)
                    if node.exists:
                        return node
                except Exception:
                    continue
        else:
            xpath_el = self._xpath_first_editable(exclude_password=True)
            if xpath_el is not None:
                return xpath_el

        sel = self._try_selector(focused=True)
        if sel and sel.exists:
            if code_context or not self._is_password_input(sel):
                return sel
        return None

    def _log_current_screen(self, context=""):
        snap = self._take_poll_snapshot()
        screen = self._detect_registration_screen(screen_text=snap)
        prefix = f"   📍 Экран: {screen}"
        if context:
            prefix += f" ({context})"
        self._log(prefix)
        return screen

    def _log_current_screen_light(self, context=""):
        """Быстрый лог текущего экрана без dump_hierarchy."""
        screen = self._detect_registration_screen_light()
        prefix = f"   📍 Экран: {screen}"
        if context:
            prefix += f" ({context})"
        self._log(prefix)
        return screen

    def _check_code_page_error_text(self):
        """Одна проверка ошибки на экране кода — u2-only (без dump)."""
        return self._check_code_page_error_light()

    def _wait_code_page_error_text(
        self,
        total_timeout=CODE_ERROR_WAIT,
        stable_seconds=CODE_ERROR_STABLE,
        poll=WAIT_POLL,
    ):
        """Ждёт устойчивую ошибку на странице кода."""
        end = time.time() + float(total_timeout)
        first_seen_ts = None
        last_error = ""
        while time.time() < end:
            err = self._check_code_page_error_text()
            if err:
                if err == last_error:
                    if first_seen_ts is not None and (time.time() - first_seen_ts) >= float(
                        stable_seconds
                    ):
                        return err
                else:
                    last_error = err
                    first_seen_ts = time.time()
            else:
                last_error = ""
                first_seen_ts = None
            time.sleep(max(0.1, float(poll)))
        return ""

    def _process_code_error(self, code_err):
        """Обработка ошибки кода: invalid → ретрай email; прочее → стоп."""
        code_err_l = (code_err or "").lower()
        is_invalid = any(n in code_err_l for n in self._INVALID_CODE_NEEDLES) or any(
            n in code_err_l for n in ("неверн", "недействит", "истек", "истёк", "incorrect", "invalid")
        )
        if is_invalid:
            bad_code = str(self.last_submitted_confirmation_code or "").strip()
            if bad_code:
                self.blocked_confirmation_codes.add(bad_code)
            self.invalid_confirmation_code_retry = True
            self._log(
                f"   ❌ Код invalid/expired: {code_err[:220]}. "
                f"Ретрай на том же email, код в blocklist: {bad_code}"
            )
            return False
        self.invalid_confirmation_code_retry = False
        self._log(f"   ❌ Ошибка подтверждения после кода: {code_err[:260]}. Пропускаем аккаунт.")
        return False

    def _wait_transition_after_code(self, timeout=REG_TRANSITION_TIMEOUT):
        """
        После ввода кода ждём пароль. S9: light-poll без dump_hierarchy.
        Fallback: если 3+ сек всё ещё code — один раз «Далее».
        """
        self._log(
            f"   ⏳ После кода жду пароль (light-poll {POLL_EMAIL_TRANSITION}с, "
            f"до {timeout} сек)..."
        )
        self._trace_step(
            "wait_after_code",
            f"пауза {CODE_TO_PASSWORD_SETTLE}с — анимация, device не трогаем",
        )
        time.sleep(CODE_TO_PASSWORD_SETTLE)

        start = time.monotonic()
        last_log = -10
        next_fallback_done = False

        while time.monotonic() - start < timeout:
            sec = int(time.monotonic() - start)

            if not self._is_instagram_running():
                self._log(f"   ❌ [{sec}/{timeout}] Instagram закрыт/упал")
                return False

            err = self._check_code_page_error_light()
            if err:
                return self._process_code_error(err)

            screen = self._detect_registration_screen_light()

            if screen == "password" or self._has_password_screen_light():
                self._log(f"   ✅ Экран пароля ({sec} сек)")
                return True

            if screen in ("birthday", "full_name", "username", "name_username", "agree"):
                self._log(
                    f"   ✅ Уже перешли дальше пароля → «{screen}» ({sec} сек)"
                )
                return True

            if (
                (screen == "code" or self._has_code_screen_light())
                and sec >= 3
                and not next_fallback_done
            ):
                self._log(
                    f"   ⚠️ [{sec}/{timeout}] Всё ещё код — один fallback «Далее»"
                )
                self.click_by_text(["Далее", "Next"], max_attempts=1, quiet=True)
                next_fallback_done = True
                time.sleep(CODE_TO_PASSWORD_SETTLE)
                continue

            if sec - last_log >= 5:
                self._log(f"   ⏳ [{sec}/{timeout}] Жду пароль, сейчас «{screen}»...")
                last_log = sec

            time.sleep(POLL_EMAIL_TRANSITION)

        if not self._is_instagram_running():
            self._log("   ❌ Instagram закрыт/упал (таймаут)")
            return False

        screen = self._detect_registration_screen_light()
        if screen in (
            "password",
            "birthday",
            "full_name",
            "username",
            "name_username",
            "agree",
        ) or self._has_password_screen_light():
            self._log(f"   ✅ Переход подтверждён: «{screen}»")
            return True

        code_err = self._wait_code_page_error_text()
        if code_err:
            return self._process_code_error(code_err)

        self._log(
            f"   ⚠️ Таймаут — один dump для диагностики (экран «{screen}»)..."
        )
        snap = self._take_poll_snapshot(force=True)
        screen = self._detect_registration_screen(screen_text=snap)
        if screen in (
            "password",
            "birthday",
            "full_name",
            "username",
            "name_username",
            "agree",
        ):
            self._log(f"   ✅ Переход подтверждён после dump: «{screen}»")
            return True

        self._log(f"   ❌ Не дождались пароля. Текущий экран: «{screen}»")
        return False

    def _wait_registration_transition(
        self,
        label,
        success_screens,
        stuck_screens=None,
        timeout=REG_TRANSITION_TIMEOUT,
        settle=REG_TRANSITION_SETTLE,
        extra_success_check=None,
    ):
        """
        Ждёт смену экрана после Next (light-poll, без dump).
        success_screens — куда должны попасть; stuck_screens — если застряли, ждём дальше.
        """
        stuck_screens = tuple(stuck_screens or ())
        if isinstance(success_screens, str):
            success_screens = (success_screens,)
        success_screens = tuple(success_screens)

        self._log(
            f"   ⏳ {label} (light-poll {POLL_EMAIL_TRANSITION}с, до {timeout} сек)..."
        )
        self._trace_step(
            "wait_transition",
            f"{label}: polling сразу, fallback-пауза {settle}с",
        )

        start = time.monotonic()
        last_log = -10
        next_fallback_done = False

        while time.monotonic() - start < timeout:
            sec = int(time.monotonic() - start)

            if not self._is_instagram_running():
                self._log(f"   ❌ [{sec}/{timeout}] Instagram закрыт/упал ({label})")
                return None

            screen = self._detect_registration_screen_light()

            if screen in success_screens:
                self._log(f"   ✅ {label}: «{screen}» ({sec} сек)")
                return screen

            if extra_success_check and extra_success_check():
                self._log(f"   ✅ {label}: целевой экран ({sec} сек)")
                return screen if screen != "unknown" else success_screens[0]

            if screen not in stuck_screens and stuck_screens:
                if sec >= 3:
                    self._log(f"   ✅ {label}: ушли с «{stuck_screens}» → «{screen}» ({sec} сек)")
                    return screen

            if (
                screen in stuck_screens
                and sec >= 5
                and not next_fallback_done
            ):
                self._log(f"   ⚠️ [{sec}/{timeout}] {label}: повторный Next")
                self.click_by_text(["Next", "Далее"], max_attempts=1, quiet=True)
                next_fallback_done = True
                time.sleep(settle)
                continue

            if sec - last_log >= 5:
                self._log(f"   ⏳ [{sec}/{timeout}] {label}: сейчас «{screen}»...")
                last_log = sec

            time.sleep(POLL_EMAIL_TRANSITION)

        screen = self._detect_registration_screen_light()
        if screen in success_screens:
            self._log(f"   ✅ {label}: «{screen}» (таймаут-край)")
            return screen
        if extra_success_check and extra_success_check():
            return screen if screen != "unknown" else success_screens[0]
        if stuck_screens and screen not in stuck_screens:
            return screen
        return None

    def _wait_after_password_next(self, timeout=REG_TRANSITION_TIMEOUT):
        """После Next на пароле — ждём дату рождения или следующий шаг."""
        return self._wait_registration_transition(
            label="Жду уход с экрана пароля",
            success_screens=("birthday", "full_name", "username", "name_username", "agree"),
            stuck_screens=("password",),
            timeout=timeout,
            extra_success_check=lambda: self._ui_has_any_light(*self._UI_BIRTHDAY_MARKERS),
        )

    def _wait_after_birthday_next(self, timeout=REG_TRANSITION_TIMEOUT):
        """После Next на дате — ждём имя/username или «Принимаю»."""
        return self._wait_registration_transition(
            label="Жду экран после даты рождения",
            success_screens=("full_name", "username", "name_username", "agree"),
            stuck_screens=("birthday",),
            timeout=timeout,
            extra_success_check=lambda: (
                self._ui_has_any_light(*self._UI_FULLNAME_MARKERS)
                or self._ui_has_any_light(*self._UI_NICKNAME_MARKERS)
                or self._ui_has_any_light(*self._UI_USERNAME_MARKERS)
                or self._ui_has_any_light(*self._UI_AGREE_MARKERS)
            ),
        )

    def _find_password_input_field(self):
        pwd_el = self._password_edit_field()
        if pwd_el is not None:
            return pwd_el
        for hint in ("Пароль", "Password", "пароль", "password"):
            try:
                el = self.device(className="android.widget.EditText", textContains=hint)
                if el.exists:
                    return el
                el = self.device(className="android.widget.EditText", descriptionContains=hint)
                if el.exists:
                    return el
            except Exception:
                continue
        return None

    def _wait_for_password_field(self, timeout=ACTION_TIMEOUT):
        """Ждёт поле пароля — u2-only, без dump (S9)."""
        self._log(
            f"   ⏳ Жду поле пароля (до {timeout} сек, light-poll {POLL_EMAIL_TRANSITION}с)..."
        )
        start = time.monotonic()
        last_log = -10
        while time.monotonic() - start < timeout:
            sec = int(time.monotonic() - start)

            if not self._is_instagram_running():
                self._log(f"   ❌ [{sec}/{timeout}] Instagram закрыт/упал")
                return None

            if self._has_code_screen_light():
                err = self._check_code_page_error_light()
                if err:
                    self._process_code_error(err)
                    return None

            screen = self._detect_registration_screen_light()
            if screen in ("birthday", "full_name", "username", "name_username", "agree"):
                self._log(f"   ⚠️ Пароль уже пройден, экран: «{screen}»")
                return None

            pwd_el = self._find_password_input_field()
            if pwd_el:
                self._log(f"   ✅ Поле пароля найдено ({sec} сек)")
                return pwd_el

            if self._has_password_screen_light() and sec >= 1:
                any_edit = self.device(className="android.widget.EditText")
                if any_edit.exists:
                    self._log(
                        f"   ✅ Поле пароля (EditText на экране password, {sec} сек)"
                    )
                    return any_edit

            if sec - last_log >= 5:
                self._log(
                    f"   ⏳ [{sec}/{timeout}] Пароль: экран «{screen}»..."
                )
                last_log = sec
            time.sleep(POLL_EMAIL_TRANSITION)

        self._log(f"   ❌ Поле пароля не найдено за {timeout} сек")
        screen = self._detect_registration_screen_light()
        self._log(f"   📍 Экран: {screen} (таймаут пароля)")
        return None

    def _check_suspended_or_verification(self):
        result = {"suspended": False, "verification": False}
        for kw in (
            "suspended", "заблокирован", "account disabled", "аккаунт заблокирован",
            "disabled account", "your account has been disabled",
        ):
            if self._ui_has_text(kw):
                result["suspended"] = True
                return result

        if self._is_human_verification_screen():
            result["verification"] = True
            return result

        for kw in (
            "security check", "проверка безопасности", "confirm it's you",
        ):
            if self._ui_has_text(kw):
                result["verification"] = True
                return result

        text = self._get_screen_text_lower()
        suspended_kw = [
            "suspended", "заблокирован", "blocked", "account disabled",
            "аккаунт заблокирован", "disabled account", "your account has been disabled",
        ]
        verification_kw = [
            "verify", "verification", "верифика", "challenge",
            "security check", "проверка безопасности",
            "confirm it's you", "подозритель",
        ]

        if any(k in text for k in suspended_kw):
            result["suspended"] = True
        elif self._blob_has_any(text, *self._UI_HUMAN_VERIFY_TEXT):
            result["verification"] = True
        elif any(k in text for k in verification_kw):
            if not (
                "код подтвержд" in text
                and "ошиб" not in text
                and "неверн" not in text
            ):
                result["verification"] = True

        return result

    def _check_rate_limit_error(self, screen_text=None):
        """Instagram rate limit на email: «Please wait a few minutes...»."""
        markers = [
            "please wait a few minutes before you try again",
            "wait a few minutes before you try again",
            "please wait a few minutes",
            "подождите несколько минут",
        ]
        for m in markers:
            if self._ui_has_text(m):
                return True
        text = screen_text
        if text is None:
            text = self._get_screen_text_lower(max_age=POLL_DUMP_MIN_INTERVAL)
        return any(m in text for m in markers)

    def _handle_rate_limit_retry(self, attempt, max_attempts):
        """Rate-limit — ретрай с той же почтой до исчерпания MAX_RETRIES."""
        self.rate_limit_hits_on_email += 1
        self._entry_path = None
        left = max(0, max_attempts - attempt)
        self._log(
            f"   🔄 Rate-limit на почте {self.email} "
            f"(срабатывание {self.rate_limit_hits_on_email}) — "
            f"та же почта, переустановка Instagram "
            f"(осталось попыток {left})"
        )

    def _check_post_email_errors(self, screen_text=None):
        """Ошибки сразу после Next на email. Возвращает: rate_limit | error | None."""
        if self._check_rate_limit_error(screen_text=screen_text):
            return "rate_limit"
        reg_err = self._check_registration_error(screen_text=screen_text)
        if reg_err:
            err_l = reg_err.lower()
            if "wait a few minutes" in err_l or "try again later" in err_l:
                return "rate_limit"
            return "error"
        return None

    def _wait_after_email_next(self, timeout=ACTION_TIMEOUT):
        """
        После Next → код. S9: минимум adb-запросов, без dump, редкий poll.
        """
        self._log(
            f"   ⏳ Жду экран кода (light-poll {POLL_EMAIL_TRANSITION}с, "
            f"до {timeout} сек)..."
        )
        self._trace_step(
            "wait_after_email",
            f"пауза {EMAIL_TO_CODE_SETTLE}с — анимация, device не трогаем",
        )
        time.sleep(EMAIL_TO_CODE_SETTLE)

        start = time.monotonic()
        last_log = -10
        rate_limit_streak = 0
        next_retry_done = False

        while time.monotonic() - start < timeout:
            sec = int(time.monotonic() - start)

            if not self._is_instagram_running():
                self._log(f"   ❌ [{sec}/{timeout}] Instagram закрыт/упал")
                return "fail"

            if self._has_code_screen_light():
                self._log(f"   ✅ [{sec}/{timeout}] Экран кода найден")
                return "ok"

            if (
                sec >= 8
                and not next_retry_done
                and self._has_email_screen_light()
            ):
                self._log(f"   ⚠️ [{sec}/{timeout}] Всё ещё email — повторный Next")
                self.click_by_text(["Next", "Далее"], max_attempts=1, quiet=True)
                next_retry_done = True
                time.sleep(EMAIL_TO_CODE_SETTLE)
                continue

            if sec >= 3:
                if self._has_rate_limit_light():
                    rate_limit_streak += 1
                    if rate_limit_streak >= 2:
                        self._log(
                            f"   ⚠️ [{sec}/{timeout}] "
                            "Please wait a few minutes before you try again"
                        )
                        return "rate_limit"
                else:
                    rate_limit_streak = 0

            if sec - last_log >= 5:
                if self._has_email_screen_light():
                    self._log(f"   ⏳ [{sec}/{timeout}] Пока экран email...")
                else:
                    self._log(f"   ⏳ [{sec}/{timeout}] Жду экран кода...")
                last_log = sec

            time.sleep(POLL_EMAIL_TRANSITION)

        if not self._is_instagram_running():
            self._log(f"   ❌ Instagram закрыт/упал (таймаут)")
            return "fail"
        if self._has_code_screen_light():
            return "ok"
        if self._has_rate_limit_light():
            return "rate_limit"

        self._log(f"   ⚠️ Таймаут — один dump для диагностики...")
        screen_text = self._take_poll_snapshot(force=True)
        if self._has_code_screen_markers(screen_text=screen_text):
            return "ok"
        err = self._check_post_email_errors(screen_text=screen_text)
        if err == "rate_limit":
            return "rate_limit"
        self._log(f"   ❌ Таймаут {timeout} сек — экран кода не появился")
        return "fail"

    def _check_registration_error(self, screen_text=None):
        """Ошибки регистрации на экране (аналог ix_ios _check_registration_error)."""
        for kw in (
            "something went wrong",
            "registration error",
            "ошибка регистрации",
            "please try again later",
            "please wait a few minutes before you try again",
        ):
            if self._ui_has_text(kw):
                return kw

        text = screen_text
        if text is None:
            text = self._get_screen_text_lower(max_age=POLL_DUMP_MIN_INTERVAL)
        if not text:
            return ""

        keywords = [
            "registration error",
            "error occurred during your registration",
            "ошибка регистрации",
            "произошла ошибка при регистрации",
            "registrierungsfehler",
            "fehler bei der registrierung",
            "error de registro",
            "erreur d'inscription",
        ]
        retry_markers = [
            "try again",
            "please try again",
            "try again later",
            "попробуйте снова",
            "повторите попытку",
            "spróbuj ponownie",
            "sprobuj ponownie",
        ]
        error_markers = [
            "error",
            "went wrong",
            "problem",
            "ошибка",
            "fehler",
            "erreur",
            "erro",
            "błąd",
            "blad",
        ]
        hard_errors = [
            "не удалось создать аккаунт",
            "что-то пошло не так",
            "something went wrong",
            "please try again later",
            "попробуйте позже",
            "please wait a few minutes before you try again",
            "wait a few minutes before you try again",
        ]

        for kw in keywords:
            if kw in text:
                return kw
        for err in hard_errors:
            if err in text:
                return err

        has_retry = any(m in text for m in retry_markers)
        has_error = any(m in text for m in error_markers)
        if has_retry and has_error:
            return "registration error (retry prompt)"

        return ""

    def _check_main_app_ready(self):
        """Главная лента Instagram — регистрация и onboarding завершены."""
        if self._ui_has_text(*self._UI_AGREE_MARKERS):
            return False
        nav_home = self._ui_has_text("Home", "Главная")
        nav_other = self._ui_has_text(
            "Search", "Поиск", "Reels", "Profile", "Профиль", "Create", "Создать"
        )
        if nav_home and nav_other:
            return True
        if nav_home and self._ui_has_text("Direct", "Сообщения"):
            return True
        return False

    # Next → Don't allow → Continue, далее динамически Skip / No,skip / Got it
    _POST_REG_DENY_LABELS = (
        "Don't allow",
        "Don't Allow",
        "Don't allow",
        "Dont allow",
        "Don’t allow",
        "Запретить",
        "Deny",
    )

    def _has_no_skip_button(self):
        for text in ("No, skip", "No,skip", "No skip", "Нет, пропустить"):
            try:
                if self.device(text=text).exists:
                    return True
                if self.device(textContains=text).exists:
                    return True
            except Exception:
                continue
        try:
            for el in self.device(textContains="skip"):
                try:
                    if not el.exists:
                        continue
                    raw = (el.info.get("text") or el.info.get("contentDescription") or "")
                    low = raw.strip().lower().replace("'", "'")
                    if "no" in low and "skip" in low and low not in ("skip", "пропустить"):
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _has_plain_skip_button(self):
        for text in ("Skip", "Пропустить"):
            try:
                if self.device(text=text).exists:
                    return True
            except Exception:
                continue
        try:
            for el in self.device(textContains="skip"):
                try:
                    if not el.exists:
                        continue
                    raw = (el.info.get("text") or el.info.get("contentDescription") or "")
                    low = raw.strip().lower().replace("'", "'")
                    if not low:
                        continue
                    if "no" in low and "skip" in low:
                        continue
                    if low in ("skip", "пропустить") or (
                        "skip" in low and "no," not in low and not low.startswith("no ")
                    ):
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _has_got_it_button(self):
        return self._ui_has_text("Got it", "Got It", "Понятно")

    def _post_reg_skip_buttons_visible(self):
        return self._has_no_skip_button() or self._has_plain_skip_button()

    def _post_reg_ready_for_got_it(self):
        """Got it — только когда Skip / No,skip уже не видны на экране."""
        if not self._has_got_it_button():
            return False
        return not self._post_reg_skip_buttons_visible()

    def _post_reg_smart_click(self, continue_done):
        """
        Post-reg AI в стиле POST_ONBOARDING:
        permission dialog -> Continue (1 раз) -> динамические onboarding-кнопки.
        """
        if self._is_device_permission_screen() and self._ui_has_text("Next", "Далее"):
            if self._fast_click(["Next", "Далее"], "Next"):
                return True, continue_done, "next"

        if self._ui_has_text(*self._POST_REG_DENY_LABELS):
            if self._fast_click(list(self._POST_REG_DENY_LABELS), "Don't allow"):
                return True, continue_done, "deny"

        if not continue_done and self._ui_has_text("Continue", "Продолжить"):
            if self._fast_click(["Continue", "Продолжить"], "Continue"):
                return True, True, "continue"

        # После Continue (или если его не было) — динамический onboarding.
        if self._has_no_skip_button():
            if self._fast_click_no_skip():
                return True, continue_done, "no_skip"

        if self._has_plain_skip_button():
            if self._fast_click_skip_only(label="Skip"):
                return True, continue_done, "skip"

        if self._fast_click(["Not now", "Не сейчас"], "Not now"):
            return True, continue_done, "not_now"

        if self._fast_click(["Next", "Далее"], "Next"):
            return True, continue_done, "next"

        if self._fast_click(["No", "Нет"], "No"):
            return True, continue_done, "no"

        if self._post_reg_ready_for_got_it() or self._has_got_it_button():
            if self._fast_click(["Got it", "Got It", "Понятно"], "Got it"):
                return True, continue_done, "got_it"

        return False, continue_done, None

    def _mark_account_registered(self, reason, sec=0):
        if not self.account_registered:
            self.account_registered = True
            self._log(
                f"   ✅ Аккаунт зарегистрирован — {reason} ({sec} сек)"
            )

    def _detect_post_registration_surface(self, text=None):
        """Post-reg экран — один dump на цикл через text."""
        if self._is_human_verification_screen(text):
            return "human_verification"
        if self._check_main_app_ready():
            return "logged_in"
        if text is None:
            text = self._get_screen_text_lower()
        if self._blob_has_any(text, "got it", "понятно"):
            return "complete"
        if self._blob_has_any(
            text,
            "allow instagram to access",
            "access to your device",
            "access your device",
            "разрешить instagram",
            "доступ к устройству",
        ):
            return "device_permission"
        if self._blob_has_any(
            text, "don't allow", "dont allow", "don’t allow", "запретить"
        ):
            return "device_deny"
        if self._blob_has_any(text, "continue", "продолжить"):
            return "onboarding_continue"
        if self._blob_has_any(text, "no, skip", "no skip", "нет, пропустить"):
            return "onboarding_no_skip"
        if self._blob_has_any(text, "skip", "пропустить"):
            return "onboarding_skip"
        if self._blob_has_any(text, "next", "далее") and not self._blob_has_any(
            text, "i agree", "принимаю", "agree to"
        ):
            return "onboarding_next"
        return "unknown"

    def _is_device_permission_screen(self):
        return self._ui_has_text(
            "Allow Instagram to access",
            "access to your device",
            "access your device",
            "Разрешить Instagram",
            "доступ к устройству",
        )

    def _fast_click_skip_only(self, label="Skip", log=True):
        """Skip / Пропустить — не путать с «No, skip»."""
        for spec in (
            {"text": "Skip"},
            {"text": "Пропустить"},
            {"description": "Skip"},
            {"descriptionContains": "Skip"},
        ):
            try:
                nodes = self.device(**spec)
                if not nodes.exists:
                    continue
                for el in nodes:
                    try:
                        raw = (el.info.get("text") or el.info.get("contentDescription") or "")
                        low = raw.strip().lower()
                        if not low:
                            continue
                        if "no," in low or low.startswith("no skip") or low.startswith("no, skip"):
                            continue
                        if low not in ("skip", "пропустить") and "skip" not in low:
                            continue
                        if log:
                            self._log(f"   🤖 [Post-reg {label}] «{raw or label}»")
                        el.click()
                        self._invalidate_screen_cache()
                        return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    def _fast_click_no_skip(self, log=True):
        variants = (
            "No, skip",
            "No,skip",
            "No skip",
            "Don't skip",
            "Not now, skip",
            "Нет, пропустить",
        )
        for text in variants:
            for spec in ({"text": text}, {"textContains": text}):
                try:
                    el = self.device(**spec)
                    if el.exists:
                        if log:
                            self._log(f"   🤖 [Post-reg] «No, skip»")
                        el.click()
                        self._invalidate_screen_cache()
                        return True
                except Exception:
                    continue

        try:
            for el in self.device(textContains="skip"):
                try:
                    if not el.exists:
                        continue
                    raw = (el.info.get("text") or el.info.get("contentDescription") or "")
                    low = raw.strip().lower().replace("’", "'")
                    if "no" in low and "skip" in low and low not in ("skip", "пропустить"):
                        if log:
                            self._log(f"   🤖 [Post-reg] «{raw}»")
                        el.click()
                        self._invalidate_screen_cache()
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _fast_click_deny(self, log=True):
        return self._fast_click(list(self._POST_REG_DENY_LABELS), "Don't allow", log=log)

    def _wait_post_registration_after_agree(self, timeout=POST_REG_TIMEOUT):
        """
        После I agree:
        1) Динамически прожимаем permission/onboarding-кнопки по приоритету
        2) Терминал успеха: Got it (предпочтительно) + лента
        3) Если Got it не появился, допускаем успех по стабильной ленте
        """
        self._log(f"   ⏳ Post-reg: динамический режим (до {timeout} сек)...")
        start = time.monotonic()
        last_log = 0
        continue_done = False
        got_it_done = False
        onboarding_seen = False
        clicks = 0
        last_action = None
        same_action_hits = 0
        last_progress_ts = time.monotonic()

        while time.monotonic() - start < timeout:
            loop_t0 = time.monotonic()
            sec = int(time.monotonic() - start)

            if self._should_stop():
                return self.account_registered or got_it_done

            if self._is_human_verification_screen():
                self._log(
                    f"   ⚠️ Верификация «Confirm you're human» ({sec} сек) — не успех"
                )
                self.verification_detected = True
                return False

            if got_it_done and self._check_main_app_ready():
                self._log(f"   ✅ Главная лента ({sec} сек)")
                return True

            self._invalidate_screen_cache()
            txt = self._get_screen_text_lower(force=True)

            if self._is_human_verification_screen(txt):
                self.verification_detected = True
                return False

            if self._blob_has_any(
                txt,
                "suspended",
                "account disabled",
                "disabled account",
                "заблокирован",
                "аккаунт заблокирован",
            ):
                self._log(f"   ❌ Suspended ({sec}/{timeout} сек)")
                return False

            if self._blob_has_any(
                txt, "security check", "verification", "challenge", "checkpoint"
            ) and not (
                "код подтвержд" in txt and "ошиб" not in txt and "неверн" not in txt
            ):
                self.verification_detected = True
                return False

            reg_err = self._check_registration_error()
            if reg_err and not self.account_registered:
                self._log(f"   ❌ Ошибка ({sec}/{timeout}): {reg_err}")
                return False

            if self._is_device_permission_screen():
                self._mark_account_registered(
                    "«Allow Instagram to access your device?»", sec
                )

            if self._post_reg_skip_buttons_visible() or self._has_got_it_button():
                onboarding_seen = True

            clicked, continue_done, action = self._post_reg_smart_click(continue_done)
            if clicked:
                clicks += 1
                onboarding_seen = True
                if action == last_action:
                    same_action_hits += 1
                else:
                    same_action_hits = 1
                    last_action = action
                last_progress_ts = time.monotonic()

                # Антизалипание: если крутимся на Skip/No,skip, пытаемся раскрыть нижние кнопки.
                if action in ("skip", "no_skip") and same_action_hits >= 3:
                    if self._fast_click(["Got it", "Got It", "Понятно"], "Got it", log=False):
                        action = "got_it"
                    else:
                        try:
                            self.device.swipe(
                                self._screen_w // 2,
                                int(self._screen_h * 0.72),
                                self._screen_w // 2,
                                int(self._screen_h * 0.32),
                                0.22,
                            )
                            self._invalidate_screen_cache()
                        except Exception:
                            pass
                    same_action_hits = 0

                if action == "got_it":
                    got_it_done = True
                    self._log(f"   ✅ Post-reg: Got it ({sec} сек)")
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        if self._check_main_app_ready():
                            self._log(f"   ✅ Главная лента после Got it")
                            return True
                        time.sleep(POLL_FAST)
                    return True
                time.sleep(WAIT_SHORT)
                continue

            # Если UI есть, но действий нет — слегка прокручиваем экран, чтобы показать скрытые CTA.
            if onboarding_seen and (time.monotonic() - last_progress_ts) > 5.0:
                try:
                    self.device.swipe(
                        self._screen_w // 2,
                        int(self._screen_h * 0.68),
                        self._screen_w // 2,
                        int(self._screen_h * 0.40),
                        0.18,
                    )
                    self._invalidate_screen_cache()
                    last_progress_ts = time.monotonic()
                except Exception:
                    pass

            if onboarding_seen and self._check_main_app_ready() and not self._has_got_it_button():
                self._log(f"   ✅ Главная лента ({sec} сек)")
                return True

            if sec - last_log >= 4:
                surface = self._detect_post_registration_surface(txt)
                has_skip = self._has_plain_skip_button()
                has_no_skip = self._has_no_skip_button()
                has_got_it = self._has_got_it_button()
                ready_got_it = self._post_reg_ready_for_got_it()
                self._log(
                    f"   ⏳ Post-reg: «{surface}» skip={has_skip} no_skip={has_no_skip} "
                    f"got_it={has_got_it} ready={ready_got_it} "
                    f"({sec}/{timeout}с, кликов {clicks})"
                )
                last_log = sec

            spent = time.monotonic() - loop_t0
            if spent < POLL_FAST:
                time.sleep(POLL_FAST - spent)

        if got_it_done:
            self._log("   ✅ Post-reg: Got it нажат (таймаут ожидания ленты)")
            return True
        if self._check_main_app_ready() and not self._has_got_it_button():
            return True

        self._log(f"   ❌ Post-reg не завершён за {timeout} сек (кликов {clicks})")
        return False

    def _check_registration_success(self):
        """Признаки успешной регистрации после «Принимаю» (Android)."""
        if self._is_human_verification_screen():
            return False
        if self._check_main_app_ready():
            return True
        if self._is_device_permission_screen():
            return True
        if self._ui_has_text(
            "Пропустить",
            "Skip",
            "Not now",
            "Не сейчас",
            "Согласиться",
            "Agree to Terms",
            "Добавить фото",
            "Add profile photo",
            "Add a profile photo",
            "Найдите друзей",
            "Find friends",
            "Follow friends",
            "Подписаться",
            "Turn on notifications",
            "Включить уведомления",
        ):
            return True

        nav_home = self._ui_has_text("Главная", "Home", "Лента")
        nav_other = self._ui_has_text(
            "Поиск", "Search", "Reels", "Профиль", "Profile", "Создать", "Create"
        )
        if nav_home and nav_other:
            return True

        if self._ui_has_text("Профиль", "Profile", "Direct", "Сообщения"):
            if not self._ui_has_text(*self._UI_AGREE_MARKERS):
                screen = self._detect_registration_screen_light()
                if screen not in ("full_name", "username", "birthday", "password", "code", "email", "agree"):
                    return True

        return False

    def _find_edit_text_for_hints(self, hint_variants=None):
        hints = hint_variants or []
        if isinstance(hints, str):
            hints = [hints]
        for hint in hints:
            for spec in (
                {"className": "android.widget.EditText", "textContains": hint},
                {"className": "android.widget.EditText", "descriptionContains": hint},
            ):
                el = self._try_selector(**spec)
                if el and el.exists:
                    return el
        el = self._try_selector(className="android.widget.EditText")
        if el and el.exists and not self._is_password_input(el):
            return el
        return None

    def _wait_registration_step(
        self,
        expected_screens,
        markers=None,
        field_hints=None,
        timeout=ACTION_TIMEOUT,
        label="шаг",
    ):
        """Ждёт экран/поле — выходит сразу как нашло, timeout только потолок."""
        if isinstance(expected_screens, str):
            expected_screens = (expected_screens,)
        markers = list(markers or [])
        field_hints = field_hints or []
        self._log(f"   ⏳ Жду {label} (макс {timeout} сек)...")
        start = time.monotonic()
        last_log = 0
        expects_username = "username" in expected_screens

        while time.monotonic() - start < timeout:
            elapsed = int(time.monotonic() - start)

            if markers and self._ui_has_any_light(*markers):
                if field_hints:
                    field = self._find_field_by_hints(field_hints)
                    if not field:
                        field = self._find_edit_text_for_hints(field_hints)
                    if field:
                        self._log(f"   ✅ {label}: готов ({elapsed} сек)")
                        return True, field
                else:
                    self._log(f"   ✅ {label}: готов ({elapsed} сек)")
                    return True, None

            if field_hints:
                field = self._find_field_by_hints(field_hints)
                if not field:
                    field = self._find_edit_text_for_hints(field_hints)
                if field:
                    if expects_username and self._ui_has_text(
                        *self._UI_FULLNAME_MARKERS, *self._UI_NICKNAME_MARKERS
                    ):
                        pass
                    else:
                        self._log(f"   ✅ {label}: поле найдено ({elapsed} сек)")
                        return True, field

            screen = self._detect_registration_screen_light()
            if screen in expected_screens:
                if field_hints:
                    field = self._find_field_by_hints(field_hints)
                    if not field:
                        field = self._find_edit_text_for_hints(field_hints)
                    if field:
                        self._log(f"   ✅ {label}: готов ({elapsed} сек, «{screen}»)")
                        return True, field
                else:
                    self._log(f"   ✅ {label}: готов ({elapsed} сек, «{screen}»)")
                    return True, None

            if screen == "agree" and "agree" not in expected_screens:
                if elapsed >= 1:
                    self._log(f"   ℹ️ Уже «Принимаю» — пропуск «{label}»")
                    return True, None

            if elapsed - last_log >= 5:
                self._log(f"   ⏳ {label}: «{screen}» ({elapsed}/{timeout} сек)")
                last_log = elapsed
            time.sleep(POLL_UI_ONLY)

        self._log(f"   ❌ {label}: не дождались за {timeout} сек")
        self._log_current_screen_light(f"таймаут «{label}»")
        return False, None

    def _field_bounds(self, el):
        if not el:
            return {}
        try:
            return el.info.get("bounds") or {}
        except Exception:
            return {}

    def _fields_same(self, a, b):
        if not a or not b:
            return False
        if a == b:
            return True
        ba = self._field_bounds(a)
        bb = self._field_bounds(b)
        if not ba or not bb:
            return False
        return (
            ba.get("top") == bb.get("top")
            and ba.get("bottom") == bb.get("bottom")
            and ba.get("left") == bb.get("left")
            and ba.get("right") == bb.get("right")
        )

    def _list_plain_edit_fields(self):
        fields = []
        seen_bounds = set()
        for cls in ("android.widget.EditText", "android.widget.AutoCompleteTextView"):
            try:
                for el in self.device(className=cls):
                    try:
                        if not el.exists or self._is_password_input(el):
                            continue
                        b = el.info.get("bounds") or {}
                        top = b.get("top", 0)
                        key = (
                            top,
                            b.get("left", 0),
                            b.get("bottom", 0),
                            b.get("right", 0),
                        )
                        if key in seen_bounds:
                            continue
                        seen_bounds.add(key)
                        fields.append((top, el))
                    except Exception:
                        continue
            except Exception:
                continue
        fields.sort(key=lambda item: item[0])
        result = [el for _, el in fields]
        if not result:
            xpath_el = self._xpath_first_editable(exclude_password=True)
            if xpath_el is not None:
                result = [xpath_el]
        return result

    def _find_field_by_hints(self, hints, exclude=None):
        if isinstance(hints, str):
            hints = [hints]
        for hint in hints:
            for spec in (
                {"className": "android.widget.EditText", "textContains": hint},
                {"className": "android.widget.EditText", "descriptionContains": hint},
                {"className": "android.widget.AutoCompleteTextView", "textContains": hint},
                {
                    "className": "android.widget.AutoCompleteTextView",
                    "descriptionContains": hint,
                },
            ):
                el = self._try_selector(**spec)
                if el and el.exists and not self._is_password_input(el):
                    if exclude and self._fields_same(el, exclude):
                        continue
                    return el
        return None

    def _find_name_and_username_fields(self):
        """Имя/ник + username — по подсказкам или по порядку полей сверху вниз."""
        plain = self._list_plain_edit_fields()

        username_field = self._find_field_by_hints(
            ["Username", "Имя пользователя", "username", "имя пользователя"]
        )
        name_field = self._find_field_by_hints(
            list(self._UI_FULLNAME_MARKERS)
            + list(self._UI_NICKNAME_MARKERS)
            + ["Full name", "Полное имя", "Nickname", "Никнейм"],
            exclude=username_field,
        )

        if len(plain) >= 2:
            if not name_field:
                name_field = plain[0]
            if not username_field:
                username_field = plain[1]
            if self._fields_same(name_field, username_field):
                username_field = plain[1] if plain[1] != name_field else plain[0]
        elif len(plain) == 1:
            only = plain[0]
            if not name_field and not username_field:
                if self._ui_has_text(*self._UI_USERNAME_MARKERS) and not self._ui_has_text(
                    *self._UI_FULLNAME_MARKERS, *self._UI_NICKNAME_MARKERS
                ):
                    username_field = only
                else:
                    name_field = only
            elif name_field and not username_field:
                if not self._fields_same(name_field, only):
                    name_field = only
            elif username_field and not name_field:
                if not self._fields_same(username_field, only):
                    username_field = only

        if name_field and username_field and self._fields_same(name_field, username_field):
            if len(plain) >= 2:
                name_field = plain[0]
                username_field = plain[1]
            elif self._ui_has_text(*self._UI_USERNAME_MARKERS) and not self._ui_has_text(
                *self._UI_FULLNAME_MARKERS, *self._UI_NICKNAME_MARKERS
            ):
                name_field = None
            else:
                username_field = None

        if name_field and username_field:
            try:
                nb = name_field.info.get("bounds") or {}
                ub = username_field.info.get("bounds") or {}
                if nb.get("top", 0) > ub.get("top", 0):
                    name_field, username_field = username_field, name_field
            except Exception:
                pass

        return name_field, username_field

    def _fill_name_then_username_separate(self, name_f):
        """Layout A: имя → Next → username → Next."""
        self._log("   📋 Layout A: сначала только имя")
        self.full_name = self.generate_full_name()
        if not self.input_into_field(
            name_f, self.full_name, label="полное имя", clear_first=True
        ):
            return False
        if not self.click_next_once(reason="после имени"):
            return False

        ok, username_field = self._wait_registration_step(
            expected_screens=("username",),
            markers=self._UI_USERNAME_MARKERS,
            field_hints=["Имя пользователя", "Username", "username"],
            label="экран username",
        )
        if not ok or not username_field:
            return False
        if not self._enter_username_with_retries(username_field):
            return False
        if not self.click_next_once(reason="после username"):
            return False
        if self._detect_registration_screen_light() == "username":
            err_text = self._get_screen_text_lower()
            if any(
                n in err_text
                for n in (
                    "under 30 characters",
                    "менее 30 символ",
                    "недопустим",
                    "already taken",
                    "занят",
                    "not available",
                )
            ):
                username_field = self._find_field_by_hints(
                    ["Имя пользователя", "Username", "username"]
                )
                if username_field and self._enter_username_with_retries(username_field):
                    if not self.click_next_once(reason="повтор username"):
                        return False
        return True

    def _fill_combined_name_username(self, name_f, user_f):
        """Layout B: имя + username на одном экране → Next."""
        self._log("   📋 Layout B: имя + username на одном экране")
        self.full_name = self.generate_full_name()
        if not self.input_into_field(
            name_f, self.full_name, label="имя/ник", clear_first=True
        ):
            return False
        if not self._enter_username_with_retries(user_f):
            return False
        if not self.click_next_once(reason="имя+username"):
            return False
        return True

    def _is_combined_name_username_screen(self):
        plain = self._list_plain_edit_fields()
        if len(plain) < 2:
            return False
        name_f, user_f = self._find_name_and_username_fields()
        if not name_f or not user_f or name_f == user_f:
            return False
        try:
            nb = name_f.info.get("bounds") or {}
            ub = user_f.info.get("bounds") or {}
            if nb.get("top") == ub.get("top") and nb.get("bottom") == ub.get("bottom"):
                return False
        except Exception:
            pass
        return True

    def _handle_name_username_after_birthday(self):
        """
        После даты — 2 layout:
        A) имя → Next → username → Next → I agree
        B) имя + username на одном экране → Next → I agree
        """
        self._log("\n📝 Шаг: имя + username (авто layout)")
        start = time.monotonic()
        last_log = 0

        while time.monotonic() - start < ACTION_TIMEOUT:
            elapsed = int(time.monotonic() - start)

            if self._ui_has_text(*self._UI_AGREE_MARKERS):
                self._log(f"   ℹ️ Уже «Принимаю» ({elapsed} сек)")
                return True

            screen = self._detect_registration_screen_light()
            plain = self._list_plain_edit_fields()
            name_f, user_f = self._find_name_and_username_fields()

            if name_f and user_f and self._fields_same(name_f, user_f):
                user_f = None

            if len(plain) >= 2:
                name_f = name_f or plain[0]
                user_f = user_f or plain[1]
                if name_f and user_f and not self._fields_same(name_f, user_f):
                    return self._fill_combined_name_username(name_f, user_f)

            on_name_screen = screen in ("full_name", "name_username") or self._ui_has_any_light(
                *self._UI_FULLNAME_MARKERS, *self._UI_NICKNAME_MARKERS
            )
            if on_name_screen and len(plain) < 2:
                name_f = name_f or (plain[0] if plain else None)
                if not name_f:
                    name_f = self._find_field_by_hints(
                        list(self._UI_FULLNAME_MARKERS) + list(self._UI_NICKNAME_MARKERS)
                    )
                if name_f:
                    return self._fill_name_then_username_separate(name_f)

            has_username_markers = self._ui_has_any_light(*self._UI_USERNAME_MARKERS)
            has_name_markers = self._ui_has_any_light(
                *self._UI_FULLNAME_MARKERS, *self._UI_NICKNAME_MARKERS
            )
            on_username_screen = (
                (screen == "username" and not has_name_markers)
                or (has_username_markers and not has_name_markers)
            )
            if on_username_screen:
                user_f = user_f or (plain[0] if plain else None)
                if not user_f:
                    user_f = self._find_field_by_hints(
                        ["Username", "Имя пользователя", "username"]
                    )
                if user_f:
                    try:
                        probe = (user_f.get_text() or "").strip()
                        if probe and " " in probe and probe == (self.full_name or "").strip():
                            user_f = None
                    except Exception:
                        pass
                if user_f:
                    self._log("   📋 Только username (имя пропущено)")
                    if not self._enter_username_with_retries(user_f):
                        return False
                    if not self.click_next_once(reason="после username"):
                        return False
                    return True

            if elapsed - last_log >= 5:
                self._log(
                    f"   ⏳ имя/username: «{screen}» полей={len(plain)} "
                    f"name={'да' if name_f else 'нет'} user={'да' if user_f else 'нет'} "
                    f"({elapsed}/{ACTION_TIMEOUT}с)"
                )
                last_log = elapsed
            time.sleep(POLL_UI_ONLY)

        self._log(f"   ❌ Не дождались имя/username за {ACTION_TIMEOUT} сек")
        self._log_current_screen_light("таймаут имя/username")
        return False

    def _username_error_visible(self):
        text = self._get_screen_text_lower()
        needles = [
            "under 30 characters",
            "менее 30 символ",
            "имя пользователя",
            "недопустим",
            "only include numbers, letters",
            "underscores and periods",
            "занят",
            "already taken",
            "not available",
        ]
        return any(n in text for n in needles)

    # ========== USERNAME (логика ix_ios) ==========

    def _prepare_username(self):
        if not self.username:
            email_local = self.email.split("@")[0]
            self.username = self._sanitize_ig_username(email_local.replace("-", "_"))
        if len(self.username) > 30:
            self.username = self.username[:30]
        self._log(f"   👤 Username: {self.username}")

    def _enter_username_with_retries(self, username_field, max_fix_attempts=3):
        self._prepare_username()

        for attempt in range(1, max_fix_attempts + 1):
            chk = self._check_suspended_or_verification()
            if chk["suspended"]:
                self._log("   ❌ Suspended при вводе username")
                return False
            if chk["verification"]:
                self._log("   ⚠️ Верификация при вводе username")
                self.verification_detected = True
                return False

            current = ""
            try:
                current = username_field.get_text() or ""
            except Exception:
                pass

            current = (current or "").strip()
            is_valid_existing = (
                current
                and 3 <= len(current) <= 30
                and " " not in current
                and current != (self.full_name or "").strip()
            )
            if is_valid_existing:
                self.username = current
                self._log(f"   ✅ Username уже в поле: {self.username}")
                return True

            if not self.input_into_field(
                username_field,
                self.username,
                label="username",
                clear_first=True,
            ):
                username_field = self._wait_for_edit_text(
                    timeout=10,
                    hint_variants=["Имя пользователя", "Username", "username"],
                    label="поле username",
                )
                if not username_field:
                    return False
                continue

            self._short_pause(reason="после ввода username")

            if self._username_error_visible():
                self._log(
                    f"   ⚠️ Ошибка username, попытка {attempt}/{max_fix_attempts} — укорачиваю"
                )
                self.username = self.username[: max(3, 30 - attempt)]
                continue

            return True

        return False

    # ========== DEVICE EMULATOR (ROOT) ==========

    def _is_device_emulator_installed(self):
        try:
            self.device.app_info(DEVICE_EMULATOR_PKG)
            return True
        except Exception:
            pass
        try:
            out = (self.device.shell(f"pm path {DEVICE_EMULATOR_PKG}").output or "").strip()
            return out.startswith("package:")
        except Exception:
            return False

    def _device_emulator_menu_open(self):
        return self._ui_has_text(
            "Save Snap",
            "Randomise All",
            "Randomize All",
            "Randomise all",
            "Randomize all",
            "Export Snap",
            "Import Snap",
        )

    def _device_emulator_click_fab_menu(self):
        """Фиолетовая FAB-кнопка с ≡ внизу справа (над вкладкой Account)."""
        try:
            nodes = self.device.xpath(
                '//*[@clickable="true" and (@class="android.widget.ImageButton" '
                'or @class="android.widget.FrameLayout" or @class="android.view.ViewGroup")]'
            ).all()
            candidates = []
            for node in nodes:
                try:
                    info = node.info or {}
                    bounds = info.get("bounds") or {}
                    left = bounds.get("left", 0)
                    top = bounds.get("top", 0)
                    right = bounds.get("right", 0)
                    bottom = bounds.get("bottom", 0)
                    if right <= left or bottom <= top:
                        continue
                    cx = (left + right) / 2
                    cy = (top + bottom) / 2
                    if cx < self._screen_w * 0.65 or cy < self._screen_h * 0.72:
                        continue
                    size = min(right - left, bottom - top)
                    if size < 40 or size > 220:
                        continue
                    candidates.append((cx, cy, size, node))
                except Exception:
                    continue
            if candidates:
                candidates.sort(key=lambda item: (item[0], -item[1]), reverse=True)
                cx, cy, size, node = candidates[0]
                self._log(
                    f"   🔘 Device Emulator: FAB-меню ({int(cx)}, {int(cy)}), "
                    f"размер {int(size)}"
                )
                node.click()
                time.sleep(1.2)
                return True
        except Exception:
            pass

        positions = (
            (0.90, 0.84),
            (0.88, 0.86),
            (0.92, 0.82),
            (0.86, 0.85),
            (0.91, 0.87),
        )
        for rx, ry in positions:
            x = int(self._screen_w * rx)
            y = int(self._screen_h * ry)
            self._log(f"   🔘 Device Emulator: FAB-меню ({x}, {y})")
            try:
                self.device.click(x, y)
            except Exception:
                pass
            time.sleep(1.2)
            if self._device_emulator_menu_open():
                return True
        return False

    def _device_emulator_open_drawer(self, timeout=15):
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if self._device_emulator_menu_open():
                return True

            if self._device_emulator_click_fab_menu():
                if self._device_emulator_menu_open():
                    return True

            time.sleep(0.4)
        return False

    def _device_emulator_click_randomise_all(self):
        """Randomise All — фиолетовая кнопка под Save Snap (текст часто не в a11y)."""
        for text in ("Randomise All", "Randomize All", "Randomise all", "Randomize all"):
            for spec in (
                {"text": text},
                {"textContains": text},
                {"description": text},
                {"descriptionContains": text},
            ):
                try:
                    el = self.device(**spec)
                    if el.exists:
                        self._log(f"   ✅ Device Emulator: нажимаю «{text}»")
                        el.click()
                        time.sleep(1.5)
                        return True
                except Exception:
                    continue

        try:
            nodes = self.device.xpath(
                '//*[contains(@text, "Random") or contains(@content-desc, "Random")]'
            ).all()
            for node in nodes:
                info = node.info or {}
                label = f"{info.get('text') or ''} {info.get('contentDescription') or ''}".strip()
                lower = label.lower()
                if "all" not in lower:
                    continue
                if "randomise" not in lower and "randomize" not in lower:
                    continue
                bounds = info.get("bounds") or {}
                cx = (bounds.get("left", 0) + bounds.get("right", 0)) / 2
                if cx < self._screen_w * 0.55:
                    continue
                self._log(f"   ✅ Device Emulator: xpath «{label}»")
                node.click()
                time.sleep(1.5)
                return True
        except Exception:
            pass

        try:
            snap_el = None
            for snap_text in ("Save Snap", "Save snap"):
                el = self.device(textContains=snap_text)
                if el.exists:
                    snap_el = el
                    break
                el = self.device(descriptionContains=snap_text)
                if el.exists:
                    snap_el = el
                    break
            if snap_el is not None:
                bounds = snap_el.info.get("bounds") or {}
                x = int((bounds.get("left", 0) + bounds.get("right", 0)) / 2)
                height = max(40, bounds.get("bottom", 0) - bounds.get("top", 0))
                y = int(bounds.get("bottom", 0) + height * 0.85)
                self._log(
                    f"   🔘 Device Emulator: Randomise All под «Save Snap» ({x}, {y})"
                )
                self.device.click(x, y)
                time.sleep(1.5)
                return True
        except Exception:
            pass

        positions = (
            (0.81, 0.78),
            (0.83, 0.76),
            (0.79, 0.80),
            (0.85, 0.77),
            (0.82, 0.79),
        )
        for rx, ry in positions:
            x = int(self._screen_w * rx)
            y = int(self._screen_h * ry)
            self._log(f"   🔘 Device Emulator: Randomise All ({x}, {y})")
            try:
                self.device.click(x, y)
            except Exception:
                pass
            time.sleep(1.5)
            return True

        return False

    def _close_device_emulator(self):
        try:
            self.device.app_stop(DEVICE_EMULATOR_PKG)
        except Exception:
            pass
        self.device.press("home")
        time.sleep(0.8)

    def run_device_emulator_randomise(self):
        """Root: Device Emulator → меню → Randomise All → выход."""
        self._log("\n" + "=" * 50)
        self._log("🔄 ROOT: Device Emulator — Randomise All")
        self._log("=" * 50)

        if not self._is_device_emulator_installed():
            self._log(f"   ❌ Device Emulator не установлен ({DEVICE_EMULATOR_PKG})")
            return False

        try:
            self.device.app_stop(DEVICE_EMULATOR_PKG)
        except Exception:
            pass
        time.sleep(0.5)

        try:
            self.device.app_start(DEVICE_EMULATOR_PKG, wait=True)
        except Exception as exc:
            self._log(f"   ❌ Не удалось открыть Device Emulator: {exc}")
            return False

        time.sleep(2.5)

        if not self._device_emulator_open_drawer():
            self._log("   ❌ Не удалось открыть меню (FAB ≡ внизу справа)")
            self._close_device_emulator()
            return False

        self._log("   ✅ Меню Device Emulator открыто")

        if not self._device_emulator_click_randomise_all():
            self._log("   ❌ Кнопка Randomise All не найдена")
            self._close_device_emulator()
            return False

        self._log("   ✅ Randomise All нажато")
        time.sleep(2.0)
        self._close_device_emulator()
        self._log("   ✅ Device Emulator завершён — продолжаю регистрацию")
        return True

    # ========== INSTAGRAM APP ==========

    def _is_instagram_installed(self):
        try:
            self.device.app_info(INSTAGRAM_PKG)
            return True
        except Exception:
            pass
        try:
            out = (self.device.shell(f"pm path {INSTAGRAM_PKG}").output or "").strip()
            return out.startswith("package:")
        except Exception:
            return False

    def uninstall_instagram(self):
        import inspect

        caller = inspect.stack()[1].function
        if self._device_action_blocked_during_code_wait(f"uninstall ({caller})"):
            return False

        if not self._is_instagram_installed():
            self._log("   ℹ️ Instagram уже не установлен")
            return True

        self._log("🗑️ Удаляю Instagram...")
        try:
            self.force_close_instagram()
        except Exception:
            pass

        for attempt in range(1, 4):
            try:
                self.device.app_uninstall(INSTAGRAM_PKG)
            except Exception:
                pass
            try:
                self.device.shell(f"pm uninstall --user 0 {INSTAGRAM_PKG}")
            except Exception:
                pass
            time.sleep(2)
            if not self._is_instagram_installed():
                self._log("   ✅ Instagram удалён")
                self.device.press("home")
                time.sleep(1)
                return True
            self._log(f"   ⚠️ Повтор удаления ({attempt}/3)...")

        self._log("   ❌ Не удалось удалить Instagram")
        return False

    def _play_store_details_app_id(self):
        """Пытается определить id приложения на открытой странице Play Маркета (лёгкий dumpsys)."""
        for cmd in (
            "dumpsys activity top",
            "dumpsys window displays | grep -E 'mCurrentFocus|mFocusedApp'",
        ):
            try:
                out = (self.device.shell(cmd).output or "")
                matches = re.findall(r"details\?id=([a-zA-Z0-9_.]+)", out)
                if matches:
                    return matches[-1]
            except Exception:
                continue
        return None

    def _play_store_on_instagram_page(self):
        on_page_ui = self._ui_has_text("Instagram") and self._ui_has_text(
            "Установить",
            "Install",
            "Открыть",
            "Open",
            "Обновить",
            "Update",
            "Отменить",
            "Cancel",
            "Скачать",
            "Download",
        )
        if on_page_ui:
            return True
        app_id = self._play_store_details_app_id()
        if app_id:
            return app_id == INSTAGRAM_PKG
        return False

    def _open_play_store_instagram_page(self):
        self._log("   📦 Открываю Instagram в Play Маркете...")
        self.device.press("home")
        time.sleep(0.8)

        market_cmds = [
            f'am start -a android.intent.action.VIEW -d "market://details?id={INSTAGRAM_PKG}"',
            (
                f'am start -a android.intent.action.VIEW -d '
                f'"https://play.google.com/store/apps/details?id={INSTAGRAM_PKG}"'
            ),
        ]
        for cmd in market_cmds:
            try:
                self.device.shell(cmd)
                time.sleep(4)
                if self._play_store_on_instagram_page() and self._ui_has_text(
                    "Instagram",
                    "Установить",
                    "Install",
                    "Открыть",
                    "Open",
                    "Обновить",
                    "Update",
                    "Отменить",
                    "Cancel",
                ):
                    return True
            except Exception:
                continue

        try:
            self.device.app_start(PLAY_STORE_PKG, wait=True)
            time.sleep(2)
            search = self.device(className="android.widget.EditText")
            if search.exists:
                search.click()
                time.sleep(0.5)
                search.set_text("Instagram")
                time.sleep(1)
                self.device.press("enter")
                time.sleep(3)
                if self.click_by_text(["Instagram"], max_attempts=2, quiet=True):
                    time.sleep(3)
                    return self._play_store_on_instagram_page()
        except Exception:
            pass

        self._log("   ❌ Не удалось открыть страницу Instagram в Play Маркете")
        return False

    def _play_store_installing(self):
        return self._ui_has_text(
            "Загрузка",
            "Downloading",
            "Установка",
            "Installing",
            "Идёт установка",
            "Pending",
            "Ожидание",
            "Загружается",
        )

    def _play_store_download_in_progress(self):
        """Загрузка уже идёт — на экране Cancel/Stop, но не Install/Open."""
        if self._play_store_installing():
            return True
        has_cancel = self._ui_has_text("Отменить", "Cancel", "Остановить", "Stop")
        has_terminal = self._ui_has_text(
            "Открыть", "Open", "Установить", "Install", "Скачать", "Download"
        )
        return has_cancel and not has_terminal

    def _play_store_cancel_download(self):
        """Пробует отменить зависшую установку в Play Маркете."""
        cancel_variants = ["Отменить", "Cancel", "Остановить", "Stop"]
        if self.click_by_text(cancel_variants, max_attempts=1, quiet=True):
            self._log("   ⚠️ Установка зависла — нажимаю Cancel/Отменить")
            time.sleep(2.5)
            return True
        return False

    def _play_store_abort_wrong_app_download(self):
        """Отменяет загрузку, если на экране не Instagram."""
        if self._play_store_on_instagram_page():
            return False
        wrong_id = self._play_store_details_app_id() or "неизвестное приложение"
        self._log(f"   ⚠️ Скачивается не Instagram ({wrong_id}) — отменяю")
        self._play_store_cancel_download()
        time.sleep(1.5)
        return True

    def install_instagram_via_play_store(self, timeout=PLAY_STORE_INSTALL_TIMEOUT):
        self._log("\n📥 Устанавливаю Instagram через Play Маркет...")

        if self._is_instagram_installed():
            self._log("   ℹ️ Instagram уже установлен")
            return True

        install_variants = ["Установить", "Install", "Скачать", "Download"]
        open_variants = ["Открыть", "Open"]
        update_variants = ["Обновить", "Update"]
        max_install_cycles = 3

        for cycle in range(1, max_install_cycles + 1):
            self._log(f"   🔁 Цикл установки {cycle}/{max_install_cycles}")

            if cycle > 1:
                self._play_store_cancel_download()
                time.sleep(1.5)

            if not self._open_play_store_instagram_page():
                self._log("   ❌ Не удалось открыть страницу Instagram — следующий цикл")
                continue
            self._log("   ✅ Страница Instagram в Play Маркете")

            clicked_install = False
            download_started_at = None
            start = time.time()

            while time.time() - start < timeout:
                elapsed = int(time.time() - start)

                if self._is_instagram_installed():
                    if self._ui_has_text(*open_variants):
                        self._log(f"   ✅ Instagram установлен («Открыть», {elapsed} сек)")
                        return True
                    if clicked_install and not self._play_store_installing():
                        self._log(f"   ✅ Instagram установлен ({elapsed} сек)")
                        return True

                if self.click_by_text(open_variants, max_attempts=1, quiet=True):
                    if self._is_instagram_installed():
                        self._log(f"   ✅ Instagram установлен ({elapsed} сек)")
                        return True

                if self._play_store_abort_wrong_app_download():
                    clicked_install = False
                    download_started_at = None
                    if not self._open_play_store_instagram_page():
                        break
                    self._log("   ✅ Страница Instagram в Play Маркете (после отмены чужой загрузки)")
                    continue

                if self._play_store_download_in_progress():
                    if download_started_at is None:
                        download_started_at = time.time()
                        self._log("   ⏳ Загрузка Instagram в процессе...")
                    elif time.time() - download_started_at >= PLAY_STORE_STUCK_DOWNLOAD_SEC:
                        self._log(
                            f"   ⚠️ Загрузка зависла ({PLAY_STORE_STUCK_DOWNLOAD_SEC} сек) — "
                            "отменяю и переоткрываю Instagram"
                        )
                        self._play_store_cancel_download()
                        time.sleep(1.5)
                        clicked_install = False
                        download_started_at = None
                        if not self._open_play_store_instagram_page():
                            break
                        self._log("   ✅ Страница Instagram в Play Маркете (после зависания)")
                        continue
                    if elapsed > 0 and elapsed % 15 == 0:
                        self._log(f"   ⏳ Установка из Play Маркета... {elapsed}/{timeout} сек")
                    time.sleep(2)
                    continue

                if not clicked_install:
                    if not self._play_store_on_instagram_page():
                        self._log("   🔄 Ушли со страницы Instagram — переоткрываю...")
                        if not self._open_play_store_instagram_page():
                            time.sleep(2)
                            continue
                    self._log("   🔍 Ищу кнопку «Установить» на странице Instagram...")
                    if self.click_by_text(install_variants, max_attempts=1, quiet=False):
                        clicked_install = True
                        download_started_at = time.time()
                        self._log("   ⏳ Нажато «Установить», жду загрузку...")
                        time.sleep(4)
                        continue
                    if self.click_by_text(update_variants, max_attempts=1, quiet=True):
                        clicked_install = True
                        download_started_at = time.time()
                        self._log("   ⏳ Обновление Instagram...")
                        time.sleep(4)
                        continue

                if elapsed > 0 and elapsed % 15 == 0:
                    self._log(f"   ⏳ Установка из Play Маркета... {elapsed}/{timeout} сек")
                time.sleep(2)

            if self._is_instagram_installed():
                self._log("   ✅ Instagram установлен (пакет найден после ожидания)")
                return True

            self._log(f"   ⚠️ Таймаут цикла установки {cycle}/{max_install_cycles} ({timeout} сек)")
            self._play_store_cancel_download()
            time.sleep(1.5)

        if self._is_instagram_installed():
            self._log("   ✅ Instagram установлен (пакет найден после ожидания)")
            return True

        self._log(f"   ❌ Установка через Play Маркет не завершилась после {max_install_cycles} циклов")
        return False

    def install_instagram_via_apkm(self):
        """Установка из apk/com.instagram.apkm (или com.instagram.apk) через adb install-multiple."""
        self._log("\n📥 Устанавливаю Instagram из APKM/APK...")

        if self._is_instagram_installed():
            self._log("   ℹ️ Instagram уже установлен")
            return True

        apkm_path = APK_DIR / APKM_FILENAME
        apk_path = APK_DIR / APK_FILENAME
        if not apkm_path.exists() and not apk_path.exists():
            self._log(f"   ❌ Не найден {APK_DIR / APKM_FILENAME} или {apk_path}")
            return False

        extract_dir = None
        try:
            if apkm_path.exists():
                self._log(f"   📦 Распаковка {APKM_FILENAME}...")
                extract_dir = Path(tempfile.mkdtemp(prefix="ig_apkm_"))
                with zipfile.ZipFile(apkm_path, "r") as archive:
                    archive.extractall(extract_dir)
                apk_files = sorted(
                    extract_dir.glob("**/*.apk"),
                    key=lambda p: (0 if p.name == "base.apk" else 1, p.name),
                )
            else:
                self._log(f"   📦 Использую {APK_FILENAME}")
                apk_files = [apk_path]

            if not apk_files:
                self._log("   ❌ В пакете нет .apk файлов")
                return False

            adb_executable = find_adb_executable()
            if not adb_executable:
                self._log("   ❌ adb.exe не найден — установите Android platform-tools")
                return False

            self._log(f"   📲 adb install-multiple ({len(apk_files)} файлов)...")
            cmd = [adb_executable]
            if self.device_serial:
                cmd += ["-s", self.device_serial]
            cmd += ["install-multiple", "-r", "-d"] + [str(p) for p in apk_files]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            out = f"{result.stdout or ''}{result.stderr or ''}".strip()
            if result.returncode == 0 or "Success" in out:
                time.sleep(2)
                if self._is_instagram_installed():
                    self._log(f"   ✅ Instagram установлен из APKM/APK ({len(apk_files)} файлов)")
                    return True

            self._log(f"   ❌ Ошибка install-multiple: {out[:400]}")
            return False
        except subprocess.TimeoutExpired:
            self._log("   ❌ Таймаут установки APKM/APK (600 сек)")
            return False
        except zipfile.BadZipFile:
            self._log(f"   ❌ Повреждённый APKM: {apkm_path}")
            return False
        except Exception as exc:
            self._log(f"   ❌ Ошибка установки APKM/APK: {exc}")
            return False
        finally:
            if extract_dir:
                shutil.rmtree(extract_dir, ignore_errors=True)

    def _install_instagram(self):
        if self.install_method == INSTALL_METHOD_APKM:
            return self.install_instagram_via_apkm()
        return self.install_instagram_via_play_store()

    def reinstall_instagram(self):
        import inspect

        caller = inspect.stack()[1].function
        self._trace_step("reinstall_instagram", f"caller={caller}")
        if self._device_action_blocked_during_code_wait(f"reinstall ({caller})"):
            return False

        self._log("\n" + "=" * 50)
        self._log(f"🔄 ПЕРЕУСТАНОВКА INSTAGRAM (вызов: {caller})")
        self._log("=" * 50)

        if not self.uninstall_instagram():
            return False

        time.sleep(2)

        if not self._install_instagram():
            if self.install_method == INSTALL_METHOD_APKM:
                self._log("   ❌ Установка через APKM/APK не удалась")
            else:
                self._log("   ❌ Установка через Play Маркет не удалась (позже можно APK)")
            return False

        self.device.press("home")
        time.sleep(1)
        self._log("✅ Instagram переустановлен и готов к запуску")
        return True

    def _remove_instagram_for_new_account(self):
        """Закрыть и удалить Instagram перед сменой отпечатка / новой установкой."""
        self._log("\n🔄 Закрываю и удаляю Instagram...")
        try:
            self.force_close_instagram()
        except Exception:
            pass
        time.sleep(0.5)
        if not self.uninstall_instagram():
            return False
        time.sleep(1)
        return True

    def _prepare_device_for_registration(self):
        """Порядок: удалить Instagram → Root (Device Emulator) → готово к установке."""
        if not self._remove_instagram_for_new_account():
            return False
        if self.root_enabled:
            if not self.run_device_emulator_randomise():
                return False
        return True

    def _detect_startup_screen(self, screen_text=None):
        """
        Экран при старте Instagram — только стартовые состояния.
        НЕ вызывает _detect_registration_screen (лавина dump на S9).
        """
        if self._is_login_screen(screen_text=screen_text):
            if self._ui_has_text(*self._UI_CREATE_NEW_ACCOUNT):
                return "create_new_account"
            return "login"

        if self._ui_has_text(*self._UI_EMAIL_SIGNUP_CHOICE):
            return "signup_choice"

        if self._ui_has_text(*self._UI_STARTUP_AGREE):
            return "startup_agree"

        if self._ui_has_text(*self._UI_STARTUP_CONTINUE):
            return "startup_continue"

        if self._ui_has_text(*self._UI_CREATE_NEW_ACCOUNT):
            return "create_new_account"

        if self._ui_has_text(*self._UI_GET_STARTED):
            return "get_started"

        if self._is_signup_email_screen(screen_text=screen_text):
            return "email"

        if self._ui_has_text("Log in", "Log In", "Войти"):
            return "welcome"

        return "unknown"

    def _startup_menu_done(self, screen):
        return screen in (
            "signup_choice",
            "email",
            "code",
            "password",
            "birthday",
            "full_name",
            "username",
            "agree",
        )

    def _dismiss_startup_consent(self):
        """Cookies / согласие перед стартовыми кнопками."""
        if self._ui_has_text(*self._UI_STARTUP_AGREE):
            self._log("   📍 Cookies / согласие...")
            if self.click_by_text(list(self._UI_STARTUP_AGREE), max_attempts=2):
                time.sleep(0.5)
                return True
        if self._ui_has_text(*self._UI_STARTUP_CONTINUE):
            self._log("   📍 Continue...")
            if self.click_by_text(list(self._UI_STARTUP_CONTINUE), max_attempts=2):
                time.sleep(0.5)
                return True
        return False

    def _click_sign_up_with_email(self):
        self._short_pause(reason="перед Sign up with email")
        self._log("   🔍 Ищу «Sign up with email»...")
        if self.click_by_text(list(self._UI_EMAIL_SIGNUP_CHOICE), max_attempts=3):
            self._short_pause(reason="после Sign up with email")
            time.sleep(0.4)
            return True
        return False

    def _entry_ready(self, screen, screen_text=None):
        """Стартовое меню пройдено — появилась форма регистрации (classic или one-page)."""
        if self._entry_path not in ("create_account", "get_started"):
            return False

        if self._is_onboarding_one_page_screen(screen_text=screen_text):
            return True
        if self._is_classic_signup_email_form(screen_text=screen_text):
            return True
        if (
            self._has_email_screen_light()
            and self._has_plain_edit_field()
            and not self._has_password_field()
        ):
            return True
        if self._is_login_screen(screen_text=screen_text):
            return False
        if screen == "signup_choice" and self._entry_path == "get_started":
            return True
        if screen == "email" and self._entry_path in ("create_account", "get_started"):
            return self._is_signup_email_screen(screen_text=screen_text)
        return False

    def _wait_startup_entry_button(self, timeout=ACTION_TIMEOUT):
        """Фаза 1: Get started / Create new account (отдельный таймаут)."""
        self._log(f"\n🔍 Фаза 1: стартовое меню (до {timeout} сек)...")
        start = time.time()
        last_log = 0
        self._short_pause(reason="загрузка Instagram")
        time.sleep(0.5)

        while time.time() - start < timeout:
            elapsed = int(time.time() - start)
            screen_text = self._take_poll_snapshot()

            if self._dismiss_startup_consent():
                time.sleep(0.5)
                continue

            screen = self._detect_startup_screen(screen_text=screen_text)

            if screen in ("login", "create_new_account", "welcome") and self._entry_path is None:
                if not self._ui_has_text(*self._UI_CREATE_NEW_ACCOUNT) and not self._ui_has_text(
                    *self._UI_GET_STARTED
                ):
                    self.device.swipe(
                        self._screen_w // 2,
                        int(self._screen_h * 0.75),
                        self._screen_w // 2,
                        int(self._screen_h * 0.35),
                        0.3,
                    )
                    time.sleep(0.6)
                    continue

            acted = False

            if screen == "create_new_account" and self._entry_path != "create_account":
                self._log("   📍 Меню B: Create new account (как ix_ios web)...")
                if self.click_by_text(
                    list(self._UI_CREATE_NEW_ACCOUNT), max_attempts=3, scroll=True
                ):
                    self._entry_path = "create_account"
                    acted = True
                    self._short_pause(reason="после Create new account")
                    time.sleep(0.8)

            elif screen == "login" and self._entry_path is None:
                self._log("   📍 Экран входа — ищу Create new account...")
                if self.click_by_text(
                    list(self._UI_CREATE_NEW_ACCOUNT), max_attempts=3, scroll=True
                ):
                    self._entry_path = "create_account"
                    acted = True
                    self._short_pause(reason="после Create new account")
                    time.sleep(0.8)

            elif screen == "get_started" and self._entry_path is None:
                self._log("   📍 Меню A: Get started...")
                if self.click_by_text(
                    list(self._UI_GET_STARTED), max_attempts=2, scroll=True
                ):
                    self._entry_path = "get_started"
                    acted = True
                    self._short_pause(reason="после Get started")
                    time.sleep(0.8)

            elif screen == "welcome" and self._entry_path is None:
                if self._ui_has_text(*self._UI_CREATE_NEW_ACCOUNT):
                    self._log("   📍 Меню B: Create new account (welcome)...")
                    if self.click_by_text(
                        list(self._UI_CREATE_NEW_ACCOUNT), max_attempts=2, scroll=True
                    ):
                        self._entry_path = "create_account"
                        acted = True
                        time.sleep(0.8)
                elif self._ui_has_text(*self._UI_GET_STARTED):
                    self._log("   📍 Меню A: Get started (welcome)...")
                    if self.click_by_text(
                        list(self._UI_GET_STARTED), max_attempts=2, scroll=True
                    ):
                        self._entry_path = "get_started"
                        acted = True
                        time.sleep(0.8)

            if acted and self._entry_path:
                self._log(
                    f"   ✅ Фаза 1: нажато ({elapsed} сек), путь «{self._entry_path}»"
                )
                return True

            if elapsed - last_log >= 4:
                self._log(
                    f"   ⏳ Фаза 1: жду Get started / Create new account... "
                    f"({elapsed}/{timeout} сек, экран «{screen}»)"
                )
                last_log = elapsed
            time.sleep(0.4)

        snap = self._take_poll_snapshot(force=True)
        screen = self._detect_startup_screen(screen_text=snap)
        self._log("   ❌ Фаза 1: стартовое меню не появилось за отведённое время")
        self._log(f"   📍 Экран при таймауте: «{screen}»")
        return False

    def _wait_registration_form_after_entry(self, timeout=ACTION_TIMEOUT):
        """Фаза 2: Sign up with email / форма регистрации (свой таймаут 40 сек)."""
        self._log(f"\n🔍 Фаза 2: форма регистрации (до {timeout} сек)...")
        start = time.time()
        last_log = 0

        while time.time() - start < timeout:
            elapsed = int(time.time() - start)
            screen_text = self._take_poll_snapshot()

            if self._dismiss_startup_consent():
                time.sleep(0.5)
                continue

            screen = self._detect_startup_screen(screen_text=screen_text)

            if self._entry_ready(screen, screen_text=screen_text):
                self._log(
                    f"   ✅ Фаза 2: форма готова ({elapsed} сек), "
                    f"путь «{self._entry_path}», экран «{screen}»"
                )
                return True

            if self._entry_path == "create_account":
                if self._ui_has_text(*self._UI_EMAIL_SIGNUP_CHOICE):
                    self._log("   📍 Create new account → Sign up with email...")
                    if self._click_sign_up_with_email():
                        time.sleep(0.8)
                        continue
                if screen in ("login", "unknown", "welcome", "create_new_account"):
                    if self._is_classic_signup_email_form(screen_text=screen_text):
                        self._log(
                            f"   ✅ Фаза 2: classic email ({elapsed} сек), экран «{screen}»"
                        )
                        return True
                    if self._is_onboarding_one_page_screen(screen_text=screen_text):
                        self._log(
                            f"   ✅ Фаза 2: one-page ({elapsed} сек), экран «{screen}»"
                        )
                        return True

            if self._entry_path == "get_started" and screen == "signup_choice":
                self._log("   📍 Get started → Sign up with email...")
                if self._click_sign_up_with_email():
                    time.sleep(0.8)
                    continue

            if elapsed - last_log >= 4:
                self._log(
                    f"   ⏳ Фаза 2: жду форму регистрации... "
                    f"({elapsed}/{timeout} сек, экран «{screen}»)"
                )
                last_log = elapsed
            time.sleep(0.4)

        snap = self._take_poll_snapshot(force=True)
        screen = self._detect_startup_screen(screen_text=snap)
        self._log("   ❌ Фаза 2: форма регистрации не появилась за отведённое время")
        self._log(f"   📍 Экран при таймауте: «{screen}»")
        return False

    def _handle_instagram_entry(self, timeout=ACTION_TIMEOUT):
        """
        Вход: Get started ИЛИ Create new account → форма регистрации.
        Две фазы по timeout сек каждая (не один общий таймер).
        """
        self._log("\n🔍 Ожидание стартового меню (Get started / Create new account)...")
        self._entry_path = None
        self._onboarding_one_page_done = False
        self._signup_form_design = None

        if not self._wait_startup_entry_button(timeout=timeout):
            return False
        if not self._wait_registration_form_after_entry(timeout=timeout):
            return False

        self._log(
            f"   ✅ Стартовое меню пройдено, путь «{self._entry_path or 'direct'}»"
        )
        return True

    def _go_to_email_signup(self):
        design = self._detect_signup_form_design(timeout=ACTION_TIMEOUT)

        if design == "one_page":
            self._log("   ✅ One-page: email + пароль + дата на одном экране")
            return True

        if self._entry_path and self._has_email_screen_light():
            self._log("   ✅ Уже на экране ввода email (classic)")
            return True
        if self._ui_has_any_light(*self._UI_SIGNUP_EMAIL_SCREEN):
            self._log("   ✅ Уже на экране ввода email (classic)")
            return True
        if self._ui_has_text(*self._UI_EMAIL_SIGNUP_CHOICE):
            self._log("   📍 Classic: Sign up with email...")
            return self._click_sign_up_with_email()
        if self._ui_has_text("Log in", "Log In", "Войти") and not self._entry_path:
            self._log("   ❌ Всё ещё на экране входа — Create new account не пройден")
            return False
        self._log("   🔍 Classic: выбор регистрации по email...")
        return self._click_sign_up_with_email()

    def force_close_instagram(self):
        import inspect

        caller = inspect.stack()[1].function
        self._trace_step("force_close_instagram", f"caller={caller}")
        if self._device_action_blocked_during_code_wait(f"force_close ({caller})"):
            return
        self._log(f"🛑 Полностью закрываю Instagram (вызов: {caller})...")
        self.device.app_stop(INSTAGRAM_PKG)
        time.sleep(1)
        self.device.press("home")
        time.sleep(1)
        self._log("✅ Instagram закрыт")

    def open_instagram(self):
        self._trace_step("open_instagram")
        if self._device_action_blocked_during_code_wait("open_instagram"):
            return
        self._log("📱 Открываю Instagram...")
        self.device.app_start(INSTAGRAM_PKG, wait=True, stop=False)
        time.sleep(WAIT_LONG)
        self._log("✅ Instagram запущен")

    # ========== 2FA (Android UI, логика как test.py web) ==========

    # ===== 2FA FAST (как ter.py пользователя) =====
    _2FA_FAST_SETTINGS_MARKERS = (
        "Settings and activity",
        "Настройки и действия",
        "Settings",
        "Настройки",
        "Accounts Center",
        "Центр аккаунтов",
        "Your account",
        "Ваш аккаунт",
        "How to use Instagram",
        "Как использовать Instagram",
        "Saved",
        "Сохраненное",
        "Archive",
        "Архив",
        "Your activity",
        "Ваши действия",
        "Notifications",
        "Уведомления",
    )

    _2FA_FAST_PROFILE_MARKERS = (
        "Edit profile",
        "Редактировать профиль",
        "Share profile",
        "Поделиться профилем",
        "Posts",
        "Публикации",
        "Followers",
        "Подписчики",
        "Following",
        "Подписки",
    )

    _2FA_FAST_CODE_SCREEN_MARKERS = (
        "Enter code",
        "Введите код",
        "Enter the 6-digit code",
        "Введите 6-значный код",
        "generated by your authenticator app",
        "сгенерированный вашим приложением",
    )

    _2FA_FAST_SUCCESS_MARKERS = (
        "Two-factor authentication is on",
        "Двухфакторная аутентификация включена",
        "We'll now ask for a login code",
        "Теперь мы будем запрашивать код входа",
        "backup codes",
        "резервные коды",
        "Done",
        "Готово",
    )

    def _2fa_fast_has_any(self, *needles):
        for n in needles:
            if n and self._ui_has_text(n):
                return True
        return False

    def _2fa_fast_is_settings_screen(self):
        for marker in self._2FA_FAST_SETTINGS_MARKERS:
            if self._2fa_fast_has_any(marker):
                return True
        try:
            text = self._get_screen_text_lower(force=True, max_age=0)
            settings_keywords = [
                "settings",
                "настройк",
                "accounts center",
                "центр аккаунтов",
                "your account",
            ]
            found = 0
            for kw in settings_keywords:
                if kw in text:
                    found += 1
            if found >= 2:
                return True
        except Exception:
            pass
        return False

    def _2fa_fast_is_profile_screen(self):
        for marker in self._2FA_FAST_PROFILE_MARKERS:
            if self._2fa_fast_has_any(marker):
                return True
        try:
            text = self._get_screen_text_lower(force=True, max_age=0)
            profile_keywords = [
                "edit profile",
                "редактировать профиль",
                "posts",
                "публикаций",
                "followers",
            ]
            for kw in profile_keywords:
                if kw in text:
                    return True
        except Exception:
            pass
        return False

    def _2fa_fast_is_code_input_screen(self):
        for marker in self._2FA_FAST_CODE_SCREEN_MARKERS:
            if self._2fa_fast_has_any(marker):
                return True
        try:
            text = self._get_screen_text_lower(force=True, max_age=0)
            code_keywords = ["enter code", "введите код", "6-digit", "6-значн"]
            for kw in code_keywords:
                if kw in text:
                    return True
        except Exception:
            pass
        return False

    def _2fa_fast_is_success_screen(self):
        for marker in self._2FA_FAST_SUCCESS_MARKERS:
            if self._2fa_fast_has_any(marker):
                return True
        try:
            text = self._get_screen_text_lower(force=True, max_age=0)
            success_keywords = [
                "two-factor authentication is on",
                "двухфакторная аутентификация включена",
                "backup codes",
                "резервные коды",
            ]
            for kw in success_keywords:
                if kw in text:
                    return True
        except Exception:
            pass
        return False

    def _2fa_fast_wait(self, check_fn, timeout=15, poll=0.5, label="экран"):
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if check_fn():
                self._log(f"   ✅ 2FA: {label} ({int(time.monotonic() - start)} сек)")
                return True
            time.sleep(poll)
        return bool(check_fn())

    def _2fa_fast_wait_for_edit_text(self, timeout=10, label="поле ввода"):
        self._log(f"   ⏳ 2FA: жду {label} (до {timeout} сек)...")
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            field = self.device(className="android.widget.EditText")
            if field.exists:
                self._log(f"   ✅ 2FA: {label} найдено ({int(time.monotonic() - start)} сек)")
                return field
            if self._2fa_fast_has_any("Enter code", "Введите код"):
                time.sleep(0.5)
                field = self.device(className="android.widget.EditText")
                if field.exists:
                    return field
            time.sleep(0.5)
        self._log(f"   ❌ 2FA: {label} не найдено за {timeout} сек")
        return None

    def _2fa_fast_open_profile_tab(self, timeout=10):
        self._log("   📍 2FA: открываю профиль...")
        if self._2fa_fast_is_profile_screen():
            self._log("   ✅ 2FA: уже на профиле")
            return True
        x = int(self._screen_w * 0.92)
        y = int(self._screen_h * 0.94)
        self._log(f"   🔘 2FA: тап по координатам профиля: ({x}, {y})")
        try:
            self.device.click(x, y)
        except Exception:
            pass
        time.sleep(2.0)
        if self._2fa_fast_wait(self._2fa_fast_is_profile_screen, timeout=8, label="профиль"):
            return True
        if self.click_by_text(["Profile", "Профиль"], max_attempts=2, quiet=True):
            time.sleep(1.5)
            if self._2fa_fast_wait(
                self._2fa_fast_is_profile_screen, timeout=4, label="профиль (текст)"
            ):
                return True
        self._log("   ❌ 2FA: не удалось открыть профиль")
        return False

    def _2fa_fast_open_menu(self, timeout=15):
        self._log("   📍 2FA: открываю меню (3 полоски)...")
        if self._2fa_fast_is_settings_screen():
            self._log("   ✅ 2FA: уже на экране настроек")
            return True
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            x = int(self._screen_w * 0.93)
            y = int(self._screen_h * 0.07)
            self._log(f"   🔘 2FA: тап по координатам: ({x}, {y})")
            try:
                self.device.click(x, y)
            except Exception:
                pass
            time.sleep(1.5)
            if self._2fa_fast_wait(
                self._2fa_fast_is_settings_screen, timeout=3, label="Settings and activity"
            ):
                return True
            for desc in ("Options", "Profile options", "Menu", "Настройки", "Параметры"):
                try:
                    el = self.device(descriptionContains=desc)
                    if el.exists:
                        self._log(f"   🔘 2FA: тап по description: {desc}")
                        el.click()
                        time.sleep(1.5)
                        if self._2fa_fast_wait(
                            self._2fa_fast_is_settings_screen,
                            timeout=2,
                            label="Settings and activity (description)",
                        ):
                            return True
                except Exception:
                    continue
            self._log("   🔄 2FA: пробую двойной тап...")
            try:
                self.device.click(x, y)
                time.sleep(0.4)
                self.device.click(x, y)
            except Exception:
                pass
            time.sleep(1.5)
            if self._2fa_fast_wait(
                self._2fa_fast_is_settings_screen,
                timeout=2,
                label="Settings and activity (двойной тап)",
            ):
                return True
            time.sleep(0.5)
        self._log("   ❌ 2FA: не удалось открыть меню")
        return False

    def _2fa_fast_click_menu_item(self, text, timeout=10, label=None):
        if label is None:
            label = text
        self._log(f"   🔍 2FA: ищу «{label}»...")
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if self.click_by_text([text], max_attempts=1, quiet=True, scroll=True):
                time.sleep(1.5)
                return True
            try:
                self.device.swipe(
                    self._screen_w // 2,
                    int(self._screen_h * 0.6),
                    self._screen_w // 2,
                    int(self._screen_h * 0.3),
                    0.2,
                )
            except Exception:
                pass
            time.sleep(0.5)
        self._log(f"   ❌ 2FA: не найдено «{label}»")
        return False

    def _2fa_fast_select_account(self):
        self._log("   🔍 2FA: выбираю аккаунт...")
        if self.username and self.click_by_text([self.username], max_attempts=2, quiet=True):
            self._log(f"   ✅ 2FA: выбран {self.username}")
            time.sleep(1.5)
            return True
        if self.click_by_text(["Instagram"], max_attempts=2, quiet=True):
            self._log("   ✅ 2FA: выбран аккаунт Instagram")
            time.sleep(1.5)
            return True
        self._log("   ❌ 2FA: не удалось выбрать аккаунт")
        return False

    def _2fa_fast_extract_secret(self, timeout=20):
        self._log("   🔑 2FA: получаю секретный ключ...")
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            try:
                text = self._get_screen_text_lower(force=True, max_age=0)
                match = re.search(
                    r"([A-Z0-9]{4}\s+[A-Z0-9]{4}\s+[A-Z0-9]{4}\s+[A-Z0-9]{4}\s+[A-Z0-9]{4}\s+[A-Z0-9]{4}\s+[A-Z0-9]{4}\s+[A-Z0-9]{4})",
                    (text or "").upper(),
                )
                if match:
                    secret = match.group(1).replace(" ", "")
                    self._log(f"   ✅ 2FA: ключ найден: {secret[:8]}...")
                    return secret
            except Exception:
                pass
            time.sleep(0.5)
        self._log("   ❌ 2FA: секретный ключ не найден")
        return None

    def _2fa_fast_enter_totp(self, code):
        self._log(f"   ✏️ 2FA: ввожу TOTP: {code}")
        field = self._2fa_fast_wait_for_edit_text(timeout=10, label="поле ввода кода")
        if field:
            try:
                field.click()
                time.sleep(0.5)
                try:
                    field.clear_text()
                except Exception:
                    pass
                time.sleep(0.3)
                field.set_text(str(code))
                time.sleep(0.5)
                self._log("   ✅ 2FA: код введён")
                return True
            except Exception as e:
                self._log(f"   ⚠️ 2FA: ошибка ввода: {e}, пробую через send_keys...")
                try:
                    field.click()
                    time.sleep(0.3)
                    self.device.send_keys(str(code))
                    time.sleep(0.3)
                    self._log("   ✅ 2FA: код введён через send_keys")
                    return True
                except Exception:
                    pass
        try:
            focused = self.device(focused=True)
            if focused.exists:
                focused.set_text(str(code))
                time.sleep(0.3)
                self._log("   ✅ 2FA: код введён через focused")
                return True
        except Exception:
            pass
        self._log("   ❌ 2FA: не удалось ввести код")
        return False

    _2FA_SETTINGS_MARKERS = (
        "Settings and activity",
        "settings and activity",
        "Settings and",
        "settings and",
        "Настройки и действия",
        "Accounts Center",
        "Accounts center",
        "accounts center",
        "Центр аккаунтов",
        "Your account",
        "your account",
        "How you use Instagram",
        "how you use instagram",
        "How you use",
        "how you use",
        "Saved",
        "saved",
        "personal details",
        "connected experiences",
    )

    _2FA_ACCOUNTS_CENTER_MARKERS = (
        "Accounts Center",
        "Accounts center",
        "accounts center",
        "Центр аккаунтов",
        "Profiles and personal details",
        "profiles and personal details",
        "Password and security",
        "password and security",
        "Meta",
        "Manage your connected experiences",
    )

    _2FA_PASSWORD_SECURITY_MARKERS = (
        "Password and security",
        "Пароль и безопасность",
        "Two-factor authentication",
        "Login & recovery",
        "Login and recovery",
        "Change password",
    )

    def _fast_click_2fa(self, variants, label="", log=True):
        """Клик для 2FA — отдельный лог, не Post-reg."""
        if isinstance(variants, str):
            variants = [variants]
        for text in variants:
            for spec in (
                {"text": text},
                {"textContains": text},
                {"description": text},
                {"descriptionContains": text},
            ):
                try:
                    el = self.device(**spec)
                    if el.exists:
                        if log and label:
                            self._log(f"   🤖 [2FA] «{label or text}»")
                        el.click()
                        self._invalidate_screen_cache()
                        return True
                except Exception:
                    continue
        return False

    def _2fa_find_on_screen(self, *needles):
        needles = [n for n in needles if n]
        if not needles:
            return False
        if self._ui_has_any_light(*needles):
            return True
        return self._ui_has_text(*needles)

    def _is_settings_activity_screen(self):
        if self._2fa_find_on_screen(*self._2FA_SETTINGS_MARKERS):
            return True
        # Один лёгкий dump только если u2 не увидел (разбитый текст на Samsung)
        try:
            blob = self._get_screen_text_lower(force=True, max_age=0)
            if blob and any(n.lower() in blob for n in (
                "settings and activity",
                "accounts center",
                "how you use instagram",
                "your account",
                "настройки и действия",
                "центр аккаунтов",
            )):
                return True
        except Exception:
            pass
        return False

    def _is_accounts_center_screen(self):
        if self._2fa_find_on_screen(*self._2FA_ACCOUNTS_CENTER_MARKERS):
            return True
        try:
            blob = self._get_screen_text_lower(force=True, max_age=0)
            if blob and any(n.lower() in blob for n in (
                "accounts center",
                "password and security",
                "profiles and personal details",
                "центр аккаунтов",
            )):
                return True
        except Exception:
            pass
        return False

    def _is_password_security_screen(self):
        if self._2fa_find_on_screen(*self._2FA_PASSWORD_SECURITY_MARKERS):
            return True
        try:
            blob = self._get_screen_text_lower(force=True, max_age=0)
            if blob and any(n.lower() in blob for n in (
                "password and security",
                "two-factor authentication",
                "login & recovery",
                "change password",
            )):
                return True
        except Exception:
            pass
        return False

    def _wait_screen(
        self,
        check_fn,
        timeout=REG_TRANSITION_TIMEOUT,
        poll=0.45,
        label="экран",
    ):
        """Ждёт появления экрана — polling, без слепых пауз."""
        if check_fn():
            return True
        start = time.monotonic()
        deadline = start + float(timeout)
        last_log = -10
        while time.monotonic() < deadline:
            sec = int(time.monotonic() - start)
            if check_fn():
                self._log(f"   ✅ 2FA: {label} ({sec} сек)")
                return True
            if sec - last_log >= 4:
                self._log(f"   ⏳ 2FA: жду «{label}»... ({sec}/{int(timeout)} сек)")
                last_log = sec
            time.sleep(poll)
        return check_fn()

    def _wait_any_light(self, needles, timeout=REG_TRANSITION_TIMEOUT, poll=0.45):
        """Быстро ждёт любой маркер (u2-only), без dump."""
        needles = [n for n in (needles or []) if n]
        if not needles:
            return True
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            if self._ui_has_any_light(*needles):
                return True
            time.sleep(poll)
        return False

    def _click_menu_item_wait(
        self,
        *variants,
        timeout=REG_TRANSITION_TIMEOUT,
        label="",
        next_check=None,
    ):
        title = label or (variants[0] if variants else "пункт меню")
        if self._fast_click_2fa(variants, label=title):
            if next_check is None or self._wait_screen(
                next_check, timeout=8, poll=0.4, label=f"после «{title}»"
            ):
                return True
        self._log(f"   🔍 2FA: ищу «{title}» (до {timeout} сек)...")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.click_by_text(
                list(variants), max_attempts=1, quiet=True, scroll=True
            ):
                if next_check is None or self._wait_screen(
                    next_check, timeout=8, poll=0.4, label=f"после «{title}»"
                ):
                    return True
            if self._fast_click_2fa(variants, label=title):
                if next_check is None or self._wait_screen(
                    next_check, timeout=8, poll=0.4, label=f"после «{title}»"
                ):
                    return True
            time.sleep(POLL_UI_ONLY)
        self._log(f"   ❌ 2FA: не найдено «{title}»")
        return False

    def _open_profile_tab(self, timeout=REG_TRANSITION_TIMEOUT):
        self._ensure_instagram_foreground_2fa("профиль")
        self._log("   📍 2FA: открываю профиль...")

        def _on_profile():
            return self._ui_has_any_light(
                "Edit profile",
                "Редактировать профиль",
                "Share profile",
                "Поделиться профилем",
                "posts",
                "публикаций",
            ) or self._ui_has_text("Edit profile", "Share profile")

        if _on_profile():
            return True

        deadline = time.monotonic() + timeout
        clicked = False
        while time.monotonic() < deadline:
            if _on_profile():
                self._log("   ✅ 2FA: профиль открыт")
                return True
            if not clicked:
                if self._fast_click_2fa(["Profile", "Профиль"], label="Profile"):
                    clicked = True
                else:
                    try:
                        self.device.click(
                            int(self._screen_w * 0.9), int(self._screen_h * 0.965)
                        )
                        clicked = True
                    except Exception:
                        pass
            time.sleep(0.45)
        self._log("   ❌ 2FA: не удалось открыть профиль")
        return _on_profile()

    def _open_profile_settings_menu(self, timeout=REG_TRANSITION_TIMEOUT):
        self._log("   📍 2FA: открываю меню (3 полоски)...")

        if self._is_settings_activity_screen():
            self._log("   ✅ 2FA: уже на Settings and activity")
            return True

        deadline = time.monotonic() + timeout
        menu_clicks = 0
        last_menu_click = 0.0

        while time.monotonic() < deadline:
            if self._is_settings_activity_screen():
                self._log("   ✅ 2FA: Settings and activity открыт")
                return True
            if self._is_accounts_center_screen():
                self._log("   ✅ 2FA: сразу открыли Accounts Center")
                return True

            now = time.monotonic()
            if menu_clicks < 3 and (now - last_menu_click) >= 1.2:
                clicked = False
                for desc in (
                    "Options",
                    "Profile options",
                    "Menu",
                    "Настройки",
                    "Параметры",
                ):
                    if self._fast_click_2fa([desc], label="меню"):
                        clicked = True
                        break
                if not clicked:
                    try:
                        self.device.click(
                            int(self._screen_w * 0.93), int(self._screen_h * 0.08)
                        )
                        clicked = True
                    except Exception:
                        pass
                if clicked:
                    menu_clicks += 1
                    last_menu_click = now
                    # После клика меню ждём коротко появление Settings/Accounts Center.
                    if self._wait_screen(
                        lambda: self._is_settings_activity_screen() or self._is_accounts_center_screen(),
                        timeout=5,
                        poll=0.35,
                        label="меню настроек",
                    ):
                        return True

            # Иногда экран уже в Settings, но текст разбит/частично недоступен.
            # Пробуем целевой клик по Accounts Center как практический индикатор.
            if self._fast_click_2fa(
                ["Accounts Center", "Accounts center", "Центр аккаунтов"],
                label="Accounts Center",
                log=False,
            ):
                if self._wait_screen(
                    self._is_accounts_center_screen,
                    timeout=6,
                    poll=0.35,
                    label="Accounts Center",
                ):
                    return True

            time.sleep(0.4)

        if self._is_settings_activity_screen():
            self._log("   ✅ 2FA: Settings and activity (таймаут-край)")
            return True

        self._log("   ❌ 2FA: меню настроек не открылось")
        return False

    def _select_2fa_account(self, timeout=REG_TRANSITION_TIMEOUT):
        uname = (self.username or "").strip()
        self._log(f"   📍 2FA: выбираю аккаунт {uname or '(Instagram)'}...")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if uname and self._fast_click_2fa([uname], label=uname):
                return True
            if uname and self.click_by_text([uname], max_attempts=1, quiet=True):
                return True
            if self._ui_has_any_light(
                "Help protect your account",
                "Authentication app",
                "Choose how to confirm",
            ):
                return True
            if self._fast_click_2fa(["Instagram"], label="Instagram"):
                return True
            time.sleep(POLL_UI_ONLY)
        self._log("   ❌ 2FA: аккаунт не выбран")
        return False

    def _select_authentication_app_and_next(self):
        self._log("   📍 2FA: Authentication app → Next...")
        self._fast_click_2fa(
            ["Authentication app", "Приложение аутентификации"],
            label="Authentication app",
            log=False,
        )
        time.sleep(0.25)
        if not self.click_by_text(
            ["Next", "Далее", "Continue", "Продолжить"],
            max_attempts=3,
            pause_after=0.2,
        ):
            self._log("   ❌ 2FA: Next после Authentication app не нажат")
            return False
        # реактивно ждём появления страницы с ключом/QR
        self._wait_any_light(
            ["Copy key", "View barcode/QR code", "Enter code", "Instructions for setup"],
            timeout=8,
            poll=0.4,
        )
        return True

    def _normalize_2fa_secret(self, raw):
        if not raw:
            return ""
        cleaned = re.sub(r"[^A-Za-z2-7]", "", str(raw).upper())
        return cleaned if len(cleaned) >= 16 else ""

    def _extract_2fa_secret_key(self, timeout=REG_TRANSITION_TIMEOUT):
        self._log("   🔑 2FA: получаю секретный ключ...")
        deadline = time.monotonic() + timeout

        # Дадим UI 1-2 секунды дорисовать ключ/кнопки (без фикс-паузы на 20с).
        self._wait_any_light(
            ["Copy key", "View barcode/QR code", "Instructions for setup", "Enter code"],
            timeout=6,
            poll=0.4,
        )

        if self.click_by_text(
            ["Copy key", "Скопировать ключ", "Copy"],
            max_attempts=2,
            quiet=True,
        ):
            time.sleep(0.35)
            try:
                clip = self.device.clipboard
                secret = self._normalize_2fa_secret(clip)
                if secret:
                    self._log("   ✅ 2FA: ключ из буфера обмена")
                    return secret
            except Exception:
                pass

        while time.monotonic() < deadline:
            try:
                for el in self.device(className="android.widget.TextView"):
                    try:
                        if not el.exists:
                            continue
                        text = (el.info.get("text") or "").strip()
                        if not text:
                            continue
                        match = _2FA_SECRET_RE.search(text.upper())
                        if match:
                            secret = self._normalize_2fa_secret(match.group(0))
                            if secret:
                                self._log("   ✅ 2FA: ключ найден на экране")
                                return secret
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(POLL_UI_ONLY)

        snap = self._get_screen_text_lower(force=True)
        match = _2FA_SECRET_RE.search((snap or "").upper())
        if match:
            secret = self._normalize_2fa_secret(match.group(0))
            if secret:
                self._log("   ✅ 2FA: ключ найден (диагностический dump)")
                return secret

        self._log("   ❌ 2FA: секретный ключ не найден")
        return ""

    def _enter_2fa_totp_code(self, code):
        self._log(f"   ✏️ 2FA: ввожу TOTP {code}...")
        for spec in (
            {"className": "android.widget.EditText", "textContains": "Enter code"},
            {"className": "android.widget.EditText", "textContains": "Введите код"},
            {"className": "android.widget.EditText"},
            {"focused": True, "className": "android.widget.EditText"},
        ):
            el = self._try_selector(**spec)
            if el and el.exists:
                if self.input_into_field(el, code, label="2FA TOTP", clear_first=True):
                    return True
        self._log("   ❌ 2FA: поле ввода TOTP не найдено")
        return False

    def _wait_for_2fa_totp_input(self, timeout=REG_TRANSITION_TIMEOUT):
        """Ждёт экран ввода 6-значного TOTP после Enter code."""
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            el = self._try_selector(
                className="android.widget.EditText", textContains="Enter code"
            )
            if el and el.exists:
                return True
            el = self._try_selector(
                className="android.widget.EditText", textContains="Введите код"
            )
            if el and el.exists:
                return True
            el = self._try_selector(className="android.widget.EditText")
            if el and el.exists:
                return True
            if self._ui_has_any_light("Enter the 6-digit code", "Enter code", "Введите код"):
                return True
            time.sleep(POLL_UI_ONLY)
        return False

    def _open_enter_code_screen(self):
        """
        Тост после Copy key может перекрыть кнопку Enter code.
        Делаем до 3 попыток с короткими паузами и проверкой поля.
        """
        self._log("   🔘 2FA: открываю экран Enter code...")
        for attempt in range(1, 4):
            if self.click_by_text(
                ["Enter code", "Ввести код"],
                max_attempts=1,
                quiet=True,
                pause_after=0.2,
            ):
                if self._wait_for_2fa_totp_input(timeout=6):
                    return True
            # тост "Copied" часто висит снизу ~1с и перекрывает кнопку
            time.sleep(1.1 if attempt == 1 else 0.7)
        self._log("   ❌ 2FA: Enter code не открыл экран ввода кода")
        return False

    def _wait_2fa_enabled(self, timeout=25):
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if self._ui_has_any_light(
                "Done",
                "Готово",
                "Two-factor authentication is on",
                "двухфакторная аутентификация включена",
                "is on",
            ):
                return True
            time.sleep(1)
        return True

    def setup_2fa_and_get_otp(self):
        """2FA fast: логика как в рабочем ter.py (пользователь)."""
        if pyotp is None:
            self._log("   ❌ 2FA: модуль pyotp не установлен (pip install pyotp)")
            return False
        if not self.username or not self.password:
            self._log("   ❌ 2FA: нет username/password")
            return False

        self._log("\n" + "=" * 60)
        self._log("🔐 ШАГ 2FA: настройка TOTP")
        self._log("=" * 60)

        # Быстрый вывод Instagram вперёд без тяжёлых проверок.
        try:
            if not self._is_instagram_foreground():
                self.device.app_start(INSTAGRAM_PKG, wait=True, stop=False)
                time.sleep(2)
        except Exception:
            pass

        # 1) Профиль
        if not self._2fa_fast_open_profile_tab():
            return False
        # 2) Меню (3 полоски) → Settings and activity
        if not self._2fa_fast_open_menu():
            self._log("   ❌ 2FA: меню настроек не открылось")
            return False

        # 3) Accounts Center
        if not self._2fa_fast_click_menu_item("Accounts Center", label="Accounts Center"):
            return False
        # 4) Password and security
        if not self._2fa_fast_click_menu_item("Password and security", label="Password and security"):
            return False
        # 5) Two-factor authentication
        if not self._2fa_fast_click_menu_item("Two-factor authentication", label="Two-factor authentication"):
            return False

        # 6) Выбор аккаунта
        if not self._2fa_fast_select_account():
            return False

        # 7) Authentication app → Next
        self._log("   🔍 2FA: выбираю Authentication app...")
        try:
            if self.click_by_text(["Authentication app", "Приложение аутентификации"], max_attempts=2, quiet=True):
                self._log("   ✅ 2FA: выбрано Authentication app")
        except Exception:
            pass
        time.sleep(1.5)
        if not self.click_by_text(["Next", "Далее"], max_attempts=3):
            self._log("   ❌ 2FA: Next не нажата")
            return False
        time.sleep(2.5)

        # 8) Секрет
        secret = self._2fa_fast_extract_secret()
        if not secret:
            return False

        # 9) Экран ввода кода
        self._log("   🔍 2FA: открываю экран ввода кода...")
        try:
            if self.click_by_text(["Enter code", "Ввести код"], max_attempts=3, scroll=True):
                self._log("   ✅ 2FA: экран ввода кода открыт")
        except Exception:
            pass
        time.sleep(2.0)

        # 10) Генерация и ввод TOTP
        try:
            totp_code = pyotp.TOTP(secret).now()
            self._log(f"   🔢 2FA: TOTP: {totp_code}")
        except Exception as exc:
            self._log(f"   ❌ 2FA: ошибка TOTP: {exc}")
            return False

        if not self._2fa_fast_enter_totp(totp_code):
            return False

        # 11) Next после TOTP
        time.sleep(1.0)
        if not self.click_by_text(["Next", "Далее"], max_attempts=4):
            self._log("   ❌ 2FA: Next после TOTP не нажата")
            return False

        # 12) Ждём подтверждение 2FA
        self._log("   ⏳ 2FA: жду экран подтверждения (до 20 сек)...")
        if not self._2fa_fast_wait(self._2fa_fast_is_success_screen, timeout=20, poll=0.8, label="2FA success"):
            self._log("   ⚠️ 2FA: экран подтверждения не появился, пробую перепроверить...")
            time.sleep(3)
            if not self._2fa_fast_is_success_screen():
                self._log("   ❌ 2FA: не подтверждена — настройка не удалась")
                return False

        self._log("   ✅ 2FA: успешно настроена (экран подтверждения найден)")
        time.sleep(1.5)
        try:
            if self.click_by_text(["Done", "Готово"], max_attempts=3):
                self._log("   ✅ 2FA: Done нажата")
            else:
                self._log("   ⚠️ 2FA: Done не нажата (но 2FA настроена)")
        except Exception:
            pass

        self.otp_key = secret
        self._log(f"   ✅ 2FA настроена, ключ сохранён ({secret[:8]}...)")
        return True

    def save_registered_account(self):
        """Сохранить username:pass:2faotp в registered_accounts.txt."""
        if not self.username or not self.password:
            self._log("   ❌ Сохранение: нет username/password")
            return False
        if not self.otp_key:
            self._log("   ❌ Сохранение: нет otp_key (2FA не настроена)")
            return False

        line = f"{self.username}:{self.password}:{self.otp_key}"
        try:
            with REGISTERED_ACCOUNTS_FILE.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            self._log(f"   💾 Аккаунт сохранён: {REGISTERED_ACCOUNTS_FILE.name}")
            self._log(f"   📝 {self.username}:{self.password}:{self.otp_key[:8]}...")
            return True
        except Exception as exc:
            self._log(f"   ❌ Ошибка сохранения аккаунта: {exc}")
            return False

    def _post_code_registration_flow(self):
        """Продолжение после успешного ввода кода."""
        if not self._is_instagram_usable_without_restart():
            self._ensure_instagram_foreground("post-code flow", gentle=True)
        self._log("\n" + "=" * 60)
        self._log("📋 ПРОДОЛЖЕНИЕ РЕГИСТРАЦИИ ПОСЛЕ КОДА")
        self._log("=" * 60)

        screen = self._detect_registration_screen_light()
        self._log(f"   📍 Экран: {screen} (начало post-code)")
        onboarding_done = getattr(self, "_onboarding_one_page_done", False)

        if onboarding_done:
            self._log("   ⏭ One-page: пароль и дата уже введены — к имени/username")
        elif screen in ("birthday", "full_name", "username", "agree"):
            self._log(f"   ⏭ Пароль уже пройден, продолжаю с «{screen}»")
        else:
            # --- галочка «запомнить» (на экране пароля) ---
            if screen == "password" or self._text_has_any(["Запомнить", "Remember"]):
                self.uncheck_remember_login()

            # --- пароль ---
            self._log("\n🔐 Шаг: пароль")
            self.password = self.generate_password()
            self._log(f"   🔑 Сгенерирован пароль: {self.password}")

            pwd_field = self._wait_for_password_field()
            if not pwd_field:
                scr = self._detect_registration_screen_light()
                self._log(f"   📍 Экран: {scr} (не нашли пароль)")
                return False

            if not self.paste_into_field(
                pwd_field, self.password, label="пароль", clear_first=True
            ):
                return False

            if not self.click_next_repeated(max_attempts=3, extra_clicks=1, reason="после пароля"):
                return False

            chk = self._check_suspended_or_verification()
            if chk["suspended"] or chk["verification"]:
                self._log("   ❌ Бан/вериф после пароля")
                if chk["verification"]:
                    self.verification_detected = True
                return False

            screen = self._wait_after_password_next()
            if not screen or screen == "password" or self._has_password_screen_light():
                scr = screen or self._detect_registration_screen_light()
                self._log(f"   📍 Экран: {scr} (после пароля)")
                self._log("   ❌ Застряли на экране пароля")
                return False
            self._log(f"   📍 Экран: {screen} (после пароля)")

        if not onboarding_done:
            if screen == "password" or self._has_password_screen_light():
                screen = self._wait_after_password_next()
                if not screen or screen == "password" or self._has_password_screen_light():
                    self._log("   ❌ Застряли на экране пароля")
                    return False

            # --- дата рождения ---
            self._log("\n📅 Шаг: дата рождения")
            if not self._birthday_screen_ready():
                if not self._screen_has_any_text(
                    list(self._UI_BIRTHDAY_MARKERS),
                    label="экран даты рождения",
                ):
                    self._log_current_screen("не дождались даты")
                    return False
            if not self.set_birthday_via_picker():
                return False

            if not self.click_next_once(reason="после даты"):
                return False

            next_screen = self._wait_after_birthday_next()
            if not next_screen or next_screen == "birthday":
                self._log("   ❌ Застряли на экране даты рождения")
                return False

            chk = self._check_suspended_or_verification()
            if chk["suspended"] or chk["verification"]:
                self._log("   ❌ Бан/вериф после даты")
                if chk["verification"]:
                    self.verification_detected = True
                return False

        if not self._handle_name_username_after_birthday():
            return False

        # --- Принимаю (I Agree) ---
        self._log("\n✅ Шаг: «Принимаю»")
        agree_found = False
        start = time.time()
        while time.time() - start < ACTION_TIMEOUT:
            chk = self._check_suspended_or_verification()
            if chk["suspended"]:
                self._log("   ❌ Suspended перед «Принимаю»")
                return False
            if chk["verification"]:
                self._log("   ⚠️ Верификация перед «Принимаю»")
                self.verification_detected = True
                return False

            reg_err = self._check_registration_error()
            if reg_err:
                self._log(f"   ❌ Ошибка регистрации перед «Принимаю»: {reg_err}")
                return False

            if self._ui_has_text(*self._UI_AGREE_MARKERS):
                agree_found = True
                break

            elapsed = int(time.time() - start)
            if elapsed > 0 and elapsed % 5 == 0:
                screen = self._detect_registration_screen_light()
                self._log(
                    f"   ⏳ Жду «Принимаю»... "
                    f"({elapsed}/{ACTION_TIMEOUT} сек, экран «{screen}»)"
                )
            time.sleep(POLL_EMAIL_TRANSITION)

        if not agree_found:
            self._log(f"   ❌ Кнопка «Принимаю» не появилась за {ACTION_TIMEOUT} сек")
            self._log_current_screen("нет «Принимаю»")
            return False

        self._log("   ✅ Кнопка «Принимаю» на экране")
        time.sleep(WAIT_SHORT)

        if not self.click_by_text(
            ["Принимаю", "I agree", "Agree", "Согласен", "Accept"],
            max_attempts=4,
        ):
            return False

        if not self._wait_post_registration_after_agree(timeout=POST_REG_TIMEOUT):
            return False

        self._mark_account_registered("post-reg onboarding", 0)

        if not self.setup_2fa_and_get_otp():
            self._log("   ❌ 2FA не настроена — аккаунт не сохраняем")
            return False

        if not self.save_registered_account():
            return False

        self._log("\n" + "=" * 60)
        self._log("✅ РЕГИСТРАЦИЯ + 2FA УСПЕШНО ЗАВЕРШЕНА")
        self._log(f"📧 Email: {self.email}")
        self._log(f"👤 Username: {self.username}")
        self._log(f"🔑 Password: {self.password}")
        self._log(f"🔐 2FA OTP: {self.otp_key}")
        self._log(f"📝 Name: {self.full_name}")
        self._log("=" * 60)

        # Перед следующим аккаунтом: сначала закрыть/удалить Instagram (Root — в начале след. регистрации)
        self._log("\n🧹 Очистка после успеха: закрываю и удаляю Instagram...")
        try:
            self.force_close_instagram()
        except Exception:
            pass
        try:
            self.uninstall_instagram()
        except Exception:
            pass
        return True

    # ========== ОСНОВНАЯ РЕГИСТРАЦИЯ ==========

    def register(self):
        self._log("\n" + "=" * 60)
        self._log("🚀 НАЧАЛО РЕГИСТРАЦИИ")
        self._log("=" * 60)
        return self._register_locked()

    def _register_locked(self):
        max_attempts = self.max_retries
        previous_code_sent = False
        self.account_registered = False
        self.verification_detected = False
        self.code_submitted_to_instagram = False
        self._onboarding_one_page_done = False
        self._signup_form_design = None

        if self._should_stop():
            self._log("⏹ Остановка до старта регистрации")
            return False

        self._log("\n📧 Шаг 0: получение email (в начале запуска)...")
        email, activation_id = self.get_email(site="instagram.com")
        if not email or not activation_id:
            self._log("❌ Не удалось получить email")
            return False

        saved_email = email
        saved_activation_id = activation_id
        self.email = email
        self.activation_id = activation_id
        self.last_identifier = email
        self.rate_limit_hits_on_email = 0
        self._log(f"✅ Email получен: {email}")

        for attempt in range(1, max_attempts + 1):
            if self._should_stop():
                self._log("⏹ Остановка регистрации по запросу")
                return False

            if self.account_registered:
                self._log("   ✅ Аккаунт уже зарегистрирован — повторная переустановка не нужна")
                return True

            self._log(f"\n{'=' * 40}")
            self._log(f"ПОПЫТКА {attempt}/{max_attempts}")
            self._log(f"{'=' * 40}")

            needs_full_reinstall = self._needs_full_reinstall(attempt)

            self.email = saved_email
            self.activation_id = saved_activation_id
            if self.rate_limit_hits_on_email > 0:
                self._log(f"\n📧 Ретрай с той же почтой (rate-limit): {saved_email}")
            elif attempt == 1:
                self._log(f"\n📧 Используем почту: {saved_email}")
            else:
                self._log(f"\n📧 Почта: {saved_email} (попытка {attempt})")

            if self.code_submitted_to_instagram or self.account_registered:
                self._log(
                    "   ⛔ Код/аккаунт уже в процессе — переустановка Instagram запрещена"
                )
                return False

            resume_from = None
            if (
                not needs_full_reinstall
                and attempt > 1
                and self.code_submitted_to_instagram
            ):
                resume_from = self._get_registration_resume_point()
                if resume_from:
                    self._log(
                        f"\n♻️ Код уже введён, экран «{resume_from}» — "
                        "переустановка пропущена"
                    )
                    self._ensure_instagram_foreground(
                        "продолжение после кода", gentle=True
                    )

            if resume_from in (
                "password",
                "birthday",
                "full_name",
                "username",
                "name_username",
                "agree",
            ):
                self._log(
                    f"\n⏭ Продолжаю с экрана «{resume_from}» (после кода)..."
                )
                if not self._post_code_registration_flow():
                    return False
                return True

            if resume_from is None:
                if needs_full_reinstall:
                    self._log(
                        "\n🔄 Полный ретрай: удаление Instagram → Root → установка..."
                    )
                self._log("\n🔄 Шаг 1: подготовка устройства...")
                if not self._prepare_device_for_registration():
                    self._log(
                        "   ❌ Подготовка не удалась "
                        "(удаление Instagram"
                        + (" или Root" if self.root_enabled else "")
                        + ")"
                    )
                    if self._should_retry_registration(attempt, max_attempts):
                        time.sleep(0.5)
                        continue
                    return False

                self._log("\n📥 Установка Instagram...")
                if not self._install_instagram():
                    self._log("   ❌ Установка Instagram не удалась")
                    if self._should_retry_registration(attempt, max_attempts):
                        time.sleep(0.5)
                        continue
                    return False

                self._log("\n📱 Шаг 2: запуск Instagram...")
                self.open_instagram()

                if not self._handle_instagram_entry():
                    if self._should_retry_registration(attempt, max_attempts):
                        time.sleep(0.5)
                        continue
                    return False

                self._log("\n🔍 Шаг 3: регистрация по email...")
                if not self._go_to_email_signup():
                    if self._should_retry_registration(attempt, max_attempts):
                        time.sleep(0.5)
                        continue
                    return False

            if resume_from != "code":
                if (
                    self._signup_form_design == "one_page"
                    or self._entry_path == "onboarding_one_page"
                ):
                    self._log("\n✏️ Шаг 4: one-page onboarding (email + пароль + дата)...")
                    if not self._complete_onboarding_one_page_signup():
                        if self._should_retry_registration(attempt, max_attempts):
                            time.sleep(0.5)
                            continue
                        return False
                    self._log("\n🔘 Шаг 5: жду экран кода после Next...")
                    email_result = self._wait_after_email_next(timeout=ACTION_TIMEOUT)
                else:
                    self._log("\n✏️ Шаг 4: ввод email...")
                    if (
                        not self._entry_path
                        and resume_from is None
                        and self._ui_has_text(
                            "Log in", "Log In", "Войти", "Forgot password"
                        )
                    ):
                        self._log("   ❌ Ошибка: экран входа — Create new account не отработан")
                        if self._should_retry_registration(attempt, max_attempts):
                            time.sleep(0.5)
                            continue
                        return False
                    if not self._ui_has_text(*self._UI_SIGNUP_EMAIL_SCREEN) and not (
                        self._entry_path
                        and self._ui_has_text("Email")
                        and self._has_plain_edit_field()
                    ):
                        self._log("   ⏳ Жду экран регистрации email...")
                        if not self._screen_has_any_text(
                            list(self._UI_SIGNUP_EMAIL_SCREEN) + ["Email"],
                            timeout=ACTION_TIMEOUT,
                            label="signup email",
                        ):
                            if self._should_retry_registration(attempt, max_attempts):
                                continue
                            return False
                    if not self.input_text(None, self.email, label="поле email"):
                        if self._should_retry_registration(attempt, max_attempts):
                            time.sleep(0.5)
                            continue
                        return False

                    self._log("\n🔘 Шаг 5: Next после email...")
                    time.sleep(WAIT_SHORT)
                    if not self.click_by_text(["Next", "Далее"], max_attempts=3):
                        if self._should_retry_registration(attempt, max_attempts):
                            time.sleep(0.5)
                            continue
                        return False

                    email_result = self._wait_after_email_next(timeout=ACTION_TIMEOUT)

                if email_result == "rate_limit":
                    self._handle_rate_limit_retry(attempt, max_attempts)
                    if self._should_retry_registration(attempt, max_attempts):
                        time.sleep(0.5)
                        continue
                    return False
                if email_result != "ok":
                    self._log("   ❌ После Next (email) нет кода и нет явной ошибки")
                    if self._should_retry_registration(attempt, max_attempts):
                        time.sleep(0.5)
                        continue
                    return False
            else:
                self._log("\n♻️ Шаг 4–5 пропущены — уже на экране кода")

            self._trace_step("Шаг 6", "wait_for_code_input")
            self._log("\n🔑 Шаг 6: поле кода...")
            code_input = self.wait_for_code_input(timeout=ACTION_TIMEOUT)
            if getattr(self, "_code_wait_result", None) == "rate_limit":
                self._handle_rate_limit_retry(attempt, max_attempts)
                if self._should_retry_registration(attempt, max_attempts):
                    time.sleep(0.5)
                    continue
                return False
            if not code_input:
                if self._should_retry_registration(attempt, max_attempts):
                    previous_code_sent = False
                    time.sleep(0.5)
                    continue
                return False

            self._trace_step("Шаг 6b", "get_confirmation_code — device заблокирован")
            self._log("\n📨 Шаг 6b: получение кода из почты...")
            self.code_sent = True
            previous_code_sent = True

            self._begin_code_api_wait()
            try:
                code = self.get_confirmation_code(max_attempts=60)
            finally:
                self._end_code_api_wait()

            if not code:
                if self._should_retry_registration(attempt, max_attempts):
                    time.sleep(0.5)
                    continue
                return False

            if self._has_code_screen_markers() and self._is_instagram_running():
                self._log(
                    "   ℹ️ Экран кода на месте после API — Instagram не трогаем"
                )
            else:
                self._ensure_instagram_foreground(
                    "после получения кода", gentle=True
                )

            code_input = self._refind_code_input_field(allow_focus_tap=False)
            if not code_input:
                code_input = self._refind_code_input_field(allow_focus_tap=True)
            if not code_input:
                self._log("   ⚠️ Поле кода не найдено сразу — короткий повтор...")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not code_input:
                    time.sleep(POLL_FAST)
                    code_input = self._refind_code_input_field(allow_focus_tap=False)

            if str(code) in self.blocked_confirmation_codes:
                self._log(f"   ⚠️ Код {code} в blocklist, жду новый...")
                fresh = None
                deadline = time.time() + ACTION_TIMEOUT
                while time.time() < deadline:
                    cand = self.get_confirmation_code(max_attempts=1, delay=1)
                    if cand and str(cand) not in self.blocked_confirmation_codes:
                        fresh = cand
                        break
                    time.sleep(0.5)
                if not fresh:
                    if self._should_retry_registration(attempt, max_attempts):
                        continue
                    return False
                code = fresh

            self.last_submitted_confirmation_code = str(code)

            self._trace_step("Шаг 7", f"input_confirmation_code code={code}")
            self._log(f"\n✏️ Шаг 7: ввод кода {code}")
            if not self.input_confirmation_code(code_input, code):
                if self._should_retry_registration(attempt, max_attempts):
                    time.sleep(0.5)
                    continue
                return False

            self.code_submitted_to_instagram = True
            self._log("   🔒 Код отправлен — повтор на этой почте больше не будет")

            self._log("\n⏳ Шаг 8: жду авто-переход после кода...")
            if not self._wait_transition_after_code():
                return False

            if not self._post_code_registration_flow():
                return False

            return True

        self._log("\n❌ Все попытки регистрации исчерпаны")
        return False

    def cleanup_after_failure(self):
        """Закрыть Instagram и удалить приложение после неуспешной регистрации."""
        if self.account_registered:
            self._log(
                "🧹 Аккаунт уже создан — закрываю Instagram без удаления"
            )
            try:
                self.force_close_instagram()
            except Exception:
                pass
            return

        self._log("🧹 Очистка после неуспеха: закрываю и удаляю Instagram...")
        try:
            self.force_close_instagram()
        except Exception:
            pass
        try:
            self.uninstall_instagram()
        except Exception:
            pass

    def cleanup(self, uninstall=False):
        try:
            self.force_close_instagram()
        except Exception:
            pass
        if uninstall:
            try:
                self.uninstall_instagram()
            except Exception:
                pass


if __name__ == "__main__":
    print("Запускайте через app_gui.py")
