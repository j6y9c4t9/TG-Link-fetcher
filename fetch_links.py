import os
import re
import urllib.request
import ssl

channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')
URL = f"https://t.me/s/{channel_username}"

# 精准匹配包含 token= 的机场订阅网址
SUBSCRIBE_REGEX = r'https?://[^\s"\'<>]+token=[a-zA-Z0-9]+'

def main():
    try:
        # 创建忽略 SSL 证书验证的上下文
        ssl_context = ssl._create_unverified_context()

        # 1. 请求 Telegram 页面
        req = urllib.request.Request(
            URL, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, context=ssl_context) as response:
            html_content = response.read().decode('utf-8')
            
        # 2. 抓取所有订阅链接
        raw_links = re.findall(SUBSCRIBE_REGEX, html_content, re.IGNORECASE)
        
        if not raw_links:
            print("ℹ️ 未发现任何订阅链接。")
            return

        # 3. 取出最新发布的那一个并清洗
        latest_link = raw_links[-1]
        latest_link = latest_link.replace('&amp;', '&')
        latest_link = re.split(r'[<>\s"\']', latest_link)[0]
        print(f"🔗 成功抓取最新订阅链接: {latest_link}")

        # 4. 读取本地的高级配置文件模板
        template_path = 'template.yaml'
        if not os.path.exists(template_path):
            print(f"❌ 错误：未在仓库中找到 {template_path} 模板文件！")
            return

        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()

        # 🎯 强力正则：精准锁定 “主:” 这一行中的 url: '...'
        # 无论单引号里面是空的还是有内容的，都能完美替换
        modified_content = re.sub(
            r"(主:\s*\{[^}]*url:\s*['\"]).*?(['\"])", 
            f"\\1{latest_link}\\2", 
            template_content
        )

        # 5. 将融合后的高级配置写入到 config.yaml
        with open('config.yaml', 'w', encoding='utf-8') as f:
            f.write(modified_content)
            
        print("🎉 新模板融合成功！已生成带有最新节点的 config.yaml 文件。")
                
    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == '__main__':
    main()
