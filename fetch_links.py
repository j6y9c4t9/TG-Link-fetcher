import os
import re
import urllib.request
import urllib.parse
import ssl
import socket
import random

channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')
URL = f"https://t.me/s/{channel_username}"

# 精准匹配包含 token= 的机场订阅网址
SUBSCRIBE_REGEX = r'https?://[^\s"\'<>]+token=[a-zA-Z0-9]+'
# 使用极其稳定的纯 API 后端来临时解析节点列表
SUB_CONVERTER_API = "https://api.v1.mk/sub?"

def test_tcp_port(server, port, timeout=3):
    """测试单个节点的服务器端口是否通畅"""
    try:
        # 如果是域名，自动解析；如果是 IP 直接连接
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((server, int(port)))
        sock.close()
        return True
    except Exception:
        return False

def is_subscription_alive(link, ssl_context):
    """
    不仅下载订阅，还抽检里面的节点是否真的能连通
    """
    try:
        # 1. 调用转换接口尝试把该链接转成包含节点明文的 YAML
        params = {"target": "clash", "url": link, "insert": "false"}
        convert_url = SUB_CONVERTER_API + urllib.parse.urlencode(params)
        
        req = urllib.request.Request(convert_url, headers={'User-Agent': 'clash'})
        with urllib.request.urlopen(req, context=ssl_context, timeout=8) as res:
            yaml_content = res.read().decode('utf-8', errors='ignore')

        # 2. 暴力提取所有的 server 和 port
        # 匹配 Clash 节点格式中的 server: xxxx 和 port: xxxx
        servers = re.findall(r'server:\s*([^\s\'"]+)', yaml_content)
        ports = re.findall(r'port:\s*(\d+)', yaml_content)

        if not servers or not ports or len(servers) != len(ports):
            print("   ❌ 转换失败或该订阅内没有任何有效节点。")
            return False, None

        total_nodes = len(servers)
        print(f"   📦 成功解析出 {total_nodes} 个节点，开始抽检节点连通性...")

        # 3. 随机抽取最多 3 个节点进行 TCP 握手测试（防止某些机场前几个节点是垃圾提示节点）
        sample_indices = random.sample(range(total_nodes), min(3, total_nodes))
        
        success_count = 0
        for idx in sample_indices:
            srv = servers[idx].strip("'\" ")
            prt = ports[idx]
            # 过滤掉一些明显的机场公告伪节点（比如 server 是 127.0.0.1 或者 github.com 的）
            if "127.0.0.1" in srv or "localhost" in srv or "github" in srv:
                continue
                
            print(f"      ⚡ 正在测试测节点服务器: {srv}:{prt} ...")
            if test_tcp_port(srv, prt, timeout=2.5):
                success_count += 1
                print("      ✅ 连通成功！")
                break # 只要有一个节点活着的，就说明整个机场能用，立马通过
            else:
                print("      ❌ 超时无响应")

        if success_count > 0:
            return True, yaml_content
        else:
            print("   ❌ 抽检节点全部超时！该机场所有节点已瘫痪。")
            return False, None

    except Exception as e:
        print(f"   ❌ 订阅解析或网络请求失败: {e}")
        return False, None

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

        print(f"📦 共有 {len(cleaned_links)} 个不重复的原始链接。开始由新到旧进行“节点生死抽检”...")

        # 3. 🎯 核心：由新到旧遍历测活
        valid_link = None
        for i, link in enumerate(reversed(cleaned_links)):
            print(f"🔄 [{i+1}/{len(cleaned_links)}] 正在检测: {link}")
            
            is_alive, _ = is_subscription_alive(link, ssl_context)
            if is_alive:
                valid_link = link
                print(f"🎉 终极测活成功！该机场节点真实可用。锁定链接: {valid_link}")
                break
            else:
                print("⚠️ 该链接判定不可用，自动向上寻找上一个备份...")

        if not valid_link:
            print("❌ 灾难提示：TG 页面上所有历史机场的节点已全部超时挂掉！保持原样。")
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
            
        print("🎉 过滤完毕！真正活着的机场订阅已成功融入 config.yaml。")
                
    except Exception as e:
        print(f"❌ 运行崩溃: {e}")

if __name__ == '__main__':
    main()
