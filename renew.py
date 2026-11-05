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


def click_single_clean(page, ele):
    """
    干净地单次点击 Mantine / React 按钮（避免连点 3 次导致打开弹窗后又把弹窗关掉）
    """
    if not ele:
        return
    try:
        ele.scroll.to_see()
        time.sleep(0.2)
    except Exception:
        pass

    try:
        ele.click()
        return
    except Exception:
        pass

    try:
        ele.click(by_js=True)
    except Exception:
        pass


def find_turnstile_coords_via_cdp(page):
    """通过 CDP DOM.getDocument(pierce=True) 穿透 Closed Shadow DOM 获取真实 Turnstile 复选框坐标"""
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

        for bid in reversed(target_backend_ids):
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
    """严格通过 Turnstile 专属 DOM 特征定位验证框坐标"""
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
                const els = document.querySelectorAll(sel);
                for (let i = els.length - 1; i >= 0; i--) {
                    const r = els[i].getBoundingClientRect();
                    if (r.width >= 60 && r.height >= 25 && r.top > 0) {
                        return {x: r.left + 28, y: r.top + Math.min(r.height / 2, 32)};
                    }
                }
            }

            const inputs = document.querySelectorAll('input[name="cf-turnstile-response"], input[name*="turnstile"]');
            for (let i = inputs.length - 1; i >= 0; i--) {
                const p = inputs[i].parentElement;
                if (p) {
                    const r = p.getBoundingClientRect();
                    if (r.width >= 60 && r.height >= 25 && r.top > 0) {
                        return {x: r.left + 28, y: r.top + 32};
                    }
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
    """定位登录页第一步的账号输入框"""
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
    """检测浏览器是否处于网络错误页"""
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
    """等待登录页加载出账号输入框；若遇到全屏 Cloudflare 拦截页则自动点击通过"""
    print("[*] 正在等待登录界面加载 (自动处理可能出现的 Cloudflare 验证)...")
    start_time = time.time()
    attempt = 0

    while time.time() - start_time < timeout:
        if find_email_input(page, timeout=1):
            print("[+] 登录页账号输入框已就绪！")
            return True

        if is_chrome_error_page(page):
            print("[!] 检测到浏览器网络/代理连接错误页！")
            return False

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
            for (let i = inputs.length - 1; i >= 0; i--) {
                if (inputs[i].value && inputs[i].value.length > 20) {
                    return inputs[i].value;
                }
            }
            return "";
        """)
        return str(val or "")
    except Exception:
        return ""


def reset_cf_tokens(page):
    """清空页面上残留的旧 Turnstile token"""
    try:
        page.run_js("""
            const inputs = document.querySelectorAll('input[name="cf-turnstile-response"], input[name*="turnstile"]');
            for (const inp of inputs) {
                inp.value = "";
            }
        """)
    except Exception:
        pass


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
    """查找登录步骤按钮"""
    for kw in keywords:
        btn = page.ele(
            f'xpath://button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "{kw.lower()}")]',
            timeout=0.5,
        )
        if btn:
            return btn
    return page.ele('css:button[type="submit"]', timeout=0.5)


def pass_form_turnstile(page, stage_name, step_btn_keywords=None, timeout=35):
    """处理表单内的 Cloudflare Turnstile 验证"""
    print(f"[*] 正在检查 [{stage_name}] 阶段的 Cloudflare 验证状态...")
    start_time = time.time()
    attempt = 0

    while time.time() - start_time < timeout:
        token = get_cf_token(page)
        btn = find_step_button(page, step_btn_keywords) if step_btn_keywords else None

        if len(token) > 20:
            print(f"[+] [{stage_name}] 检测到 Turnstile Token (长度 {len(token)})，CF 验证通过！")
            return True
        if btn and is_button_ready(btn) and attempt >= 1:
            print(f"[+] [{stage_name}] 检测到提交按钮已激活，CF 验证通过！")
            return True

        attempt += 1
        coords = find_turnstile_coords_via_cdp(page) or find_turnstile_coords_via_js(page)
        if coords:
            cx, cy = coords
            print(f"[*] [{stage_name} 第{attempt}次] 点击 Cloudflare 验证框坐标: ({cx}, {cy})")
            xdotool_click(page, cx, cy)
        elif attempt % 2 == 0:
            try:
                page.actions.key_down("Tab").key_up("Tab").wait(0.3).key_down("Space").key_up("Space")
            except Exception:
                pass

        for _ in range(4):
            time.sleep(1)
            token = get_cf_token(page)
            btn = find_step_button(page, step_btn_keywords) if step_btn_keywords else None
            if len(token) > 20 or (btn and is_button_ready(btn)):
                print(f"[+] [{stage_name}] CF 验证通过！")
                return True

    print(f"[!] [{stage_name}] CF 验证等待超时，尝试继续...")
    return False


def fill_input_react(page, ele, text):
    """适配 React 受控输入框的填写方式"""
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
    """定位控制面板右上角的绿色 'Click to renew' 按钮"""
    selectors = [
        'xpath://button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "click to renew")]',
        'xpath://a[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "click to renew")]',
        'xpath://div[contains(@class, "acore-renewal-action")]//button',
        'xpath://button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "renew")]',
    ]
    for sel in selectors:
        btn = page.ele(sel, timeout=1.5)
        if btn:
            return btn
    return None


def find_modal_confirm_button(page):
    """
    精准查找点击 Click to renew 后弹出的 Mantine Dialog 内的 'Unsuspend' / 'Renew' 确认按钮
    （对应日志捕获的弹窗：'Unsuspend server - Unsuspend Lick to renew it. [Cancel] [Unsuspend]'）
    """
    # 1. 直接优先查找文字为 Unsuspend / 解除停權 的 button
    direct_selectors = [
        'xpath://*[@role="dialog" or contains(@class, "mantine-Modal") or contains(@class, "mantine-Dialog")]//button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "unsuspend")]',
        'xpath://button[translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")="unsuspend"]',
        'xpath://button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "unsuspend") and not(contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "click to renew"))]',
    ]
    for sel in direct_selectors:
        btn = page.ele(sel, timeout=0.5)
        if btn:
            return btn

    # 2. 扫描所有 dialog / modal 容器内的确认按钮
    modal_xpaths = [
        'xpath://*[@role="dialog" or @aria-modal="true" or contains(@class, "Modal") or contains(@class, "Dialog")]//button',
    ]
    exclude_words = ["cancel", "close", "取消", "關閉", "关闭", "back", "返回", "click to renew"]
    target_words = [
        "unsuspend",
        "renew",
        "confirm",
        "continue",
        "submit",
        "yes",
        "解除停權",
        "解除停权",
        "確認",
        "确认",
        "續期",
        "续期",
    ]

    for xp in modal_xpaths:
        try:
            btns = page.eles(xp, timeout=0.5)
            for b in btns:
                txt = (b.text or "").strip().lower()
                if not txt:
                    continue
                if any(ex in txt for ex in exclude_words):
                    continue
                if any(tw in txt for tw in target_words):
                    return b
        except Exception:
            continue
    return None


def get_page_notifications(page):
    """收集页面上的弹窗提示、Toast 消息或错误横幅文本"""
    try:
        return page.run_js("""
            const selectors = [
                '[role="alert"]',
                '[role="status"]',
                '[role="dialog"]',
                '[aria-modal="true"]',
                '.mantine-Notification-root',
                '.mantine-Alert-root',
                '.Toastify__toast',
                '[data-sonner-toast]'
            ];
            const msgs = [];
            for (const sel of selectors) {
                document.querySelectorAll(sel).forEach(el => {
                    const t = (el.innerText || '').trim().replace(/\\s+/g, ' ');
                    if (t && !msgs.includes(t)) msgs.push(t.slice(0, 220));
                });
            }
            return msgs;
        """) or []
    except Exception:
        return []


def is_server_suspended_on_page(page):
    """检查当前服务器控制面板是否仍然处于 SUSPENDED / 已停權 状态"""
    try:
        text = page.run_js("return document.body ? document.body.innerText : '';") or ""
        suspended_markers = [
            "你的伺服器目前已停權",
            "你的服务器目前已停权",
            "Renew to unsuspend your server",
            "Renew once suspended",
        ]
        if any(m in text for m in suspended_markers):
            return True
        if "SUSPENDED" in text and "Expired" in text:
            return True
    except Exception:
        pass
    return False


def try_direct_serverplans_api_renew(page, server_uuid=None, cf_token=""):
    """
    直接调用 AcoreHosting 真实的续期接口:
    POST /api/client/extensions/dev.sqidgeon.serverplans/renew?server=<uuid>
    """
    print("[*] 正在通过 dev.sqidgeon.serverplans/renew 接口发起续期请求...")
    try:
        res = page.run_js(
            """
            const passedUuid = arguments[0];
            const cfToken = arguments[1];

            return (async () => {
                let xsrf = '';
                const match = document.cookie.match(/XSRF-TOKEN=([^;]+)/);
                if (match) xsrf = decodeURIComponent(match[1]);

                const headers = {
                    'Accept': 'application/json, text/plain, */*',
                    'Content-Type': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest'
                };
                if (xsrf) headers['X-XSRF-TOKEN'] = xsrf;

                let uuid = passedUuid;
                if (!uuid) {
                    try {
                        const r = await fetch(`/api/client/extensions/dev.sqidgeon.serverplans/mine`, { headers });
                        const d = await r.json();
                        if (d && Array.isArray(d.servers) && d.servers.length > 0) {
                            uuid = d.servers[0].uuid;
                        }
                    } catch (e) {}
                }

                if (!uuid) return { ok: false, error: '未能获取到服务器 UUID' };

                const ep = `/api/client/extensions/dev.sqidgeon.serverplans/renew?server=${uuid}`;
                const payload = cfToken ? JSON.stringify({ 'cf-turnstile-response': cfToken, token: cfToken }) : JSON.stringify({});

                try {
                    const resp = await fetch(ep, {
                        method: 'POST',
                        headers: headers,
                        body: payload,
                        credentials: 'same-origin'
                    });
                    const text = await resp.text();
                    return {
                        ok: resp.status >= 200 && resp.status < 300,
                        status: resp.status,
                        endpoint: ep,
                        body: text.slice(0, 400)
                    };
                } catch (err) {
                    return { ok: false, error: err.message };
                }
            })();
            """,
            server_uuid,
            cf_token,
        )
        print(f"[*] 续期接口响应: {res}")
        return res
    except Exception as e:
        print(f"[!] 调用续期接口异常: {e}")
        return None


def execute_renew_flow(page, renew_btn):
    """
    执行完整的：
    1. 单次点击 Click to renew 打开 'Unsuspend server' 弹窗
    2. 在弹窗中点击 'Unsuspend' 按钮（如有 Turnstile 则先过验证）
    3. 捕获后台 /api/client/extensions/dev.sqidgeon.serverplans/renew 响应
    """
    reset_cf_tokens(page)

    try:
        page.listen.start("acorehosting.com")
    except Exception:
        pass

    print(f"[*] 步骤 4.1: 单次点击 '{renew_btn.text}' 打开解封续期弹窗...")
    click_single_clean(page, renew_btn)
    time.sleep(2.5)
    page.get_screenshot(path="modal_opened.png")

    api_logs = []
    mutation_api_logs = []
    detected_uuid = None
    discord_error_detected = False
    clicked_unsuspend = False
    start_time = time.time()

    while time.time() - start_time < 30:
        # 1. 收集网络包
        try:
            while True:
                pkt = page.listen.wait(timeout=0.4)
                if not pkt:
                    break
                url_str = pkt.url or ""
                method_str = (pkt.method or "GET").upper()

                if "server=" in url_str and not detected_uuid:
                    detected_uuid = url_str.split("server=")[-1].split("&")[0]

                if any(k in url_str.lower() for k in ["renew", "serverplans", "unsuspend", "verify", "turnstile"]):
                    status = pkt.response.status if pkt.response else "N/A"
                    body_str = str(pkt.response.body)[:350] if pkt.response else ""
                    log_line = f"{method_str} {url_str} -> HTTP {status} | {body_str}"
                    if log_line not in api_logs:
                        print(f"[API] {log_line}")
                        api_logs.append(log_line)
                    if method_str in ("POST", "PUT", "PATCH"):
                        mutation_api_logs.append(log_line)
                    if "join the required discord server" in body_str.lower():
                        discord_error_detected = True
        except Exception:
            pass

        if discord_error_detected:
            break

        # 2. 检查弹窗内是否有 Cloudflare Turnstile 验证框
        coords = find_turnstile_coords_via_cdp(page) or find_turnstile_coords_via_js(page)
        if coords and len(get_cf_token(page)) <= 20:
            print(f"[*] 检测到弹窗内 Cloudflare Turnstile 验证框，坐标: {coords}，正在点击...")
            xdotool_click(page, coords[0], coords[1])
            time.sleep(3.5)
            continue

        # 3. 查找并点击弹窗内的 'Unsuspend' 按钮
        modal_btn = find_modal_confirm_button(page)
        if modal_btn and is_button_ready(modal_btn):
            print(f"[*] 步骤 4.2: 找到弹窗确认按钮 '{modal_btn.text}'，正在点击...")
            click_single_clean(page, modal_btn)
            clicked_unsuspend = True
            time.sleep(4)
            continue

        # 4. 如果还没打开弹窗（没找到 Unsuspend 按钮）且未触发 POST，补点一次 Click to renew
        if not clicked_unsuspend and not mutation_api_logs and time.time() - start_time > 5:
            cur_renew_btn = find_renew_button(page)
            if cur_renew_btn and is_button_ready(cur_renew_btn):
                print("[*] 弹窗尚未打开，补点一次 'Click to renew'...")
                click_single_clean(page, cur_renew_btn)
                time.sleep(2.5)
                modal_btn = find_modal_confirm_button(page)
                if modal_btn and is_button_ready(modal_btn):
                    print(f"[*] 找到弹窗确认按钮 '{modal_btn.text}'，正在点击...")
                    click_single_clean(page, modal_btn)
                    clicked_unsuspend = True
                    time.sleep(4)
                continue

        # 5. 如果已经触发了 POST 请求，跳出等待检查结果
        if mutation_api_logs:
            break

        time.sleep(1.5)

    try:
        page.listen.stop()
    except Exception:
        pass

    # 如果还没有触发 POST 请求，直接调用后台 renew API
    if not mutation_api_logs and not discord_error_detected:
        cf_tok = get_cf_token(page)
        api_res = try_direct_serverplans_api_renew(page, server_uuid=detected_uuid, cf_token=cf_tok)
        if api_res:
            api_logs.append(f"DIRECT_API: {api_res}")
            if "join the required discord server" in str(api_res).lower():
                discord_error_detected = True

    notifs = get_page_notifications(page)
    if notifs:
        print(f"[*] 页面提示信息: {notifs}")
        if any("join the required discord server" in n.lower() for n in notifs):
            discord_error_detected = True

    return api_logs, notifs, discord_error_detected


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

        # ==================== 打开登录页 ====================
        print(f"[*] 正在打开登录页: {LOGIN_URL}")
        page.get(LOGIN_URL)
        time.sleep(3)

        if not wait_for_login_page_ready(page, timeout=40):
            if local_proxy:
                print("[!] 挂载代理状态下未能加载出登录框，正在切换为直连模式重试...")
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

        # ==================== 第一步：输入账号 -> 过 CF 验证 -> 点击继续 ====================
        print("[*] 正在定位并填写账号...")
        email_input = find_email_input(page, timeout=10)
        if not email_input:
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

        # ==================== 第二步：输入密码 -> 点击登入 ====================
        print("[*] 等待密码输入框加载...")
        pwd_input = page.ele('css:input[type="password"]', timeout=15)
        if not pwd_input:
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

        if not is_button_ready(btn2):
            pass_form_turnstile(page, stage_name="第二步(密码页)", step_btn_keywords=step2_btns, timeout=30)
            btn2 = find_step_button(page, step2_btns)

        page.get_screenshot(path="step2_before_login.png")

        if btn2:
            print(f"[*] 点击第二步登录按钮: {btn2.text}")
            btn2.click()
        else:
            pwd_input.input("\n")

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

        # ==================== 第三步：进入服务器控制面板 ====================
        print(f"[*] 登录后页面 URL: {page.url}")
        if "auth/login" in page.url:
            page.get_screenshot(path="error_login.png")
            raise RuntimeError("登录后仍停留在登录页，请检查账号密码或查看截图 error_login.png")

        time.sleep(3)

        if SERVER_ID:
            target_url = SERVER_ID if SERVER_ID.startswith("http") else f"{DASH_URL}/server/{SERVER_ID.strip('/')}"
            print(f"[*] 已配置 SERVER_ID，直接进入服务器面板: {target_url}")
            page.get(target_url)
        else:
            print("[*] 正在服务器列表页点击进入服务器...")
            srv_target = (
                page.ele('xpath://a[contains(@href, "/server/")]', timeout=5)
                or page.ele('xpath://*[contains(text(), "Lick") or contains(text(), "已停權") or contains(text(), "Suspended")]', timeout=5)
                or page.ele('xpath://*[contains(text(), ":20") or contains(text(), "MiB") or contains(text(), "GiB")]', timeout=5)
            )
            if srv_target:
                srv_target.click()
                time.sleep(4)
            if "/server/" not in page.url:
                page.get(f"{DASH_URL}/server/{DEFAULT_SERVER_ID}")

        time.sleep(5)
        print(f"[*] 已进入服务器控制面板: {page.url}")
        page.get_screenshot(path="panel_status.png")

        # ==================== 第四步：点击 Click to renew -> 点击弹窗 Unsuspend ====================
        print("[*] 正在查找右上角 Click to renew 按钮...")
        renew_btn = find_renew_button(page)

        if not renew_btn:
            page.get_screenshot(path="error_no_renew_btn.png")
            raise RuntimeError("未在页面上找到 Click to renew 按钮，请查看截图 error_no_renew_btn.png")

        print(f"[*] 找到续期按钮: '{renew_btn.text}'")

        if not is_button_ready(renew_btn) and not is_server_suspended_on_page(page):
            msg = f"当前续期按钮 ({renew_btn.text}) 处于不可点击（未到期/冷却中）状态，且服务器状态正常，无需操作。"
            print(f"[=] {msg}")
            send_tg(msg)
            return

        print(f"[+] 检测到 '{renew_btn.text}' 可点击！开始执行续期流程...")
        api_logs, notifs, discord_err = execute_renew_flow(page, renew_btn)
        page.get_screenshot(path="after_renew_click.png")

        if discord_err:
            raise RuntimeError(
                "AcoreHosting 后台返回 422 拒绝续期：'Join the required Discord server to continue'！"
                "请登录 AcoreHosting 面板检查 OAuth 绑定的 Discord 账号是否已加入 AcoreHosting 官方 Discord 群组。"
            )

        # 刷新页面以验证服务器端真实状态是否已解除停权
        print("[*] 正在刷新控制面板以核实服务器真实续期状态...")
        page.refresh()
        time.sleep(6)
        page.get_screenshot(path="renew_result.png")

        still_suspended = is_server_suspended_on_page(page)
        renew_btn_after = find_renew_button(page)
        still_can_renew = is_button_ready(renew_btn_after) if renew_btn_after else False

        if still_suspended and still_can_renew:
            detail = f"API记录: {api_logs} | 页面提示: {notifs}"
            raise RuntimeError(
                f"点击 Unsuspend 后刷新页面，服务器仍处于已停权 (SUSPENDED) 状态！详情: {detail}"
            )

        # ==================== 第五步：解除停权后自动点击『啟動 / Start』开机 ====================
        print("[*] 正在检查服务器是否需要开机 (啟動 / Start)...")
        start_btn = page.ele(
            'xpath://button[normalize-space(.)="啟動" or normalize-space(.)="启动" or translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")="start"]',
            timeout=5,
        )
        if start_btn and is_button_ready(start_btn):
            print("[+] 检测到『啟動 / Start』按钮可用，正在点击启动服务器...")
            click_single_clean(page, start_btn)
            time.sleep(5)
            page.get_screenshot(path="server_started.png")

        msg = f"✅ 已成功完成续期并解除停权状态！\n当前面板 URL: {page.url}"
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
