import os
import re
import urllib.request
import urllib.error
import ssl
import socket
import base64
import random
import sys

channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')
URL = f"https://t.me/s/{channel_username}"
SUBSCRIBE_REGEX = r'https?://[^\s"\'<>]+token=[a-zA-Z0-9]+'

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
        
        if not servers:
            ip_port_pairs = re.findall(r'([a-zA-Z0-9][-a-zA-Z0-9]{0,62}(?:\.[a-zA-Z0-9][-a-zA-Z0-9]{0,62})+):(\d+)', decoded_text)
            servers = [item[0] for item in ip_port_pairs]
            ports = [item[1] for item in ip_port_pairs]

        if not servers or not ports:
            print("   ❌ 解析失败：未能从数据中提取到任何节点。")
            return False

        valid_pairs = []
        for s, p in zip(servers, ports):
            s_clean = s.strip("'\" ,\r\n\t").split(',')[0].split('"')[0].split("'")[0]
            try:
                p_clean = int(p)
            except:
                continue
            if s_clean and p_clean:
                if not any(x in s_clean.lower() for x in ["127.0.0.1", "localhost", "github", "google", "网址", "官网", "频道"]):
                    valid_pairs.append((s_clean, p_clean))

        if not valid_pairs:
            print("   ❌ 过滤后，该订阅内无任何有效代理节点。")
            return False

        print(f"   📦 成功清洗出 {len(valid_pairs)} 个有效节点，开始抽检连通性...")

        # 安全抽取，防止长度为 0 报错
        sample_size = min(3, len(valid_pairs))
        sample_pairs = random.sample(valid_pairs, sample_size)
        
        for srv, prt in sample_pairs:
            print(f"      ⚡ 正在检测节点: {srv}:{prt} ...")
            if test_tcp_port(srv, prt, timeout=2.5):
                print("      ✅ 连通成功！该机场生还，判定可用。")
                return True
            else:
                print("      ❌ 超时无响应")

        print("   ❌ 抽检节点全部超时！该机场目前全员处于断连状态。")
        return False

    except Exception as e:
        print(f"   ❌ 请求或解析失败: {e}")
        return False

def main():
    try:
        ssl_context = ssl._create_unverified_context()

        req = urllib.request.Request(
            URL, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, context=ssl_context) as response:
            html_content = response.read().decode('utf-8')
            
        raw_links = re.findall(SUBSCRIBE_REGEX, html_content, re.IGNORECASE)
        if not raw_links:
            print("ℹ️ 未在 TG 页面发现任何订阅链接。")
            sys.exit(0)

        cleaned_links = []
        for link in raw_links:
            clean = link.replace('&amp;', '&').split('<')[0].split('>')[0].strip()
            if clean not in cleaned_links:
                cleaned_links.append(clean)

        print(f"📦 共有 {len(cleaned_links)} 个不重复的原始链接。开始由新到旧进行本地解密测活...")

        valid_link = None
        for i, link in enumerate(reversed(cleaned_links)):
            print(f"🔄 [{i+1}/{len(cleaned_links)}] 正在检测: {link}")
            if is_subscription_alive(link, ssl_context):
                valid_link = link
                print(f"🎉 终极测活成功！锁定可用链接: {valid_link}")
                break
            else:
                print("⚠️ 该链接判定不可用，自动向上寻找上一个备份...")

        if not valid_link:
            # 🎯 核心改进：即使全挂了，也优雅地以 exit code 0 退出，不让工作流变红
            print("⚠️ 提示：TG 页面上所有机场订阅已全军覆没！保持原有配置，今天暂不更新。")
            sys.exit(0)

        template_path = 'template.yaml'
        if not os.path.exists(template_path):
            print(f"❌ 错误：未在仓库中找到 {template_path} 模板文件！")
            sys.exit(1)

        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()

        modified_content = re.sub(
            r"(主:\s*\{[^}]*url:\s*['\"]).*?(['\"])", 
            f"\\1{valid_link}\\2", 
            template_content
        )

        with open('config.yaml', 'w', encoding='utf-8') as f:
            f.write(modified_content)
            
        print("🎉 [完美收工] 真正鲜活、有节点的机场订阅已成功更新至 config.yaml。")
        sys.exit(0)
                
    except Exception as e:
        print(f"❌ 运行崩溃: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
