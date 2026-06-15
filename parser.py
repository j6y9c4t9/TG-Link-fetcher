import os
import re
import requests
import yaml

# 1. 固定的订阅链接列表
SUB_URLS = [
    "https://raw.githubusercontent.com/LeilaoMi/AutoMergePublicNodes-Optimized/refs/heads/main/output/verified.yaml",
    "https://raw.githubusercontent.com/peasoft/NoMoreWalls/master/list.meta.yml"
]

# 2. 目标地区的正则表达式匹配
TARGET_REG = re.compile(r"香港|HK|HongKong|Hong Kong|新加坡|SG|Singapore|日本|JP|Japan|美国|US|United States|UnitedStates|台湾|TW|Taiwan|Formosa", re.I)

def fetch_and_parse_nodes():
    all_proxies = []
    seen_names = set()  # 用于名字去重
    seen_servers = set() # 用于物理服务器（IP/域名+端口）去重
    
    headers = {'User-Agent': 'clash.meta'}
    summary_lines = [] # 用于记录通知文本
    
    for url in SUB_URLS:
        try:
            print(f"正在下载订阅: {url}")
            res = requests.get(url, headers=headers, timeout=20)
            res.raise_for_status()
            
            data = yaml.safe_load(res.text)
            if not data or not isinstance(data, dict):
                print(f"警告: 订阅 {url} 返回的内容不是有效的 YAML 结构")
                continue
                
            proxies = data.get("proxies", [])
            if not isinstance(proxies, list):
                print(f"警告: {url} 中未找到有效的 proxies 列表")
                continue
                
            count_before = len(proxies)
            match_count = 0
            duplicate_count = 0
            
            for p in proxies:
                if not isinstance(p, dict):
                    continue
                
                name = str(p.get("name", "")).strip()
                server = str(p.get("server", "")).strip()
                port = str(p.get("port", "")).strip()
                
                server_key = f"{server}:{port}"
                
                if TARGET_REG.search(name):
                    if name not in seen_names and server_key not in seen_servers:
                        all_proxies.append(p)
                        seen_names.add(name)
                        seen_servers.add(server_key)
                        match_count += 1
                    else:
                        duplicate_count += 1
            
            source_name = url.split('/')[-1]
            log_msg = f"📦 `{source_name}`: 筛选 *{match_count}* 个 (过滤 {duplicate_count} 重复 / 源码共 {count_before} 个)"
            print(f"-> {log_msg}")
            summary_lines.append(log_msg)
                    
        except Exception as e:
            print(f"❌ 获取或解析订阅失败: {url}\n错误信息: {e}")
            
    return all_proxies, summary_lines

def main():
    if not os.path.exists("template.yaml"):
        print("❌ 错误: 找不到 template.yaml 模板文件")
        return
        
    print("正在读取 template.yaml 模板...")
    with open("template.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        
    filtered_proxies, summary_lines = fetch_and_parse_nodes()
    print(f"🎉 完美的双重去重完成！最终保留 {len(filtered_proxies)} 个唯一节点。")
    
    config["proxies"] = filtered_proxies
    
    output_filename = "config.yaml"
    with open(output_filename, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)
    print(f"🚀 最终配置文件 {output_filename} 生成成功！")
    
    total_msg = f"🔥 *完美去重完成！最终保留 {len(filtered_proxies)} 个唯一节点。*"
    summary_lines.append(total_msg)
    
    with open("summary.txt", "w", encoding="utf-8") as sf:
        sf.write("\n".join(summary_lines))

if __name__ == "__main__":
    main()
