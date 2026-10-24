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


class NotSupported(ValueError):
    """العملية غير متاحة في هذه المنصة (خطأ دائم، لا فائدة من إعادة المحاولة)."""


class BasePlatform(ABC):
    name = ""
    # ما تستطيع المنصة نشره: تستخدمه الواجهة والخادم لتعطيل الخيارات غير الممكنة
    capabilities = {"text": True, "image": True, "video": False, "max_len": 2000}
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
        self.reply_limiter = RateLimiter(account_id, "replies", limits.get("replies_per_hour", 20))
        self.dm_limiter = RateLimiter(account_id, "dms", limits.get("dms_per_hour", 10))

    @abstractmethod
    def publish(self, text, media_path=None):
        """ينشر منشوراً على حساب صاحب الأداة."""

    def publish_video(self, text, media_path):
        """نشر فيديو. تتجاوزه المنصات التي تدعمه."""
        raise NotSupported("هذه المنصة لا تدعم نشر الفيديو")

    def publish_media(self, text, media_path=None, kind=""):
        """نقطة دخول المحرك: يوجّه إلى publish أو publish_video حسب نوع الملف."""
        if kind == "video":
            return self.publish_video(text, media_path)
        return self.publish(text, media_path)

    def recent_comments(self, media_limit=10, per_media=40):
        """تعليقات الناس على منشورات حسابك: [{comment_id, media_id, user_id, username, text, ts}]."""
        raise NotSupported("الردود التلقائية غير مدعومة في هذه المنصة")

    def reply_to_comment(self, media_id, comment_id, text):
        raise NotSupported("الردود التلقائية غير مدعومة في هذه المنصة")

    def send_dm(self, user_id, text, comment_id=None):
        raise NotSupported("الرسائل الخاصة غير مدعومة في هذه المنصة")

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
