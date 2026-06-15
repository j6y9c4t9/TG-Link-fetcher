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

# 固定的备用链接
BACKUP_LINK = "https://d.zrfme.com/vless-all"

# 需要提取节点的远程订阅源 URL
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

        if not raw_data:
            return False

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

        # 1. 抓取 TG 页面
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

        # 2. 筛选主链接
        valid_link = None
        for i, link in enumerate(reversed(cleaned_links)):
            print(f"🔄 [{i+1}/{len(cleaned_links)}] 正在检测: {link}")
            if is_subscription_alive(link, ssl_context):
                valid_link = link
                print(f"🎉 成功锁定最新可用的[主]链接: {valid_link}")
                break

        if not valid_link:
            print("⚠️ 提示：TG 页面上所有机场订阅已全军覆没！保持原有配置，今天暂不更新。")
            sys.exit(0)

        # 3. 运行新架构状态机提取外部独立节点
        external_proxies_block = fetch_external_proxies(EXTERNAL_NODES_URL, ssl_context)

        # 4. 读取模板
        template_path = 'template.yaml'
        if not os.path.exists(template_path):
            print(f"❌ 错误：未在仓库中找到 {template_path} 模板文件！")
            sys.exit(1)

        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()

        # 5. 精准正则替换 [主] 链路（直连拉取模式，无 proxy）
        main_pattern = r"(\b主\s*:\s*\{[^}]*url\s*:\s*['\"]?).*?(['\"]?\s*[,}])"
        modified_content = re.sub(main_pattern, f"\\1{valid_link}\\2", template_content)
        modified_content = re.sub(r"(\b主\s*:\s*\{[^}]*),?\s*proxy\s*:\s*[^,}]+", r"\1", modified_content)

        # 6. 精准正则替换 [备] 链路并注入固定链接与代理更新机制
        backup_pattern = r"(\b备\s*:\s*\{[^}]*url\s*:\s*['\"]?).*?(['\"]?\s*[,}])"
        modified_content = re.sub(backup_pattern, f"\\1{BACKUP_LINK}\\2", modified_content)
        if "proxy: 故障转移" not in modified_content:
            modified_content = re.sub(r"(\b备\s*:\s*\{[^}]*url\s*:\s*['\"][^'\"]+['\"]\s*)", f"\\1, proxy: 故障转移", modified_content)

        # 7. 🎯 终极修复：使用纯字符串替代正则替换，完美免疫 Unicode Emoji (\U) 的报错
        if external_proxies_block:
            print("📝 正在安全写入多行复合节点到 config.yaml 的 proxies 中...")
            # 先定位模板中 proxies: 及随后的注释内容，然后用纯字符串的 replace 完美灌入
            target_placeholder = "proxies:\n"
            if target_placeholder in modified_content:
                # 先把旧的可能残留的空注释或旧占位抹平，换成干净的注入
                # 使用带换行的纯文本强塞
                modified_content = modified_content.replace(target_placeholder, f"proxies:\n{external_proxies_block}\n")
            else:
                print("⚠️ 警告：模板中未发现顶格的 proxies: 标记，尝试强行追加。")
                modified_content += f"\nproxies:\n{external_proxies_block}\n"
        else:
            print("⚠️ 提示：由于未提取到有效的远端节点，proxies 块保持模板默认状态。")

        # 8. 加入时间戳
        current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        final_yaml_content = f"# Generated & Checked at: {current_time}\n" + modified_content

        # 9. 保存最终配置
        with open('config.yaml', 'w', encoding='utf-8') as f:
            f.write(final_yaml_content)
            
        print("🎉 [完美收工] 突破 Unicode 限制，大合流配置已全部成功生成！")
        sys.exit(0)
                
    except Exception as e:
        print(f"❌ 运行崩溃: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
