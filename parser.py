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
                
                # 构造一个唯一的服务器标识，如 "128.1.1.1:443"
                server_key = f"{server}:{port}"
                
                # 核心筛选逻辑：
                # 1. 名字匹配目标地区
                if TARGET_REG.search(name):
                    # 2. 双重去重：名字没见过 且 服务器IP+端口也没见过
                    if name not in seen_names and server_key not in seen_servers:
                        all_proxies.append(p)
                        seen_names.add(name)
                        seen_servers.add(server_key)
                        match_count += 1
                    else:
                        duplicate_count += 1
                        
            print(f"-> 成功筛选出 {match_count} 个节点（过滤了 {duplicate_count} 个重复节点 / 源码共 {count_before} 个）")
                    
        except Exception as e:
            print(f"❌ 获取或解析订阅失败: {url}\n错误信息: {e}")
            
    return all_proxies

def main():
    if not os.path.exists("template.yaml"):
        print("❌ 错误: 找不到 template.yaml 模板文件")
        return
        
    print("正在读取 template.yaml 模板...")
    with open("template.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        
    # 获取并过滤节点
    filtered_proxies = fetch_and_parse_nodes()
    print(f"🎉 完美的双重去重完成！最终保留 {len(filtered_proxies)} 个唯一节点。")
    
    # 覆盖写入
    config["proxies"] = filtered_proxies
    
    # 导出
    output_filename = "config.yaml"
    with open(output_filename, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)
    print(f"🚀 最终配置文件 {output_filename} 生成成功！")

if __name__ == "__main__":
    main()
