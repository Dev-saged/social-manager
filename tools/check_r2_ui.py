import tempfile,os,threading,shutil,json
import config
from core import accounts,database as db,server,net,relay,scheduler
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
with tempfile.TemporaryDirectory() as td:
 config.DATA_DIR=td;config.DB_PATH=td+'/ui.db';config.SECRET_KEY_PATH=td+'/key'
 accounts._KEY=None;db.init_db();net.online=lambda *a,**k:True
 scheduler.running=lambda:True; scheduler.tick=lambda:True
 relay.probe=lambda:{'ok':True,'unsigned_status':400,'signed_status':200,'deleted_status':404}
 http=make_server('127.0.0.1',18090,server.app,threaded=True)
 threading.Thread(target=http.serve_forever,daemon=True).start()
 with sync_playwright() as p:
  browser=p.chromium.launch(executable_path=shutil.which('google-chrome') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
  page=browser.new_page(viewport={'width':390,'height':844},device_scale_factor=1)
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.route('https://**/*',lambda route:route.abort())
  page.goto('http://127.0.0.1:18090/')
  page.evaluate('go("settings")')
  page.wait_for_selector('#set-r2-account',state='visible')
  page.fill('#set-r2-account','a'*32)
  page.fill('#set-r2-bucket','social-manager-private-media-test')
  page.fill('#set-r2-access','dummy-access')
  page.fill('#set-r2-secret','dummy-secret')
  page.check('#set-r2-enabled')
  page.evaluate('saveSettings()')
  page.wait_for_function('()=>document.getElementById("relay-state").textContent.includes("مهيّأ")')
  assert page.input_value('#set-r2-access')==''
  assert page.input_value('#set-r2-secret')==''
  page.click('#btn-relay-check')
  page.wait_for_function('()=>document.getElementById("relay-r").textContent.includes("نجح الفحص")')
  page.wait_for_function('()=>!document.getElementById("toast").matches(":popover-open")&&!document.getElementById("toast").classList.contains("on")')
  page.locator('#set-r2-enabled').scroll_into_view_if_needed()
  page.screenshot(path='/tmp/sm-r2-settings-bottom.png')
  page.locator('.card').filter(has=page.locator('h2').filter(has_text='تخزين الوسائط')).screenshot(path='/tmp/sm-r2-settings-top.png')
  assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
  assert errors==[]
  print(json.dumps({'mobile_settings_save':True,'keys_cleared_after_save':True,'probe_ui_mocked':True,'page_errors':errors,'horizontal_overflow':False}))
  browser.close()
 http.shutdown()
