"""ملفات الوسائط المرفوعة: مسارها، نوعها، وحذفها بعد النشر."""
import os
import re
import time
import config

IMAGE_EXT = {"jpg", "jpeg", "png", "webp"}
VIDEO_EXT = {"mp4", "mov"}
_ID = re.compile(r"[a-f0-9]{32}\.(jpg|jpeg|png|webp|mp4|mov)")


def media_dir():
    return os.path.join(config.DATA_DIR, "media")


def valid_id(media_id):
    return bool(_ID.fullmatch(media_id or ""))


def path_for(media_id):
    return os.path.join(media_dir(), media_id)


def kind_of(media_id):
    ext = (media_id or "").rsplit(".", 1)[-1].lower()
    return "image" if ext in IMAGE_EXT else "video" if ext in VIDEO_EXT else ""


def delete(media_id):
    if not valid_id(media_id):
        return False
    try:
        os.remove(path_for(media_id))
        return True
    except OSError:
        return False


def sweep(keep_ids, older_than=48 * 3600):
    """يحذف الملفات اليتيمة (غير مرتبطة بمنشور حي) الأقدم من المهلة."""
    removed = 0
    folder = media_dir()
    if not os.path.isdir(folder):
        return 0
    cutoff = time.time() - older_than
    for name in os.listdir(folder):
        if valid_id(name) and name not in keep_ids:
            try:
                if os.path.getmtime(os.path.join(folder, name)) < cutoff:
                    os.remove(os.path.join(folder, name))
                    removed += 1
            except OSError:
                pass
    return removed
