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
    ADAPTERS = {
        "instagram": InstagramPlatform,
        "facebook": FacebookPlatform,
        "x": XPlatform,
        "youtube": YouTubePlatform,
    }


def get(platform, account_row):
    _load()
    if platform not in ADAPTERS:
        raise ValueError(f"منصة غير مدعومة: {platform}")
    return ADAPTERS[platform](account_row)


def platform_names():
    return ["instagram", "facebook", "x", "youtube"]
