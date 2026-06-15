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
    print(f"正在抓取频道: {channel_username} 的最新一条消息...")
    
    try:
        # 模拟浏览器发送请求
        req = urllib.request.Request(
            URL, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        
        with urllib.request.urlopen(req) as response:
            html_content = response.read().decode('utf-8')
            
        print("网页下载成功，开始定位最新消息...")
        
        # 1. 切割网页，把每条消息分离出来
        # Telegram 网页版中，每条消息都被包裹在带有 tgme_channel_post_text 类名的 div 中
        posts = re.findall(r'<div class="[^"]*tgme_channel_post_text[^"]*"[^>]*>(.*?)</div>', html_content, re.DOTALL)
        
        if not posts:
            print("❌ 未能在网页中解析到任何消息，可能是频道名错误或页面结构发生变化。")
            return

        latest_links = []
        
        # 2. 从后往前遍历消息（因为最后一条是最新发布的）
        for post in reversed(posts):
            # 尝试在这条消息中匹配订阅链接
            found_links = re.findall(LINK_REGEX, post, re.IGNORECASE)
            
            if found_links:
                # 找到了包含链接的最新一条消息！
                # 去除这条消息里的重复链接，并保持原有顺序
                seen = set()
                latest_links = [x for x in found_links if not (x in seen or seen.add(x))]
                print("🎉 成功定位到最新的一条订阅消息！")
                break  # ❌ 核心：找到最新的就直接跳出循环，不再往前找旧消息
        
        # 3. 写入文件
        if latest_links:
            with open('subscribe.txt', 'w', encoding='utf-8') as f:
                for link in latest_links:
                    f.write(link + '\n')
            print(f"🎉 成功提取到最新一条消息中的 {len(latest_links)} 个节点，已更新至 subscribe.txt")
        else:
            print("ℹ️ 网页里的最近几条消息中都没有包含订阅链接。")
                
    except Exception as e:
        print(f"❌ 抓取失败，错误原因: {e}")

if __name__ == '__main__':
    main()
