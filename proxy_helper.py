import json
import subprocess
import time
import urllib.parse
import requests

LOCAL_PROXY_PORT = 10808


def parse_vless(url):
    parsed = urllib.parse.urlsplit(url)
    uuid = parsed.username
    server = parsed.hostname
    port = parsed.port or 443
    params = dict(urllib.parse.parse_qsl(parsed.query))

    outbound = {
        "type": "vless",
        "tag": "proxy",
        "server": server,
        "server_port": int(port),
        "uuid": uuid,
    }

    flow = params.get("flow", "")
    if flow:
        outbound["flow"] = flow

    security = params.get("security", "none").lower()
    if security in ("tls", "reality"):
        tls_cfg = {
            "enabled": True,
            "server_name": params.get("sni") or params.get("host") or server,
            "insecure": params.get("allowInsecure") == "1" or params.get("insecure") == "1",
        }
        fp = params.get("fp", "")
        if fp:
            tls_cfg["utls"] = {"enabled": True, "fingerprint": fp}
        if security == "reality":
            tls_cfg["reality"] = {
                "enabled": True,
                "public_key": params.get("pbk", ""),
                "short_id": params.get("sid", ""),
            }
        outbound["tls"] = tls_cfg

    net_type = params.get("type", "tcp").lower()
    if net_type == "ws":
        outbound["transport"] = {
            "type": "ws",
            "path": params.get("path", "/"),
            "headers": {"Host": params.get("host") or params.get("sni") or server},
        }
    elif net_type == "grpc":
        outbound["transport"] = {
            "type": "grpc",
            "service_name": params.get("serviceName", ""),
        }

    return outbound


def start_proxy(proxy_url):
    """启动本地代理服务，若节点不可用则自动返回 None 切换直连"""
    if not proxy_url:
        print("[*] 未配置 PROXY_URL，将使用直连模式。")
        return None, None

    proxy_url = proxy_url.strip().splitlines()[0].strip()
    scheme = proxy_url.split("://")[0].lower()
    print(f"[*] 正在解析节点协议: {scheme}://***")

    if scheme in ("http", "https", "socks5", "socks5h"):
        return proxy_url, None

    if scheme != "vless":
        print(f"[!] 暂不支持协议 {scheme}，将使用直连模式。")
        return None, None

    try:
        outbound = parse_vless(proxy_url)
        config = {
            "log": {"level": "warn"},
            "inbounds": [
                {
                    "type": "mixed",
                    "tag": "mixed-in",
                    "listen": "127.0.0.1",
                    "listen_port": LOCAL_PROXY_PORT,
                }
            ],
            "outbounds": [outbound, {"type": "direct", "tag": "direct"}],
        }

        with open("singbox_config.json", "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        print("[*] 正在启动 sing-box 本地代理服务...")
        proc = subprocess.Popen(
            ["sing-box", "run", "-c", "singbox_config.json"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(2.5)

        local_proxy = f"http://127.0.0.1:{LOCAL_PROXY_PORT}"
        proxies = {"http": local_proxy, "https": local_proxy}

        for test_url in ["https://api.ipify.org?format=json", "https://www.cloudflare.com/cdn-cgi/trace"]:
            try:
                r = requests.get(test_url, proxies=proxies, timeout=8)
                if r.status_code == 200:
                    print(f"[+] 代理连接成功! 响应预览: {r.text.strip()[:60]}")
                    return local_proxy, proc
            except Exception:
                continue

        print("[!] 代理节点无法连通外网，已自动关闭失效代理并回退直连模式。")
        proc.terminate()
        return None, None

    except Exception as e:
        print(f"[!] 启动代理异常 ({e})，回退直连模式。")
        return None, None
