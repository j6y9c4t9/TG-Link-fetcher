import os
import re
import urllib.request
import urllib.error
import ssl

channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')
URL = f"https://t.me/s/{channel_username}"

# 精准匹配包含 token= 的机场订阅网址
SUBSCRIBE_REGEX = r'https?://[^\s"\'<>]+token=[a-zA-Z0-9]+'

def is_link_available(link, ssl_context):
    """
    测试订阅链接是否可用，且检查流量是否充足
    """
    try:
        # 模拟 Clash 客户端去请求这个机场链接
        req = urllib.request.Request(
            link, 
            headers={'User-Agent': 'clash'}
        )
        # 设置 10 秒超时，防止死卡在某个挂掉的机场上
        with urllib.request.urlopen(req, context=ssl_context, timeout=10) as response:
            # 读取部分内容验证是不是真的配置，而不是错误网页
            content = response.read(1024).decode('utf-8', errors='ignore')
            
            # 检查 HTTP 响应头里的流量信息 (机场标准响应头)
            user_info = response.headers.get('subscription-userinfo')
            if user_info:
                print(f"   📊 发现流量标签: {user_info}")
                # 解析流量数据: upload, download, total
                data = dict(re.findall(r'(\w+)=(\d+)', user_info))
                if 'total' in data:
                    total = int(data['total'])
                    used = int(data.get('upload', 0)) + int(data.get('download', 0))
                    if used >= total and total > 0:
                        print("   ❌ 测试失败：该账号流量已耗尽 (100%)。")
                        return False

            # 如果能正常返回数据，且内容看起来像节点配置（通常包含 prox 或 vmess/ss 等特征）
            if response.status == 200 and len(content) > 10:
                return True
                
            return False
            
    except urllib.error.HTTPError as e:
        print(f"   ❌ HTTP 错误 (代码 {e.code})，链接可能已失效或被封禁。")
        return False
    except Exception as e:
        print(f"   ❌ 网络连接超时或失败: {e}")
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
            clean = link.replace('&amp;', '&')
            clean = re.split(r'[<>\s"\']', clean)[0]
            if clean not in cleaned_links:
                cleaned_links.append(clean)

        print(f"📦 网页内共解析出 {len(cleaned_links)} 个不重复的原始订阅链接。开始由新到旧“测活”...")

        # 3. 🎯 核心：从后往前（由新到旧）遍历测试
        valid_link = None
        for i, link in enumerate(reversed(cleaned_links)):
            print(f"🔄 [{i+1}/{len(cleaned_links)}] 正在测试节点: {link}")
            
            if is_link_available(link, ssl_context):
                valid_link = link
                print(f"🎉 测活成功！寻找到当前最新可用的活链接: {valid_link}")
                break
            else:
                print("⚠️ 尝试向上寻找上一个备份链接...")

        if not valid_link:
            print("❌ 灾难提示：爬取到的所有历史链接全部失效或流量耗尽！将保持原模板不变。")
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
            
        print("🎉 完美！可用节点已成功融入 config.yaml 文件。")
                
    except Exception as e:
        print(f"❌ 运行崩溃: {e}")

if __name__ == '__main__':
    main()
