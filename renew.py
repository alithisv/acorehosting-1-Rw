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


def xdotool_click(page, client_x, client_y):
    """结合浏览器窗口偏移量，使用系统级 xdotool 模拟真实鼠标移动并点击"""
    try:
        offset = page.run_js("""
            return {
                x: (window.screenX || 0) + Math.max(0, (window.outerWidth - window.innerWidth) / 2),
                y: (window.screenY || 0) + Math.max(0, window.outerHeight - window.innerHeight)
            };
        """) or {"x": 0, "y": 80}
        screen_x = int(offset["x"] + client_x + random.randint(-2, 2))
        screen_y = int(offset["y"] + client_y + random.randint(-2, 2))

        # 先用系统级 xdotool 模拟真实硬件鼠标移动与点击
        subprocess.run(
            ["xdotool", "mousemove", "--sync", str(screen_x - 40), str(screen_y - 25)],
            timeout=3,
            check=False,
        )
        time.sleep(0.15)
        subprocess.run(
            ["xdotool", "mousemove", "--sync", str(screen_x), str(screen_y), "click", "1"],
            timeout=3,
            check=False,
        )
    except Exception:
        pass

    # 同时补充 DrissionPage 内部坐标点击作为双保险
    try:
        page.actions.move_to((int(client_x), int(client_y))).click()
    except Exception:
        pass


def find_turnstile_coords_via_cdp(page):
    """
    通过 CDP DOM.getDocument(pierce=True) 直接穿透 Closed Shadow DOM，
    精准获取 Cloudflare Turnstile iframe 的真实坐标
    """
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
    """通过页面 DOM 容器尺寸与特征定位 Turnstile 宿主元素坐标"""
    try:
        coords = page.run_js("""
            // 1. 优先查找已知的 Turnstile 容器或隐藏 input 的父容器
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

            // 2. 扫描符合 Turnstile 标准尺寸 (宽 250~420, 高 50~90) 的宿主 div
            const divs = document.querySelectorAll('div');
            for (const d of divs) {
                const r = d.getBoundingClientRect();
                if (r.width >= 250 && r.width <= 420 && r.height >= 50 && r.height <= 90 && r.top > 80) {
                    return {x: r.left + 28, y: r.top + r.height / 2};
                }
            }

            // 3. 全屏 Cloudflare 页面兜底：根据主内容区位置计算复选框坐标
            const mainContent = document.querySelector('.main-content, .main-wrapper, #challenge-body-text, h1');
            if (mainContent) {
                const r = mainContent.getBoundingClientRect();
                if (r.left > 0) {
                    return {x: r.left + 28, y: r.top + 135};
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
    """查找登录页的邮箱输入框"""
    return page.ele(
        'css:input[type="email"], input[name="email"], input[name="username"], input[type="text"]',
        timeout=timeout,
    )


def is_global_cf_interstitial(page):
    """判断当前是否处于全屏 Cloudflare 拦截页"""
    if find_email_input(page, timeout=0.5):
        return False
    if page.ele('css:input[type="password"]', timeout=0.5):
        return False

    try:
        title = (page.title or "").lower()
        html = (page.html or "").lower()
    except Exception:
        return False

    cf_signals = [
        "just a moment",
        "請稍候",
        "请稍候",
        "verify you are human",
        "security verification",
        "确认您是真人",
        "確認您是真人",
        "cf-browser-verification",
        "challenge-platform",
    ]
    return any(sig in title or sig in html for sig in cf_signals)


def handle_global_cf_page(page, timeout=45):
    """处理全屏 Cloudflare 验证拦截页并等待跳转"""
    print("[*] 检查是否存在全屏 Cloudflare 验证拦截页...")
    start_time = time.time()
    attempt = 0

    while time.time() - start_time < timeout:
        if not is_global_cf_interstitial(page):
            print("[+] 已通过全屏验证，进入目标页面。")
            return True

        attempt += 1
        print(f"[*] [第{attempt}次] 检测到全屏 Cloudflare 验证页，正在定位验证框...")

        coords = find_turnstile_coords_via_cdp(page) or find_turnstile_coords_via_js(page)
        if coords:
            cx, cy = coords
            print(f"[*] 定位到全屏 Turnstile 验证框坐标: ({cx}, {cy})，正在执行点击...")
            xdotool_click(page, cx, cy)
        else:
            # 1920x1080 分辨率下全屏 CF 居中布局的合理兜底位置
            print("[*] 暂未解析到精确 DOM 框，使用居中布局兜底坐标点击: (510, 340)")
            xdotool_click(page, 510, 340)

        # 每隔 3 次尝试用键盘 Tab + Space 触发一次
        if attempt % 3 == 0:
            try:
                subprocess.run(["xdotool", "key", "Tab", " KP_Space"], timeout=2, check=False)
                page.actions.key_down("Tab").key_up("Tab").wait(0.3).key_down("Space").key_up("Space")
            except Exception:
                pass

        print("[*] 已点击验证框，等待页面校验跳转...")
        for _ in range(6):
            time.sleep(1)
            if not is_global_cf_interstitial(page):
                print("[+] 已成功通过全屏 Cloudflare 验证！")
                return True

    print("[!] 全屏 Cloudflare 验证等待超时...")
    return False


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


def reset_cf_tokens(page):
    """在切换步骤前清空旧的 Turnstile token，防止第二步误判第一步残留的 token"""
    try:
        page.run_js("""
            const inputs = document.querySelectorAll('input[name="cf-turnstile-response"], input[name*="turnstile"]');
            for (const inp of inputs) {
                inp.value = "";
            }
        """)
    except Exception:
        pass


def has_turnstile_widget(page):
    """检查当前页面是否存在 Turnstile 组件"""
    if find_turnstile_coords_via_cdp(page) or find_turnstile_coords_via_js(page):
        return True
    try:
        return bool(
            page.run_js("""
                return !!document.querySelector(
                    'input[name="cf-turnstile-response"], input[name*="turnstile"], .cf-turnstile, [data-sitekey], iframe[src*="cloudflare"]'
                );
            """)
        )
    except Exception:
        return False


def is_button_ready(btn):
    """判断按钮是否处于可点击激活状态"""
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
        or "opacity-50" in class_attr
    ):
        return False
    return True


def find_step_button(page, keywords):
    """根据关键词列表查找步骤提交按钮"""
    for kw in keywords:
        btn = page.ele(
            f'xpath://button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "{kw.lower()}")]',
            timeout=0.5,
        )
        if btn:
            return btn
    return page.ele('css:button[type="submit"]', timeout=0.5)


def click_turnstile_box(page, stage_name="", timeout=35):
    """穿透 Shadow DOM 并点击表单内的 Cloudflare Turnstile 复选框"""
    print(f"[*] 正在处理 [{stage_name}] 阶段的 Cloudflare 验证...")
    start_time = time.time()
    attempt = 0

    # 先等待 2 秒让组件渲染或自动通过
    time.sleep(2)
    if not has_turnstile_widget(page):
        print(f"[*] [{stage_name}] 当前界面未检测到 Turnstile 组件，直接继续。")
        return True

    while time.time() - start_time < timeout:
        attempt += 1

        token = get_cf_token(page)
        if len(token) > 20:
            print(f"[+] [{stage_name}] 检测到 Turnstile Token (长度 {len(token)})，验证已通过！")
            return True

        coords = find_turnstile_coords_via_cdp(page) or find_turnstile_coords_via_js(page)
        if coords:
            cx, cy = coords
            print(f"[*] [第{attempt}次] 点击 [{stage_name}] Turnstile 坐标: ({cx}, {cy})")
            xdotool_click(page, cx, cy)
        elif attempt % 2 == 0:
            try:
                page.actions.key_down("Tab").key_up("Tab").wait(0.3).key_down("Space").key_up("Space")
            except Exception:
                pass

        for _ in range(4):
            time.sleep(1)
            token = get_cf_token(page)
            if len(token) > 20:
                print(f"[+] [{stage_name}] 检测到 Turnstile Token，验证已通过！")
                return True

    print(f"[!] [{stage_name}] Cloudflare 验证等待超时，尝试继续执行...")
    return False


def fill_input_react(page, ele, text):
    """适配 React/Vue 受控组件的输入方式"""
    ele.click()
    time.sleep(0.2)
    ele.clear()
    time.sleep(0.2)
    ele.input(text)
    time.sleep(0.4)
    try:
        page.run_js(
            """
            const el = arguments[0];
            const val = arguments[1];
            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            if (nativeInputValueSetter) {
                nativeInputValueSetter.call(el, val);
            }
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.blur();
            """,
            ele,
            text,
        )
    except Exception:
        pass


def find_renew_button(page):
    """
    大小写不敏感地定位控制面板上的 'Click to renew' / 'Renew' 按钮，
    严格限制为 button 或按钮样式的 a 标签，避免误匹配 'Renewals' 标题文本
    """
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

        co = ChromiumOptions()
        co.auto_port()
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--window-position=0,0")
        co.set_argument("--window-size=1920,1080")
        co.set_argument("--lang=zh-TW")
        # 禁用 WebRTC 非代理 UDP 流量，防止泄露 GitHub Actions 真实机房 IP
        co.set_argument("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
        co.set_argument("--enforce-webrtc-ip-handling-policy")
        co.set_pref("webrtc.ip_handling_policy", "disable_non_proxied_udp")
        co.set_pref("credentials_enable_service", False)

        if local_proxy:
            print(f"[*] 浏览器已挂载代理: {local_proxy}")
            co.set_proxy(local_proxy)

        page = ChromiumPage(co)

        # ==================== 打开登录页并处理全局 CF 拦截页 ====================
        print(f"[*] 正在打开登录页: {LOGIN_URL}")
        page.get(LOGIN_URL)
        time.sleep(4)

        if not handle_global_cf_page(page, timeout=45):
            print("[*] 首次通过全屏验证超时，尝试刷新页面重试一次...")
            page.refresh()
            time.sleep(4)
            handle_global_cf_page(page, timeout=40)

        time.sleep(2)

        # ==================== 第一步：输入账号 + CF验证 + 点击继续 ====================
        print("[*] 正在定位并填写账号...")
        email_input = find_email_input(page, timeout=20)
        if not email_input:
            page.get_screenshot(path="error_no_email_input.png")
            raise RuntimeError("未找到账号输入框，请查看截图 error_no_email_input.png")

        fill_input_react(page, email_input, EMAIL)
        time.sleep(1.5)

        step1_btns = ["繼續", "继续", "continue", "next"]
        click_turnstile_box(page, stage_name="step1", timeout=35)
        page.get_screenshot(path="step1_after_cf.png")

        btn1 = find_step_button(page, step1_btns)
        if btn1:
            print(f"[*] 点击第一步按钮: {btn1.text}")
            btn1.click()
        else:
            email_input.input("\n")

        # ==================== 第二步：输入密码 + CF验证 + 点击登入 ====================
        print("[*] 等待密码输入框加载...")
        pwd_input = page.ele('css:input[type="password"]', timeout=15)
        if not pwd_input:
            # 如果第一次点击未进入密码页，补充验证并再点一次
            click_turnstile_box(page, stage_name="step1_retry", timeout=20)
            btn1 = find_step_button(page, step1_btns)
            if btn1:
                btn1.click()
            pwd_input = page.ele('css:input[type="password"]', timeout=15)

        if not pwd_input:
            page.get_screenshot(path="error_step1.png")
            raise RuntimeError("未能进入密码输入界面，请查看截图 error_step1.png")

        reset_cf_tokens(page)
        print("[*] 正在填写密码...")
        fill_input_react(page, pwd_input, PASSWORD)
        time.sleep(1.5)

        step2_btns = ["登入", "登录", "login", "sign in"]
        click_turnstile_box(page, stage_name="step2", timeout=35)
        page.get_screenshot(path="step2_after_cf.png")

        btn2 = find_step_button(page, step2_btns)
        if btn2:
            print(f"[*] 点击第二步登录按钮: {btn2.text}")
            btn2.click()
        else:
            pwd_input.input("\n")

        # 等待登录跳转
        for _ in range(12):
            time.sleep(1)
            if "auth/login" not in page.url:
                break

        # ==================== 第三步：进入服务器控制面板 ====================
        print(f"[*] 登录后页面 URL: {page.url}")
        if "auth/login" in page.url:
            page.get_screenshot(path="error_login.png")
            raise RuntimeError("登录后仍停留在登录页，请检查密码或查看截图 error_login.png")

        if SERVER_ID:
            if SERVER_ID.startswith("http"):
                target_url = SERVER_ID
            else:
                target_url = f"{DASH_URL}/server/{SERVER_ID.strip('/')}"
            print(f"[*] 已配置 SERVER_ID，直接跳转至服务器面板: {target_url}")
            page.get(target_url)
        else:
            print("[*] 正在查找并点击服务器卡片...")
            srv_link = page.ele('xpath://a[contains(@href, "/server/")]', timeout=12)
            if srv_link:
                srv_link.click()
            else:
                srv_card = page.ele(
                    'xpath://*[contains(text(), "MiB") or contains(text(), "GiB") or contains(text(), "plan")]',
                    timeout=10,
                )
                if srv_card:
                    srv_card.click()
                else:
                    page.get_screenshot(path="error_server_list.png")
                    raise RuntimeError("未能在列表中找到服务器，建议在 Secrets 中配置 ACORE_SERVER_ID")

        time.sleep(5)
        handle_global_cf_page(page, timeout=25)
        time.sleep(4)
        print(f"[*] 已进入服务器控制面板: {page.url}")

        # ==================== 第四步：检查并点击 Click to renew 按钮 ====================
        print("[*] 正在检查面板上的 Click to renew / Renew 按钮状态...")
        renew_btn = find_renew_button(page)

        if not renew_btn:
            page.get_screenshot(path="error_no_renew_btn.png")
            raise RuntimeError("未在页面上找到 Click to renew 按钮，请查看截图 error_no_renew_btn.png")

        print(f"[*] 找到续期按钮，按钮文本: '{renew_btn.text}'")

        status_hint = ""
        hint_ele = page.ele(
            'xpath://*[contains(text(), "Renew once suspended") or contains(text(), "Renew available") or contains(text(), "Until suspension") or contains(text(), "Deletes in")]',
            timeout=2,
        )
        if hint_ele:
            status_hint = hint_ele.text

        can_click = is_button_ready(renew_btn)
        page.get_screenshot(path="panel_status.png")

        if not can_click:
            msg = f"当前续期按钮 ({renew_btn.text}) 处于不可点击（冷却/未到期）状态，无需操作。\n面板提示: {status_hint}"
            print(f"[=] {msg}")
            send_tg(msg)
        else:
            print(f"[+] 检测到 '{renew_btn.text}' 按钮可点击！正在执行点击续期...")
            reset_cf_tokens(page)
            renew_btn.click()
            time.sleep(4)

            # 如果点击后弹出验证框或确认弹窗则自动处理
            if has_turnstile_widget(page):
                click_turnstile_box(page, stage_name="renew_modal", timeout=25)

            confirm_btn = page.ele(
                'xpath://button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "confirm") or contains(., "確認") or contains(., "确认")]',
                timeout=3,
            )
            if confirm_btn and is_button_ready(confirm_btn):
                print(f"[*] 点击弹窗确认按钮: {confirm_btn.text}")
                confirm_btn.click()
                time.sleep(4)

            # 如果服务器此前因到期被停权离线，续期解封后尝试点击“啟動 / Start”按钮恢复运行
            start_btn = page.ele(
                'xpath://button[normalize-space(.)="啟動" or normalize-space(.)="启动" or translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")="start"]',
                timeout=3,
            )
            if start_btn and is_button_ready(start_btn):
                print("[*] 检测到『啟動/Start』按钮可用，正在自动启动服务器...")
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
