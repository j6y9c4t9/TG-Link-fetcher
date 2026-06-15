import os
import re
import urllib.request
import urllib.parse

channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')
URL = f"https://t.me/s/{channel_username}"

# 精准匹配包含 token= 的机场订阅网址
SUBSCRIBE_REGEX = r'https?://[^\s"\'<>]+token=[a-zA-Z0-9]+'

# 🎯 开源通用的订阅转换 API 接口（如果失效，可以自行更换为其他公共后端）
SUB_CONVERTER_API = "https://sub.id9.cc/sub?"

def main():
    try:
        # 1. 模拟浏览器请求 Telegram 页面
        req = urllib.request.Request(
            URL, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req) as response:
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

        # 4. 🚀 核心步骤：构建订阅转换请求
        # target=clash 代表输出标准的 Clash YAML 格式
        params = {
            "target": "clash",
            "url": latest_link,
            "insert": "false"
        }
        # 对参数进行编码，防止链接中的特殊字符导致请求失败
        encoded_params = urllib.parse.urlencode(params)
        convert_url = SUB_CONVERTER_API + encoded_params
        
        print("⏳ 正在调用订阅转换接口生成 YAML 文件...")
        convert_req = urllib.request.Request(
            convert_url,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        
        with urllib.request.urlopen(convert_req) as convert_res:
            yaml_content = convert_res.read().decode('utf-8')
            
        # 5. 将获取到的 YAML 内容覆盖写入到 config.yaml
        with open('config.yaml', 'w', encoding='utf-8') as f:
            f.write(yaml_content)
            
        print("🎉 成功生成包含节点信息的 config.yaml 文件！")
                
    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == '__main__':
    main()
