import os
import re
import urllib.request
import urllib.parse
import ssl

channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')
URL = f"https://t.me/s/{channel_username}"

# 精准匹配包含 token= 的机场订阅网址
SUBSCRIBE_REGEX = r'https?://[^\s"\'<>]+token=[a-zA-Z0-9]+'

# 🎯 更换为大厂纯 API 后端（不带前端网页，专门处理脚本请求，更稳定）
SUB_CONVERTER_API = "https://api.v1.mk/sub?"

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

        # 4. 构建订阅转换请求
        params = {
            "target": "clash",
            "url": latest_link,
            "insert": "false"
        }
        encoded_params = urllib.parse.urlencode(params)
        convert_url = SUB_CONVERTER_API + encoded_params
        
        print("⏳ 正在调用大厂 API 接口生成 YAML 文件...")
        convert_req = urllib.request.Request(
            convert_url,
            # 模拟真实的 Clash 客户端请求，防止被接口拒绝
            headers={'User-Agent': 'clash'} 
        )
        
        with urllib.request.urlopen(convert_req, context=ssl_context) as convert_res:
            yaml_content = convert_res.read().decode('utf-8')
            
        # 📄 检查返回内容是否包含 Clash 核心标志，防止误抓网页
        if "proxies:" in yaml_content or "proxy-groups:" in yaml_content or "port:" in yaml_content:
            # 5. 将获取到的 YAML 内容覆盖写入到 config.yaml
            with open('config.yaml', 'w', encoding='utf-8') as f:
                f.write(yaml_content)
            print("🎉 成功生成包含节点信息的 config.yaml 文件！")
        else:
            print("⚠️ 警告：接口返回的好像不是有效的 Clash 配置文件。以下是返回前 200 字：")
            print(yaml_content[:200])
                
    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == '__main__':
    main()
