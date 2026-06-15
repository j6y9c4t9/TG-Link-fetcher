import os
import re
import urllib.request
import urllib.error
import ssl
import socket
import base64
import random
import sys

# 从环境变量读取配置（可在 GitHub Actions Workflow 中设置）
channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')
URL = f"https://t.me/s/{channel_username}"

# 精准匹配包含 token= 的机场订阅网址
SUBSCRIBE_REGEX = r'https?://[^\s"\'<>]+token=[a-zA-Z0-9]+'

# 从 GitHub Secret 中读取您配置的专属隐私备用链接
BACKUP_LINK_FROM_SECRET = os.environ.get('BACKUP_SUB_URL', '').strip()

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
    智能直连测活：兼容 Base64 与明文 YAML，清洗异常字符并进行节点 TCP 抽检
    """
    try:
        # 1. 伪装成现代 Mihomo 客户端请求原始订阅，促使其吐出对应配置
        req = urllib.request.Request(link, headers={'User-Agent': 'Mihomo'})
        with urllib.request.urlopen(req, context=ssl_context, timeout=8) as res:
            raw_data = res.read().decode('utf-8', errors='ignore').strip()

        if not raw_data:
            return False

        # 2. 智能识别并尝试 Base64 本地解码
        decoded_text = raw_data
        if re.match(r'^[a-zA-Z0-9+/=\s]+$', raw_data) and len(raw_data) > 30:
            try:
                missing_padding = len(raw_data) % 4
                if missing_padding:
                    raw_data += '=' * (4 - missing_padding)
                decoded_text = base64.b64decode(raw_data).decode('utf-8', errors='ignore')
                print("   🔓 成功识别并完成 Base64 本地解密。")
            except Exception:
                pass # 解码失败则作为普通文本继续处理

        # 3. 双正则混合提取服务器和端口
        servers = re.findall(r'server:\s*([^\s\'",]+)', decoded_text)
        ports = re.findall(r'port:\s*(\d+)', decoded_text)
        
        # 如果不是标准 YAML，采用第二套通用的 [域名/IP]:[端口] 正则提取
        if not servers:
            ip_port_pairs = re.findall(r'([a-zA-Z0-9][-a-zA-Z0-9]{0,62}(?:\.[a-zA-Z0-9][-a-zA-Z0-9]{0,62})+):(\d+)', decoded_text)
            servers = [item[0] for item in ip_port_pairs]
            ports = [item[1] for item in ip_port_pairs]

        if not servers or not ports:
            print("   ❌ 解析失败：未能从下载的数据中提取到任何节点服务器和端口。")
            return False

        # 4. 彻底清洗提取出来的域名和端口（干掉末尾粘连的逗号、引号、空格）
        valid_pairs = []
        for s, p in zip(servers, ports):
            s_clean = s.strip("'\" ,\r\n\t").split(',')[0].split('"')[0].split("'")[0]
            try:
                p_clean = int(p)
            except:
                continue
            
            # 过滤掉机场内置的广告、官网提示、本地回环等干扰伪节点
            if s_clean and p_clean:
                if not any(x in s_clean.lower() for x in ["127.0.0.1", "localhost", "github", "google", "网址", "官网", "频道", "公告"]):
                    valid_pairs.append((s_clean, p_clean))

        if not valid_pairs:
            print("   ❌ 过滤干扰项后，该订阅内无可用的有效代理节点。")
            return False

        print(f"   📦 成功清洗出 {len(valid_pairs)} 个节点，开始抽检连通性...")

        # 5. 随机抽取最多 3 个节点进行真实的 TCP 握手探测
        sample_size = min(3, len(valid_pairs))
        sample_pairs = random.sample(valid_pairs, sample_size)
        
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
        print(f"   ❌ 机场服务器拒绝请求 (HTTP {e.code})，链接或 Token 可能已被封禁。")
        return False
    except Exception as e:
        print(f"   ❌ 请求机场链接超时或失败: {e}")
        return False

def main():
    try:
        ssl_context = ssl._create_unverified_context()

        # 1. 请求 Telegram 网页端页面
        req = urllib.request.Request(
            URL, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, context=ssl_context) as response:
            html_content = response.read().decode('utf-8')
            
        # 2. 抓取所有订阅链接并去重清洗
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

        # 3. 🎯 由新到旧遍历：只找出一个最新、最健康的链接填入“主”
        valid_link = None
        for i, link in enumerate(reversed(cleaned_links)):
            print(f"🔄 [{i+1}/{len(cleaned_links)}] 正在检测: {link}")
            if is_subscription_alive(link, ssl_context):
                valid_link = link
                print(f"🎉 成功锁定最新可用的[主]链接: {valid_link}")
                break
            else:
                print("⚠️ 该链接判定不可用，自动向上寻找上一个备份...")

        if not valid_link:
            # 如果今天频道的链接全挂了，优雅退出，不让 GitHub Actions 报错变红
            print("⚠️ 提示：TG 页面上所有机场订阅经测活后全军覆没！保持原有配置，今天暂不更新。")
            sys.exit(0)

        # 4. 读取本地模板文件
        template_path = 'template.yaml'
        if not os.path.exists(template_path):
            print(f"❌ 错误：未在仓库中找到 {template_path} 模板文件！")
            sys.exit(1)

        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()

        # 5. 动态替换[主]链接，并注入代理更新机制，防止客户端直连触发 EOF
        modified_content = re.sub(
            r"(主:\s*\{[^}]*url:\s*['\"]).*?(['\"])", 
            f"\\1{valid_link}\\2, proxy: 故障转移", 
            template_content
        )

        # 6. 动态替换[备]链接为 Secret 中的隐私内容。如果 Secret 为空则保持模板原样
        if BACKUP_LINK_FROM_SECRET:
            print("🔒 成功读取 GitHub Secret 备用链接，正在注入配置...")
            modified_content = re.sub(
                r"(备:\s*\{[^}]*url:\s*['\"]).*?(['\"])", 
                f"\\1{BACKUP_LINK_FROM_SECRET}\\2, proxy: 故障转移", 
                modified_content
            )
        else:
            print("⚠️ 提示：未在环境变量中检测到 BACKUP_SUB_URL，[备] 链接将保持模板默认值。")

        # 7. 写入最终生成的配置文件
        with open('config.yaml', 'w', encoding='utf-8') as f:
            f.write(modified_content)
            
        print("🎉 [完美收工] 薅羊毛的[主]链接与私人的[备]链接已完美各就各位！")
        sys.exit(0)
                
    except Exception as e:
        print(f"❌ 运行崩溃: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
