import os
import sys
import time
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
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": f"【AcoreHosting 续期】\n{msg}"}, timeout=10)
    except Exception as e:
        print(f"[!] TG 通知发送失败: {e}")


def handle_global_cf_page(page, timeout=20):
    """处理初次打开网址时遇到的全屏 Cloudflare 验证页"""
    print("[*] 检查是否存在全屏 Cloudflare 验证拦截页...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            # 检查页面标题或文本是否包含安全验证
            if "security verification" in page.html.lower() or "verify you are human" in page.html.lower():
                print("[*] 遇到全屏 Cloudflare 验证页，正在尝试点击验证框...")
                # 寻找 iframe 或 checkbox 坐标
                iframe = page.ele('xpath://iframe[contains(@src, "cloudflare") or contains(@src, "turnstile")]', timeout=2)
                if iframe:
                    rect = iframe.rect
                    x, y = rect.location[0] + 30, rect.location[1] + rect.size[1] / 2
                    page.actions.move_to((int(x), int(y))).click()
                else:
                    # 兜底：直接在页面中心偏左位置点击常见复选框区域
                    page.actions.move_to((270, 370)).click()
                time.sleep(4)
            else:
                # 已经离开拦截页，进入正常登录页
                print("[+] 已成功通过或未遇到全屏 Cloudflare 拦截页。")
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def is_cf_passed(page):
    """检查 Cloudflare Turnstile 隐藏的 token 是否已生成"""
    try:
        val = page.run_js('''
            const inp = document.querySelector('input[name="cf-turnstile-response"], input[name*="turnstile"], input[name*="cf-"]');
            return inp ? inp.value : "";
        ''')
        if val and len(str(val)) > 20:
            return True
    except Exception:
        pass
    return False


def is_submit_enabled(page, keywords):
    """检查提交按钮是否已经解除 disabled 状态"""
    for kw in keywords:
        btn = page.ele(f'xpath://button[contains(., "{kw}")]', timeout=0.5)
        if btn:
            disabled = btn.attr("disabled") is not None or "disabled" in (btn.attr("class") or "")
            if not disabled:
                return btn
    return None


def click_turnstile_box(page, stage_name="", btn_keywords=None, timeout=30):
    """穿透 Shadow DOM 或通过容器坐标点击 Cloudflare Turnstile 复选框"""
    print(f"[*] 正在处理 [{stage_name}] 阶段的 Cloudflare 验证...")
    start_time = time.time()
    attempt = 0

    while time.time() - start_time < timeout:
        attempt += 1

        if is_cf_passed(page):
            print(f"[+] [{stage_name}] 检测到 Turnstile Token，验证已通过！")
            return True
        if btn_keywords and is_submit_enabled(page, btn_keywords):
            print(f"[+] [{stage_name}] 检测到提交按钮已激活，验证已通过！")
            return True

        clicked = False

        try:
            targets = [
                'xpath://iframe[contains(@src, "cloudflare") or contains(@src, "turnstile")]',
                'css:div.cf-turnstile',
                'xpath://div[contains(@id, "turnstile") or contains(@class, "turnstile")]',
                'xpath://input[@name="cf-turnstile-response"]/parent::*'
            ]
            for selector in targets:
                ele = page.ele(selector, timeout=0.5)
                if ele:
                    rect = ele.rect
                    if rect.size[0] > 50 and rect.size[1] > 20:
                        x, y = rect.location[0] + 30, rect.location[1] + rect.size[1] / 2
                        print(f"[*] [第{attempt}次] 通过选择器点击 Turnstile: ({int(x)}, {int(y)})")
                        page.actions.move_to((int(x), int(y))).click()
                        clicked = True
                        break
        except Exception:
            pass

        if not clicked:
            try:
                coords = page.run_js('''
                    const all = document.querySelectorAll('div, iframe');
                    for (const el of all) {
                        const r = el.getBoundingClientRect();
                        if (r.width >= 250 && r.width <= 360 && r.height >= 55 && r.height <= 80 && r.top > 200) {
                            return {x: r.left + 30, y: r.top + r.height / 2, w: r.width, h: r.height};
                        }
                    }
                    return null;
                ''')
                if coords:
                    x, y = int(coords['x']), int(coords['y'])
                    print(f"[*] [第{attempt}次] 通过尺寸特征识别验证框，点击坐标: ({x}, {y})")
                    page.actions.move_to((x, y)).click()
                    clicked = True
            except Exception:
                pass

        if not clicked and attempt % 2 == 0:
            try:
                page.actions.key_down('Tab').key_up('Tab')
                time.sleep(0.5)
                page.actions.key_down('Space').key_up('Space')
            except Exception:
                pass

        time.sleep(4)

    print(f"[!] [{stage_name}] Cloudflare 验证等待超时...")
    return False


def fill_input_react(page, ele, text):
    """适配 React/Vue 受控组件的输入方式"""
    ele.click()
    time.sleep(0.3)
    ele.clear()
    ele.input(text)
    time.sleep(0.5)
    page.run_js('''
        const el = arguments[0];
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.blur();
    ''', ele)


def main():
    if not EMAIL or not PASSWORD:
        print("[-] 错误: 请在 GitHub Secrets 中设置 ACORE_EMAIL 和 ACORE_PASSWORD")
        sys.exit(1)

    proxy_proc = None
    page = None

    try:
        local_proxy, proxy_proc = start_proxy(PROXY_URL)

        co = ChromiumOptions()
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-gpu')
        co.set_argument('--disable-dev-shm-usage')
        co.set_argument('--disable-blink-features=AutomationControlled')
        co.set_argument('--window-size=1920,1080')
        co.set_argument('--lang=zh-TW')

        if local_proxy:
            print(f"[*] 浏览器已挂载代理: {local_proxy}")
            co.set_proxy(local_proxy)

        page = ChromiumPage(co)

        # ==================== 打开登录页并处理全局 CF 拦截页 ====================
        print(f"[*] 正在打开登录页: {LOGIN_URL}")
        page.get(LOGIN_URL)
        time.sleep(5)

        # 自动处理可能出现的全局安全验证页
        handle_global_cf_page(page, timeout=25)
        time.sleep(3)

        # ==================== 第一步：输入账号 + CF验证 + 点击继续 ====================
        print("[*] 正在定位并填写账号...")
        email_input = page.ele('css:input[type="text"], input[type="email"], input:not([type="hidden"])', timeout=15)
        if not email_input:
            page.get_screenshot(path="error_no_email_input.png")
            raise RuntimeError("未找到账号输入框，请查看截图 error_no_email_input.png")

        fill_input_react(page, email_input, EMAIL)
        time.sleep(2)

        step1_btns = ["繼續", "继续", "Continue", "Next"]
        click_turnstile_box(page, stage_name="step1", btn_keywords=step1_btns, timeout=30)
        page.get_screenshot(path="step1_after_cf.png")

        btn1 = is_submit_enabled(page, step1_btns) or page.ele('xpath://button[contains(., "繼續") or contains(., "Continue")]', timeout=3)
        if btn1:
            print(f"[*] 点击第一步按钮: {btn1.text}")
            btn1.click()
        else:
            email_input.input('\n')

        time.sleep(5)

        # ==================== 第二步：输入密码 + CF验证 + 点击登入 ====================
        print("[*] 等待密码输入框加载...")
        pwd_input = page.ele('css:input[type="password"]', timeout=20)
        if not pwd_input:
            page.get_screenshot(path="error_step1.png")
            raise RuntimeError("未能进入密码输入界面，请查看截图 error_step1.png")

        print("[*] 正在填写密码...")
        fill_input_react(page, pwd_input, PASSWORD)
        time.sleep(2)

        step2_btns = ["登入", "登录", "Login", "Sign in"]
        click_turnstile_box(page, stage_name="step2", btn_keywords=step2_btns, timeout=30)
        page.get_screenshot(path="step2_after_cf.png")

        btn2 = is_submit_enabled(page, step2_btns) or page.ele('xpath://button[contains(., "登入") or contains(., "Login")]', timeout=3)
        if btn2:
            print(f"[*] 点击第二步登录按钮: {btn2.text}")
            btn2.click()
        else:
            pwd_input.input('\n')

        time.sleep(6)

        # ==================== 第三步：进入服务器控制面板 ====================
        print(f"[*] 登录后页面 URL: {page.url}")
        if "auth/login" in page.url:
            page.get_screenshot(path="error_login.png")
            raise RuntimeError("登录后仍停留在登录页，请检查密码或查看截图 error_login.png")

        if SERVER_ID:
            target_url = f"{DASH_URL}/server/{SERVER_ID}"
            print(f"[*] 已配置 SERVER_ID，直接跳转至服务器面板: {target_url}")
            page.get(target_url)
        else:
            print("[*] 正在查找并点击服务器卡片...")
            srv_link = page.ele('xpath://a[contains(@href, "/server/")]', timeout=10)
            if srv_link:
                srv_link.click()
            else:
                srv_card = page.ele('xpath://*[contains(text(), "MiB") or contains(text(), "GiB")]', timeout=10)
                if srv_card:
                    srv_card.click()
                else:
                    page.get_screenshot(path="error_server_list.png")
                    raise RuntimeError("未能在列表中找到服务器，建议在 Secrets 中配置 ACORE_SERVER_ID")

        time.sleep(6)
        print(f"[*] 已进入服务器控制面板: {page.url}")

        # ==================== 第四步：检查右上角 Renew 按钮 ====================
        print("[*] 正在检查右上角 Renew 按钮状态...")
        renew_btn = page.ele('xpath://button[contains(., "Renew")]', timeout=15) or page.ele('text:Renew', timeout=5)

        if not renew_btn:
            page.get_screenshot(path="error_no_renew_btn.png")
            raise RuntimeError("未在页面上找到 Renew 按钮")

        status_hint = ""
        hint_ele = page.ele('xpath://*[contains(text(), "Renew available") or contains(text(), "Until suspension")]', timeout=2)
        if hint_ele:
            status_hint = hint_ele.text

        disabled_attr = renew_btn.attr("disabled")
        aria_disabled = renew_btn.attr("aria-disabled")
        class_attr = (renew_btn.attr("class") or "").lower()

        is_disabled = (
            disabled_attr is not None
            or aria_disabled == "true"
            or "disabled" in class_attr
            or "cursor-not-allowed" in class_attr
            or "opacity-50" in class_attr
        )

        page.get_screenshot(path="panel_status.png")

        if is_disabled:
            msg = f"当前 Renew 按钮处于不可点击（冷却）状态，无需操作。\n面板提示: {status_hint}"
            print(f"[=] {msg}")
            send_tg(msg)
        else:
            print("[+] 检测到 Renew 按钮可点击！正在执行点击续期...")
            renew_btn.click()
            time.sleep(5)

            click_turnstile_box(page, stage_name="renew_modal", timeout=15)
            confirm_btn = page.ele('xpath://button[contains(., "Confirm") or contains(., "確認")]', timeout=3)
            if confirm_btn:
                confirm_btn.click()
                time.sleep(3)

            page.get_screenshot(path="renew_result.png")
            msg = f"✅ 已成功点击 Renew 按钮完成续期！\n当前面板 URL: {page.url}"
            print(f"[+] {msg}")
            send_tg(msg)

    except Exception as e:
        err_msg = f"❌ 运行异常: {str(e)}"
        print(err_msg)
        if page:
            page.get_screenshot(path="error_final.png")
        send_tg(err_msg)
        sys.exit(1)
    finally:
        if page:
            page.quit()
        if proxy_proc:
            proxy_proc.terminate()


if __name__ == "__main__":
    main()
