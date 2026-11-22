"""ناقل وسائط خاص عبر R2. تحضيري فقط، لا يفعّل إعدادات التطبيق تلقائياً.

الرابط الموقّع إذن قراءة مؤقت لمن يملكه. لا تسجله ولا تضفه إلى Git.
يحتاج تفعيل الحساب وموافقة الفوترة والتحقق من خصوصية الحاوية قبل الاستخدام.
"""
import os
import re
import uuid
from dataclasses import dataclass, field
from core.errors import AppError

PREFIX = "ephemeral-media/"
TTL = 3600
MAX_BYTES = 1024 * 1024 * 1024


@dataclass(frozen=True)
class R2Config:
    account_id: str
    bucket: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    def validate(self):
        if not re.fullmatch(r"[a-f0-9]{32}", self.account_id):
            raise AppError("invalid_input", "معرّف حساب التخزين غير صالح")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", self.bucket):
            raise AppError("invalid_input", "اسم حاوية التخزين غير صالح")
        if not self.access_key or not self.secret_key:
            raise AppError("invalid_input", "مفاتيح التخزين الخاص ناقصة")


def client_for(cfg):
    cfg.validate()
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise AppError("not_supported", "مكتبة التخزين الخاص غير مثبتة؛ البديل غير مفعّل")
    return boto3.client("s3", endpoint_url=f"https://{cfg.account_id}.r2.cloudflarestorage.com",
                        aws_access_key_id=cfg.access_key, aws_secret_access_key=cfg.secret_key,
                        region_name="auto", config=Config(signature_version="s3v4", retries={"max_attempts": 2},
                                                         connect_timeout=20, read_timeout=120))


class PrivateRelay:
    """لا يُنشئ حاوية ولا يعدّل صلاحياتها. يستقبل حاوية خاصة تحقّق منها المشغّل."""
    def __init__(self, cfg, client=None):
        cfg.validate()
        self.cfg = cfg
        self.client = client if client is not None else client_for(cfg)

    def upload(self, path, kind="image"):
        # مسار الجهاز لا يدخل اسم الكائن؛ الاسم عشوائي ولا يحوي اسم المستخدم.
        ext = os.path.splitext(path)[1].lower()
        allowed = {"image": {".jpg": "image/jpeg", ".jpeg": "image/jpeg"},
                   "video": {".mp4": "video/mp4"}}
        content_type = allowed.get(kind, {}).get(ext)
        if not content_type:
            raise AppError("media_unsupported", "الناقل الخاص يقبل JPG للصور وMP4 للفيديو")
        size = os.path.getsize(path)
        if size <= 0 or size > MAX_BYTES:
            raise AppError("too_large", "حجم الملف غير صالح أو يتجاوز 1 غيغابايت")
        key = PREFIX + uuid.uuid4().hex + ext
        try:
            self.client.upload_file(path, self.cfg.bucket, key,
                                    ExtraArgs={"ContentType": content_type, "CacheControl": "private, no-store"})
            url = self.client.generate_presigned_url("get_object", Params={"Bucket": self.cfg.bucket, "Key": key},
                                                     ExpiresIn=TTL, HttpMethod="GET")
        except Exception:
            # قد يتضمن خطأ SDK رابطاً موقّعاً أو تفاصيل حساب. لا نمرره إلى سجل التطبيق.
            try:
                self.client.delete_object(Bucket=self.cfg.bucket, Key=key)
            except Exception:
                pass
            raise AppError("server", "تعذّر تجهيز الوسائط في التخزين الخاص؛ لم يبدأ نشرها إلى المنصة") from None
        return url, {"key": key}

    def remove(self, handle):
        key = (handle or {}).get("key", "")
        if not re.fullmatch(re.escape(PREFIX) + r"[a-f0-9]{32}\.(jpg|jpeg|mp4)", key):
            raise AppError("invalid_input", "مقبض حذف الوسائط غير صالح")
        try:
            self.client.delete_object(Bucket=self.cfg.bucket, Key=key)
            return True
        except Exception:
            # انتهاء الرابط لا يحذف الملف؛ قاعدة العمر الاحتياطية ضرورية.
            return False
