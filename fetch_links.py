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

# 配置部分
channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')
URL = f"https://t.me/s/{channel_username}"
SUBSCRIBE_REGEX = r'https?://[^\s"\'<>]+token=[a-zA-Z0-9]+'

# 需要提取节点的远程订阅源 URL
EXTERNAL_NODES_URL = "https://raw.githubusercontent.com/shaoyouvip/free/refs/heads/main/all.yaml"


def test_tcp_port(server, port, timeout=3.0):
    """TCP 端口连通性探测"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((server, int(port)))
        sock.close()
        return True
    except Exception:
        return False


def extract_server_port_pairs(decoded_text):
    """
    统一提取函数：同时支持 YAML 格式 和 URI 格式 (vmess://, vless://, ss://, trojan:// 等)
    返回 [(server, port), ...] 列表
    """
    pairs = []

    # ── 1) 尝试 YAML 格式 (Clash / Mihomo) ──
    servers = re.findall(r'server:\s*["\']?([^\s\'",\]]+)', decoded_text)
    ports = re.findall(r'port:\s*["\']?(\d+)', decoded_text)

    for s, p in zip(servers, ports):
        s_clean = s.strip("'\" ,\r\n\t").split(',')[0]
        try:
            pairs.append((s_clean, int(p)))
        except ValueError:
            continue

    if len(pairs) >= 3:
        # YAML 节点数量足够，直接返回
        return pairs

    # ── 2) 尝试 URI 格式 ──
    uri_pattern = r'(?:vless|trojan|vmess|ss|hysteria2?|tuic)://[^\s\'"<>]+'
    uris = re.findall(uri_pattern, decoded_text)

    for uri in uris:
        try:
            scheme = uri.split('://')[0]

            if scheme == 'vmess':
                b64_part = uri[len('vmess://'):].strip()
                missing = len(b64_part) % 4
                if missing:
                    b64_part += '=' * (4 - missing)
                vmess_obj = json.loads(base64.b64decode(b64_part).decode('utf-8', errors='ignore'))
                s = str(vmess_obj.get('add', '')).strip()
                p = vmess_obj.get('port', '')
                if s and p:
                    pairs.append((s, int(p)))
                continue

            # vless://uuid@server:port?params#name
            # trojan://pass@server:port?params#name
            # hysteria2://auth@server:port?params#name
            # tuic://uuid:pass@server:port?params#name
            # ss://base64(method:pass)@server:port#name
            after_scheme = uri.split('://', 1)[1]

            if '@' in after_scheme:
                host_part = after_scheme.split('@', 1)[1]
            else:
                # ss://base64 整体编码 格式: ss://BASE64#name
                b64_part = after_scheme.split('#')[0].split('?')[0]
                missing = len(b64_part) % 4
                if missing:
                    b64_part += '=' * (4 - missing)
                try:
                    decoded_ss = base64.b64decode(b64_part).decode('utf-8', errors='ignore')
                    if '@' in decoded_ss:
                        host_part = decoded_ss.split('@', 1)[1]
                    else:
                        host_part = decoded_ss
                except Exception:
                    continue

            # 清理 params 和 fragment
            host_part = host_part.split('?')[0].split('#')[0].strip()

            if not host_part:
                continue

            # 提取 server:port，兼容 IPv6
            if host_part.startswith('['):
                # IPv6: [::1]:443
                bracket_end = host_part.find(']')
                if bracket_end != -1:
                    server = host_part[1:bracket_end]
                    remainder = host_part[bracket_end + 1:]
                    if ':' in remainder:
                        port_str = remainder.split(':')[1]
                    else:
                        continue
                else:
                    continue
            elif ':' in host_part:
                parts = host_part.rsplit(':', 1)
                server = parts[0]
                port_str = parts[1]
            else:
                continue

            # 清理 IPv6 bracket
            server = server.strip('[]')

            try:
                port_val = int(port_str)
            except ValueError:
                continue

            if server and 0 < port_val < 65536:
                pairs.append((server, port_val))

        except Exception:
            continue

    return pairs


def is_subscription_alive(link, ssl_context):
    """检测订阅链接是否存活：下载内容 -> 解析节点 -> 抽检 TCP 连通性"""
    try:
        req = urllib.request.Request(link, headers={'User-Agent': 'Mihomo'})
        with urllib.request.urlopen(req, context=ssl_context, timeout=8) as res:
            raw_data = res.read().decode('utf-8', errors='ignore').strip()

        if not raw_data:
            print("   ⚠️ 订阅返回内容为空。")
            return False

        decoded_text = raw_data

        # 尝试 Base64 解码（常见的订阅编码方式）
        if re.match(r'^[a-zA-Z0-9+/=\s]+$', raw_data) and len(raw_data) > 30:
            try:
                b64_input = raw_data
                missing_padding = len(b64_input) % 4
                if missing_padding:
                    b64_input += '=' * (4 - missing_padding)
                decoded_text = base64.b64decode(b64_input).decode('utf-8', errors='ignore')
                print("   🔓 成功识别并完成 Base64 本地解密。")
            except Exception:
                # 解码失败，用原文继续
                pass

        # 使用统一提取函数（同时支持 YAML + URI 两种格式）
        valid_pairs = extract_server_port_pairs(decoded_text)

        # 过滤明显无效的地址
        invalid_keywords = ["127.0.0.1", "localhost", "网址", "官网", "频道", "公告", "example.com"]
        valid_pairs = [
            (s, p) for s, p in valid_pairs
            if s and not any(x in s.lower() for x in invalid_keywords)
        ]

        if not valid_pairs:
            print("   ⚠️ 未能从订阅内容中提取到任何有效 server:port 对。")
            return False

        print(f"   📦 成功清洗出 {len(valid_pairs)} 个节点，开始抽检连通性...")

        # ── 分离 CDN 节点和普通节点，优先抽检普通节点 ──
        cdn_keywords = [
            'fastly', 'cloudflare', 'speedtest', 'amazonaws',
            'akamaiedge', 'cloudfront', 'cdn', 'edgekey',
            'akamai', 'edgesuite', 'footprint', 'cachefly',
            'stackpath', 'bunny', 'azure', 'edgecast'
        ]

        normal_nodes = []
        cdn_nodes = []
        seen = set()

        for s, p in valid_pairs:
            key = (s.lower(), p)
            if key in seen:
                continue
            seen.add(key)
            s_lower = s.lower()
            if any(k in s_lower for k in cdn_keywords):
                cdn_nodes.append((s, p))
            else:
                normal_nodes.append((s, p))

        # 优先从普通节点中抽样；如果全是 CDN 节点则用 CDN 的
        candidates = normal_nodes if normal_nodes else cdn_nodes
        sample_size = min(5, len(candidates))
        sample_pairs = random.sample(candidates, sample_size)

        is_all_cdn = not normal_nodes
        success_needed = 1 if is_all_cdn else 2  # CDN 节点放宽标准

        success_count = 0
        for srv, prt in sample_pairs:
            print(f"      🔍 抽检 {srv}:{prt} ... ", end="")
            if test_tcp_port(srv, prt, timeout=3.0):
                print("✅")
                success_count += 1
                if success_count >= success_needed:
                    if is_all_cdn:
                        print("      ℹ️ 全为 CDN 节点，TCP 通判为候选存活（非完全确认）。")
                    else:
                        print("      ✅ 连通成功！该订阅处于存活状态。")
                    return True
            else:
                print("❌")

        print("      ❌ 抽检节点均不可达，判定为失效。")
        return False

    except urllib.error.HTTPError as e:
        print(f"   ❌ HTTP 错误: {e.code} {e.reason}")
        return False
    except urllib.error.URLError as e:
        print(f"   ❌ URL 错误: {e.reason}")
        return False
    except Exception as e:
        print(f"   ❌ 检测异常: {e}")
        return False


def fetch_external_proxies(url, ssl_context):
    """从远端 YAML 文件中提取 proxies 节点列表（多行格式）"""
    print(f"🌐 正在从远端源提取独立节点: {url}")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mihomo'})
        with urllib.request.urlopen(req, context=ssl_context, timeout=15) as res:
            content = res.read().decode('utf-8', errors='ignore')

        lines = content.split('\n')
        extracted_lines = []

        in_proxies_section = False
        current_node_lines = []
        node_count = 0

        for line in lines:
            stripped = line.rstrip()

            # 检测顶级 keys 出现，结束 proxies 段
            if in_proxies_section and stripped and not line.startswith(' ') and not stripped.startswith('-'):
                if not stripped.startswith('proxies'):
                    break

            if line.startswith('proxies:') or line.startswith('proxies :'):
                in_proxies_section = True
                continue

            if in_proxies_section:
                if stripped == '':
                    if current_node_lines:
                        extracted_lines.extend(current_node_lines)
                        node_count += 1
                        current_node_lines = []
                    continue

                if line.lstrip().startswith('-'):
                    # 新节点开始
                    if current_node_lines:
                        extracted_lines.extend(current_node_lines)
                        node_count += 1
                    current_node_lines = [line]
                elif current_node_lines:
                    current_node_lines.append(line)

        # 处理最后一个节点
        if current_node_lines:
            extracted_lines.extend(current_node_lines)
            node_count += 1

        if extracted_lines:
            result = '\n'.join(extracted_lines).rstrip()
            print(f"🎉 [大获成功] 状态机完美剥离出 {node_count} 个多行高级代理节点！")
            return result

        print("⚠️ 未能从远端 YAML 中发现标准的 proxies 节点列表。")
        return ""
    except Exception as e:
        print(f"❌ 抓取远端节点失败: {e}")
        return ""


def main():
    try:
        ssl_context = ssl._create_unverified_context()

        # ── 1. 抓取 TG 页面机场链接 ──
        print(f"📡 正在抓取 TG 频道页面: {URL}")
        req = urllib.request.Request(
            URL,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, context=ssl_context, timeout=15) as response:
            html_content = response.read().decode('utf-8')

        raw_links = re.findall(SUBSCRIBE_REGEX, html_content, re.IGNORECASE)
        if not raw_links:
            print("ℹ️ 未在 TG 页面发现任何有效机场订阅链接。")
            sys.exit(0)

        # 去重 + 清洗
        cleaned_links = []
        for link in raw_links:
            clean = link.replace('&amp;', '&').split('<')[0].split('>')[0].strip()
            if clean not in cleaned_links:
                cleaned_links.append(clean)

        print(f"📦 共有 {len(cleaned_links)} 个不重复的原始链接。开始由新到旧进行本地筛选...\n")

        # ── 2. 依次筛选出可用的 [主] 和 [备] 两个不同的有效链接 ──
        valid_main_link = None
        valid_backup_link = None

        for i, link in enumerate(reversed(cleaned_links)):
            print(f"🔄 [{i + 1}/{len(cleaned_links)}] 正在检测: {link}")
            if is_subscription_alive(link, ssl_context):
                if not valid_main_link:
                    valid_main_link = link
                    print(f"🎉 [成功锁定主链] 最新可用的[主]链接: {valid_main_link}\n")
                elif not valid_backup_link and link != valid_main_link:
                    valid_backup_link = link
                    print(f"🎉 [成功锁定备链] 次新可用的[备]链接: {valid_backup_link}\n")
                    break  # 找齐了主备两个有效链接，提前收工
            print()

        if not valid_main_link:
            print("⚠️ 提示：TG 页面上所有机场订阅已全军覆没！保持原有配置，今天暂不更新。")
            sys.exit(0)

        # 兜底逻辑：如果 TG 频道里只洗出了一个有效的活机场，备链路置空
        if not valid_backup_link:
            print("ℹ️ 提示：目前全频道仅筛出一个有效订阅，[备]链路将置为空。")
            valid_backup_link = ""

        # ── 3. 提取远端独立节点 ──
        external_proxies_block = fetch_external_proxies(EXTERNAL_NODES_URL, ssl_context)

        # ── 4. 读取模板文件 ──
        template_path = 'template.yaml'
        if not os.path.exists(template_path):
            print(f"❌ 错误：未在仓库中找到 {template_path} 模板文件！")
            sys.exit(1)

        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()

        # ── 5. 🎯 动态更新 [主] 链路 url ──
        main_pattern = r"(\b主\s*:\s*\{[^}]*url\s*:\s*['\"]?).*?(['\"]?\s*[,}])"
        modified_content = re.sub(main_pattern, f"\\1{valid_main_link}\\2", template_content)

        # ── 6. 🎯 动态更新 [备] 链路 url ──
        if valid_backup_link:
            backup_pattern = r"(\b备\s*:\s*\{[^}]*url\s*:\s*['\"]?).*?(['\"]?\s*[,}])"
            modified_content = re.sub(backup_pattern, f"\\1{valid_backup_link}\\2", modified_content)
        else:
            print("ℹ️ 备链路为空，将 [备] 的 url 清空。")
            backup_pattern = r"(\b备\s*:\s*\{[^}]*url\s*:\s*['\"]?).*?(['\"]?\s*[,}])"
            modified_content = re.sub(backup_pattern, r"\1\2", modified_content)

        # ── 7. 🎯 彻底清洗：确保整个配置文件的 proxy-providers 中不存在任何 proxy 代理尾巴 ──
        modified_content = re.sub(
            r"(?<=\{)[^}]*?,\s*proxy\s*:\s*[^,}]+",
            lambda m: m.group(0).split(',')[0],
            modified_content
        )
        modified_content = re.sub(r",\s*proxy\s*:\s*[^,}]+(?=\s*\})", "", modified_content)

        # ── 8. 安全写入远端多行复合节点到 config.yaml 的 proxies 中 ──
        if external_proxies_block:
            print("📝 正在安全写入多行复合节点到 config.yaml 的 proxies 中...")
            target_placeholder = "proxies:\n"
            if target_placeholder in modified_content:
                modified_content = modified_content.replace(
                    target_placeholder, f"proxies:\n{external_proxies_block}\n"
                )
            else:
                print("⚠️ 警告：模板中未发现顶格的 proxies: 标记，尝试强行追加。")
                modified_content += f"\nproxies:\n{external_proxies_block}\n"
        else:
            print("⚠️ 提示：由于未提取到有效的远端节点，proxies 块保持模板默认状态。")

        # ── 9. 加入防无变动提交的时间戳 ──
        current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        final_yaml_content = f"# Generated & Checked at: {current_time}\n" + modified_content

        # ── 10. 保存最终配置 ──
        with open('config.yaml', 'w', encoding='utf-8') as f:
            f.write(final_yaml_content)

        print("\n🎉 [完美收工] 主备动态双链路、无 proxy 直连化更新成功！")
        sys.exit(0)

    except Exception as e:
        print(f"❌ 运行崩溃: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
