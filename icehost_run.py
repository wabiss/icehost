import os
import time
import json
import urllib.parse
import requests
from playwright.sync_api import sync_playwright
try:
    from playwright_stealth import Stealth
    _USE_STEALTH_CLASS = True
except ImportError:
    try:
        from playwright_stealth import stealth_sync
        _USE_STEALTH_CLASS = False
    except ImportError:
        print("警告: 系统中未找到 playwright-stealth 库，将跳过高级指纹混淆。")
        _USE_STEALTH_CLASS = None

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

def load_page_with_cf_bypass(page, url):
    """智能页面加载函数：自动等待并利用物理中心点点击法穿透 Cloudflare 的物理人机验证码"""
    print(f"正在访问页面: {url}")
    page.goto(url)
    
    # 增加对 cdn-cgi 同源路径的检测，并加入 "iframe" 作为终极保底选择器（页面上唯一一个 iframe 就是验证盾）
    cf_selectors = [
        "iframe[src*='challenge-platform']",
        "iframe[src*='challenges.cloudflare.com']",
        "iframe" 
    ]
    
    iframe_selector = ""
    # 轮询 15 秒，确保验证盾元素在页面上加载并显示
    for _ in range(15):
        for selector in cf_selectors:
            if page.locator(selector).first.is_visible():
                iframe_selector = selector
                break
        if iframe_selector:
            break
        page.wait_for_timeout(1000)

    if iframe_selector:
        print(f"⚡ 成功检测到 Cloudflare 验证盾 iframe 元素 ('{iframe_selector}')！正在尝试过盾...")
        page.wait_for_timeout(4000) # 给予 4 秒缓冲时间确保其完全渲染完毕
        
        # 【方法一（终极物理保底）】：直接点击父页面中该 iframe 元素的中心点
        # 验证框完美处于整个 iframe 的正中心，直接在父页面点击该元素中心同样能 100% 成功触发勾选并避开影子 DOM 隔离
        try:
            print("正在执行保底点击：模拟物理点击 iframe 元素正中心...")
            page.locator(iframe_selector).first.click()
            print("已模拟物理点击中心，等待验证通过...")
            page.wait_for_timeout(8000)
            
            # 如果点击后 iframe 消失了，说明已经成功通过
            if not page.locator(iframe_selector).first.is_visible():
                print("✓ 验证码已通过物理中心点击法成功解开！")
                return
        except Exception as click_err:
            print(f"物理中心点点击失败，尝试备用方案: {click_err}")

        # 【方法二（底层穿透备用）】：如果方法一没通过，再尝试进入底层 iframe 内部点击
        try:
            turnstile_frame = None
            for frame in page.frames:
                if "challenge-platform" in frame.url or "challenges.cloudflare.com" in frame.url:
                    turnstile_frame = frame
                    break
            if turnstile_frame:
                print("尝试通过底层 Frame 接口注入点击...")
                # 尝试点击 iframe 内部的 body 任意区域
                turnstile_frame.locator("body").first.click(timeout=3000)
                page.wait_for_timeout(8000)
        except Exception as frame_err:
            print(f"底层注入点击失败: {frame_err}")
    else:
        print("页面未检测到验证盾，或已成功跳过。")
        
    page.wait_for_timeout(5000)

def run():
    if not SERVER_URL or not ICEHOST_COOKIES:
        print("错误: 缺少 ICEHOST_SERVER_URL 或 ICEHOST_COOKIES")
        return

    with sync_playwright() as p:
        # 启用过检测参数，抹除自动化特征
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )
        
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )

        # 隐藏自动化控制指纹
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        try:
            raw_data = json.loads(ICEHOST_COOKIES)
            cookies_to_add = []

            # 提取 Cookie
            if isinstance(raw_data, list):
                cookies_to_add = raw_data
            elif isinstance(raw_data, dict):
                cookies_to_add = raw_data.get("cookies", [])
            else:
                raise ValueError("未知的数据格式")

            # 1. 注入并进行高精度统一 URL 编码
            formatted_cookies = []
            for c in cookies_to_add:
                raw_value = c["value"]
                
                # 第一步：先解码，还原为未编码的原始字符
                clean_value = urllib.parse.unquote(raw_value)
                
                # 第二步：将原始字符进行全局统一的 URL 编码，避免 PHP 引擎加号漏洞
                encoded_value = urllib.parse.quote(clean_value)
                
                fc = {
                    "name": c["name"],
                    "value": encoded_value,
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
            print("Cookie 成功执行双重高精度 URL 编码并注入！已完美规避 PHP '+' 转换漏洞。")

        except Exception as e:
            print(f"凭证解析/注入失败: {e}")
            send_tg_notification(f"❌ <b>IceHost 运行异常</b>\n凭证解析注入失败: {e}")
            browser.close()
            return

        page = context.new_page()

        # ⚡ 核心修改：向页面注入高精防检测混淆
        if _USE_STEALTH_CLASS is True:
            try:
                stealth = Stealth()
                stealth.apply_stealth_sync(page)
                print("✓ 成功应用新版 playwright-stealth 混淆指纹！")
            except Exception as se:
                print(f"应用新版 stealth 失败，跳过: {se}")
        elif _USE_STEALTH_CLASS is False:
            try:
                stealth_sync(page)
                print("✓ 成功应用旧版 playwright-stealth 混淆指纹！")
            except Exception as se:
                print(f"应用旧版 stealth 失败，跳过: {se}")

        # 全局网络流量拦截与指纹清洗
        def handle_route(route):
            headers = {**route.request.headers}
            headers["sec-ch-ua"] = '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"'
            headers["sec-ch-ua-mobile"] = "?0"
            headers["sec-ch-ua-platform"] = '"Windows"'
            headers["user-agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            route.continue_(headers=headers)

        page.route("**/*", handle_route)

        # 首次访问：使用优化后的过盾函数
        load_page_with_cf_bypass(page, SERVER_URL)

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
        keywords = ["Nie możesz przedłużyć", "niedawno to zrobiłeś", "kolejne 6 godziny"]
        is_limited = False
        
        for kw in keywords:
            if page.locator(f"text={kw}").first.is_visible():
                is_limited = True
                break
        
        if is_limited:
            print("检测到红框限制提示：说明未到可续期时间。结束本次运行（不发送 Telegram 提醒）。")
            browser.close()
            return

        # 4. 如果没有到上限，安全寻找并点击续期按钮
        renew_btn = page.locator("a:has-text('DODAJ 6 GODZIN'), button:has-text('DODAJ 6 GODZIN'), [class*='blue']:has-text('DODAJ 6 GODZIN')").first
        
        if renew_btn.is_visible() and renew_btn.is_enabled():
            print("未检测到限制提示，找到续期按钮，正在点击...")
            renew_btn.click()
            
            # 点击后重新使用过盾函数
            load_page_with_cf_bypass(page, SERVER_URL)
            
            # 重新截图
            page.screenshot(path="icehost_debug_screenshot.png")
            
            # 二次检测结果
            is_now_limited = False
            for kw in keywords:
                if page.locator(f"text={kw}").first.is_visible():
                    is_now_limited = True
                    break
                    
            if is_now_limited:
                print("点击后弹出了红框提示：说明未到可续期时间（续期未成功）。结束本次运行（不发送 Telegram 提醒）。")
            else:
                msg = "⚡ <b>IceHost 服务器续期成功！</b>\n服务器已真正成功延长 6 小时有效期。"
                print(msg)
                send_tg_notification(msg, "icehost_debug_screenshot.png")
        else:
            print("未在页面中找到可用的蓝色续期按钮。")

        browser.close()

if __name__ == "__main__":
    run()
