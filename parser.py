import os
import re
import requests
import yaml

# 1. 固定的订阅链接列表
SUB_URLS = [
    "https://raw.githubusercontent.com/LeilaoMi/AutoMergePublicNodes-Optimized/refs/heads/main/output/verified.yaml",
    "https://raw.githubusercontent.com/peasoft/NoMoreWalls/master/list.meta.yml"
]

# 2. 目标地区的正则表达式匹配（包含常见中英文简称，忽略大小写）
TARGET_REG = re.compile(r"香港|HK|HongKong|Hong Kong|新加坡|SG|Singapore|日本|JP|Japan|美国|US|United States|UnitedStates|台湾|TW|Taiwan|Formosa", re.I)

def fetch_and_parse_nodes():
    all_proxies = []
    seen_names = set()
    
    # 模拟 Clash Meta 客户端的请求头，防止部分节点站拦截
    headers = {'User-Agent': 'clash.meta'}
    
    for url in SUB_URLS:
        try:
            print(f"正在下载订阅: {url}")
            res = requests.get(url, headers=headers, timeout=20)
            res.raise_for_status()
            
            # 使用 safe_load 解析 YAML 文本
            data = yaml.safe_load(res.text)
            if not data or not isinstance(data, dict):
                print(f"警告: 订阅 {url} 返回的内容不是有效的 YAML 字典结构")
                continue
                
            # 获取节点列表（标准 Clash 配置节点在 'proxies' 键下）
            proxies = data.get("proxies", [])
            if not isinstance(proxies, list):
                print(f"警告: {url} 中未找到有效的 proxies 列表")
                continue
                
            count_before = len(proxies)
            match_count = 0
            
            for p in proxies:
                if not isinstance(p, dict):
                    continue
                name = p.get("name", "")
                
                # 核心筛选逻辑：匹配地区名称 且 名字没有重复
                if TARGET_REG.search(str(name)):
                    if name not in seen_names:
                        all_proxies.append(p)
                        seen_names.add(name)
                        match_count += 1
                        
            print(f"-> 成功从该源中筛选出 {match_count}/{count_before} 个目标节点。")
                    
        except Exception as e:
            print(f"❌ 获取或解析订阅失败: {url}\n错误信息: {e}")
            
    return all_proxies

def main():
    # 检查模板文件是否存在
    if not os.path.exists("template.yaml"):
        print("❌ 错误: 找不到 template.yaml 模板文件，请确保它存在于当前目录下。")
        return
        
    print("正在读取 template.yaml 模板...")
    with open("template.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        
    # 获取并过滤节点
    filtered_proxies = fetch_and_parse_nodes()
    print(f"🎉 筛选去重完成！共计获得 {len(filtered_proxies)} 个有效节点。")
    
    # 将筛选后的节点覆盖写入模板的 proxies 字段
    config["proxies"] = filtered_proxies
    
    # 导出最终的 Clash 配置文件
    output_filename = "config.yaml"
    with open(output_filename, "w", encoding="utf-8") as f:
        # allow_unicode=True 确保中文不变成 \uXXXX 编码，sort_keys=False 保持模板原本的顺序
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)
    print(f"🚀 最终配置文件 {output_filename} 生成成功！")

if __name__ == "__main__":
    main()
