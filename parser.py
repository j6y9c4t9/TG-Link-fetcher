import os
import re
import requests
import yaml

# 1. 目标地区的正则表达式匹配
TARGET_REG = re.compile(r"香港|HK|HongKong|Hong Kong|新加坡|SG|Singapore|日本|JP|Japan|美国|US|United States|UnitedStates|台湾|TW|Taiwan|Formosa", re.I)

def load_sub_urls(file_path="urls.txt"):
    """从指定文件动态读取订阅链接，自动忽略空行和注释"""
    if not os.path.exists(file_path):
        print(f"⚠️ 警告: 找不到链接列表文件 {file_path}，将尝试读取默认逻辑...")
        return []
        
    urls = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # 过滤掉空行和以 # 开头的注释行
            if line and not line.startswith("#"):
                urls.append(line)
                
    print(f"📂 成功从 {file_path} 加载了 {len(urls)} 个订阅链接。")
    return urls

def fetch_and_parse_nodes(sub_urls):
    all_proxies = []
    seen_names = set()  # 用于名字去重（确保写入 config 的名字唯一）
    seen_servers = set() # 用于物理服务器（IP/域名+端口）去重
    
    headers = {'User-Agent': 'clash.meta'}
    summary_lines = [] # 用于记录通知文本
    
    for url in sub_urls:
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
                
                # 避坑检查：如果核心字段为空，直接跳过
                if not name or not server or not port:
                    continue
                
                server_key = f"{server}:{port}"
                
                if TARGET_REG.search(name):
                    # 【核心修改点】唯一判定的标准：只看物理服务器(IP:Port)有没有出现过
                    if server_key not in seen_servers:
                        
                        # 如果物理服务器没出现过，说明这是一个新节点，我们必须要！
                        # 接下来解决 Clash 的名字冲突问题：
                        final_name = name
                        counter = 1
                        while final_name in seen_names:
                            final_name = f"{name} ({counter})"
                            counter += 1
                        
                        # 把最终不重复的名字写回节点
                        p["name"] = final_name
                        
                        # 登记到账本
                        all_proxies.append(p)
                        seen_names.add(final_name)
                        seen_servers.add(server_key)
                        match_count += 1
                    else:
                        # 只要 IP:Port 出现过了，不管名字叫什么，都算重复节点
                        duplicate_count += 1
            
            # 提取 URL 的尾部文件名作为标识，防止链接太长在 TG 里显示错乱
            source_name = url.split('/')[-1] if '/' in url else "Unknown"
            # 如果文件名太单调（比如都叫 sub），可以截取域名部分
            if len(source_name) < 5 and "github" in url:
                parts = url.split('/')
                source_name = f"{parts[3]}_{source_name}" # 加上作者名区分
                
            log_msg = f"📦 `{source_name}`: 筛选 *{match_count}* 个 (过滤 {duplicate_count} 重复 / 源码共 {count_before} 个)"
            print(f"-> {log_msg}")
            summary_lines.append(log_msg)
                    
        except Exception as e:
            # 某个链接挂了不影响大局，记录下来并继续
            err_msg = f"❌ 订阅请求失败: {url.split('/')[-1]} (错误: {str(e)[:30]})"
            print(err_msg)
            summary_lines.append(err_msg)
            
    return all_proxies, summary_lines

def main():
    if not os.path.exists("template.yaml"):
        print("❌ 错误: 找不到 template.yaml 模板文件")
        return
        
    # 动态加载订阅链接
    sub_urls = load_sub_urls("urls.txt")
    if not sub_urls:
        print("❌ 错误: urls.txt 中没有可用的订阅链接！")
        return
        
    print("正在读取 template.yaml 模板...")
    with open("template.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        
    filtered_proxies, summary_lines = fetch_and_parse_nodes(sub_urls)
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
