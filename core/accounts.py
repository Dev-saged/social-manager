"""تشفير بيانات دخول الحسابات: مفتاح Fernet يُولَّد مرة واحدة على الجهاز."""
import os
import json
from cryptography.fernet import Fernet
import config

_KEY = None


def _key():
    global _KEY
    if _KEY is None:
        if os.path.exists(config.SECRET_KEY_PATH):
            with open(config.SECRET_KEY_PATH, "rb") as f:
                _KEY = f.read()
        else:
            _KEY = Fernet.generate_key()
            with open(config.SECRET_KEY_PATH, "wb") as f:
                f.write(_KEY)
            try:
                os.chmod(config.SECRET_KEY_PATH, 0o600)  # لا يقرأه إلا مالك الجهاز
            except OSError:
                pass
    return _KEY


def encrypt(credentials: dict) -> str:
    f = Fernet(_key())
    return f.encrypt(json.dumps(credentials, ensure_ascii=False).encode()).decode()


def decrypt(token: str) -> dict:
    f = Fernet(_key())
    return json.loads(f.decrypt(token.encode()).decode())
