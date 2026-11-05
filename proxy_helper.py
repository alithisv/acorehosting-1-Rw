import os
import json
import time
import base64
import subprocess
import urllib.parse
import requests

LOCAL_PROXY_PORT = 10808


def parse_vless(url):
    parsed = urllib.parse.urlparse(url)
    uuid = parsed.username
    server = parsed.hostname
    port = parsed.port or 443
    params = urllib.parse.parse_qs(parsed.query)
    get_p = lambda k, d="": params.get(k, [d])[0]

    outbound = {
        "type": "vless",
        "tag": "proxy",
        "server": server,
        "server_port": int(port),
        "uuid": uuid,
    }

    flow = get_p("flow")
    if flow:
        outbound["flow"] = flow

    security = get_p("security", "none")
    if security in ["tls", "reality"]:
        sni = get_p("sni") or get_p("host") or server
        fp = get_p("fp", "chrome")
        tls_cfg = {
            "enabled": True,
            "server_name": sni,
            "insecure": get_p("allowInsecure") in ["1", "true"],
            "utls": {"enabled": True, "fingerprint": fp}
        }
        alpn = get_p("alpn")
        if alpn:
            tls_cfg["alpn"] = alpn.split(",")
        if security == "reality":
            tls_cfg["reality"] = {
                "enabled": True,
                "public_key": get_p("pbk"),
                "short_id": get_p("sid")
            }
        outbound["tls"] = tls_cfg

    net_type = get_p("type", "tcp")
    if net_type == "ws":
        path = get_p("path", "/")
        host = get_p("host") or get_p("sni") or server
        outbound["transport"] = {
            "type": "ws",
            "path": path,
            "headers": {"Host": host}
        }
    elif net_type == "grpc":
        outbound["transport"] = {
            "type": "grpc",
            "service_name": get_p("serviceName", "")
        }
    elif net_type == "httpupgrade":
        outbound["transport"] = {
            "type": "httpupgrade",
            "path": get_p("path", "/"),
            "host": get_p("host") or server
        }

    return outbound


def parse_vmess(url):
    b64_str = url.replace("vmess://", "").strip()
    b64_str += "=" * ((4 - len(b64_str) % 4) % 4)
    data = json.loads(base64.b64decode(b64_str).decode("utf-8"))

    server = data.get("add")
    port = int(data.get("port", 443))
    uuid = data.get("id")
    aid = int(data.get("aid", 0))
    scy = data.get("scy", "auto")
    net = data.get("net", "tcp")
    tls = data.get("tls", "")
    host = data.get("host", "")
    path = data.get("path", "/")
    sni = data.get("sni") or host or server

    outbound = {
        "type": "vmess",
        "tag": "proxy",
        "server": server,
        "server_port": port,
        "uuid": uuid,
        "alter_id": aid,
        "security": scy
    }

    if tls == "tls":
        outbound["tls"] = {
            "enabled": True,
            "server_name": sni,
            "insecure": True,
            "utls": {"enabled": True, "fingerprint": "chrome"}
        }

    if net == "ws":
        outbound["transport"] = {
            "type": "ws",
            "path": path,
            "headers": {"Host": host or server}
        }
    elif net == "grpc":
        outbound["transport"] = {
            "type": "grpc",
            "service_name": path
        }

    return outbound


def parse_hy2(url):
    url_norm = url.replace("hy2://", "hysteria2://", 1)
    parsed = urllib.parse.urlparse(url_norm)
    password = urllib.parse.unquote(parsed.username or "")
    if parsed.password:
        password = f"{password}:{urllib.parse.unquote(parsed.password)}"

    server = parsed.hostname
    port = parsed.port or 443
    params = urllib.parse.parse_qs(parsed.query)
    get_p = lambda k, d="": params.get(k, [d])[0]

    sni = get_p("sni") or get_p("peer") or server
    insecure = get_p("insecure") in ["1", "true"] or get_p("allowInsecure") in ["1", "true"]

    outbound = {
        "type": "hysteria2",
        "tag": "proxy",
        "server": server,
        "server_port": int(port),
        "password": password,
        "tls": {
            "enabled": True,
            "server_name": sni,
            "insecure": insecure
        }
    }

    obfs = get_p("obfs")
    if obfs and obfs != "none":
        outbound["obfs"] = {
            "type": obfs,
            "password": get_p("obfs-password")
        }

    return outbound


def parse_trojan(url):
    parsed = urllib.parse.urlparse(url)
    password = urllib.parse.unquote(parsed.username or "")
    server = parsed.hostname
    port = parsed.port or 443
    params = urllib.parse.parse_qs(parsed.query)
    get_p = lambda k, d="": params.get(k, [d])[0]

    sni = get_p("sni") or get_p("host") or server
    outbound = {
        "type": "trojan",
        "tag": "proxy",
        "server": server,
        "server_port": int(port),
        "password": password,
        "tls": {
            "enabled": True,
            "server_name": sni,
            "insecure": get_p("allowInsecure") in ["1", "true"],
            "utls": {"enabled": True, "fingerprint": "chrome"}
        }
    }

    net_type = get_p("type", "tcp")
    if net_type == "ws":
        outbound["transport"] = {
            "type": "ws",
            "path": get_p("path", "/"),
            "headers": {"Host": get_p("host") or sni}
        }
    return outbound


def start_proxy(proxy_url):
    """根据代理链接启动 sing-box 或返回直连代理地址，返回 (local_proxy_str, process)"""
    if not proxy_url:
        return None, None

    proxy_url = proxy_url.strip()

    # 若本身就是标准 http/socks5 代理且不含账号密码，Chromium 可直接使用，也可统一通过 sing-box 转发解决认证问题
    if proxy_url.startswith(("vless://", "vmess://", "hy2://", "hysteria2://", "trojan://")):
        print(f"[*] 正在解析节点协议: {proxy_url.split('://')[0]}://***")
        if proxy_url.startswith("vless://"):
            outbound = parse_vless(proxy_url)
        elif proxy_url.startswith("vmess://"):
            outbound = parse_vmess(proxy_url)
        elif proxy_url.startswith(("hy2://", "hysteria2://")):
            outbound = parse_hy2(proxy_url)
        elif proxy_url.startswith("trojan://"):
            outbound = parse_trojan(proxy_url)

        config = {
            "log": {"level": "warn"},
            "inbounds": [
                {
                    "type": "mixed",
                    "tag": "mixed-in",
                    "listen": "127.0.0.1",
                    "listen_port": LOCAL_PROXY_PORT
                }
            ],
            "outbounds": [
                outbound,
                {"type": "direct", "tag": "direct"}
            ]
        }

        with open("sb_config.json", "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        print("[*] 正在启动 sing-box 本地代理服务...")
        proc = subprocess.Popen(
            ["sing-box", "run", "-c", "sb_config.json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        time.sleep(3)

        if proc.poll() is not None:
            out, _ = proc.communicate()
            raise RuntimeError(f"sing-box 启动失败，输出日志:\n{out}")

        local_proxy = f"http://127.0.0.1:{LOCAL_PROXY_PORT}"
        # 测试代理连通性并打印出口 IP
        try:
            resp = requests.get(
                "https://api.ipify.org?format=json",
                proxies={"http": local_proxy, "https": local_proxy},
                timeout=10
            )
            print(f"[+] 代理连接成功！当前节点出口 IP: {resp.json().get('ip')}")
        except Exception as e:
            print(f"[!] 代理连通性测试警告 ({e})，将继续尝试使用该代理运行浏览器...")

        return local_proxy, proc

    elif proxy_url.startswith(("http://", "https://", "socks5://")):
        print(f"[*] 使用常规代理协议: {proxy_url.split('://')[0]}://***")
        return proxy_url, None
    else:
        print("[!] 未识别的代理链接格式，将使用直连模式运行")
        return None, None
