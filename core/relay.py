"""ناقل R2 خاص لكل تثبيت. ناقل GitHub العام لا يُستخدم ولا يعود تلقائياً."""
import importlib.util
import os
import tempfile
import requests
from core import accounts, database as db
from core.errors import AppError
from core.private_relay import R2Config, PrivateRelay

_KEYS = ('r2_enabled', 'r2_account_id', 'r2_bucket', 'r2_access_key_id', 'r2_secret_access_key')


def _snapshot():
    with db._conn() as c:
        rows = c.execute('SELECT key,value FROM settings WHERE key IN (?,?,?,?,?)', _KEYS).fetchall()
    values = {r['key']: r['value'] for r in rows}
    for key in _KEYS[-2:]:
        try:
            values[key] = accounts.decrypt(values[key]).get('value', '') if values.get(key) else ''
        except Exception:
            values[key] = ''
    return values


def _config(values):
    return R2Config(values.get('r2_account_id', ''), values.get('r2_bucket', ''),
                    values.get('r2_access_key_id', ''), values.get('r2_secret_access_key', ''))


def configure(data):
    """تغيير ذري حتى لا يجمع النشر معرّفاً قديماً مع مفتاح جديد."""
    if not set(data).intersection(_KEYS):
        return
    current = _snapshot()
    values = dict(current)
    if 'r2_enabled' in data:
        if not isinstance(data['r2_enabled'], bool):
            raise AppError('invalid_input', 'حالة التخزين الخاص يجب أن تكون تفعيل أو إيقاف')
        values['r2_enabled'] = '1' if data['r2_enabled'] else '0'
    for key in _KEYS[1:]:
        if key in data:
            if not isinstance(data[key], str) or len(data[key]) > 4096:
                raise AppError('invalid_input', 'قيمة إعداد التخزين غير صالحة')
            value = data[key].strip()
            if value or key not in _KEYS[-2:]:
                values[key] = value
    if values.get('r2_enabled') == '1':
        _config(values).validate()
    saved = dict(values)
    for key in _KEYS[-2:]:
        saved[key] = accounts.encrypt({'value': values.get(key, '')}) if values.get(key) else ''
    with db._conn() as c:
        for key in _KEYS:
            c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                      (key, saved.get(key, '')))


def configured():
    values = _snapshot()
    if values.get('r2_enabled') != '1':
        return False
    try:
        _config(values).validate()
        return importlib.util.find_spec('boto3') is not None
    except AppError:
        return False


def settings():
    values = _snapshot()
    return {'provider': 'r2', 'r2_enabled': values.get('r2_enabled') == '1',
            'r2_account_id': values.get('r2_account_id', ''), 'r2_bucket': values.get('r2_bucket', ''),
            'has_r2_credentials': bool(values.get('r2_access_key_id') and values.get('r2_secret_access_key'))}


def _adapter():
    values = _snapshot()
    if values.get('r2_enabled') != '1':
        raise AppError('relay_disabled')
    return PrivateRelay(_config(values))


def _as_jpeg(path):
    if os.path.splitext(path)[1].lower() in ('.jpg', '.jpeg'):
        return path, False
    try:
        from PIL import Image
    except ImportError:
        raise AppError('media_unsupported', 'حوّل الصورة إلى JPG أو ثبّت مكتبة Pillow') from None
    fd, out = tempfile.mkstemp(suffix='.jpg')
    os.close(fd)
    try:
        with Image.open(path) as im:
            im.convert('RGB').save(out, 'JPEG', quality=92)
    except Exception:
        os.unlink(out)
        raise AppError('media_unsupported', 'تعذّر تحويل الصورة إلى JPG') from None
    return out, True


def upload(path, kind='image'):
    adapter = _adapter()
    converted = False
    if kind == 'image':
        path, converted = _as_jpeg(path)
    try:
        url, handle = adapter.upload(path, kind)
        handle.update(provider='r2', account_id=adapter.cfg.account_id, bucket=adapter.cfg.bucket)
        return url, handle
    finally:
        if converted:
            os.unlink(path)


def remove(handle):
    """لا نحذف من حاوية بديلة إذا تغيرت الإعدادات أثناء النشر."""
    if not handle or handle.get('provider') != 'r2':
        return False
    try:
        adapter = _adapter()
        if handle.get('account_id') != adapter.cfg.account_id or handle.get('bucket') != adapter.cfg.bucket:
            return False
        return adapter.remove({'key': handle.get('key', '')})
    except AppError:
        return False


def probe():
    """فحص صريح يرفع صورة صغيرة، يختبر منع القراءة العامة، ثم يحذفها."""
    if not configured():
        raise AppError('relay_disabled')
    try:
        from PIL import Image
    except ImportError:
        raise AppError('not_supported', 'فحص التخزين يحتاج مكتبة Pillow') from None
    handle = None
    try:
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'probe.jpg')
            Image.new('RGB', (8, 8), (20, 90, 140)).save(path, 'JPEG')
            with open(path, 'rb') as f:
                expected = f.read()
            url, handle = upload(path)
            unsigned = requests.get(url.split('?', 1)[0], timeout=20)
            signed = requests.get(url, timeout=20)
            if unsigned.status_code not in (400, 401, 403) or signed.status_code != 200 or signed.content != expected:
                raise AppError('server', 'لم يتأكد منع القراءة العامة أو سلامة الرابط الموقّع')
            if not remove(handle):
                raise AppError('server', 'نجح الرفع لكن لم يتأكد حذف صورة الفحص')
            deleted = requests.get(url, timeout=20)
            if deleted.status_code != 404:
                raise AppError('server', 'لم يتأكد حذف صورة الفحص')
            handle = None
            return {'ok': True, 'provider': 'r2', 'unsigned_status': unsigned.status_code,
                    'signed_status': signed.status_code, 'deleted_status': deleted.status_code, 'ttl_seconds': 3600}
    except AppError:
        raise
    except Exception:
        raise AppError('server', 'فشل فحص التخزين الخاص؛ لم تُسجّل المفاتيح أو الروابط الموقّعة') from None
    finally:
        if handle:
            remove(handle)
