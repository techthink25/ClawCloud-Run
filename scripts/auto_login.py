#!/usr/bin/env python3
"""
ClawCloud 自动登录脚本 - Super OAuth 版
集成: 区域自动检测, Secret 自动更新, 强力反爬, Telegram 通知
参考: oyz8/ClawCloud-Run & frankiejun/ClawCloud-Run
"""

import os
import re
import time
import json
import random
import base64
import argparse
import subprocess
from dataclasses import dataclass
from typing import Optional, Literal, List

import requests
try:
    from playwright.sync_api import sync_playwright, Page, BrowserContext
except ImportError:
    print("请先安装: pip install playwright && playwright install chromium")
    exit(1)

try:
    from nacl import encoding, public
except ImportError:
    print("警告: 未安装 PyNaCl，Secret 自动更新功能将不可用。请运行: pip install pynacl")

# ==================== 工具类 ====================

@dataclass
class Telegram:
    bot_token: str
    chat_id: str

    def send(self, text: str):
        if not self.bot_token or not self.chat_id:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        try:
            requests.post(url, data=data, timeout=10)
        except Exception as e:
            print(f"TG 发送失败: {e}")

    def photo(self, path: str, caption: str = ""):
        if not self.bot_token or not self.chat_id or not path or not os.path.exists(path):
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        try:
            with open(path, "rb") as f:
                requests.post(
                    url,
                    data={"chat_id": self.chat_id, "caption": caption},
                    files={"photo": f},
                    timeout=30,
                )
        except Exception as e:
            print(f"TG 图片发送失败: {e}")

class SecretUpdater:
    """自动更新 GitHub Repository Secrets"""
    def __init__(self, token: str, repo: str):
        self.token = token
        self.repo = repo
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }

    def _get_public_key(self):
        url = f"https://api.github.com/repos/{self.repo}/actions/secrets/public-key"
        res = requests.get(url, headers=self.headers, timeout=10)
        res.raise_for_status()
        return res.json()

    def _encrypt(self, public_key: str, secret_value: str) -> str:
        """使用 PyNaCl 加密 Secret"""
        public_key = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder)
        sealed_box = public.SealedBox(public_key)
        encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
        return base64.b64encode(encrypted).decode("utf-8")

    def update(self, secret_name: str, secret_value: str):
        try:
            key_data = self._get_public_key()
            encrypted_value = self._encrypt(key_data["key"], secret_value)
            
            url = f"https://api.github.com/repos/{self.repo}/actions/secrets/{secret_name}"
            data = {
                "encrypted_value": encrypted_value,
                "key_id": key_data["key_id"],
            }
            res = requests.put(url, headers=self.headers, json=data, timeout=10)
            if res.status_code in [201, 204]:
                print(f"✅ Secret '{secret_name}' 更新成功")
                return True
            else:
                print(f"❌ Secret 更新失败: {res.status_code} {res.text}")
        except Exception as e:
            print(f"❌ Secret 更新异常: {e}")
        return False

# ==================== 核心逻辑 ====================

class AutoLogin:
    def __init__(self):
        # 环境变量
        self.gh_token = os.getenv("GH_PAT") or os.getenv("REPO_TOKEN")
        self.gh_session = os.getenv("GH_SESSION", "")
        self.tg_bot = os.getenv("TG_BOT_TOKEN", "")
        self.tg_chat = os.getenv("TG_CHAT_ID", "")
        self.repo = os.getenv("GITHUB_REPOSITORY", "")
        
        # 配置
        self.LOGIN_ENTRY = "https://ap-northeast-1.run.claw.cloud/login"
        self.REGIONS = ["ap-northeast-1", "us-east-1", "eu-central-1", "ap-southeast-1"]
        self.TIMEOUT = 120000 # 120s
        self.shots = []
        
        # 初始化
        self.tg = Telegram(self.tg_bot, self.tg_chat)
        self.updater = SecretUpdater(self.gh_token, self.repo) if self.gh_token and self.repo else None

    def log(self, msg: str, level: str = "INFO"):
        prefix = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "❌", "SUCCESS": "✅"}.get(level, "📌")
        print(f"[{level}] {msg}")

    def shot(self, page: Page, name: str) -> Optional[str]:
        try:
            os.makedirs("screenshots", exist_ok=True)
            path = f"screenshots/{name}_{int(time.time())}.png"
            page.screenshot(path=path)
            self.shots.append(path)
            return path
        except:
            return None

    def apply_anti_detection(self, page: Page):
        """注入脚本抹除自动化特征"""
        page.add_init_script("""
            // 抹除 webdriver 特征
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            
            // 模拟 Chrome 特性
            window.chrome = { runtime: {} };
            
            // 模拟语言和插件
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            
            // 模拟硬件并发
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
            
            // 模拟 WebGL 渲染
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Intel Open Source Technology Center';
                if (parameter === 37446) return 'Mesa DRI Intel(R) HD Graphics 520 (Skylake GT2)';
                return getParameter.apply(this, arguments);
            };
        """)

    def wait_redirect(self, page: Page) -> Optional[str]:
        """等待重定向并检测区域"""
        self.log("等待重定向 (最长 120 秒)...", "INFO")
        start_time = time.time()
        
        while time.time() - start_time < 120:
            current_url = page.url
            title = page.title()
            
            # 检测是否遇到 Cloudflare 验证
            if "Just a moment..." in title or "Security Verification" in title or "cloudflare" in current_url:
                self.log("检测到 Cloudflare 验证，尝试自动点击...", "WARN")
                try:
                    # 尝试点击 CF 的复选框（如果可见）
                    cf_frame = page.frame_locator('iframe[src*="cloudflare"]')
                    if cf_frame.locator('input[type="checkbox"]').is_visible():
                        cf_frame.locator('input[type="checkbox"]').click()
                        self.log("已点击 Cloudflare 复选框", "SUCCESS")
                except:
                    pass
                time.sleep(5)
                continue

            # 检测是否进入控制台
            if ".run.claw.cloud/apps" in current_url or ".run.claw.cloud/profile" in current_url:
                match = re.search(r"https://(.*?)\.run\.claw\.cloud", current_url)
                region = match.group(1) if match else "unknown"
                self.log(f"成功进入控制台! 区域: {region}", "SUCCESS")
                return region
            
            # 检测是否卡在登录页（OAuth 循环）
            if "/login/signin" in current_url:
                github_btn = page.locator('button:has-text("GitHub"), a:has-text("GitHub")').first
                if github_btn.is_visible():
                    self.log("检测到登录循环，尝试再次点击 GitHub 登录...", "WARN")
                    github_btn.click()
                    time.sleep(5)
            
            # 检测是否需要授权
            if "github.com/login/oauth/authorize" in current_url:
                auth_btn = page.locator('button#js-oauth-authorize-btn')
                if auth_btn.is_visible():
                    self.log("点击 GitHub 授权按钮...", "INFO")
                    auth_btn.click()
            
            time.sleep(2)
        
        self.log(f"重定向超时，最后 URL: {page.url}", "ERROR")
        return None


    def run(self):
        self.log("🚀 开始 ClawCloud 自动登录流程", "INFO")
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            
            # 加载 Session
            if self.gh_session:
                try:
                    cookies = json.loads(self.gh_session)
                    context.add_cookies(cookies)
                    self.log("已加载 GH_SESSION Cookies", "INFO")
                except Exception as e:
                    self.log(f"加载 Cookies 失败: {e}", "WARN")

            page = context.new_page()
            self.apply_anti_detection(page)
            
            try:
                # 1. 访问登录页
                self.log(f"访问入口: {self.LOGIN_ENTRY}", "INFO")
                page.goto(self.LOGIN_ENTRY, wait_until="domcontentloaded", timeout=60000)
                
                # 2. 点击 GitHub 登录
                github_btn = page.locator('button:has-text("GitHub"), a:has-text("GitHub")').first
                if github_btn.is_visible(timeout=10000):
                    self.log("点击 GitHub 登录按钮", "INFO")
                    github_btn.click()
                else:
                    self.log("未找到 GitHub 登录按钮，可能已登录", "WARN")

                # 3. 等待重定向
                region = self.wait_redirect(page)
                
                if region:
                    # 登录成功
                    self.log("登录成功！", "SUCCESS")
                    self.shot(page, "success")
                    
                    # 提取并更新 Cookie
                    all_cookies = context.cookies()
                    github_cookies = [c for c in all_cookies if "github.com" in c.get("domain", "")]
                    if github_cookies:
                        cookie_str = json.dumps(github_cookies)
                        if self.updater:
                            self.updater.update("GH_SESSION", cookie_str)
                            self.log("GH_SESSION 已自动更新", "SUCCESS")
                    
                    # 保活访问
                    self.log("执行保活访问...", "INFO")
                    for r in self.REGIONS:
                        try:
                            page.goto(f"https://{r}.run.claw.cloud/apps", wait_until="domcontentloaded", timeout=30000)
                            self.log(f"保活 {r} ✅")
                            time.sleep(2)
                        except:
                            self.log(f"保活 {r} ❌", "WARN")
                    
                    self.tg.send(f"✅ <b>ClawCloud 登录成功</b>\n区域: {region}\n状态: 已保活并更新 Session")
                else:
                    # 登录失败
                    self.log("登录失败，重定向超时", "ERROR")
                    path = self.shot(page, "failed")
                    self.tg.send("❌ <b>ClawCloud 登录失败</b>\n原因: 重定向超时")
                    if path: self.tg.photo(path, "失败截图")

            except Exception as e:
                self.log(f"运行异常: {e}", "ERROR")
                path = self.shot(page, "error")
                self.tg.send(f"❌ <b>ClawCloud 运行异常</b>\n错误: {e}")
                if path: self.tg.photo(path, "错误截图")
            
            finally:
                browser.close()

if __name__ == "__main__":
    AutoLogin().run()
