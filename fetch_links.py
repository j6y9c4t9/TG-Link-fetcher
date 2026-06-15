import os
import re
import urllib.request

# 读取要抓取的频道名
channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')

# 拼接 Telegram 官方公开网页版链接
URL = f"https://t.me/s/{channel_username}"

# 正则表达式：匹配常见的订阅链接
LINK_REGEX = r'(vmess|vless|ss|ssr|trojan|clash|hysteria|juicity)://[^\s"\'<>]+'

def main():
    print(f"正在抓取频道: {channel_username} 的网页版...")
    
    try:
        # 模拟浏览器发送请求
        req = urllib.request.Request(
            URL, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        
        with urllib.request.urlopen(req) as response:
            html_content = response.read().decode('utf-8')
            
        print("网页下载成功，开始解析链接...")
        
        # 使用正则提取所有订阅链接
        links = re.findall(LINK_REGEX, html_content, re.IGNORECASE)
        
        # 去重
        unique_links = list(set(links))
        
        # 写入文件
        with open('subscribe.txt', 'w', encoding='utf-8') as f:
            for link in unique_links:
                f.write(link + '\n')
                
        print(f"🎉 抓取成功！共提取到 {len(unique_links)} 个节点链接，已保存至 subscribe.txt")
        
    except Exception as e:
        print(f"❌ 抓取失败，错误原因: {e}")

if __name__ == '__main__':
    main()
