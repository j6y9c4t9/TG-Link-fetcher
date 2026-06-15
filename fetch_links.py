import os
import re
import urllib.request
import urllib.error
import ssl
import socket
import base64
import random
import sys
import datetime

# 配置部分
channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')
URL = f"https://t.me/s/{channel_username}"
SUBSCRIBE_REGEX = r'https?://[^\s"\'<>]+token=[a-zA-Z0-9]+'

EXTERNAL_NODES_URL = "https://raw.githubusercontent.com/shaoyouvip/free/refs/heads/main/all.yaml"

def test_tcp_port(server, port, timeout=2.5):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((server, int(port)))
        sock.close()
        return True
    except Exception:
        return False

def is_subscription_alive(link, ssl_context):
    try:
        req = urllib.request.Request(link, headers={'User-Agent': 'Mihomo'})
        with urllib.request.urlopen(req, context=ssl_context, timeout=8) as res:
            raw_data = res.read().decode('utf-8', errors='ignore').strip()

        if not raw_data: return False
        decoded_text = raw_data
        if re.match(r'^[a-zA-Z0-9+/=\s]+$', raw_data) and len(raw_data) > 30:
            try:
                missing_padding = len(raw_data) % 4
                if missing_padding: raw_data += '=' * (4 - missing_padding)
                decoded_text = base64.b64decode(raw_data).decode('utf-8', errors='ignore')
            except Exception: pass

        servers = re.findall(r'server:\s*([^\s\'",]+)', decoded_text)
        ports = re.findall(r'port:\s*(\d+)', decoded_text)
        
        if not servers or not ports: return False
        valid_pairs = []
        for s, p in zip(servers, ports):
            s_clean = s.strip("'\" ,\r\n\t").split(',')[0].split('"')[0].split("'")[0]
            try: p_clean = int(p)
            except: continue
            if s_clean and p_clean:
                if not any(x in s_clean.lower() for x in ["127.0.0.1", "localhost", "github", "google"]):
                    valid_pairs.append((s_clean, p_clean))

        if not valid_pairs: return False
        sample_pairs = random.sample(valid_pairs, min(3, len(valid_pairs)))
        for srv, prt in sample_pairs:
            if test_tcp_port(srv, prt, timeout=2.5): return True
        return False
    except Exception: return False

def fetch_external_proxies(url, ssl_context):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mihomo'})
        with urllib.request.urlopen(req, context=ssl_context, timeout=10) as res:
            content = res.read().decode('utf-8', errors='ignore')
        
        lines = content.split('\n')
        extracted_lines = []
        in_proxies_section = False
        current_node_lines = []
        
        for line in lines:
            if line.startswith('proxies:'):
                in_proxies_section = True
                continue
            if in_proxies_section:
                if line.strip() and not line.startswith(' ') and not line.startswith('-'): break
                if line.lstrip().startswith('-'):
                    if current_node_lines: extracted_lines.extend(current_node_lines)
                    current_node_lines = [line]
                elif line.startswith(' ') and current_node_lines: current_node_lines.append(line)
        if current_node_lines: extracted_lines.extend(current_node_lines)
        return '\n'.join(extracted_lines).rstrip()
    except Exception: return ""

def main():
    try:
        ssl_context = ssl._create_unverified_context()
        req = urllib.request.Request(URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, context=ssl_context) as response:
            html_content = response.read().decode('utf-8')
            
        raw_links = re.findall(SUBSCRIBE_REGEX, html_content, re.IGNORECASE)
        cleaned_links = list(dict.fromkeys([l.split('<')[0].split('>')[0].strip() for l in raw_links]))

        print(f"📦 共有 {len(cleaned_links)} 个不重复链接。")
        valid_main_link = None
        valid_backup_link = None
        
        for i, link in enumerate(reversed(cleaned_links)):
            print(f"🔄 [{i+1}/{len(cleaned_links)}] 正在检测: {link}")
            if is_subscription_alive(link, ssl_context):
                if not valid_main_link:
                    valid_main_link = link
                    print(f"🎉 [锁定主链]: {valid_main_link}")
                elif not valid_backup_link and link != valid_main_link:
                    valid_backup_link = link
                    print(f"🎉 [锁定备链]: {valid_backup_link}")
                    break

        if not valid_main_link:
            print("❌ 没有任何有效链接，退出。")
            sys.exit(1)

        # 🎯 修改点：留空处理
        if not valid_backup_link:
            print("ℹ️ 提示：目前全频道仅筛出一个有效订阅，[备]链路将留空。")
            valid_backup_link = ""

        external_proxies_block = fetch_external_proxies(EXTERNAL_NODES_URL, ssl_context)

        with open('template.yaml', 'r', encoding='utf-8') as f:
            template_content = f.read()

        main_pattern = r"(\b主\s*:\s*\{[^}]*url\s*:\s*['\"]?).*?(['\"]?\s*[,}])"
        modified_content = re.sub(main_pattern, f"\\1{valid_main_link}\\2", template_content)
        
        backup_pattern = r"(\b备\s*:\s*\{[^}]*url\s*:\s*['\"]?).*?(['\"]?\s*[,}])"
        modified_content = re.sub(backup_pattern, f"\\1{valid_backup_link}\\2", modified_content)

        modified_content = re.sub(r",\s*proxy\s*:\s*[^,}]+(?=\s*\})", "", modified_content)

        if external_proxies_block:
            modified_content = modified_content.replace("proxies:\n", f"proxies:\n{external_proxies_block}\n")

        with open('config.yaml', 'w', encoding='utf-8') as f:
            f.write(f"# Generated at: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n" + modified_content)
        print("🎉 [完美收工] 更新完成！")
        sys.exit(0)
    except Exception as e:
        print(f"❌ 运行崩溃: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()            return False

        decoded_text = raw_data
        if re.match(r'^[a-zA-Z0-9+/=\s]+$', raw_data) and len(raw_data) > 30:
            try:
                missing_padding = len(raw_data) % 4
                if missing_padding:
                    raw_data += '=' * (4 - missing_padding)
                decoded_text = base64.b64decode(raw_data).decode('utf-8', errors='ignore')
                print("   🔓 成功识别并完成 Base64 本地解密。")
            except Exception:
                pass

        servers = re.findall(r'server:\s*([^\s\'",]+)', decoded_text)
        ports = re.findall(r'port:\s*(\d+)', decoded_text)
        
        if not servers or not ports:
            return False

        valid_pairs = []
        for s, p in zip(servers, ports):
            s_clean = s.strip("'\" ,\r\n\t").split(',')[0].split('"')[0].split("'")[0]
            try:
                p_clean = int(p)
            except:
                continue
            if s_clean and p_clean:
                if not any(x in s_clean.lower() for x in ["127.0.0.1", "localhost", "github", "google", "网址", "官网", "频道", "公告"]):
                    valid_pairs.append((s_clean, p_clean))

        if not valid_pairs:
            return False

        print(f"   📦 成功清洗出 {len(valid_pairs)} 个节点，开始抽检连通性...")
        sample_size = min(3, len(valid_pairs))
        sample_pairs = random.sample(valid_pairs, sample_size)
        
        for srv, prt in sample_pairs:
            if test_tcp_port(srv, prt, timeout=2.5):
                print("      ✅ 连通成功！该机场处于存活状态。")
                return True
        return False
    except Exception:
        return False

def fetch_external_proxies(url, ssl_context):
    print(f"🌐 正在从远端源提取独立节点: {url}")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mihomo'})
        with urllib.request.urlopen(req, context=ssl_context, timeout=10) as res:
            content = res.read().decode('utf-8', errors='ignore')
        
        lines = content.split('\n')
        extracted_lines = []
        
        in_proxies_section = False
        current_node_lines = []
        node_count = 0

        for line in lines:
            if line.startswith('proxies:'):
                in_proxies_section = True
                continue
            
            if in_proxies_section:
                if line.strip() and not line.startswith(' ') and not line.startswith('-'):
                    break
                
                if line.lstrip().startswith('-'):
                    if current_node_lines:
                        extracted_lines.extend(current_node_lines)
                        node_count += 1
                    current_node_lines = [line]
                elif line.startswith(' ') and current_node_lines:
                    current_node_lines.append(line)
                elif not line.strip() and current_node_lines:
                    current_node_lines.append(line)

        if current_node_lines:
            extracted_lines.extend(current_node_lines)
            node_count += 1
            
        if extracted_lines:
            print(f"🎉 [大获成功] 状态机完美剥离出 {node_count} 个多行高级代理节点！")
            return '\n'.join(extracted_lines).rstrip()
            
        print("⚠️ 未能从远端 YAML 中发现标准的 proxies 节点列表。")
        return ""
    except Exception as e:
        print(f"❌ 抓取远端节点失败: {e}")
        return ""

def main():
    try:
        ssl_context = ssl._create_unverified_context()

        # 1. 抓取 TG 页面机场链接
        req = urllib.request.Request(
            URL, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, context=ssl_context) as response:
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

        print(f"📦 共有 {len(cleaned_links)} 个不重复的原始链接。开始由新到旧进行本地筛选...")

        # 2. 依次筛选出可用的 [主] 和 [备] 两个不同的有效链接
        valid_main_link = None
        valid_backup_link = None
        
        for i, link in enumerate(reversed(cleaned_links)):
            print(f"🔄 [{i+1}/{len(cleaned_links)}] 正在检测: {link}")
            if is_subscription_alive(link, ssl_context):
                if not valid_main_link:
                    valid_main_link = link
                    print(f"🎉 [成功锁定主链] 最新可用的[主]链接: {valid_main_link}")
                elif not valid_backup_link and link != valid_main_link:
                    valid_backup_link = link
                    print(f"🎉 [成功锁定备链] 次新可用的[备]链接: {valid_backup_link}")
                    break  # 找齐了主备两个有效链接，提前收工

        if not valid_main_link:
            print("⚠️ 提示：TG 页面上所有机场订阅已全军覆没！保持原有配置，今天暂不更新。")
            sys.exit(0)

        # 兜底逻辑：如果 TG 频道里只洗出了一个有效的活机场，那就让备链路复制主链路的值
        if not valid_backup_link:
            print("ℹ️ 提示：目前全频道仅筛出一个有效订阅，[备]链路将复制[主]链路进行填充。")
            valid_backup_link = valid_main_link

        # 3. 提取远端独立节点
        external_proxies_block = fetch_external_proxies(EXTERNAL_NODES_URL, ssl_context)

        # 4. 读取模板文件
        template_path = 'template.yaml'
        if not os.path.exists(template_path):
            print(f"❌ 错误：未在仓库中找到 {template_path} 模板文件！")
            sys.exit(1)

        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()

        # 5. 🎯 动态更新 [主] 链路 url
        main_pattern = r"(\b主\s*:\s*\{[^}]*url\s*:\s*['\"]?).*?(['\"]?\s*[,}])"
        modified_content = re.sub(main_pattern, f"\\1{valid_main_link}\\2", template_content)

        # 6. 🎯 动态更新 [备] 链路 url
        backup_pattern = r"(\b备\s*:\s*\{[^}]*url\s*:\s*['\"]?).*?(['\"]?\s*[,}])"
        modified_content = re.sub(backup_pattern, f"\\1{valid_backup_link}\\2", modified_content)

        # 7. 🎯 彻底清洗：确保整个配置文件的 proxy-providers 中不存在任何 proxy 代理尾巴
        modified_content = re.sub(r"(?<=\{)[^}]*?,\s*proxy\s*:\s*[^,}]+", lambda m: m.group(0).split(',')[0], modified_content)
        modified_content = re.sub(r",\s*proxy\s*:\s*[^,}]+(?=\s*\})", "", modified_content)

        # 8. 安全写入远端多行复合节点到 config.yaml 的 proxies 中
        if external_proxies_block:
            print("📝 正在安全写入多行复合节点到 config.yaml 的 proxies 中...")
            target_placeholder = "proxies:\n"
            if target_placeholder in modified_content:
                modified_content = modified_content.replace(target_placeholder, f"proxies:\n{external_proxies_block}\n")
            else:
                print("⚠️ 警告：模板中未发现顶格的 proxies: 标记，尝试强行追加。")
                modified_content += f"\nproxies:\n{external_proxies_block}\n"
        else:
            print("⚠️ 提示：由于未提取到有效的远端节点，proxies 块保持模板默认状态。")

        # 9. 加入防无变动提交的时间戳
        current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        final_yaml_content = f"# Generated & Checked at: {current_time}\n" + modified_content

        # 10. 保存最终配置
        with open('config.yaml', 'w', encoding='utf-8') as f:
            f.write(final_yaml_content)
            
        print("🎉 [完美收工] 主备动态双链路、无 proxy 直连化更新成功！")
        sys.exit(0)
                
    except Exception as e:
        print(f"❌ 运行崩溃: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
