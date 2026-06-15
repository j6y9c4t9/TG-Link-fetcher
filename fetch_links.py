```python
import os
import re
import urllib.request
import ssl
import socket
import base64
import random
import sys
import datetime
import time
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed

# =============================
# 基础配置
# =============================
channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')
URL = f"https://t.me/s/{channel_username}"
SUBSCRIBE_REGEX = r'https?://[^\s"\'<>]+token=[a-zA-Z0-9]+'

EXTERNAL_NODES_URL = "https://raw.githubusercontent.com/shaoyouvip/free/refs/heads/main/all.yaml"


# =============================
# TCP 延迟测试
# =============================
def tcp_ping(server, port, timeout=3):
    start = time.time()
    try:
        sock = socket.create_connection((server, port), timeout=timeout)
        sock.close()
        delay = (time.time() - start) * 1000
        return True, delay
    except:
        return False, None


def benchmark_nodes(nodes, max_workers=20):
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(tcp_ping, s, p): (s, p)
            for s, p in nodes
        }

        for future in as_completed(future_map):
            srv, prt = future_map[future]
            ok, delay = future.result()
            results.append({
                "server": srv,
                "port": prt,
                "ok": ok,
                "delay": delay
            })

    return results


# =============================
# 评分函数
# =============================
def score_result(results):
    total = len(results)
    success = [r for r in results if r["ok"]]

    if not success:
        return 0, 0, None

    success_rate = len(success) / total
    delays = [r["delay"] for r in success if r["delay"]]

    avg_delay = sum(delays) / len(delays) if delays else 9999

    score = (success_rate * 70) + (max(0, 300 - avg_delay) / 300 * 30)

    return score, success_rate, avg_delay


# =============================
# 核心：评估订阅
# =============================
def evaluate_subscription(link, ssl_context):
    try:
        req = urllib.request.Request(link, headers={'User-Agent': 'Mihomo'})
        with urllib.request.urlopen(req, context=ssl_context, timeout=8) as res:
            raw_data = res.read().decode('utf-8', errors='ignore').strip()

        if not raw_data:
            return None

        decoded_text = raw_data

        # Base64 处理
        if "proxies:" not in raw_data:
            try:
                padded = raw_data + '=' * (-len(raw_data) % 4)
                decoded = base64.b64decode(padded).decode('utf-8', errors='ignore')
                if "proxies:" in decoded:
                    decoded_text = decoded
            except:
                pass

        data = yaml.safe_load(decoded_text)
        proxies = data.get("proxies", [])

        nodes = []
        for p in proxies:
            if not isinstance(p, dict):
                continue

            server = p.get("server")
            port = p.get("port")

            if server and port:
                try:
                    nodes.append((server, int(port)))
                except:
                    continue

        if len(nodes) < 3:
            return None

        # 抽样
        sample_size = min(15, max(5, len(nodes)//3))
        sample_nodes = random.sample(nodes, sample_size)

        print(f"   🚀 并发测速 {sample_size} 个节点...")

        results = benchmark_nodes(sample_nodes)

        score, success_rate, avg_delay = score_result(results)

        print(f"   📊 成功率: {success_rate:.2f} | 延迟: {avg_delay:.0f} ms | 评分: {score:.1f}")

        if success_rate < 0.3:
            return None

        return {
            "link": link,
            "score": score,
            "delay": avg_delay
        }

    except:
        return None


# =============================
# 提取远端节点
# =============================
def fetch_external_proxies(url, ssl_context):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mihomo'})
        with urllib.request.urlopen(req, context=ssl_context, timeout=10) as res:
            content = res.read().decode('utf-8', errors='ignore')

        lines = content.split('\n')
        extracted_lines = []

        in_proxies_section = False
        current_node = []

        for line in lines:
            if line.startswith('proxies:'):
                in_proxies_section = True
                continue

            if in_proxies_section:
                if line.lstrip().startswith('-'):
                    if current_node:
                        extracted_lines.extend(current_node)
                    current_node = [line]
                elif line.startswith(' ') and current_node:
                    current_node.append(line)
                else:
                    break

        if current_node:
            extracted_lines.extend(current_node)

        return '\n'.join(extracted_lines)

    except:
        return ""


# =============================
# 主流程
# =============================
def main():
    ssl_context = ssl._create_unverified_context()

    req = urllib.request.Request(URL, headers={'User-Agent': 'Mozilla'})
    with urllib.request.urlopen(req, context=ssl_context) as res:
        html = res.read().decode('utf-8')

    raw_links = re.findall(SUBSCRIBE_REGEX, html)
    links = list(dict.fromkeys(raw_links))

    evaluated = []

    for i, link in enumerate(reversed(links)):
        print(f"\n🔄 测试订阅 [{i+1}]")

        result = evaluate_subscription(link, ssl_context)

        if result:
            evaluated.append(result)

        if len(evaluated) >= 5:
            break

    if not evaluated:
        print("❌ 无可用机场")
        sys.exit(0)

    evaluated.sort(key=lambda x: x["score"], reverse=True)

    main_link = evaluated[0]["link"]
    backup_link = evaluated[1]["link"] if len(evaluated) > 1 else ""

    print(f"\n🎯 主链: {main_link}")
    print(f"🎯 备链: {backup_link}")

    # === 写入模板 ===
    with open('template.yaml', 'r', encoding='utf-8') as f:
        content = f.read()

    content = re.sub(r"(主.*url:\s*)['\"].*?['\"]", f"\\1'{main_link}'", content)

    if backup_link:
        content = re.sub(r"(备.*url:\s*)['\"].*?['\"]", f"\\1'{backup_link}'", content)

    external = fetch_external_proxies(EXTERNAL_NODES_URL, ssl_context)

    if external:
        content = content.replace("proxies:\n", f"proxies:\n{external}\n")

    final = f"# Generated {datetime.datetime.now()}\n" + content

    with open('config.yaml', 'w', encoding='utf-8') as f:
        f.write(final)

    print("🎉 完成")


if __name__ == '__main__':
    main()
```
