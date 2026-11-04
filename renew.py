import os
import sys
import time
import random
import subprocess
import requests
from DrissionPage import ChromiumPage, ChromiumOptions
from proxy_helper import start_proxy

# 环境变量配置
EMAIL = os.environ.get("ACORE_EMAIL", "").strip()
PASSWORD = os.environ.get("ACORE_PASSWORD", "").strip()
SERVER_ID = os.environ.get("ACORE_SERVER_ID", "").strip()
PROXY_URL = os.environ.get("PROXY_URL", "").strip()
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

LOGIN_URL = "https://dash.acorehosting.com/auth/login"
DASH_URL = "https://dash.acorehosting.com"
DEFAULT_SERVER_ID = "1da63f5c"


def send_tg(msg):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        requests.post(
            url,
            json={"chat_id": TG_CHAT_ID, "text": f"【AcoreHosting 续期】\n{msg}"},
            timeout=10,
        )
    except Exception as e:
        print(f"[!] TG 通知发送失败: {e}")


def verify_proxy_alive(local_proxy):
    """校验本地代理是否真正能连通外部目标站点，若节点已失效则自动回退直连"""
    if not local_proxy:
        return False
    test_urls = [
        "https://www.cloudflare.com/cdn-cgi/trace",
        "https://dash.acorehosting.com",
        "https://api.ipify.org?format=json",
    ]
    proxies = {"http": local_proxy, "https": local_proxy}
    for url in test_urls:
        try:
            resp = requests.get(url, proxies=proxies, timeout=8)
            if resp.status_code < 500:
                print(f"[+] 代理可用性校验通过 ({url} -> HTTP {resp.status_code})")
                return True
        except Exception:
            continue
    print("[!] 代理节点无法连接外部网络 (Connection reset / 超时)，将自动切换为直连模式运行浏览器！")
    return False


def create_browser(local_proxy=None):
    """创建配置好防检测参数的 ChromiumPage 实例"""
    co = ChromiumOptions()
    co.auto_port()
    co.set_argument("--no-sandbox")
    co.set_argument("--test-type")
    co.set_argument("--disable-dev-shm-usage")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--window-position=0,0")
    co.set_argument("--window-size=1920,1080")
    co.set_argument("--lang=zh-TW")
    co.set_argument("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
    co.set_argument("--enforce-webrtc-ip-handling-policy")
    co.set_pref("webrtc.ip_handling_policy", "disable_non_proxied_udp")
    co.set_pref("credentials_enable_service", False)

    if local_proxy:
        print(f"[*] 浏览器已挂载代理: {local_proxy}")
        co.set_proxy(local_proxy)
    else:
        print("[*] 浏览器当前使用直连模式")

    return ChromiumPage(co)


def xdotool_click(page, client_x, client_y):
    """结合窗口偏移量使用系统级 xdotool 真实鼠标移动点击 + DrissionPage 内部点击"""
    try:
        offset = page.run_js("""
            return {
                x: (window.screenX || 0) + Math.max(0, (window.outerWidth - window.innerWidth) / 2),
                y: (window.screenY || 0) + Math.max(0, (window.outerHeight - window.innerHeight))
            };
        """) or {"x": 0, "y": 80}
        screen_x = int(offset["x"] + client_x + random.randint(-2, 2))
        screen_y = int(offset["y"] + client_y + random.randint(-2, 2))

        subprocess.run(
            ["xdotool", "mousemove", "--sync", str(screen_x - 35), str(screen_y - 20)],
            timeout=3,
            check=False,
        )
        time.sleep(0.12)
        subprocess.run(
            ["xdotool", "mousemove", "--sync", str(screen_x), str(screen_y), "click", "1"],
            timeout=3,
            check=False,
        )
    except Exception:
        pass

    try:
        page.actions.move_to((int(client_x), int(client_y))).click()
    except Exception:
        pass


def find_turnstile_coords_via_cdp(page):
    """通过 CDP DOM.getDocument(pierce=True) 穿透 Closed Shadow DOM 获取 Turnstile 复选框坐标"""
    try:
        doc = page.run_cdp("DOM.getDocument", depth=-1, pierce=True)
        root = doc.get("root", {})
        target_backend_ids = []

        def walk(node):
            node_name = (node.get("nodeName") or "").upper()
            attrs = node.get("attributes") or []
            attr_dict = {attrs[i]: attrs[i + 1] for i in range(0, len(attrs) - 1, 2)}

            if node_name == "IFRAME":
                src = (attr_dict.get("src") or "").lower()
                title = (attr_dict.get("title") or "").lower()
                id_val = (attr_dict.get("id") or "").lower()
                if (
                    "cloudflare" in src
                    or "turnstile" in src
                    or "challenge" in src
                    or "cloudflare" in title
                    or "cf-chl-widget" in id_val
                ):
                    if "backendNodeId" in node:
                        target_backend_ids.append(node["backendNodeId"])

            for child in node.get("children") or []:
                walk(child)
            for sr in node.get("shadowRoots") or []:
                walk(sr)
            if node.get("contentDocument"):
                walk(node["contentDocument"])

        walk(root)

        for bid in target_backend_ids:
            try:
                box = page.run_cdp("DOM.getBoxModel", backendNodeId=bid).get("model", {})
                content = box.get("border") or box.get("content")
                w = box.get("width", 0)
                h = box.get("height", 0)
                if content and w >= 50 and h >= 20:
                    left, top = content[0], content[1]
                    if left > 0 and top > 0:
                        return int(left + 28), int(top + h / 2)
            except Exception:
                continue
    except Exception:
        pass
    return None


def find_turnstile_coords_via_js(page):
    """通过页面 DOM 特征定位 Turnstile 验证框坐标"""
    try:
        coords = page.run_js("""
            const selectors = [
                'iframe[src*="cloudflare"], iframe[src*="turnstile"]',
                '#turnstile-wrapper',
                '#challenge-stage',
                '.cf-turnstile',
                '[data-sitekey]',
                'div[id^="cf-chl-widget-"]'
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el) {
                    const r = el.getBoundingClientRect();
                    if (r.width >= 60 && r.height >= 25 && r.top > 0) {
                        return {x: r.left + 28, y: r.top + Math.min(r.height / 2, 32)};
                    }
                }
            }

            const inp = document.querySelector('input[name="cf-turnstile-response"], input[name*="turnstile"]');
            if (inp && inp.parentElement) {
                const r = inp.parentElement.getBoundingClientRect();
                if (r.width >= 60 && r.height >= 25 && r.top > 0) {
                    return {x: r.left + 28, y: r.top + 32};
                }
            }

            const divs = document.querySelectorAll('div');
            for (const d of divs) {
                const r = d.getBoundingClientRect();
                if (r.width >= 240 && r.width <= 420 && r.height >= 55 && r.height <= 85 && r.top > 100) {
                    return {x: r.left + 28, y: r.top + r.height / 2};
                }
            }
            return null;
        """)
        if coords and coords.get("x") and coords.get("y"):
            return int(coords["x"]), int(coords["y"])
    except Exception:
        pass
    return None


def find_email_input(page, timeout=1.5):
    """
    定位登录页第一步的账号输入框，
    兼容无 type 属性的 React input 以及多语言 placeholder
    """
    selectors = [
        'xpath://input[contains(@placeholder, "郵件") or contains(@placeholder, "名稱") or contains(@placeholder, "邮件") or contains(@placeholder, "名称") or contains(@placeholder, "email") or contains(@placeholder, "Email") or contains(@placeholder, "username") or contains(@placeholder, "Username")]',
        'css:input[type="email"], input[type="text"], input[name="email"], input[name="username"]',
        'css:input:not([type="hidden"]):not([type="checkbox"]):not([type="password"]):not([type="submit"])',
    ]
    for sel in selectors:
        ele = page.ele(sel, timeout=timeout)
        if ele:
            return ele
    return None


def is_chrome_error_page(page):
    """检测浏览器是否因为代理断开而处于 Chrome 网络错误页"""
    try:
        url = (page.url or "").lower()
        html = (page.html or "").upper()
        if "chrome-error://" in url:
            return True
        error_codes = [
            "ERR_PROXY_CONNECTION_FAILED",
            "ERR_CONNECTION_RESET",
            "ERR_CONNECTION_CLOSED",
            "ERR_TIMED_OUT",
            "ERR_TUNNEL_CONNECTION_FAILED",
            "ERR_EMPTY_RESPONSE",
        ]
        return any(code in html for code in error_codes)
    except Exception:
        return False


def wait_for_login_page_ready(page, timeout=45):
    """
    等待登录页加载出账号输入框；
    若期间遇到全屏 Cloudflare 拦截页则自动点击通过
    """
    print("[*] 正在等待登录界面加载 (自动处理可能出现的 Cloudflare 验证)...")
    start_time = time.time()
    attempt = 0

    while time.time() - start_time < timeout:
        # 1. 如果已经出现账号输入框，说明已进入图1登录界面
        if find_email_input(page, timeout=1):
            print("[+] 登录页账号输入框已就绪！")
            return True

        # 2. 检查是否遇到 Chrome 代理错误页
        if is_chrome_error_page(page):
            print("[!] 检测到浏览器网络/代理连接错误页！")
            return False

        # 3. 检查是否存在全屏 Cloudflare 验证框
        coords = find_turnstile_coords_via_cdp(page) or find_turnstile_coords_via_js(page)
        title = (page.title or "").lower()
        html = (page.html or "").lower()
        is_cf_page = any(
            k in title or k in html
            for k in ["just a moment", "請稍候", "请稍候", "verify you are human", "security verification", "challenge-platform"]
        )

        if coords or is_cf_page:
            attempt += 1
            if coords:
                cx, cy = coords
                print(f"[*] [全屏验证 第{attempt}次] 点击 Cloudflare 验证框坐标: ({cx}, {cy})")
                xdotool_click(page, cx, cy)
            else:
                print(f"[*] [全屏验证 第{attempt}次] 使用居中兜底坐标点击: (510, 340)")
                xdotool_click(page, 510, 340)

            for _ in range(5):
                time.sleep(1)
                if find_email_input(page, timeout=0.5):
                    print("[+] 已通过全屏验证，登录页账号输入框已就绪！")
                    return True
        else:
            time.sleep(1.5)

    return find_email_input(page, timeout=1) is not None


def get_cf_token(page):
    """获取当前页面 Turnstile 隐藏 input 的 token 值"""
    try:
        val = page.run_js("""
            const inputs = document.querySelectorAll('input[name="cf-turnstile-response"], input[name*="turnstile"], input[name*="cf-"]');
            for (const inp of inputs) {
                if (inp.value && inp.value.length > 20) {
                    return inp.value;
                }
            }
            return "";
        """)
        return str(val or "")
    except Exception:
        return ""


def is_button_ready(btn):
    """判断按钮是否已解除 disabled 处于可点击状态"""
    if not btn:
        return False
    disabled_attr = btn.attr("disabled")
    aria_disabled = (btn.attr("aria-disabled") or "").lower()
    class_attr = (btn.attr("class") or "").lower()
    if (
        disabled_attr is not None
        or aria_disabled == "true"
        or "disabled" in class_attr
        or "cursor-not-allowed" in class_attr
        or "pointer-events-none" in class_attr
    ):
        return False
    return True


def find_step_button(page, keywords):
    """查找『繼續』或『登入』主操作按钮"""
    for kw in keywords:
        btn = page.ele(
            f'xpath://button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "{kw.lower()}")]',
            timeout=0.5,
        )
        if btn:
            return btn
    return page.ele('css:button[type="submit"]', timeout=0.5)


def pass_form_turnstile(page, stage_name, step_btn_keywords, timeout=35):
    """
    处理登录表单底部的 Cloudflare Turnstile 验证（对应图1 -> 图2），
    当检测到 Token 生成或『繼續/登入』按钮已变为可点击状态时立即放行
    """
    print(f"[*] 正在检查 [{stage_name}] 阶段的 Cloudflare 验证状态...")
    start_time = time.time()
    attempt = 0

    while time.time() - start_time < timeout:
        token = get_cf_token(page)
        btn = find_step_button(page, step_btn_keywords)

        # 如果已经有 token 且按钮已激活可点（如图2、图3的绿色“成功！”状态），直接通过
        if len(token) > 20 and is_button_ready(btn):
            print(f"[+] [{stage_name}] CF 验证已通过且按钮已激活！")
            return True
        if is_button_ready(btn) and attempt >= 1:
            print(f"[+] [{stage_name}] 检测到提交按钮已激活！")
            return True

        attempt += 1
        coords = find_turnstile_coords_via_cdp(page) or find_turnstile_coords_via_js(page)
        if coords:
            cx, cy = coords
            print(f"[*] [{stage_name} 第{attempt}次] 点击下方 Cloudflare 验证框坐标: ({cx}, {cy})")
            xdotool_click(page, cx, cy)
        elif attempt % 2 == 0:
            try:
                page.actions.key_down("Tab").key_up("Tab").wait(0.3).key_down("Space").key_up("Space")
            except Exception:
                pass

        for _ in range(4):
            time.sleep(1)
            token = get_cf_token(page)
            btn = find_step_button(page, step_btn_keywords)
            if len(token) > 20 or is_button_ready(btn):
                print(f"[+] [{stage_name}] CF 验证通过！")
                return True

    print(f"[!] [{stage_name}] CF 验证等待超时，尝试继续点击提交按钮...")
    return False


def fill_input_react(page, ele, text):
    """适配 React Hook Form 受控输入框的填写方式"""
    ele.click()
    time.sleep(0.2)
    ele.input(text, clear=True)
    time.sleep(0.3)
    try:
        page.run_js(
            """
            const el = arguments[0];
            const val = arguments[1];
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            if (setter && el.value !== val) {
                setter.call(el, val);
            }
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            """,
            ele,
            text,
        )
    except Exception:
        pass


def find_renew_button(page):
    """定位控制面板右上角的绿色 'Click to renew' 按钮（对应图5）"""
    selectors = [
        'xpath://button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "click to renew")]',
        'xpath://button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "renew")]',
        'xpath://a[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "click to renew")]',
        'xpath://button[contains(., "續期") or contains(., "续期")]',
    ]
    for sel in selectors:
        btn = page.ele(sel, timeout=2)
        if btn:
            return btn
    return None


def main():
    if not EMAIL or not PASSWORD:
        print("[-] 错误: 请在 GitHub Secrets 中设置 ACORE_EMAIL 和 ACORE_PASSWORD")
        sys.exit(1)

    proxy_proc = None
    page = None

    try:
        local_proxy, proxy_proc = start_proxy(PROXY_URL)
        if local_proxy and not verify_proxy_alive(local_proxy):
            local_proxy = None

        page = create_browser(local_proxy)

        # ==================== 打开登录页（若代理异常自动切换直连重试） ====================
        print(f"[*] 正在打开登录页: {LOGIN_URL}")
        page.get(LOGIN_URL)
        time.sleep(3)

        if not wait_for_login_page_ready(page, timeout=40):
            if local_proxy:
                print("[!] 挂载代理状态下未能加载出登录框，正在关闭代理切换为直连模式重试...")
                try:
                    page.quit()
                except Exception:
                    pass
                local_proxy = None
                page = create_browser(None)
                page.get(LOGIN_URL)
                time.sleep(3)
                wait_for_login_page_ready(page, timeout=40)
            else:
                print("[*] 正在刷新登录页重试...")
                page.refresh()
                time.sleep(4)
                wait_for_login_page_ready(page, timeout=35)

        # ==================== 第一步：输入账号 -> 过 CF 验证 -> 点击『繼續』（图1 & 图2） ====================
        print("[*] 正在定位并填写账号...")
        email_input = find_email_input(page, timeout=10)
        if not email_input:
            print(f"[!] 当前页面 URL: {page.url}, 标题: {page.title}")
            page.get_screenshot(path="error_no_email_input.png")
            raise RuntimeError("未找到账号输入框，请查看截图 error_no_email_input.png")

        fill_input_react(page, email_input, EMAIL)
        time.sleep(1)

        step1_btns = ["繼續", "继续", "continue", "next"]
        pass_form_turnstile(page, stage_name="第一步(账号页)", step_btn_keywords=step1_btns, timeout=35)
        page.get_screenshot(path="step1_after_cf.png")

        btn1 = find_step_button(page, step1_btns)
        if btn1:
            print(f"[*] 点击第一步按钮: {btn1.text}")
            btn1.click()
        else:
            email_input.input("\n")

        # ==================== 第二步：输入密码 -> 点击『登入』（图3） ====================
        print("[*] 等待密码输入框加载...")
        pwd_input = page.ele('css:input[type="password"]', timeout=15)
        if not pwd_input:
            print("[*] 第一次点击未进入密码页，补充验证并再次点击『繼續』...")
            pass_form_turnstile(page, stage_name="第一步重试", step_btn_keywords=step1_btns, timeout=20)
            btn1 = find_step_button(page, step1_btns)
            if btn1:
                btn1.click()
            pwd_input = page.ele('css:input[type="password"]', timeout=15)

        if not pwd_input:
            page.get_screenshot(path="error_step1.png")
            raise RuntimeError("未能进入密码输入界面，请查看截图 error_step1.png")

        print("[*] 正在填写密码...")
        fill_input_react(page, pwd_input, PASSWORD)
        time.sleep(1.5)

        step2_btns = ["登入", "登录", "login", "sign in"]
        btn2 = find_step_button(page, step2_btns)

        # 图3中第一步的 CF 验证通常保持“成功！”且登入按钮直接可用；若不可用再点击验证框
        if not is_button_ready(btn2):
            pass_form_turnstile(page, stage_name="第二步(密码页)", step_btn_keywords=step2_btns, timeout=30)
            btn2 = find_step_button(page, step2_btns)

        page.get_screenshot(path="step2_before_login.png")

        if btn2:
            print(f"[*] 点击第二步登录按钮: {btn2.text}")
            btn2.click()
        else:
            pwd_input.input("\n")

        # 等待跳转离开登录页；若 6 秒后仍在登录页则补点一次 CF 验证并再次点击登入
        for _ in range(6):
            time.sleep(1)
            if "auth/login" not in page.url:
                break

        if "auth/login" in page.url:
            print("[*] 仍在登录页，检查是否需要二次点击 CF 验证...")
            pass_form_turnstile(page, stage_name="第二步重试", step_btn_keywords=step2_btns, timeout=25)
            btn2 = find_step_button(page, step2_btns)
            if btn2:
                btn2.click()
            for _ in range(8):
                time.sleep(1)
                if "auth/login" not in page.url:
                    break

        # ==================== 第三步：进入服务器控制面板（图4 -> 图5） ====================
        print(f"[*] 登录后页面 URL: {page.url}")
        if "auth/login" in page.url:
            page.get_screenshot(path="error_login.png")
            raise RuntimeError("登录后仍停留在登录页，请检查账号密码或查看截图 error_login.png")

        time.sleep(3)
        page.get_screenshot(path="step3_server_list.png")

        if SERVER_ID:
            target_url = SERVER_ID if SERVER_ID.startswith("http") else f"{DASH_URL}/server/{SERVER_ID.strip('/')}"
            print(f"[*] 已配置 SERVER_ID，直接进入服务器面板: {target_url}")
            page.get(target_url)
        else:
            print("[*] 正在服务器列表页点击进入服务器 (图4)...")
            srv_target = (
                page.ele('xpath://a[contains(@href, "/server/")]', timeout=5)
                or page.ele('xpath://*[contains(text(), "Lick") or contains(text(), "已停權") or contains(text(), "Suspended")]', timeout=5)
                or page.ele('xpath://*[contains(text(), ":20") or contains(text(), "MiB") or contains(text(), "GiB")]', timeout=5)
            )
            if srv_target:
                print(f"[*] 点击服务器卡片元素: {srv_target.text}")
                srv_target.click()
                time.sleep(4)

            if "/server/" not in page.url:
                fallback_url = f"{DASH_URL}/server/{DEFAULT_SERVER_ID}"
                print(f"[*] 点击卡片未发生跳转，直接导航至服务器地址: {fallback_url}")
                page.get(fallback_url)

        time.sleep(5)
        print(f"[*] 已进入服务器控制面板: {page.url}")
        page.get_screenshot(path="panel_status.png")

        # ==================== 第四步：点击右上角绿色 Click to renew 按钮（图5） ====================
        print("[*] 正在查找右上角 Click to renew 按钮...")
        renew_btn = find_renew_button(page)

        if not renew_btn:
            page.get_screenshot(path="error_no_renew_btn.png")
            raise RuntimeError("未在页面上找到 Click to renew 按钮，请查看截图 error_no_renew_btn.png")

        print(f"[*] 找到续期按钮: '{renew_btn.text}'")

        status_hint = ""
        hint_ele = page.ele(
            'xpath://*[contains(text(), "Renew once suspended") or contains(text(), "Renew available") or contains(text(), "Until suspension") or contains(text(), "Deletes in")]',
            timeout=2,
        )
        if hint_ele:
            status_hint = hint_ele.text

        if not is_button_ready(renew_btn):
            msg = f"当前续期按钮 ({renew_btn.text}) 处于不可点击（未到期/冷却中）状态，无需操作。\n面板提示: {status_hint}"
            print(f"[=] {msg}")
            send_tg(msg)
        else:
            print(f"[+] 检测到 '{renew_btn.text}' 可点击！正在执行点击续期...")
            renew_btn.click()
            time.sleep(5)

            # 若点击后出现弹窗验证或确认按钮则一并处理
            coords = find_turnstile_coords_via_cdp(page) or find_turnstile_coords_via_js(page)
            if coords:
                pass_form_turnstile(page, stage_name="续期弹窗", step_btn_keywords=["confirm", "確認", "确认", "renew"], timeout=25)

            confirm_btn = page.ele(
                'xpath://button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "confirm") or contains(., "確認") or contains(., "确认")]',
                timeout=3,
            )
            if confirm_btn and is_button_ready(confirm_btn):
                print(f"[*] 点击确认按钮: {confirm_btn.text}")
                confirm_btn.click()
                time.sleep(4)

            # 续期解封后如果『啟動 / Start』按钮亮起，顺便启动服务器
            start_btn = page.ele(
                'xpath://button[normalize-space(.)="啟動" or normalize-space(.)="启动" or translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")="start"]',
                timeout=3,
            )
            if start_btn and is_button_ready(start_btn):
                print("[*] 检测到『啟動』按钮可用，正在启动服务器...")
                start_btn.click()
                time.sleep(3)

            page.get_screenshot(path="renew_result.png")
            msg = f"✅ 已成功点击 '{renew_btn.text}' 完成续期！\n当前面板 URL: {page.url}"
            print(f"[+] {msg}")
            send_tg(msg)

    except Exception as e:
        err_msg = f"❌ 运行异常: {str(e)}"
        print(err_msg)
        if page:
            try:
                page.get_screenshot(path="error_final.png")
            except Exception:
                pass
        send_tg(err_msg)
        sys.exit(1)
    finally:
        if page:
            try:
                page.quit()
            except Exception:
                pass
        if proxy_proc:
            try:
                proxy_proc.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    main()
