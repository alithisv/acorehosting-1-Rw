# 🚀 Acore Hosting 自动续期脚本 (Auto Renew)

基于 **GitHub Actions + Python (DrissionPage) + Xvfb + sing-box** 构建的 [Acore Hosting](https://dash.acorehosting.com) 免费服务器全自动续期项目[cite: 2, 3, 4]。支持绕过两步式 Cloudflare Turnstile 人机验证，支持 **VLESS / VMess / Hysteria2 / Trojan / SOCKS5 / HTTP 代理节点**，定时检查服务器控制面板右上角的 `Renew` 状态并自动完成续期[cite: 1, 2, 4]。

---

## ✨ 功能特性

- **两步式登录支持**：完整适配 Acore Hosting“输入账号 -> CF验证 -> 点击继续 -> 输入密码 -> CF验证 -> 点击登录”的双重验证流程[cite: 1, 2]。
- **多协议代理核心集成**：内置 `sing-box` 核心，完美支持 `vless://`、`vmess://`、`hysteria2:// (hy2://)`、`trojan://` 等主流代理节点，彻底解决 GitHub Actions 机房 IP 被 Cloudflare 重点拦截的问题。
- **真实桌面环境模拟**：通过 `Xvfb` 虚拟显示器运行有头模式 Chromium，配合 `DrissionPage` 自动识别并点击 Cloudflare Turnstile 验证框[cite: 1, 2]。
- **系统时钟自动校准**：内置 NTP 时间同步，防止因容器时间偏移超过 5 秒导致 Cloudflare 或双因素认证报错[cite: 1, 2]。
- **智能状态检测**：自动进入服务器控制面板检测右上角 `Renew` 按钮是否处于可点击状态；若在冷却期内则安全跳过，若可点击则自动触发续期[cite: 4]。
- **通知与排错支持**：支持 Telegram Bot 实时推送运行状态，并在 GitHub Actions 中自动上传运行截图（Artifacts）以便快速排查异常。

---

## 📁 项目结构

```text
├── .github/
│   └── workflows/
│       └── renew.yml      # GitHub Actions 定时任务与运行环境配置
├── proxy_helper.py        # 代理核心解析与 sing-box 启动管理模块
├── requirements.txt       # Python 依赖包列表
├── renew.py               # 核心自动化登录与续期脚本
└── README.md              # 项目说明文档
```

---

## 🛠️ 快速开始

### 1. 准备仓库文件
将本项目的所有代码文件（`renew.py`、`proxy_helper.py`、`requirements.txt` 以及 `.github/workflows/renew.yml`）上传至你的 GitHub 仓库（建议使用 **Private 私有仓库**，以保护账号密码和代理节点隐私）。

### 2. 配置 GitHub Secrets
进入你的 GitHub 仓库，依次点击 **`Settings`** -> **`Secrets and variables`** -> **`Actions`** -> **`New repository secret`**，添加以下环境变量：

| Secret 名称 | 是否必填 | 说明与示例 |
| :--- | :---: | :--- |
| `ACORE_EMAIL` | **必填** | Acore Hosting 登录邮箱或用户名[cite: 1, 2] |
| `ACORE_PASSWORD` | **必填** | Acore Hosting 登录密码[cite: 2] |
| `ACORE_SERVER_ID` | **推荐选填** | 服务器 ID（例如面板网址 `https://dash.acorehosting.com/server/1da63f5c` 中的 `1da63f5c`）[cite: 4]。配置后登录后可直接跳转至目标服务器面板；若不填则默认点击列表中的第一台服务器[cite: 3, 4]。 |
| `PROXY_URL` | **可选** | 代理节点分享链接（支持 `vless://`、`vmess://`、`hy2://`、`trojan://`、`socks5://`、`http://` 等）。若不填则默认使用 GitHub 官方服务器直连。 |
| `TG_BOT_TOKEN` | 可选 | Telegram 机器人 Token（用于接收续期结果通知，如 `123456789:ABCDEF...`） |
| `TG_CHAT_ID` | 可选 | 接收通知的 Telegram 用户或群组 Chat ID |

---

## 🌐 關於 `PROXY_URL` 代理節點配置說明

由于 Cloudflare Turnstile 对各大公有云机房（如 GitHub Actions 的 Azure/AWS/GitHub IP 段）风控较严，如果你在运行日志中频繁遇到 Cloudflare 验证超时，建议配置 `PROXY_URL` 环境变量。

### 支持的节点链接格式示例：
1. **VLESS 节点 (支持 Reality / WS / gRPC)**:
   ```text
   vless://uuid@your-domain.com:443?encryption=none&security=reality&sni=your-domain.com&pbk=公钥...&sid=短ID#节点名称
   ```
2. **VMess 节点**:
   ```text
   vmess://eyJ2IjoiMiIsInBzIjoi... (Base64 编码的 vmess 链接)
   ```
3. **Hysteria2 / HY2 节点**:
   ```text
   hy2://your-password@your-domain.com:443?sni=your-domain.com&insecure=1#节点名称
   ```
4. **Trojan 节点**:
   ```text
   trojan://your-password@your-domain.com:443?sni=your-domain.com#节点名称
   ```
5. **常规代理**:
   ```text
   socks5://username:password@ip:port
   或
   http://ip:port
   ```

> **💡 提示：** 项目内部集成了 `sing-box` 核心，能够自动解析上述各类主流分享链接并在运行期间临时启动本地代理通道，浏览器流量会自动走代理出口。

---

## ⏰ 定时策略说明

在 `.github/workflows/renew.yml` 中，默认配置的 Cron 表达式为每 48 小时运行一次：

```yaml
on:
  schedule:
    - cron: '0 0 */2 * *'
```

> **💡 实用建议：**
> Acore Hosting 免费计划的总有效期（Overall）通常为 **48 小时**，且每 **24 小时** 开放一次续期窗口（`Renew available in ...`）[cite: 4]。由于 GitHub Actions 的定时任务在高峰期可能会有几分钟到几十分钟的排队延迟，建议将 `.github/workflows/renew.yml` 中的定时策略修改为 **每 12 小时** 检查一次（冷却期内脚本会自动跳过，安全无副作用）：
> ```yaml
>     # 每 12 小时运行一次检查（推荐）
>     - cron: '0 */12 * * *'
> ```

---

## 🔍 常见问题与排错 (Troubleshooting)

- **如何手动触发测试？**
  1. 点击仓库顶部的 **`Actions`** 标签页。
  2. 在左侧工作流列表中选择 **`AcoreHosting Auto Renew`**。
  3. 点击右侧的 **`Run workflow`** -> **`Run workflow`** 按钮。

- **如何查看运行截图排查错误？**
  当脚本执行完毕后，点开对应的 GitHub Actions 运行记录，滑动到页面最底部的 **Artifacts** 区域，下载 `runtime-screenshots` 压缩包即可查看网页实时截图：
  - `renew_result.png`：续期成功截图。
  - `step1_after_cf.png` / `error_step1.png`：登录第一步账号与验证状态。
  - `error_login.png`：登录失败或密码错误。
  - `error_no_renew_btn.png`：进入面板后未找到 Renew 按钮。

---

## ⚠️ 免责声明

本项目仅供个人学习与技术交流使用，请勿用于高频滥用或违反服务商条款的操作。使用本脚本产生的任何账号风险由使用者自行承担。
