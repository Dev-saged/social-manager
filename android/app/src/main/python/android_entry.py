"""نقطة دخول أندرويد: تشغّل app.py من مجلد حقيقي وتبقي كل بيانات التطبيق داخل مجلد التطبيق الخاص."""
import os
import sys


def run(app_dir, data_home, port):
    os.environ["HOME"] = data_home
    os.environ["PORT"] = str(port)
    os.chdir(app_dir)
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    import app as entry
    from core import deps
    # لا pip على أندرويد: المكتبات مضمّنة في الحزمة، فلا حاجة لمثبّت الخلفية
    deps.start_background = lambda: None
    entry.main()
