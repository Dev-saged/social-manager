# بناء تطبيق أندرويد

المشروع في المجلد `android/` (Gradle + Chaquopy). لا يعدّل أي ملف من مصدر التطبيق، بل ينسخ `app.py` و`core` و`platforms` و`static` إلى الحزمة وقت البناء.

- معرّف التطبيق: `app.sajed.socialmanager` (تثبيت جديد).
- الخادم المحلي على `127.0.0.1:38417` داخل خدمة أمامية، والواجهة في WebView.
- بايثون 3.11 مضمّن، والمكتبات مضمّنة وقت البناء (لا pip وقت التشغيل). Instagram الشخصي (instagrapi) غير مضمّن في أندرويد.

## النشر

من تبويب Actions شغّل **Android release** وأدخل رقم النسخة. المسار: بناء موقّع، ثم تثبيت على محاكي أندرويد والتحقق من `/api/health`، ثم نشر Release مع ملف APK وبصمته.

الأسرار المطلوبة في إعدادات المستودع (Actions secrets):

- `ANDROID_KEYSTORE_BASE64`: ملف المفتاح `release.jks` بصيغة base64.
- `ANDROID_KEYSTORE_PASSWORD`: كلمة سر المفتاح (نفسها لمخزن المفاتيح والمفتاح، والاسم المستعار `social-manager`).

احتفظ بنفس المفتاح دائماً: تغييره يمنع التحديث فوق النسخة المثبتة.

## بناء محلي

يحتاج JDK 17 وAndroid SDK 34 وبايثون 3.11:

```
cd android
./gradlew :app:assembleDebug
```
