"""اختبار واجهة محلي معزول، لا يستخدم حسابات حقيقية أو يرسل للمنصات.
شغّله من جذر المشروع: python -m tools.check_ui_safety
يتطلب Playwright ومتصفح Chromium. يضع صور الاختبار في المجلد المؤقت.
"""
import tempfile, os, threading, time, json, shutil
import config
from core import accounts, database as db, server, net, scheduler, media
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
root=tempfile.TemporaryDirectory()
config.DATA_DIR=root.name; config.DB_PATH=os.path.join(root.name,'ui.db'); config.SECRET_KEY_PATH=os.path.join(root.name,'key')
accounts._KEY=None; db.init_db(); net.online=lambda *a,**k:True
scheduler.tick=lambda:True; scheduler.running=lambda:True
os.makedirs(media.media_dir(),exist_ok=True)
db.add_account('telegram','حساب تجريبي',{'bot_token':'dummy','chat_id':'dummy'})
aid=db.list_accounts()[0]['id']
http=make_server('127.0.0.1',18089,server.app,threaded=True)
threading.Thread(target=http.serve_forever,daemon=True).start()
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=shutil.which('google-chrome') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':390,'height':844},device_scale_factor=1)
 errors=[]; dialogs=[]; writes=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.route('https://**/*',lambda route:route.abort())
 page.goto('http://127.0.0.1:18089/');page.wait_for_function('()=>S.accs.v.length===1 && S.sel.v.length===1')
 page.evaluate('S.text.v="اختبار منع التكرار";$("c-text").value=S.text.v')
 def drop_ack(route):
  if route.request.method=='POST':
   response=route.fetch(); assert response.status==200
   writes.append(route.request.post_data_json)
   route.abort()
  else: route.continue_()
 page.route('**/api/groups',drop_ack)
 assert page.evaluate('submit()') is False
 assert len(db.list_groups())==1
 pending=page.evaluate('kvGet("pendingSubmission")')
 assert pending and pending['body']['request_key']
 page.unroute('**/api/groups',drop_ack)
 page.reload();page.wait_for_function('()=>submissionKey!==null')
 assert page.evaluate('S.text.v')=='اختبار منع التكرار'
 assert page.evaluate('submit()') is True
 assert len(db.list_groups())==1
 assert page.evaluate('kvGet("pendingSubmission")') is None
 print('نجح: ضياع الإقرار وإعادة الفتح وإعادة الطلب تنشئ مجموعة واحدة فقط')
 gid=db.list_groups()[0]['id']; target=db.group_targets(gid)[0]
 db.finish_target(target['id'],'outcome_unknown','قد يكون المنشور وصل',error_code='outcome_unknown')
 page.evaluate('loadGroups()');page.wait_for_function('()=>S.groups.v.length===1 && S.groups.v[0].state==="needs_review"')
 page.evaluate('openGroup(S.groups.v[0].id)');page.wait_for_timeout(450)
 page.screenshot(path=os.path.join(tempfile.gettempdir(),'sm-mobile-uncertain.png'),full_page=True)
 def dismiss(dialog): dialogs.append(dialog.message); dialog.dismiss()
 page.on('dialog',dismiss)
 page.get_by_role('button',name='إعادة المحاولة',exact=True).click()
 assert len(dialogs)==1 and db.group_targets(gid)[0]['status']=='outcome_unknown'
 page.remove_listener('dialog',dismiss)
 page.on('dialog',lambda dialog:dialog.accept())
 page.get_by_role('button',name='إعادة المحاولة',exact=True).click()
 page.wait_for_timeout(400)
 assert db.group_targets(gid)[0]['status']=='pending'
 print('نجح: رفض التأكيد يبقي الإرسال متوقفاً، والموافقة الصريحة تعيده للطابور')
 page.evaluate('closeSheet(true);go("settings")');page.wait_for_selector('#relay-state',state='visible');page.wait_for_timeout(300)
 page.evaluate('closeSheet(true)');page.locator('h2').filter(has_text='تخزين الوسائط').scroll_into_view_if_needed();page.wait_for_timeout(200)
 page.screenshot(path=os.path.join(tempfile.gettempdir(),'sm-mobile-relay-disabled.png'),full_page=False)
 page.evaluate('go("home")')
 page.evaluate('closeSheet(true);S.text.v="اختبار فشل التخزين";$("c-text").value=S.text.v;kvSet=async()=>{throw new Error("تعذّر حفظ سجل الطلب")};true;')
 assert page.evaluate('submit()') is False
 assert len(db.list_groups())==1
 print('نجح: فشل حفظ سجل الطلب يمنع إرساله')
 print('أخطاء الصفحة',errors)
 assert errors==[]
 print('تجاوز العرض',page.evaluate('document.documentElement.scrollWidth>innerWidth'))
 browser.close()
http.shutdown();root.cleanup()
