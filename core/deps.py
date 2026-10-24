"""تثبيت المكتبات الثقيلة (Instagram الشخصي، يوتيوب) في الخلفية بصبر.

مهلة التغليف 120 ثانية لا تكفي مع إنترنت بطيء، لذلك تُثبَّت هنا بعد تشغيل التطبيق:
بدون مهلة إجمالية، تنتظر عودة الشبكة، وتعيد المحاولة بتأخير تصاعدي، والملفات المكتملة تُحفظ في الكاش.
"""
import importlib
import importlib.util
import os
import shutil
import subprocess
import sys
import threading
import time
import config
from core import database as db
from core import net

PKG_DIR = os.path.join(config.DATA_DIR, "pylibs")
CACHE_DIR = os.path.join(config.DATA_DIR, "pipcache")
OPTIONAL = [
    ("instagrapi", "instagrapi", "Instagram الشخصي"),
    ("googleapiclient", "google-api-python-client", "يوتيوب"),
    ("google_auth_oauthlib", "google-auth-oauthlib", "يوتيوب"),
]
WAITS = [10, 30, 60, 120, 300]
MAX_ATTEMPTS = 40
UPGRADE_EVERY = 7 * 86400
PERMANENT = ("No matching distribution", "Could not find a version", "externally-managed")

_lock = threading.Lock()
_state = {"state": "idle", "attempt": 0, "error": "", "missing": [], "upgrade": False}


def activate():
    os.makedirs(PKG_DIR, mode=0o700, exist_ok=True)
    if PKG_DIR not in sys.path:
        sys.path.append(PKG_DIR)
    importlib.invalidate_caches()


def missing():
    return [spec for mod, spec, _ in OPTIONAL if importlib.util.find_spec(mod) is None]


def status():
    with _lock:
        out = dict(_state)
    out["missing"] = missing()
    out["features"] = sorted({label for mod, _, label in OPTIONAL if importlib.util.find_spec(mod) is None})
    return out


def _set(**kw):
    with _lock:
        _state.update(kw)


def _pip(args):
    """يشغّل pip كعملية منفصلة، وإن لم يتوفر مفسّر بايثون فداخل العملية نفسها."""
    exe = sys.executable
    if exe and os.path.exists(exe):
        p = subprocess.run([exe, "-m", "pip"] + args, capture_output=True, text=True)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    from pip._internal.cli.main import main as pip_main
    return pip_main(args), ""


def _install_once(specs):
    stage = PKG_DIR + ".stage"
    shutil.rmtree(stage, ignore_errors=True)
    code, out = _pip(["install", "--upgrade", "--target", stage, "--cache-dir", CACHE_DIR, "--prefer-binary",
                      "--only-binary=:all:", "--retries", "10", "--timeout", "60",
                      "--disable-pip-version-check", "--no-input"] + specs)
    if code != 0:
        shutil.rmtree(stage, ignore_errors=True)
        return False, out[-400:]
    old = PKG_DIR + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(PKG_DIR):
        os.rename(PKG_DIR, old)
    os.rename(stage, PKG_DIR)
    shutil.rmtree(old, ignore_errors=True)
    return True, ""


def _run(specs, upgrade):
    attempt = 0
    while attempt < MAX_ATTEMPTS:
        if not net.online():
            _set(state="waiting_network", upgrade=upgrade)
            time.sleep(30)
            continue
        _set(state="installing", attempt=attempt + 1, upgrade=upgrade)
        try:
            ok, err = _install_once(specs)
        except Exception as e:
            ok, err = False, str(e)[-400:]
        if ok:
            activate()
            db.set_setting("deps_checked_at", str(time.time()))
            _set(state="done", error="")
            return
        attempt += 1
        _set(error=err)
        if any(p in err for p in PERMANENT) and attempt >= 2:
            break
        _set(state="retrying")
        time.sleep(WAITS[min(attempt - 1, len(WAITS) - 1)])
    _set(state="failed")


def start(upgrade=False):
    """يبدأ التثبيت (أو التحديث) في خيط خلفي إن لم يكن يعمل. يعيد False إن كان يعمل أو لا حاجة."""
    specs = [spec for _, spec, _ in OPTIONAL] if upgrade else missing()
    with _lock:
        if _state["state"] in ("installing", "retrying", "waiting_network") or not specs:
            return False
        _state.update(state="installing", attempt=0, error="")
    threading.Thread(target=_run, args=(specs, upgrade), daemon=True).start()
    return True


def start_background():
    """عند الإقلاع: يثبّت الناقص، وإن اكتمل فيحدّثه كل أسبوع."""
    if missing():
        return start()
    try:
        last = float(db.get_setting("deps_checked_at", "0") or 0)
    except ValueError:
        last = 0
    if not last:
        db.set_setting("deps_checked_at", str(time.time()))
        return False
    if time.time() - last > UPGRADE_EVERY:
        return start(upgrade=True)
    return False
