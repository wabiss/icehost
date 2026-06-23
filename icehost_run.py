import os
import time
import json
import urllib.parse # 引入 URL 解码库
import requests
from playwright.sync_api import sync_playwright

SERVER_URL = os.getenv("ICEHOST_SERVER_URL")
ICEHOST_COOKIES = os.getenv("ICEHOST_COOKIES")

def send_tg_notification(message, photo_path=None):
    """发送结果和截图至 Telegram"""
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        print("未配置 TG 机器人变量，跳过发送 TG 推送。")
        return

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        requests.post(url, json=payload)
        print("TG 状态通知发送成功。")
    except Exception as e:
        print(f"发送 TG 消息异常: {e}")

    if photo_path and os.path.exists(photo_path):
        try:
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            with open(photo_path, "rb") as f:
                files = {"photo": f}
                data = {"chat_id": chat_id, "caption": "IceHost 实时画面"}
                requests.post(url, data=data, files=files)
            print("TG 截图发送成功。")
        except Exception as e:
            print(f"发送 TG 截图异常: {e}")

def run():
    if not SERVER_URL or not ICEHOST_COOKIES:
        print("错误: 缺少 ICEHOST_SERVER_URL 或 ICEHOST_COOKIES")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        try:
            raw_data = json.loads(ICEHOST_COOKIES)
            cookies_to_add = []
            local_storage_to_add = {}

            if isinstance(raw_data, list):
                print("检测到纯 Cookie 格式数据...")
                cookies_to_add = raw_data
            elif isinstance(raw_data, dict):
                print("检测到合并格式数据...")
                cookies_to_add = raw_data.get("cookies", [])
                local_storage_to_add = raw_data.get("localStorage", {})
            else:
                raise ValueError("未知的数据格式")

            # 1. 注入 Cookies
            formatted_cookies = []
            for c in cookies_to_add:
                raw_value = c["value"]
                # 核心修复：对 cookie 的 value 进行 URL 解码，还原 %3D 为 =，防止 Playwright 进行二次编码导致 403 报错
                decoded_value = urllib.parse.unquote(raw_value)
                
                fc = {
                    "name": c["name"],
                    "value": decoded_value,
                    "domain": c["domain"],
                    "path": c.get("path", "/")
                }
                if "expirationDate" in c:
                    fc["expires"] = int(c["expirationDate"])
                if "secure" in c:
                    fc["secure"] = c["secure"]
                if "httpOnly" in c:
                    fc["httpOnly"] = c["httpOnly"]
                if "sameSite" in c:
                    ss = str(c["sameSite"]).lower()
                    if ss in ["no_restriction", "none"]:
                        fc["sameSite"] = "None"
                    elif ss == "lax":
                        fc["sameSite"] = "Lax"
                    elif ss == "strict":
                        fc["sameSite"] = "Strict"
                formatted_cookies.append(fc)
            
            context.add_cookies(formatted_cookies)
            print("Cookie 注入并解码成功！")

            # 2. 注入 LocalStorage
            if local_storage_to_add:
                init_script = ""
                for k, v in local_storage_to_add.items():
                    escaped_k = k.replace('\\', '\\\\').replace("'", "\\'")
                    escaped_v = v.replace('\\', '\\\\').replace("'", "\\'")
                    init_script += f"window.localStorage.setItem('{escaped_k}', '{escaped_v}');\n"
                
                context.add_init_script(init_script)
                print("LocalStorage 注入设置成功！")

        except Exception as e:
            print(f"凭证解析/注入失败: {e}")
            send_tg_notification(f"❌ <b>IceHost 运行异常</b>\n凭证解析注入失败: {e}")
            browser.close()
            return

        page = context.new_page()
        print(f"正在访问 IceHost 面板: {SERVER_URL}")
        page.goto(SERVER_URL)
        page.wait_for_timeout(10000)

        # 首次截图
        page.screenshot(path="icehost_debug_screenshot.png")

        # 判断登录状态
        if "login" in page.url or page.locator("input[type='email']").first.is_visible():
            msg = "❌ <b>IceHost 登录失效！</b>\n请在浏览器重新提取并更新 ICEHOST_COOKIES。"
            print(msg)
            send_tg_notification(msg, "icehost_debug_screenshot.png")
            browser.close()
            return

        # 3. 检测是否已经达到了 6 小时限制（波兰语特征词）
        page_text = page.locator("body").text_content() or ""
        if "Nie możesz przedłużyć" in page_text or "niedawno" in page_text:
            print("检测到限制提示：当前服务器已续期满6小时上限。结束本次运行。")
            browser.close()
            return

        # 4. 如果没有到上限，安全寻找并点击续期按钮
        renew_btn = page.locator("a:has-text('DODAJ 6 GODZIN'), button:has-text('DODAJ 6 GODZIN'), [class*='blue']:has-text('DODAJ 6 GODZIN')").first
        
        if renew_btn.is_visible() and renew_btn.is_enabled():
            print("找到续期按钮，正在点击...")
            renew_btn.click()
            page.wait_for_timeout(10000) # 等待 10 秒
            
            # 重新截图
            page.screenshot(path="icehost_debug_screenshot.png")
            
            # 二次检测结果
            new_page_text = page.locator("body").text_content() or ""
            if "Nie możesz przedłużyć" in new_page_text or "niedawno" in new_page_text:
                msg = "⚡ <b>IceHost 服务器续期成功！</b>\n已成功延长 6 小时效期（已达最大上限）。"
                print(msg)
                send_tg_notification(msg, "icehost_debug_screenshot.png")
            else:
                msg = "ℹ️ <b>IceHost 续期指令已发送</b>\n按钮已点击，请检查下方截图确认是否成功。"
                print(msg)
                send_tg_notification(msg, "icehost_debug_screenshot.png")
        else:
            print("未在页面中找到可用的蓝色续期按钮。")

        browser.close()

if __name__ == "__main__":
    run()
