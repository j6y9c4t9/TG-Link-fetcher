import os
import re
import urllib.request

channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')
URL = f"https://t.me/s/{channel_username}"

# 极其强悍的链接抓取正则
LINK_REGEX = r'(vmess|vless|ss|ssr|trojan|clash|hysteria|juicity)://[^\s"\'<>\\]+'

def main():
    print(f"📡 正在请求页面: {URL}")
    
    try:
        req = urllib.request.Request(
            URL, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        )
        
        with urllib.request.urlopen(req) as response:
            html_content = response.read().decode('utf-8')
            
        print(f"✅ 网页下载成功！长度: {len(html_content)} 字节。开始暴力提取...")
        
        # 1. 直接全局匹配所有订阅链接
        all_raw_links = re.findall(LINK_REGEX, html_content, re.IGNORECASE)
        
        if not all_raw_links:
            print("ℹ️ 抓取完毕：整个网页源码中未发现任何符合格式的订阅链接。")
            with open('subscribe.txt', 'w', encoding='utf-8') as f:
                f.write("# 当前页面未发现有效订阅链接\n")
            return

        print(f"📦 全局共发现 {len(all_raw_links)} 个原始链接（包含重复和旧消息）。")

        # 2. 清洗链接（去除可能残留的 HTML 尾巴，比如 <br/>）
        cleaned_links = []
        for link in all_raw_links:
            clean = re.split(r'[<>\s"\']', link)[0]
            cleaned_links.append(clean)

        # 3. 定位最新的一组订阅链接
        # 因为我们要的是“最新一条消息的订阅”，而在整个网页里，最后出现的那个链接就是最新的
        last_link = cleaned_links[-1]
        
        # 考虑到最新的一条消息里可能包含多个节点（比如一个配置里带着好几个 ss://）
        # 我们需要从后往前，找出所有跟最后一个链接“在同一发布周期”的链接，或者简单粗暴地去重后提取最后更新的节点
        # 这里采用最稳妥的做法：提取最后出现的不重复的最新节点群
        
        latest_links = []
        # 逆序去重：保证拿到的是最后更新的、且保留它们在最后一条消息里的相对顺序
        seen = set()
        for link in reversed(cleaned_links):
            if link not in seen:
                seen.add(link)
                latest_links.append(link)
        
        # 恢复正序
        latest_links.reverse()
        
        # 💡 注意：因为 freeVPNjd 频道通常一次性发一大版（几十个节点）
        # 如果你只想严格要“最后那一条消息里的所有节点”，由于我们取消了消息切片，
        # 我们可以保守取最后更新的 50 个节点，或者直接保留网页里所有最新未重复的节点
        # 这里我们选择保留最新去重后的前 30 个节点（通常就是最新一两发的内容）
        final_links = latest_links[-30:] 

        # 4. 写入文件
        with open('subscribe.txt', 'w', encoding='utf-8') as f:
            for link in final_links:
                f.write(link + '\n')
                
        print(f"🚀 成功！已将最新的 {len(final_links)} 个不重复节点写入 subscribe.txt")
                
    except Exception as e:
        print(f"❌ 运行崩溃，错误原因: {e}")

if __name__ == '__main__':
    main()
