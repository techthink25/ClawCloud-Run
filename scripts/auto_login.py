#!/usr/bin/env python3
import base64
import os
import random
import re
import sys
import time
from urllib.parse import urlparse
import requests
from playwright.sync_api import sync_playwright

try:
    from nacl import encoding, public
except ImportError:
    pass

# ==================== 配置 ====================
PROXY_DSN = os.environ.get("PROXY_DSN", "").strip()
LOGIN_ENTRY_URL = "https://console.run.claw.cloud/login"
SIGNIN_URL = f"{LOGIN_ENTRY_URL}/signin"
DEVICE_VERIFY_WAIT = 30  
TWO_FACTOR_WAIT = int(os.environ.get("TWO_FACTOR_WAIT", "120"))

class Telegram:
    def __init__(self):
        self.token = os.environ.get('TG_BOT_TOKEN')
        self.chat_id = os.environ.get('TG_CHAT_ID')
        self.ok = bool(self.token and self.chat_id)
    
    def send(self, msg):
        if not self.ok: return
        try:
            requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                data={"chat_id": self.chat_id, "text": msg, "parse_mode": "HTML"}, timeout=30)
        except: pass
    
    def photo(self, path, caption=""):
        if not self.ok or not os.path.exists(path): return
        try:
            with open(path, 'rb') as f:
                requests.post(f"https://api.telegram.org/bot{self.token}/sendPhoto",
                    data={"chat_id": self.chat_id, "caption": caption[:1024]}, files={"photo": f}, timeout=60)
        except: pass

    def flush_updates(self):
        if not self.ok: return 0
        try:
            r = requests.get(f"https://api.telegram.org/bot{self.token}/getUpdates", params={"timeout": 0}, timeout=10)
            data = r.json()
            if data.get("ok") and data.get("result"):
                return data["result"][-1]["update_id"] + 1
        except: pass
        return 0
    
    def wait_code(self, timeout=120):
        if not self.ok: return None
        offset = self.flush_updates()
        deadline = time.time() + timeout
        pattern = re.compile(r"^/code\s+(\d{6,8})$")
        while time.time() < deadline:
            try:
                r = requests.get(f"https://api.telegram.org/bot{self.token}/getUpdates", 
                    params={"timeout": 20, "offset": offset}, timeout=30)
                data = r.json()
                if not data.get("ok"):
                    time.sleep(2)
                    continue
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or {}
                    chat = msg.get("chat") or {}
                    if str(chat.get("id")) != str(self.chat_id): continue
                    text = (msg.get("text") or "").strip()
                    m = pattern.match(text)
                    if m: return m.group(1)
            except: pass
            time.sleep(2)
        return None

class SecretUpdater:
    def __init__(self):
        self.token = os.environ.get('REPO_TOKEN')
        self.repo = os.environ.get('GITHUB_REPOSITORY')
        self.ok = bool(self.token and self.repo)
    
    def update(self, name, value):
        if not self.ok: return False
        try:
            from nacl import encoding, public
            headers = {"Authorization": f"token {self.token}", "Accept": "application/vnd.github.v3+json"}
            r = requests.get(f"https://api.github.com/repos/{self.repo}/actions/secrets/public-key", headers=headers, timeout=30)
            if r.status_code != 200: return False
            key_data = r.json()
            pk = public.PublicKey(key_data['key'].encode(), encoding.Base64Encoder())
            encrypted = public.SealedBox(pk).encrypt(value.encode())
            r = requests.put(f"https://api.github.com/repos/{self.repo}/actions/secrets/{name}",
                headers=headers, json={"encrypted_value": base64.b64encode(encrypted).decode(), "key_id": key_data['key_id']}, timeout=30)
            return r.status_code in [201, 204]
        except Exception as e:
            print(f"更新 Secret 失败: {e}")
            return False

class AutoLogin:
    def __init__(self):
        self.username = os.environ.get('GH_USERNAME')
        self.password = os.environ.get('GH_PASSWORD')
        self.gh_session = os.environ.get('GH_SESSION', '').strip()
        self.tg = Telegram()
        self.secret = SecretUpdater()
        self.shots = []
        self.logs = []
        self.n = 0
        self.detected_region = 'eu-central-1'
        self.region_base_url = 'https://eu-central-1.run.claw.cloud'

    def log(self, msg, level="INFO"):
        icons = {"INFO": "ℹ️", "SUCCESS": "✅", "ERROR": "❌", "WARN": "⚠️", "STEP": "🔹"}
        line = f"{icons.get(level, '•')} {msg}"
        print(line)
        self.logs.append(line)

    def shot(self, page, name):
        self.n += 1
        f = f"{self.n:02d}_{name}.png"
        try:
            page.screenshot(path=f)
            self.shots.append(f)
        except: pass
        return f

    def click(self, page, sels, desc=""):
        for s in sels:
            try:
                el = page.locator(s).first
                if el.is_visible(timeout=3000):
                    time.sleep(random.uniform(0.5, 1.5))
                    el.hover()
                    time.sleep(random.uniform(0.2, 0.5))
                    el.click()
                    self.log(f"已点击: {desc}", "SUCCESS")
                    return True
            except: pass
        return False

    def detect_region(self, url):
        try:
            parsed = urlparse(url)
            host = parsed.netloc
            if host.endswith('.console.claw.cloud'):
                region = host.replace('.console.claw.cloud', '')
                if region and region != 'console':
                    self.detected_region = region
                    self.region_base_url = f"https://{host}"
                    self.log(f"检测到区域: {region}", "SUCCESS")
                    return region
            self.region_base_url = f"{parsed.scheme}://{parsed.netloc}"
            return None
        except Exception as e:
            self.log(f"区域检测异常: {e}", "WARN")
            return None

    def get_base_url(self):
        return self.region_base_url if self.region_base_url else LOGIN_ENTRY_URL

    def get_session(self, context):
        try:
            for c in context.cookies():
                if c['name'] == 'user_session' and 'github' in c.get('domain', ''):
                    return c['value']
        except: pass
        return None

    def save_cookie(self, value):
        if not value: return
        self.log(f"新 Cookie: {value[:15]}...", "SUCCESS")
        if self.secret.update('GH_SESSION', value):
            self.log("已自动更新 GH_SESSION", "SUCCESS")
            self.tg.send("🔑 <b>Cookie 已自动更新</b>")
        else:
            self.tg.send(f"🔑 <b>新 Cookie</b>\n<tg-spoiler>{value}</tg-spoiler>")

    def wait_device(self, page):
        self.log(f"需要设备验证，等待 {DEVICE_VERIFY_WAIT} 秒...", "WARN")
        self.shot(page, "device_verification")
        for i in range(DEVICE_VERIFY_WAIT):
            time.sleep(1)
            if 'verified-device' not in page.url and 'device-verification' not in page.url:
                self.log("设备验证通过！", "SUCCESS")
                return True
        return False

    def wait_two_factor_mobile(self, page):
        self.log(f"需要两步验证（Mobile），等待 {TWO_FACTOR_WAIT} 秒...", "WARN")
        shot = self.shot(page, "2FA_mobile")
        self.tg.photo(shot, "请在手机 App 批准")
        for i in range(TWO_FACTOR_WAIT):
            time.sleep(1)
            if "github.com/sessions/two-factor/" not in page.url: return True
            if "github.com/login" in page.url: return False
        return False

    def handle_2fa_code_input(self, page):
        self.log("需要输入验证码", "WARN")
        shot = self.shot(page, "2FA_code")
        self.tg.photo(shot, "请在 TG 发送 /code 123456")
        code = self.tg.wait_code(timeout=TWO_FACTOR_WAIT)
        if not code: return False
        try:
            input_sel = 'input[autocomplete="one-time-code"], input[name="app_otp"]'
            page.locator(input_sel).first.fill(code)
            page.keyboard.press("Enter")
            time.sleep(3)
            return "two-factor" not in page.url
        except: return False

    def login_github(self, page, context):
        self.log("登录 GitHub...", "STEP")
        try:
            page.locator('input[name="login"]').fill(self.username)
            page.locator('input[name="password"]').fill(self.password)
            page.locator('input[type="submit"]').click()
            time.sleep(3)
            if 'verified-device' in page.url: self.wait_device(page)
            if 'two-factor' in page.url:
                if 'two-factor/mobile' in page.url: self.wait_two_factor_mobile(page)
                else: self.handle_2fa_code_input(page)
            return True
        except: return False

    def oauth(self, page):
        if 'github.com/login/oauth/authorize' in page.url:
            self.log("处理 OAuth...", "STEP")
            self.click(page, ['button[name="authorize"]'], "授权")
            time.sleep(3)

        def wait_redirect(self, page, wait=120):
        """等待重定向并检测区域"""
        self.log(f"等待重定向 (最长 {wait} 秒)...", "STEP")
        for i in range(wait):
            url = page.url
            if i % 10 == 0:
                self.log(f"  等待中... ({i}s) 当前 URL: {url}")
            
            # 成功判定：URL 不含 signin 且包含 claw.cloud，或者页面出现了 Apps 关键字
            is_dashboard = ('claw.cloud' in url and 'signin' not in url.lower() and 'login' not in url.lower())
            if not is_dashboard:
                # 额外检查页面内容，防止 URL 没变但内容已加载
                try:
                    if page.locator('text=Apps, text=Logout, .ant-avatar').first.is_visible(timeout=500):
                        is_dashboard = True
                except: pass

            if is_dashboard:
                self.log("重定向成功！", "SUCCESS")
                self.detect_region(url)
                return True
            
            # 如果回到了登录页，尝试再次点击 GitHub 按钮（OAuth 常见补丁）
            if 'signin' in url.lower() or 'login' in url.lower():
                if i > 5 and i % 20 == 0:
                    self.log("似乎回到了登录页，尝试再次点击 GitHub 按钮...", "WARN")
                    self.click(page, ['button:has-text("GitHub")', '[data-provider="github"]'], "GitHub (重试)")
            
            # 处理 GitHub 授权
            if 'github.com' in url:
                try:
                    auth_btn = page.locator('button[name="authorize"], button#js-oauth-authorize-btn').first
                    if auth_btn.is_visible(timeout=1000):
                        self.log("检测到授权按钮，点击授权...", "INFO")
                        auth_btn.click()
                except: pass
            
            time.sleep(1)
        return False

    def keepalive(self, page):
        self.log("保活...", "STEP")
        base = self.get_base_url()
        for path in ["/", "/apps"]:
            try:
                page.goto(f"{base}{path}", timeout=30000)
                time.sleep(2)
            except: pass

    def notify(self, ok, err=""):
        if not self.tg.ok: return
        msg = f"<b>🤖 ClawCloud 登录{'✅ 成功' if ok else '❌ 失败'}</b>\n用户: {self.username}\n{err}"
        self.tg.send(msg)
        if self.shots: self.tg.photo(self.shots[-1], "最后状态")

    def run(self):
        with sync_playwright() as p:
            launch_args = {"headless": True, "args": ['--no-sandbox']}
            browser = p.chromium.launch(**launch_args)
            context = browser.new_context(viewport={'width': 1920, 'height': 1080})
            page = context.new_page()
            try:
                if self.gh_session:
                    context.add_cookies([{'name': 'user_session', 'value': self.gh_session, 'domain': 'github.com', 'path': '/'}])
                page.goto(SIGNIN_URL, wait_until='domcontentloaded', timeout=90000)
                self.click(page, ['button:has-text("GitHub")'], "GitHub")
                time.sleep(5)
                if 'github.com/login' in page.url:
                    self.login_github(page, context)
                if self.wait_redirect(page):
                    self.keepalive(page)
                    new_cookie = self.get_session(context)
                    if new_cookie: self.save_cookie(new_cookie)
                    self.notify(True)
                else:
                    self.notify(False, "重定向超时")
            except Exception as e:
                self.notify(False, str(e))
            finally:
                browser.close()

if __name__ == "__main__":
    AutoLogin().run()
