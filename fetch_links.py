import os
import re
import urllib.request
import urllib.error
import ssl
import socket
import base64
import json
import random
import sys
import datetime
import concurrent.futures
from urllib.parse import unquote

# ========== 配置 ==========
channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')
URL = f"https://t.me/s/{channel_username}"
SUBSCRIBE_REGEX = r'https?://[^\s"\'<>]+token=[a-zA-Z0-9]+'
EXTERNAL_NODES_URL = "https://raw.githubusercontent.com/shaoyouvip/free/refs/heads/main/all.yaml"


# ========== CF 入口识别 ==========
def is_cf_endpoint(hostname):
    if not hostname:
        return False
    h = hostname.lower()
    return any(k in h for k in [
        '.pages.dev', '.workers.dev', '.qzz.io',
        '.kmj.io', 'dpdns.org', '.090227.xyz',
        '.7zz.cn', '.js.cool',
    ])


# ========== 网络检测（升级版） ==========

def test_tcp_port(server, port, timeout=3.0):
    """TCP + RTT"""
    try:
        start = datetime.datetime.now()

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((server, int(port)))

        end = datetime.datetime.now()
        sock.close()

        latency = (end - start).total_seconds() * 1000
        return True, latency

    except Exception:
        return False, None


def test_https_endpoint(hostname, timeout=6.0):
    """HTTPS + RTT"""
    try:
        start = datetime.datetime.now()

        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(
            f"https://{hostname}/",
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        resp = urllib.request.urlopen(req, context=ctx, timeout=timeout)

        end = datetime.datetime.now()
        latency = (end - start).total_seconds() * 1000

        code = resp.getcode()
        resp.close()

        return True, latency, code

    except Exception:
        return False, None, None


# ========== 订阅解析（原逻辑保留） ==========

def extract_check_targets(decoded_text):
    targets = []
    seen = set()

    proxies_match = re.search(r'^proxies:\s*$', decoded_text, re.MULTILINE)
    if proxies_match:
        proxies_text = decoded_text[proxies_match.end():]
        chunks = re.split(r'\n(?=\s*-\s)', proxies_text)

        for chunk in chunks:
            if 'server:' not in chunk:
                continue

            server = re.search(r'server:\s*["\']?([^\s\'",\]\}]+)', chunk)
            port = re.search(r'port:\s*["\']?(\d+)', chunk)
            sni = re.search(r'(?:sni|servername):\s*["\']?([^\s\'",\]\}]+)', chunk)
            host = re.search(r'[Hh]ost:\s*["\']?([^\s\'",\]\}]+)', chunk)

            if not server:
                continue

            server = server.group(1)
            port = int(port.group(1)) if port else 443
            sni = sni.group(1) if sni else ''
            host = host.group(1) if host else ''

            check_target = None
            method = 'tcp'

            for c in [sni, host]:
                if is_cf_endpoint(c):
                    check_target = c
                    method = 'https'
                    break

            if not check_target:
                check_target = server

            key = (check_target, 443 if method == 'https' else port)
            if key not in seen:
                seen.add(key)
                targets.append((check_target, key[1], method, ""))

        if targets:
            return targets

    return []


# ========== 核心检测（重写） ==========

def check_one(target, port, method):
    if method == 'https':
        ok, latency, _ = test_https_endpoint(target)
    else:
        ok, latency = test_tcp_port(target, port)

    return {
        "ok": ok,
        "latency": latency if latency else 9999
    }


def is_subscription_alive(link, ssl_context):
    try:
        req = urllib.request.Request(link, headers={'User-Agent': 'Mihomo'})
        with urllib.request.urlopen(req, context=ssl_context, timeout=8) as res:
            raw_data = res.read().decode('utf-8', errors='ignore').strip()

        if not raw_data:
            return False

        decoded_text = raw_data

        # Base64 decode
        if re.match(r'^[a-zA-Z0-9+/=\s]+$', raw_data):
            try:
                decoded_text = base64.b64decode(raw_data).decode('utf-8', errors='ignore')
            except:
                pass

        targets = extract_check_targets(decoded_text)
        if not targets:
            return False

        random.shuffle(targets)
        sample = targets[:min(8, len(targets))]

        # 并发检测
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(check_one, *t) for t in sample]
            results = [f.result() for f in futures]

        success = [r for r in results if r["ok"]]
        if not success:
            return False

        success_rate = len(success) / len(results)
        avg_latency = sum(r["latency"] for r in success) / len(success)

        # 打分
        score = (
            success_rate * 0.6 +
            (1 / (avg_latency + 1)) * 1000 * 0.4
        )

        print(f"成功率: {success_rate:.2f}, 延迟: {avg_latency:.0f}ms, score: {score:.2f}")

        # 判定
        if success_rate >= 0.6 and avg_latency < 2000:
            return True
        elif success_rate >= 0.4:
            return True
        else:
            return False

    except Exception:
        return False


# ========== 主流程（完全保留） ==========

def main():
    try:
        ssl_context = ssl._create_unverified_context()

        print(f"📡 抓取: {URL}")
        req = urllib.request.Request(URL)
        with urllib.request.urlopen(req, context=ssl_context, timeout=15) as response:
            html_content = response.read().decode('utf-8')

        raw_links = re.findall(SUBSCRIBE_REGEX, html_content)
        if not raw_links:
            sys.exit(0)

        links = list(set(raw_links))

        valid_main = None
        valid_backup = None

        for link in reversed(links):
            print(f"检测: {link}")
            if is_subscription_alive(link, ssl_context):
                if not valid_main:
                    valid_main = link
                elif not valid_backup:
                    valid_backup = link
                    break

        print("主链:", valid_main)
        print("备链:", valid_backup)

    except Exception as e:
        print(e)


if __name__ == '__main__':
    main()
