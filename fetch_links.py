import os
import re
import urllib.request

channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')
URL = f"https://t.me/s/{channel_username}"

# 精准匹配包含 token= 的机场订阅网址
SUBSCRIBE_REGEX = r'https?://[^\s"\'<>]+token=[a-zA-Z0-9]+'

def main():
    try:
        req = urllib.request.Request(
            URL, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req) as response:
            html_content = response.read().decode('utf-8')
            
        # 1. 抓取所有订阅链接
        raw_links = re.findall(SUBSCRIBE_REGEX, html_content, re.IGNORECASE)
        
        if not raw_links:
            print("ℹ️ 未发现任何订阅链接。")
            return

        # 2. 取出最后的一个（即最新发布的那一个）
        latest_link = raw_links[-1]
        
        # 3. 清洗可能存在的 HTML 转义字符（比如把 &amp; 还原为 &）
        latest_link = latest_link.replace('&amp;', '&')
        latest_link = re.split(r'[<>\s"\']', latest_link)[0]

        # 4. 覆盖写入文件（不带任何多余的换行或注释，纯净的一行链接）
        with open('subscribe.txt', 'w', encoding='utf-8') as f:
            f.write(latest_link)
            
        print(f"🎉 成功抓取最新的一条链接: {latest_link}")
                
    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == '__main__':
    main()
