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
    start()  # مجدول الخلفية: نشر المنشورات + إطلاق التعليقات الموافق عليها
    from core.server import app
    # منفذ داخلي للجهاز فقط؛ عدّله إذا طلبه WebToApp في إعدادات المنفذ
    app.run(host="127.0.0.1", port=8080, threaded=True, debug=False)


if __name__ == "__main__":
    main()
