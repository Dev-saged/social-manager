"""الإعدادات العامة للمشروع. تُقرأ من ملف .env إن وُجد."""
import os
from dotenv import load_dotenv

load_dotenv()

APP_VERSION = "1.3.0"

# ---- Oxion (المزوّد الأساسي للذكاء، والبديل Gemini). المفتاحان يُحفظان مشفّرين من الإعدادات ----
OXION_API_KEY = os.getenv("OXION_API_KEY", "")
# ---- Gemini (اختياري): المفتاح يُحفظ مشفراً من الإعدادات، أو من البيئة ----
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

REPLY_POLL_SECONDS = 120
REPLY_DELAY_RANGE = (20, 120)  # تأخير عشوائي قبل الرد حتى لا يبدو آلياً

# ---- مجلد البيانات (قاعدة البيانات + مفتاح التشفير) ----
DATA_DIR = os.path.join(os.path.expanduser("~"), ".social_manager")
os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
try:
    os.chmod(DATA_DIR, 0o700)
except OSError:
    pass
DB_PATH = os.path.join(DATA_DIR, "social_manager.db")
SECRET_KEY_PATH = os.path.join(DATA_DIR, "secret.key")

# ---- حدود آمنة لكل منصة (حتى لا تبدو الأنشطة آلية) ----
RATE_LIMITS = {
    "instagram": {"comments_per_hour": 8, "likes_per_hour": 20, "replies_per_hour": 20, "dms_per_hour": 12, "min_delay_sec": 180, "max_delay_sec": 3600},
    "facebook":  {"comments_per_hour": 8, "likes_per_hour": 20, "min_delay_sec": 180, "max_delay_sec": 3600},
    "x":         {"comments_per_hour": 6, "likes_per_hour": 15, "min_delay_sec": 240, "max_delay_sec": 5400},
    "youtube":   {"comments_per_hour": 5, "likes_per_hour": 10, "min_delay_sec": 300, "max_delay_sec": 7200},
}

# ---- الشخصية الافتراضية للتعليقات (تُعدّل من الإعدادات بالواجهة) ----
DEFAULT_PERSONA = (
    "أسلوب راقي وواثق وقصير، عربي واضح بلمسة عامية خفيفة، "
    "بدون مبالغة أو مجاملات فارغة، والتعليق يظهر أنه فهم البوست فعلاً."
)

# الخصائص التي تُحمَّل من الإعدادات المحفوظة في قاعدة البيانات
def get_persona(db_get_setting):
    return db_get_setting("persona", DEFAULT_PERSONA)
