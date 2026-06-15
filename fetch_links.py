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
from urllib.parse import urlparse, parse_qs, unquote

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


def test_https_endpoint(hostname, timeout=5.0):
    """
    对 CF Workers/Pages 等 HTTPS 入口做真正的 HTTP 请求检测。
    返回 (is_alive: bool, status_code: int|None, reason: str)
    """
    try:
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(
            f"https://{hostname}/",
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': '*/*',
            }
        )
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            code = resp.getcode()
            # CF Worker 存活：任何 2xx/3xx/4xx 都说明 Worker 还在运行
            # 只有 DNS 失败、连接拒绝、CF 5xx 错误页面才算死
            if code < 500:
                return True, code, f"HTTP {code}"
            else:
                # 5xx 可能是 Worker 内部错误，但也可能是 CF 本身的问题
                # 读取内容判断是否是 CF 的 5xx 错误页
                try:
                    body = resp.read(512).decode('utf-8', errors='ignore')
                except:
                    body = ''
                # Cloudflare 标准错误页会包含这些关键字
                if 'cloudflare' in body.lower() or 'error 10' in body.lower():
                    return False, code, f"CF Error {code}"
                return True, code, f"HTTP {code} (server error but reachable)"
    except urllib.error.HTTPError as e:
        code = e.code
        if code == 530:  # Cloudflare: DNS points to prohibited IP
            return False, code, "CF 530 - Worker/Page deleted"
        if code < 500:
            return True, code, f"HTTP {code}"
        return False, code, f"HTTP {code}"
    except urllib.error.URLError as e:
        reason = str(e.reason)
        # DNS 解析失败 = 域名不存在或已过期
        if 'getaddrinfo' in reason or 'Name or service not known' in reason:
            return False, None, f"DNS failed: {reason}"
        if 'Connection refused' in reason:
            return False, None, f"Connection refused"
        if 'timed out' in reason.lower():
            return False, None, f"Timeout"
        return False, None, f"URLError: {reason}"
    except socket.timeout:
        return False, None, "Socket timeout"
    except Exception as e:
        return False, None, f"Exception: {e}"


def extract_uri_check_targets(decoded_text):
    """
    从 URI 格式的订阅内容中提取用于存活检测的目标。

    对于 Cloudflare Workers/Pages 反代型 vless/ss/trojan，
    真正的入口在 sni/host 参数中，而非 @server 部分。

    返回 [(check_target, check_port, description), ...]
    """
    targets = []
    seen = set()

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
                try:
                    obj = json.loads(base64.b64decode(b64_part).decode('utf-8', errors='ignore'))
                except Exception:
                    continue
                # vmess 的 server 就是真实地址，没有 sni 概念
                s = str(obj.get('add', '')).strip()
                p = obj.get('port', 443)
                if s and p and s != '127.0.0.1':
                    key = (s, int(p))
                    if key not in seen:
                        seen.add(key)
                        targets.append((s, int(p), f"vmess direct: {s}"))
                continue

            # 非 vmess：从 URI 中解析参数
            after_scheme = uri.split('://', 1)[1]

            # 提取 @ 前面的认证部分和 @ 后面的 host:port
            at_server = None
            at_port = 443
            if '@' in after_scheme:
                _, host_part = after_scheme.split('@', 1)
                host_part = host_part.split('?')[0].split('#')[0]
                if host_part.startswith('['):
                    bracket_end = host_part.find(']')
                    if bracket_end != -1:
                        at_server = host_part[1:bracket_end]
                        rest = host_part[bracket_end + 1:]
                        if ':' in rest:
                            try:
                                at_port = int(rest.split(':')[1])
                            except:
                                pass
                elif ':' in host_part:
                    parts = host_part.rsplit(':', 1)
                    at_server = parts[0].strip('[]')
                    try:
                        at_port = int(parts[1])
                    except:
                        pass

            # 提取查询参数
            query_string = ''
            if '?' in after_scheme:
                query_string = after_scheme.split('?', 1)[1].split('#')[0]

            params = parse_qs(query_string, keep_blank_values=True)

            # 关键：提取 sni 和 host 参数
            sni = (params.get('sni', [''])[0] or '').strip()
            host = (params.get('host', [''])[0] or '').strip()

            # 判断是否是 CF 反代型（server 是知名域名或伪装域名，真正入口在 sni/host）
            cf_keywords = [
                '.pages.dev', '.workers.dev', '.workers.dev',
                '.kmj.', '.qzz.', '.cf.', 'cdn.', 'cloudflare',
                '.090227.xyz', '.7zz.cn', '.js.cool',
            ]

            is_cf_proxy = False
            real_endpoint = None

            # 优先用 sni 作为真实入口
            for candidate in [sni, host]:
                if candidate and any(k in candidate.lower() for k in cf_keywords):
                    is_cf_proxy = True
                    real_endpoint = candidate
                    break

            if is_cf_proxy and real_endpoint:
                # CF 反代型：检测 sni/host 指向的 CF 入口
                key = (real_endpoint, 443)
                if key not in seen:
                    seen.add(key)
                    targets.append((real_endpoint, 443, f"CF endpoint: {real_endpoint}"))
            elif at_server:
                # 普通直连型：检测 @server
                # 过滤无效地址
                skip = ['127.0.0.1', 'localhost', 'example.com']
                if not any(x in at_server.lower() for x in skip):
                    key = (at_server, at_port)
                    if key not in seen:
                        seen.add(key)
                        targets.append((at_server, at_port, f"direct: {at_server}:{at_port}"))

        except Exception:
            continue

    return targets


def extract_server_port_pairs(decoded_text):
    """
    统一提取函数：同时支持 YAML 格式 和 URI 格式
    用于最终写入配置时的 server:port 提取（与检测不同）
    返回 [(server, port), ...] 列表
    """
    pairs = []

    # 1) 尝试 YAML 格式 (Clash / Mihomo)
    servers = re.findall(r'server:\s*["\']?([^\s\'",\]]+)', decoded_text)
    ports = re.findall(r'port:\s*["\']?(\d+)', decoded_text)

    for s, p in zip(servers, ports):
        s_clean = s.strip("'\" ,\r\n\t").split(',')[0]
        try:
            pairs.append((s_clean, int(p)))
        except ValueError:
            continue

    if len(pairs) >= 3:
        return pairs

    # 2) URI 格式
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

            after_scheme = uri.split('://', 1)[1]

            if '@' in after_scheme:
                host_part = after_scheme.split('@', 1)[1]
            else:
                b64_part = after_scheme.split('#')[0].split('?')[0]
                missing = len(b64_part) % 4
                if missing:
                    b64_part += '=' * (4 - missing)
                try:
                    decoded_ss = base64.b64decode(b64_part).decode('utf-8', errors='ignore')
                    host_part = decoded_ss.split('@', 1)[1] if '@' in decoded_ss else decoded_ss
                except Exception:
                    continue

            host_part = host_part.split('?')[0].split('#')[0].strip()
            if not host_part:
                continue

            if host_part.startswith('['):
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
    """
    检测订阅链接是否存活。

    策略：
    1. 下载并解析订阅内容
    2. 区分 CF 反代型和直连型：
       - CF 反代型：用 HTTPS 请求 sni/host 入口判断 Worker 是否存活
       - 直连型：用 TCP 探测 @server:port
    3. 至少 2 个样本通过才判活
    """
    try:
        req = urllib.request.Request(link, headers={'User-Agent': 'Mihomo'})
        with urllib.request.urlopen(req, context=ssl_context, timeout=8) as res:
            raw_data = res.read().decode('utf-8', errors='ignore').strip()

        if not raw_data:
            print("   ⚠️ 订阅返回内容为空。")
            return False

        decoded_text = raw_data

        # 尝试 Base64 解码
        if re.match(r'^[a-zA-Z0-9+/=\s]+$', raw_data) and len(raw_data) > 30:
            try:
                b64_input = raw_data
                missing_padding = len(b64_input) % 4
                if missing_padding:
                    b64_input += '=' * (4 - missing_padding)
                decoded_text = base64.b64decode(b64_input).decode('utf-8', errors='ignore')
                print("   🔓 成功识别并完成 Base64 本地解密。")
            except Exception:
                pass

        # 提取检测目标
        targets = extract_uri_check_targets(decoded_text)

        if not targets:
            # 回退到 YAML 提取
            pairs = extract_server_port_pairs(decoded_text)
            invalid = ["127.0.0.1", "localhost", "网址", "官网", "频道", "公告", "example.com"]
            pairs = [(s, p) for s, p in pairs
                     if s and not any(x in s.lower() for x in invalid)]
            if not pairs:
                print("   ⚠️ 未能从订阅内容中提取到任何有效检测目标。")
                return False
            targets = [(s, p, f"YAML: {s}:{p}") for s, p in pairs]

        print(f"   📦 提取到 {len(targets)} 个检测目标，开始存活验证...")

        # 去重 + 随机抽样
        random.shuffle(targets)
        sample_size = min(5, len(targets))
        sample = targets[:sample_size]

        success_count = 0
        needed = 2

        for target, port, desc in sample:
            # 判断是否是 CF 入口（域名含 .pages.dev / .workers.dev / 已知 CF 前缀）
            is_cf = any(k in target.lower() for k in
                        ['.pages.dev', '.workers.dev', '.qzz.', '.kmj.'])

            if is_cf:
                # 对 CF 入口做 HTTPS 请求，判断 Worker/Page 是否存活
                print(f"      🔍 [HTTPS] {desc} ... ", end="", flush=True)
                alive, code, reason = test_https_endpoint(target, timeout=6.0)
                if alive:
                    print(f"✅ ({reason})")
                    success_count += 1
                else:
                    print(f"❌ ({reason})")
            else:
                # 普通直连地址做 TCP 探测
                print(f"      🔍 [TCP] {desc} ... ", end="", flush=True)
                if test_tcp_port(target, port, timeout=3.0):
                    print("✅")
                    success_count += 1
                else:
                    print("❌")

            if success_count >= needed:
                print(f"      ✅ 达到阈值 ({success_count}/{needed})，该订阅判定为存活。")
                return True

        print(f"      ❌ 仅 {success_count}/{needed} 个目标可达，判定为失效。")
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
                    if current_node_lines:
                        extracted_lines.extend(current_node_lines)
                        node_count += 1
                    current_node_lines = [line]
                elif current_node_lines:
                    current_node_lines.append(line)

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

        # 1. 抓取 TG 页面机场链接
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

        cleaned_links = []
        for link in raw_links:
            clean = link.replace('&amp;', '&').split('<')[0].split('>')[0].strip()
            if clean not in cleaned_links:
                cleaned_links.append(clean)

        print(f"📦 共有 {len(cleaned_links)} 个不重复的原始链接。开始由新到旧进行本地筛选...\n")

        # 2. 依次筛选出可用的 [主] 和 [备] 两个不同的有效链接
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
                    break
            print()

        if not valid_main_link:
            print("⚠️ 提示：TG 页面上所有机场订阅已全军覆没！保持原有配置，今天暂不更新。")
            sys.exit(0)

        if not valid_backup_link:
            print("ℹ️ 提示：目前全频道仅筛出一个有效订阅，[备]链路将置为空。")
            valid_backup_link = ""

        # 3. 提取远端独立节点
        external_proxies_block = fetch_external_proxies(EXTERNAL_NODES_URL, ssl_context)

        # 4. 读取模板文件
        template_path = 'template.yaml'
        if not os.path.exists(template_path):
            print(f"❌ 错误：未在仓库中找到 {template_path} 模板文件！")
            sys.exit(1)

        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()

        # 5. 动态更新 [主] 链路 url
        main_pattern = r"(\b主\s*:\s*\{[^}]*url\s*:\s*['\"]?).*?(['\"]?\s*[,}])"
        modified_content = re.sub(main_pattern, f"\\1{valid_main_link}\\2", template_content)

        # 6. 动态更新 [备] 链路 url
        if valid_backup_link:
            backup_pattern = r"(\b备\s*:\s*\{[^}]*url\s*:\s*['\"]?).*?(['\"]?\s*[,}])"
            modified_content = re.sub(backup_pattern, f"\\1{valid_backup_link}\\2", modified_content)
        else:
            print("ℹ️ 备链路为空，将 [备] 的 url 清空。")
            backup_pattern = r"(\b备\s*:\s*\{[^}]*url\s*:\s*['\"]?).*?(['\"]?\s*[,}])"
            modified_content = re.sub(backup_pattern, r"\1\2", modified_content)

        # 7. 清洗 proxy-providers 中的 proxy 尾巴
        modified_content = re.sub(
            r"(?<=\{)[^}]*?,\s*proxy\s*:\s*[^,}]+",
            lambda m: m.group(0).split(',')[0],
            modified_content
        )
        modified_content = re.sub(r",\s*proxy\s*:\s*[^,}]+(?=\s*\})", "", modified_content)

        # 8. 写入远端节点
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

        # 9. 时间戳
        current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        final_yaml_content = f"# Generated & Checked at: {current_time}\n" + modified_content

        # 10. 保存
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
