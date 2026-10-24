"""الواجهة المشتركة لكل منصات التواصل + محدودية المعدل."""
from abc import ABC, abstractmethod
from core import database as db


class RateLimitExceeded(RuntimeError):
    pass


class RateLimiter:
    """نافذة منزلقة بسيطة: يرفض السماح بعملية جديدة إذا تجاوزنا حد الساعة."""

    def __init__(self, account_id, action, max_per_hour):
        self.account_id = account_id
        self.action = action
        self.max_per_hour = max_per_hour

    def allow(self):
        return db.reserve_rate_slot(self.account_id, self.action, self.max_per_hour)


class BasePlatform(ABC):
    name = ""
    # أسماء الحقول المطلوبة في بيانات الدخول لكل حساب
    required_fields = []

    def __init__(self, account_row):
        """account_row: صف من جدول accounts. فك التشفير يحدث هنا."""
        from core import accounts as acc_store
        self.account = account_row
        self.creds = acc_store.decrypt(account_row["credentials"])
        missing = [f for f in self.required_fields if not self.creds.get(f)]
        if missing:
            raise ValueError(f"بيانات الدخول ناقصة: {', '.join(missing)}")
        import config
        limits = config.RATE_LIMITS.get(self.name, {})
        account_id = self.account["id"]
        self.comment_limiter = RateLimiter(account_id, "comments", limits.get("comments_per_hour", 8))
        self.like_limiter = RateLimiter(account_id, "likes", limits.get("likes_per_hour", 20))

    @abstractmethod
    def publish(self, text, media_path=None):
        """ينشر منشوراً على حساب صاحب الأداة."""

    @abstractmethod
    def comment(self, post_id, text):
        """يكتب تعليقاً/رداً على منشور محدد."""

    @abstractmethod
    def like(self, post_id):
        """يعجب بمنشور محدد."""

    @abstractmethod
    def recent_posts(self, username, limit=5):
        """يجلب أحدث منشورات حساب محدد: [{id, text, image_b64?}]."""

    @abstractmethod
    def stories(self, username=None):
        """يجلب حالات (ستوري) حساب محدد حيث تتاح المنصة لذلك."""

    @abstractmethod
    def validate(self):
        """يتأكد أن بيانات الدخول شغالة. يعيد True/False."""
