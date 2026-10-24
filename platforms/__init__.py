"""سجل المنصات: يحوّل اسم المنصة إلى الصنف المناسب."""

ADAPTERS = {}


def _load():
    global ADAPTERS
    if ADAPTERS:
        return
    from platforms.instagram import InstagramPlatform
    from platforms.facebook import FacebookPlatform
    from platforms.x_twitter import XPlatform
    from platforms.youtube import YouTubePlatform
    from platforms.telegram import TelegramPlatform
    from platforms.bluesky import BlueskyPlatform
    from platforms.threads import ThreadsPlatform
    ADAPTERS = {
        "instagram": InstagramPlatform,
        "facebook": FacebookPlatform,
        "x": XPlatform,
        "youtube": YouTubePlatform,
        "telegram": TelegramPlatform,
        "bluesky": BlueskyPlatform,
        "threads": ThreadsPlatform,
    }


def get(platform, account_row):
    _load()
    if platform not in ADAPTERS:
        raise ValueError(f"منصة غير مدعومة: {platform}")
    return ADAPTERS[platform](account_row)


def platform_names():
    return ["instagram", "facebook", "x", "youtube", "telegram", "bluesky", "threads"]


def capabilities():
    """إمكانيات كل منصة (نص/صورة/فيديو/أقصى طول) دون الحاجة لبيانات دخول."""
    _load()
    return {name: dict(cls.capabilities) for name, cls in ADAPTERS.items()}
