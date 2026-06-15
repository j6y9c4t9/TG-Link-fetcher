import os
import re
import urllib.request

channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')
URL = f"https://t.me/s/{channel_username}"

# 增强版正则表达式：容忍链接前后有空格、引号或被 HTML 标签包裹
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
            
        print("✅ 网页下载成功！开始解析...")
        
        # 调试：打印网页总长度和部分内容，确认有没有被 TG 拦截
        print(f"📄 网页总长度: {len(html_content)} 字符")
        if "tgme_channel_post" not in html_content:
            print("⚠️ 警告：网页中没有找到标准的 Telegram 消息标签！以下是网页前 500 个字符：")
            print(html_content[:500])
            
        # 增强版切片：同时兼容带文本和不带文本的消息框
        posts = re.findall(r'<div class="[^"]*tgme_channel_post_text[^"]*"[^>]*>(.*?)</div>', html_content, re.DOTALL)
        
        # 如果标准文本框没捞到，尝试捞取整个消息块（应对只有链接没有文字的情况）
        if not posts:
            posts = re.findall(r'<div class="[^"]*tgme_channel_post[^"]*"[^>]*>(.*?)</div>\s*</div>', html_content, re.DOTALL)
            
        print(f"📦 成功解析出 {len(posts)} 条历史消息。")

        latest_links = []
        
        # 从后往前找最新包含链接的消息
        for i, post in enumerate(reversed(posts)):
            found_links = re.findall(LINK_REGEX, post, re.IGNORECASE)
            if found_links:
                # 过滤掉 HTML 标签残留（比如链接末尾带了 <br/>）
                cleaned_links = []
                for link in found_links:
                    clean = re.split(r'[<>\s"\']', link)[0]
                    cleaned_links.append(clean)
                
                # 去重并保持顺序
                seen = set()
                latest_links = [x for x in cleaned_links if not (x in seen or seen.add(x))]
                print(f"🎉 成功在倒数第 {i+1} 条消息中定位到订阅链接！")
                break
        
        # 写入文件
        if latest_links:
            with open('subscribe.txt', 'w', encoding='utf-8') as f:
                for link in latest_links:
                    f.write(link + '\n')
            print(f"🚀 成功写入 {len(latest_links)} 个节点到 subscribe.txt")
        else:
            print("ℹ️ 抓取完毕：最近的历史消息中未发现符合格式的节点链接。")
            # 如果没找到，创建一个带提示的文件，防止文件彻底为空
            with open('subscribe.txt', 'w', encoding='utf-8') as f:
                f.write("# 最近的消息中未发现有效订阅链接\n")
                
    except Exception as e:
        print(f"❌ 运行崩溃，错误原因: {e}")

if __name__ == '__main__':
    main()
