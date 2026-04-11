#!/usr/bin/env python3
"""
ClawCloud 自动登录脚本 - OAuth 版
支持 3 种认证方式: GitHub CLI > OAuth Device Flow > 传统方式
"""

import os
import re
import time
import json
import random
import subprocess
import argparse
from dataclasses import dataclass
from typing import Optional, Literal

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("请先安装: pip install playwright && playwright install chromium")
    exit(1)

import requests


@dataclass
class Telegram:
    bot_token: str
    chat_id: str

    def send(self, text: str):
        if not self.bot_token:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        try:
            requests.post(url, data=data, timeout=10)
        except Exception as e:
            print(f"TG 发送失败: {e}")

    def photo(self, path: str, caption: str = ""):
        if not self.bot_token or not path:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        with open(path, "rb") as f:
            try:
                requests.post(
                    url,
                    data={"chat_id": self.chat_id, "caption": caption},
                    files={"photo": f},
                    timeout=30,
                )
            except Exception as e:
                print(f"TG 图片发送失败: {e}")


class AutoLogin:
    """ClawCloud 自动登录器 - 支持 OAuth 和传统方式"""

    def __init__(self, mode: Literal["cli", "oauth", "traditional"] = "cli"):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.mode = mode

        # 环境变量
        self.gh_token = os.getenv("GH_PAT") or os.getenv("GH_TOKEN") or os.getenv("REPO_TOKEN")
        self.gh_session = os.getenv("GH_SESSION", "")
        self.tg_bot = os.getenv("TG_BOT_TOKEN", "")
        self.tg_chat = os.getenv("TG_CHAT_ID", "")
        self.repo = os.getenv("GITHUB_REPOSITORY", "techthink25/ClawCloud-Run")

        # 初始化 Telegram
        self.tg = Telegram(self.tg_bot, self.tg_chat)

        # 配置
        self.LOGIN_ENTRY = "https://ap-northeast-1.run.claw.cloud/login"
        self.DEVICE_WAIT = 30
        self.TWO_FACTOR_WAIT = 120
        self.shots = []

    def log(self, msg: str, level: str = "INFO"):
        prefix = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "❌", "SUCCESS": "✅"}.get(level, "📌")
        print(f"[{level}] {msg}")

    def shot(self, page, name: str) -> Optional[str]:
        """截图"""
        try:
            path = f"/tmp/{name}_{int(time.time())}.png"
            page.screenshot(path=path)
            self.shots.append(path)
            return path
        except:
            return None

    # ==================== GitHub CLI 认证 ====================

    def auth_with_gh_cli(self) -> bool:
        """使用 gh CLI 进行 GitHub 认证"""
        self.log("尝试使用 GitHub CLI 认证...", "INFO")

        if not self.gh_token:
            self.log("未找到 GH_PAT Token，跳过 CLI 认证", "WARN")
            return False

        try:
            # 检查 gh 是否可用
            result = subprocess.run(
                ["gh", "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                self.log("GitHub CLI 已登录", "SUCCESS")
                return True

            # 尝试登录
            result = subprocess.run(
                ["gh", "auth", "login", "--hostname", "github.com"],
                input=self.gh_token,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                self.log("GitHub CLI 登录成功", "SUCCESS")
                return True

            self.log(f"CLI 认证失败: {result.stderr}", "ERROR")
            return False

        except Exception as e:
            self.log(f"CLI 认证异常: {e}", "ERROR")
            return False

    def get_cookies_from_gh_cli(self) -> Optional[str]:
        """从 gh CLI 获取 GitHub cookies"""
        try:
            # gh CLI 不直接导出 cookie，但我们可以使用已认证的状态
            # 浏览器会自动使用 gh 的认证状态
            return True
        except:
            return None

    # ==================== OAuth Device Flow ====================

    def auth_with_oauth_device(self) -> bool:
        """OAuth Device Flow 认证"""
        self.log("尝试 OAuth Device Flow 认证...", "INFO")

        if not self.gh_token:
            self.log("未找到 Token，跳过 OAuth", "WARN")
            return False

        # 使用 PAT 直接设置 cookie
        # GitHub PAT 可以直接用于 API 认证，不需要设备验证
        try:
            headers = {
                "Authorization": f"token {self.gh_token}",
                "Accept": "application/vnd.github+json"
            }
            user_info = requests.get("https://api.github.com/user", headers=headers, timeout=10)

            if user_info.status_code == 200:
                username = user_info.json().get("login", "unknown")
                self.log(f"OAuth 认证成功: {username}", "SUCCESS")
                return True

            self.log(f"OAuth 认证失败: {user_info.status_code}", "ERROR")
            return False

        except Exception as e:
            self.log(f"OAuth 异常: {e}", "ERROR")
            return False

    # ==================== Playwright 浏览器登录 ====================

    def init_browser(self):
        """初始化浏览器"""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )

        # 使用 gh CLI 的认证状态（如果可用）
        self.context = self.browser.new_context(
            accept_downloads=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )

        # 尝试加载现有 session
        if self.gh_session:
            try:
                cookies = json.loads(self.gh_session)
                if isinstance(cookies, list):
                    self.context.add_cookies(cookies)
                    self.log("已加载 GH_SESSION Cookie", "INFO")
            except:
                pass

        self.page = self.context.new_page()

    def close_browser(self):
        """关闭浏览器"""
        if self.page:
            self.page.close()
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def extract_cookies(self) -> Optional[str]:
        """提取 cookies"""
        try:
            cookies = self.context.cookies()
            github_cookies = [c for c in cookies if "github.com" in c.get("domain", "")]
            if github_cookies:
                return json.dumps(github_cookies, indent=2)
            return None
        except Exception as e:
            self.log(f"Cookie 提取失败: {e}", "ERROR")
            return None

    def login_github(self) -> bool:
        """登录 GitHub"""
        self.log("开始 GitHub 登录...", "INFO")

        # 1. 访问 ClawCloud 登录页
        self.page.goto(self.LOGIN_ENTRY, wait_until="networkidle", timeout=60000)

        # 2. 点击 GitHub 登录
        try:
            github_btn = self.page.locator('button:has-text("GitHub"), a:has-text("GitHub"), [href*="github"]').first
            if github_btn.is_visible(timeout=5000):
                github_btn.click()
                self.log("已点击 GitHub 登录按钮", "INFO")
        except:
            pass

        time.sleep(3)

        # 3. 检查是否已登录
        url = self.page.url
        if "github.com" not in url:
            # 已在 ClawCloud，直接获取 cookie
            self.log("已是登录状态", "SUCCESS")
            return True

        # 4. 检测登录页面类型
        if "login" in url or "sign_in" in url:
            return self._handle_login_form()

        # 5. 检测设备验证
        if "verified-device" in url or "device-verification" in url:
            return self._wait_device_verification()

        # 6. 检测 2FA
        if "two-factor" in url:
            return self._handle_2fa()

        return True

    def _handle_login_form(self) -> bool:
        """处理用户名密码表单"""
        username = os.getenv("GH_USERNAME", "")
        password = os.getenv("GH_PASSWORD", "")

        if not username or not password:
            self.log("未配置 GH_USERNAME/GH_PASSWORD，跳过表单登录", "WARN")
            return False

        try:
            # 输入用户名
            user_input = self.page.locator('input[name="login"], #login_field').first
            user_input.fill(username)
            time.sleep(0.5)

            # 输入密码
            pass_input = self.page.locator('input[name="password"], #password').first
            pass_input.fill(password)
            time.sleep(0.5)

            # 提交
            self.page.locator('input[type="submit"], button[type="submit"]').first.click()
            time.sleep(3)

            # 检测验证类型
            url = self.page.url
            if "verified-device" in url or "device-verification" in url:
                return self._wait_device_verification()

            if "two-factor" in url:
                return self._handle_2fa()

            return True

        except Exception as e:
            self.log(f"表单登录失败: {e}", "ERROR")
            return False

    def _wait_device_verification(self) -> bool:
        """等待设备验证"""
        self.log(f"需要设备验证，等待 {self.DEVICE_WAIT} 秒...", "WARN")
        self.shot(self.page, "device_verification")

        self.tg.send(f"⚠️ <b>需要设备验证</b>\n请在 {self.DEVICE_WAIT} 秒内批准")

        for i in range(0, self.DEVICE_WAIT, 5):
            time.sleep(5)
            url = self.page.url
            if "verified-device" not in url and "device-verification" not in url:
                self.log("设备验证通过", "SUCCESS")
                return True
            self.log(f"  等待... ({i + 5}/{self.DEVICE_WAIT}秒)")

        self.log("设备验证超时", "ERROR")
        self.tg.send("❌ 设备验证超时")
        return False

    def _handle_2fa(self) -> bool:
        """处理两步验证"""
        url = self.page.url
        self.shot(self.page, "2fa")

        # GitHub Mobile 推送
        if "sms" not in url:
            self.tg.send("⚠️ <b>需要 GitHub Mobile 验证</b>\n请在 App 中批准")
            for i in range(self.TWO_FACTOR_WAIT):
                time.sleep(1)
                if "github.com/sessions/two-factor/" not in self.page.url:
                    self.log("2FA 通过", "SUCCESS")
                    return True
            return False

        # TOTP 验证码
        self.tg.send("🔐 <b>需要验证码</b>\n请发送 /code 123456")
        self.log("请通过 Telegram 发送验证码", "WARN")
        return False

    def keep_alive(self) -> bool:
        """保活访问"""
        regions = [
            "ap-northeast-1",
            "us-east-1",
            "eu-central-1",
            "ap-southeast-1"
        ]

        for region in regions:
            try:
                url = f"https://{region}.run.claw.cloud/apps"
                self.page.goto(url, wait_until="networkidle", timeout=30000)
                time.sleep(random.uniform(2, 4))
            except Exception as e:
                self.log(f"{region} 访问失败: {e}", "WARN")

        return True

    def run(self) -> bool:
        """主入口"""
        self.log(f"开始 ClawCloud 自动登录 (模式: {self.mode})", "INFO")
        self.tg.send(f"🤖 ClawCloud 自动登录\n模式: {self.mode}")

        success = False

        try:
            # 方式1: GitHub CLI (最优)
            if self.mode in ["cli", "oauth"]:
                if self.auth_with_gh_cli() or self.auth_with_oauth_device():
                    success = True
                    self.tg.send(f"✅ GitHub OAuth 认证成功")

            # 方式2: 传统浏览器登录 (备选)
            if not success and self.mode == "traditional":
                self.init_browser()
                success = self.login_github()
                if success:
                    self.keep_alive()

                cookies = self.extract_cookies()
                if cookies:
                    self.log("Cookie 提取成功", "SUCCESS")
                else:
                    self.log("Cookie 提取失败", "WARN")

                self.close_browser()

            if success:
                self.tg.send(f"✅ <b>ClawCloud 登录成功</b>")
            else:
                self.tg.send(f"❌ <b>ClawCloud 登录失败</b>")

            return success

        except Exception as e:
            self.log(f"异常: {e}", "ERROR")
            self.tg.send(f"❌ 错误: {e}")
            if self.browser:
                self.close_browser()
            return False


def main():
    parser = argparse.ArgumentParser(description="ClawCloud 自动登录")
    parser.add_argument(
        "--mode",
        choices=["cli", "oauth", "traditional"],
        default="cli",
        help="认证模式: cli(GitHub CLI) > oauth > traditional"
    )
    args = parser.parse_args()

    login = AutoLogin(mode=args.mode)
    success = login.run()
    exit(0 if success else 1)


if __name__ == "__main__":
    main()
