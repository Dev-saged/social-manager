"""تهيئة حاوية جديدة فقط بعد موافقة صاحب الحساب على شروط R2 والفوترة.

التشغيل الافتراضي يعرض الخطة دون اتصال بالشبكة. --apply يغيّر حساب التخزين فعلاً.
لا يُستدعى من التطبيق، ولا ينشئ اشتراكاً ولا يحفظ مفاتيح في ملفات المصدر.
"""
import argparse
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.private_relay import R2Config, client_for, PREFIX

LIFECYCLE = {"Rules": [{"ID": "private-media-expiry", "Status": "Enabled",
                         "Filter": {"Prefix": PREFIX}, "Expiration": {"Days": 1},
                         "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1}}]}


def provision(client, bucket):
    # لا نستبدل إعدادات حاوية موجودة. يجب أن يكون الاسم مخصصاً لهذا التطبيق.
    try:
        client.head_bucket(Bucket=bucket)
    except Exception as e:
        code = str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))
        if code not in ("404", "NoSuchBucket", "NotFound"):
            raise RuntimeError("تعذّر التحقق من عدم وجود الحاوية؛ لم نغيّر شيئاً") from None
    else:
        raise RuntimeError("الحاوية موجودة؛ لن نستبدل إعداداتها. اختر اسماً جديداً")
    client.create_bucket(Bucket=bucket)
    # عدم تحديد ACL يبقي الحاوية خاصة افتراضياً. لا نفعّل r2.dev ولا نطاقاً عاماً.
    try:
        client.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration=LIFECYCLE)
        saved = client.get_bucket_lifecycle_configuration(Bucket=bucket)
        if saved.get("Rules") != LIFECYCLE["Rules"]:
            raise RuntimeError("تعذّر تأكيد قاعدة الحذف")
    except Exception:
        raise RuntimeError("أُنشئت الحاوية لكن لم تُؤكد قاعدة الحذف. لا تفعّل الناقل قبل إصلاحها") from None


def main():
    parser = argparse.ArgumentParser(description="خطة حاوية وسائط خاصة؛ لا تنفيذ دون --apply")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.bucket.startswith("social-manager-private-media-"):
        parser.error("يجب أن يبدأ اسم الحاوية بـ social-manager-private-media-")
    print("الخطة: حاوية خاصة جديدة، بلا وصول عام؛ رابط قراءة لمدة ساعة؛ حذف احتياطي بعد يوم مع تأخر محتمل لدى المزوّد.")
    print("التهيئة لا تضع حداً مالياً لدى المزوّد. يلزم قبول صريح للاشتراك والفوترة قبل --apply.")
    if not args.apply:
        print("لم يحدث اتصال أو تغيير. لم يُفعّل ناقل التطبيق.")
        return
    cfg = R2Config(args.account_id, args.bucket, os.environ.get("R2_ACCESS_KEY_ID", ""),
                   os.environ.get("R2_SECRET_ACCESS_KEY", ""))
    try:
        provision(client_for(cfg), cfg.bucket)
    except Exception as e:
        # رسائل provision محلية، لكن أخطاء SDK قد تحمل بيانات حساسة.
        if isinstance(e, RuntimeError):
            print(str(e), file=sys.stderr)
        else:
            print("فشلت التهيئة؛ راجع حالة الحساب دون تسجيل المفاتيح أو الروابط الموقّعة", file=sys.stderr)
        raise SystemExit(1)
    print("أُنشئت الحاوية وقاعدة الحذف. تحقق من منع القراءة غير الموقّعة ومن انتهاء الرابط قبل تفعيل التطبيق.")


if __name__ == "__main__":
    main()
