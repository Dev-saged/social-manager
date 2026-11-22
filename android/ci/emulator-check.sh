#!/usr/bin/env bash
# يثبّت الـAPK على المحاكي ويشغّله ويتحقق أن الخادم المحلي والواجهة تعملان فعلاً
set -u
APK="$1"
PKG=app.sajed.socialmanager
OUT=android/ci-out
mkdir -p "$OUT"
adb install -r "$APK" || { echo "install failed"; exit 1; }
adb shell pm grant "$PKG" android.permission.POST_NOTIFICATIONS 2>/dev/null || true
adb logcat -c
adb shell am start -n "$PKG/.MainActivity"
adb forward tcp:38417 tcp:38417
ok=0
for i in $(seq 1 90); do
  if curl -fsS -m 3 http://127.0.0.1:38417/api/health -o "$OUT/health.json"; then ok=1; break; fi
  sleep 2
done
adb logcat -d > "$OUT/logcat.txt"
adb exec-out screencap -p > "$OUT/screen.png" 2>/dev/null || true
if [ "$ok" != 1 ]; then echo "server did not come up"; tail -60 "$OUT/logcat.txt"; exit 1; fi
cat "$OUT/health.json"; echo
python3 - "$OUT/health.json" <<'PY'
import json, sys
h = json.load(open(sys.argv[1]))
assert h.get("ok") is True, h
assert h.get("scheduler") is True, h
print("health ok, version", h.get("version"))
PY
curl -fsS -m 5 http://127.0.0.1:38417/ | grep -q "<html" || { echo "UI page missing"; exit 1; }
curl -fsS -m 5 http://127.0.0.1:38417/api/dashboard > "$OUT/dashboard.json" || { echo "dashboard failed"; exit 1; }
# العملية ما زالت حية بعد الإقلاع (الخدمة الأمامية لم تُقتل)
sleep 20
adb shell pidof "$PKG" > /dev/null || { echo "app process died"; tail -60 "$OUT/logcat.txt"; exit 1; }
curl -fsS -m 5 http://127.0.0.1:38417/api/health > /dev/null || { echo "server died after 20s"; exit 1; }
echo "emulator check passed"
