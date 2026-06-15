import os
import re
import urllib.request
import urllib.error
import ssl
import socket
import random

channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')
URL = f"https://t.me/s/{channel_username}"

# 精准匹配包含 token= 的机场订阅网址
SUBSCRIBE_REGEX = r'https?://[^\s"\'<>]+token=[a-zA-Z0-9]+'

def test_tcp_port(server, port, timeout=2.5):
    """测试单个节点的服务器端口是否通畅"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((server, int(port)))
        sock.close()
        return True
    except Exception:
        return False

def is_subscription_alive(link, ssl_context):
    """
    通过伪装 Mihomo 客户端直接请求原始订阅，并抽检节点连通性
    """
    try:
        # 🎯 核心改进：换成 Mihomo 的专属 User-Agent，解锁全部新型协议节点
        req = urllib.request.Request(
            link, 
            headers={'User-Agent': 'Mihomo'}
        )
        with urllib.request.urlopen(req, context=ssl_context, timeout=8) as res:
            decoded_text = res.read().decode('utf-8', errors='ignore')

        # 强力提取解密文本中的所有服务器地址和端口
        # 兼容各种格式 (vmess://, ss://, 或者 yaml 中的 server: xxx, port: xxx)
        servers = re.findall(r'server:\s*([^\s\'"]+)', decoded_text)
        ports = re.findall(r'port:\s*(\d+)', decoded_text)
        
        # 如果不是标准 YAML 格式，尝试匹配通用的 域名:端口 格式
        if not servers:
            ip_port_pairs = re.findall(r'([a-zA-Z0-9][-a-zA-Z0-9]{0,62}(?:\.[a-zA-Z0-9][-a-zA-Z0-9]{0,62})+):(\d+)', decoded_text)
            servers = [item[0] for item in ip_port_pairs]
            ports = [item[1] for item in ip_port_pairs]

        if not servers or not ports:
            print("   ❌ 解析失败：成功下载了数据，但无法从中提取到任何节点服务器和端口。")
            return False

        # 过滤广告、提示等干扰项
        valid_pairs = []
        for s, p in zip(servers, ports):
            s_clean = s.strip("'\" ")
            if not any(x in s_clean for x in ["127.0.0.1", "localhost", "github", "google", "网址", "官网"]):
                valid_pairs.append((s_clean, p))

        if not valid_pairs:
            print("   ❌ 该订阅内无可用的有效代理节点。")
            return False

        print(f"   📦 成功解析出 {len(valid_pairs)} 个节点，开始抽检连通性...")

        # 随机抽取最多 3 个节点进行 TCP 探测
        sample_pairs = random.sample(valid_pairs, min(3, len(valid_pairs)))
        
        for srv, prt in sample_pairs:
            print(f"      ⚡ 正在检测节点: {srv}:{prt} ...")
            if test_tcp_port(srv, prt, timeout=2.5):
                print("      ✅ 连通成功！该机场处于存活状态。")
                return True
            else:
                print("      ❌ 超时无响应")

        print("   ❌ 抽检节点全部超时！该机场目前全员断连。")
        return False

    except urllib.error.HTTPError as e:
        print(f"   ❌ 机场服务器拒绝请求 (HTTP {e.code})，可能该 Token 订阅已被机场封禁。")
        return False
    except Exception as e:
        print(f"   ❌ 请求机场链接超时或失败: {e}")
        return False

def main():
    try:
        ssl_context = ssl._create_unverified_context()

        # 1. 请求 Telegram 页面
        req = urllib.request.Request(
            URL, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, context=ssl_context) as response:
            html_content = response.read().decode('utf-8')
            
        # 2. 抓取所有订阅链接并清洗
        raw_links = re.findall(SUBSCRIBE_REGEX, html_content, re.IGNORECASE)
        if not raw_links:
            print("ℹ️ 未在 TG 页面发现任何订阅链接。")
            return

        cleaned_links = []
        for link in raw_links:
            clean = link.replace('&amp;', '&').split('<')[0].split('>')[0].strip()
            if clean not in cleaned_links:
                cleaned_links.append(clean)

        print(f"📦 共有 {len(cleaned_links)} 个不重复的原始链接。开始由新到旧进行本地解密测活...")

        # 3. 🎯 由新到旧遍历测活
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
            print("❌ 灾难提示：TG 页面上所有机场订阅经本地直连测活后，已全军覆没！保持原有配置不变。")
            return

        # 4. 读取本地模板并替换
        template_path = 'template.yaml'
        if not os.path.exists(template_path):
            print(f"❌ 错误：未在仓库中找到 {template_path} 模板文件！")
            return

        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()

        modified_content = re.sub(
            r"(主:\s*\{[^}]*url:\s*['\"]).*?(['\"])", 
            f"\\1{valid_link}\\2", 
            template_content
        )

        # 5. 写入最终配置
        with open('config.yaml', 'w', encoding='utf-8') as f:
            f.write(modified_content)
            
        print("🎉 过滤完毕！真正有活节点的机场订阅已成功更新至 config.yaml。")
                
    except Exception as e:
        print(f"❌ 运行崩溃: {e}")

if __name__ == '__main__':
    main()
