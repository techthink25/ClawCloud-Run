#!/usr/bin/env python3
import os
import time
import json
import random
import subprocess
import argparse
from typing import Optional, Literal
import requests
from playwright.sync_api import sync_playwright

# 用于加密 GitHub Secret
try:
    from nacl import encoding, public
except ImportError:
    pass

class Telegram:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
    def send(self, text):
        if not self.token: return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try: requests.post(url, data={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
        except: pass

class SecretUpdater:
    def __init__(self, token, repo):
        self.token = token
        self.repo = repo
        self.headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    def update(self, name, value):
        try:
            # 获取公钥
            key_url = f"https://api.github.com/repos/{self.repo}/actions/secrets/public-key"
            res = requests.get(key_url, headers=self.headers).json()
            public_key = res['key']
            key_id = res['key_id']
            # 加密
            public_key_obj = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder)
            sealed_box = public.SealedBox(public_key_obj)
            encrypted_value = encoding.Base64Encoder.encode(sealed_box.encrypt(value.encode("utf-8"))).decode("utf-8")
            # 更新
            put_url = f"https://api.github.com/repos/{self.repo}/actions/secrets/{name}"
            requests.put(put_url, headers=self.headers, json={"encrypted_value": encrypted_value, "key_id": key_id})
            return True
        except Exception as e:
            print(f"Secret 更新失败: {e}")
            return False

class AutoLogin:
    def __init__(self, mode="cli"):
        self.mode = mode
        self.gh_token = os.getenv("GH_PAT") or os.getenv("REPO_TOKEN")
        self.repo = os.getenv("GITHUB_REPOSITORY")
        self.tg = Telegram(os.getenv("TG_BOT_TOKEN"), os.getenv("TG_CHAT_ID"))
        self.updater = SecretUpdater(self.gh_token, self.repo) if self.gh_token and self.repo else None
        self.LOGIN_ENTRY = "https://ap-northeast-1.run.claw.cloud/login"

    def log(self, msg, level="INFO"):
        print(f"[{level}] {msg}")

    def run(self):
        self.log(f"开始 ClawCloud 自动登录 (模式: {self.mode})")
        self.tg.send(f"🤖 <b>ClawCloud 自动登录开始</b>\n模式: {self.mode}")

        # 1. 认证环境
        if self.mode == "cli" and self.gh_token:
            subprocess.run(["gh", "auth", "login", "--with-token"], input=self.gh_token.encode())
            self.log("GitHub CLI 环境已就绪", "SUCCESS")

        # 2. 启动浏览器
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            
            # 加载旧 Session
            old_session = os.getenv("GH_SESSION")
            if old_session:
                try: context.add_cookies(json.loads(old_session))
                except: pass

            page = context.new_page()
            
            # 3. 登录 ClawCloud
            self.log("正在访问 ClawCloud...")
            page.goto(self.LOGIN_ENTRY, wait_until="networkidle")
            
            # 点击 GitHub 登录
            try:
                page.click('button:has-text("GitHub"), a:has-text("GitHub")', timeout=10000)
                time.sleep(5)
            except:
                pass

            # 如果还在 GitHub 页面，尝试自动授权
            if "github.com" in page.url:
                self.log("检测到 GitHub 授权页，尝试自动点击...")
                try:
                    page.click('button#js-oauth-authorize-btn', timeout=5000)
                    time.sleep(5)
                except:
                    pass

            # 4. 判断是否登录成功
            if "run.claw.cloud" in page.url and "login" not in page.url:
                self.log("ClawCloud 登录成功！", "SUCCESS")
                
                # 保活访问
                regions = ["ap-northeast-1", "us-east-1", "eu-central-1"]
                for r in regions:
                    page.goto(f"https://{r}.run.claw.cloud/apps")
                    time.sleep(2)
                
                # 提取并更新 Cookie
                cookies = context.cookies()
                gh_cookies = [c for c in cookies if "github.com" in c['domain']]
                if gh_cookies:
                    new_session = json.dumps(gh_cookies)
                    if self.updater:
                        self.updater.update("GH_SESSION", new_session)
                        self.log("GH_SESSION 已自动更新", "SUCCESS")
                        self.tg.send("✅ <b>ClawCloud 登录成功</b>\nCookie 已自动更新")
                else:
                    self.tg.send("✅ <b>ClawCloud 登录成功</b>\n(未提取到新 Cookie)")
            else:
                self.log("登录失败，当前 URL: " + page.url, "ERROR")
                self.tg.send(f"❌ <b>ClawCloud 登录失败</b>\n当前页面: {page.url}")
            
            browser.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="cli")
    args = parser.parse_args()
    AutoLogin(mode=args.mode).run()
