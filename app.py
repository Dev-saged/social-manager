"""نقطة التشغيل: يهيئ قاعدة البيانات، يشغّل مجدول الخلفية، ثم يرفع خادم الويب.

WebToApp (نوع المشروع Python) يشغّل هذا الملف (app.py) كخادم محلي على الجهاز،
ويعرض الواجهة static/index.html في WebView. الواجهة تتحدث مع الخادم عبر /api/*.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.database import init_db
from core.scheduler import start


def main():
    init_db()
    # أي فشل هنا لا يمنع الخادم من الإقلاع، فتظهر الواجهة وتعرض سبب التوقف
    try:
        from core import deps
        deps.activate()
        deps.start_background()  # مكتبات Instagram/يوتيوب تُثبَّت بصبر بعد الإقلاع
    except Exception as e:
        print("deps failed:", repr(e), file=sys.stderr)
    try:
        start()  # مجدول الخلفية: نشر المنشورات + إطلاق التعليقات الموافق عليها
    except Exception as e:
        print("scheduler failed:", repr(e), file=sys.stderr)
    from core.server import app
    # يبقى محلياً؛ يتيح WebToApp تمرير منفذ مختلف عبر البيئة.
    port = int(os.getenv("PORT", "8080"))
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
