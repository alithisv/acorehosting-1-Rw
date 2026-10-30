import os
import sys
import time
import requests
from DrissionPage import ChromiumPage, ChromiumOptions

# 环境变量配置
EMAIL = os.environ.get("ACORE_EMAIL")
PASSWORD = os.environ.get("ACORE_PASSWORD")
SERVER_ID = os.environ.get("ACORE_SERVER_ID", "").strip()  # 例如: 1da63f5c (可选，不填则自动点第一个)
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

LOGIN_URL = "https://dash.acorehosting.com/auth/login"
DASH_URL = "https://dash.acorehosting.com"


def send_tg(msg):
    """发送 Telegram 通知（如配置了环境变量）"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": f"【AcoreHosting 续期】\n{msg}"}, timeout=10)
    except Exception as e:
        print(f"[!] TG 通知发送失败: {e}")


def handle_turnstile(page, stage_name="", timeout=25):
    """定位并点击 Cloudflare Turnstile 验证框"""
    print(f"[*] 正在处理 [{stage_name}] 阶段的 Cloudflare 验证...")
    start_time = time.time()

    while time.time() - start_time < timeout:
        # 1. 检查隐藏的 cf-turnstile-response 是否已经有值（自动通过的情况）
        try:
            cf_inputs = page.eles('tag:input@name=cf-turnstile-response', timeout=1)
            for inp in cf_inputs:
                if inp.value and len(inp.value) > 20:
                    print(f"[+] [{stage_name}] Cloudflare 验证已通过！")
                    return True
        except Exception:
            pass

        # 2. 查找 Turnstile 的 iframe 或外层容器并模拟点击勾选框位置
        try:
            iframe = page.ele('xpath://iframe[contains(@src, "cloudflare") or contains(@src, "turnstile")]', timeout=2)
            if iframe:
                # 尝试在 iframe 内部点击 checkbox，若跨域受限则根据坐标偏移点击左侧复选框区域
                cb = iframe.ele('css:input[type="checkbox"]', timeout=1)
                if cb:
                    cb.click()
                else:
                    # 点击 iframe 左边缘偏右约 30px、垂直居中的复选框位置
                    iframe.click.at(offset_x=30, offset_y=32)
                print(f"[*] 已尝试点击 [{stage_name}] Turnstile 验证框，等待校验结果...")
                time.sleep(4)
                continue
        except Exception:
            pass

        time.sleep(2)

    print(f"[!] [{stage_name}] Cloudflare 验证等待超时，尝试继续后续操作...")
    return False


def wait_and_click_submit(page, btn_keywords, timeout=20):
    """等待提交按钮解除 disabled 状态并点击"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        for kw in btn_keywords:
            btn = page.ele(f'xpath://button[contains(., "{kw}")]', timeout=1)
            if not btn:
                btn = page.ele('css:button[type="submit"]', timeout=1)
            if btn:
                is_disabled = btn.attr("disabled") is not None or "disabled" in (btn.attr("class") or "")
                if not is_disabled:
                    print(f"[*] 点击按钮: {btn.text or kw}")
                    btn.click()
                    return True
        time.sleep(1.5)
    # 兜底强制点击
    btn = page.ele('css:button[type="submit"]', timeout=2) or page.ele('tag:button', timeout=2)
    if btn:
        btn.click()
        return True
    return False


def main():
    if not EMAIL or not PASSWORD:
        print("[-] 错误: 请在 GitHub Secrets 中设置 ACORE_EMAIL 和 ACORE_PASSWORD")
        sys.exit(1)

    # 配置 ChromiumOptions (配合 Xvfb 使用有头模式绕过无头指纹检测)
    co = ChromiumOptions()
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--disable-dev-shm-usage')
    co.set_argument('--window-size=1920,1080')
    co.set_argument('--lang=zh-TW')

    page = ChromiumPage(co)

    try:
        # ==================== 第一步：输入账号 + CF验证 + 点击继续 ====================
        print(f"[*] 正在打开登录页: {LOGIN_URL}")
        page.get(LOGIN_URL)
        time.sleep(5)

        print("[*] 正在填写账号...")
        email_input = page.ele('css:input[type="text"], input[type="email"], input[name="username"], input[name="email"]', timeout=15)
        if not email_input:
            email_input = page.ele('tag:input', timeout=5)
        email_input.clear()
        email_input.input(EMAIL)
        time.sleep(1)

        # 第一步 CF 验证
        handle_turnstile(page, stage_name="第一步-账号输入")

        # 点击“繼續 / Continue”
        wait_and_click_submit(page, ["繼續", "继续", "Continue", "Next"])
        time.sleep(4)

        # ==================== 第二步：输入密码 + CF验证 + 点击登入 ====================
        print("[*] 等待密码输入框加载...")
        pwd_input = page.ele('css:input[type="password"]', timeout=20)
        if not pwd_input:
            page.get_screenshot(path="error_step1.png")
            raise RuntimeError("未能进入密码输入界面，请检查第一步 CF 验证是否被拦截（已保存截图 error_step1.png）")

        print("[*] 正在填写密码...")
        pwd_input.clear()
        pwd_input.input(PASSWORD)
        time.sleep(1)

        # 第二步 CF 验证
        handle_turnstile(page, stage_name="第二步-密码输入")

        # 点击“登入 / Login”
        wait_and_click_submit(page, ["登入", "登录", "Login", "Sign in"])
        time.sleep(6)

        # ==================== 第三步：进入服务器控制面板 ====================
        print(f"[*] 当前页面 URL: {page.url}")
        if "auth/login" in page.url:
            page.get_screenshot(path="error_login.png")
            raise RuntimeError("登录后仍停留在登录页，请检查账号密码或 CF 验证状态（已保存截图 error_login.png）")

        if SERVER_ID:
            target_url = f"{DASH_URL}/server/{SERVER_ID}"
            print(f"[*] 已配置 SERVER_ID，直接跳转至服务器面板: {target_url}")
            page.get(target_url)
        else:
            print("[*] 正在查找并点击服务器卡片...")
            # 查找指向 /server/xxxx 的链接或服务器列表项
            srv_link = page.ele('xpath://a[contains(@href, "/server/")]', timeout=10)
            if srv_link:
                srv_link.click()
            else:
                # 若是 div 绑定路由跳转，点击服务器名称所在行（排除 Create Server 按钮）
                srv_card = page.ele('xpath://div[contains(@class, "server") or contains(text(), "MiB")]', timeout=10)
                if srv_card:
                    srv_card.click()
                else:
                    page.get_screenshot(path="error_server_list.png")
                    raise RuntimeError("未能在列表中找到服务器，请建议在 Secrets 中直接配置 ACORE_SERVER_ID（如 1da63f5c）")

        time.sleep(6)
        print(f"[*] 已进入服务器控制面板: {page.url}")

        # ==================== 第四步：检查右上角 Renew 按钮 ====================
        print("[*] 正在检查右上角 Renew 按钮状态...")
        renew_btn = page.ele('xpath://button[contains(., "Renew")]', timeout=15)
        if not renew_btn:
            renew_btn = page.ele('text:Renew', timeout=5)

        if not renew_btn:
            page.get_screenshot(path="error_no_renew_btn.png")
            raise RuntimeError("未在页面上找到 Renew 按钮（已保存截图 error_no_renew_btn.png）")

        # 读取下方提示信息（例如：Renew available in 23h 48m）
        status_hint = ""
        hint_ele = page.ele('xpath://*[contains(text(), "Renew available") or contains(text(), "Until suspension")]', timeout=2)
        if hint_ele:
            status_hint = hint_ele.text

        # 判断按钮是否可点击
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

        if is_disabled:
            msg = f"当前 Renew 按钮处于不可点击（冷却）状态，无需操作。\n面板提示: {status_hint}"
            print(f"[=] {msg}")
            send_tg(msg)
        else:
            print("[+] 检测到 Renew 按钮可点击！正在执行点击续期...")
            renew_btn.click()
            time.sleep(5)

            # 检查点击后是否弹出二次验证弹窗 (Modal)
            modal_cf = page.ele('xpath://iframe[contains(@src, "cloudflare") or contains(@src, "turnstile")]', timeout=3)
            if modal_cf:
                handle_turnstile(page, stage_name="续期弹窗验证")
                confirm_btn = page.ele('xpath://button[contains(., "Confirm") or contains(., "確認") or contains(., "Renew")]', timeout=3)
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
        page.get_screenshot(path="error_final.png")
        send_tg(err_msg)
        sys.exit(1)
    finally:
        page.quit()


if __name__ == "__main__":
    main()
