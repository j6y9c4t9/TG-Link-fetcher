#!/usr/bin/env python3
"""
Clash 订阅聚合脚本 — 多模板多输出版（终极修复版 v15 — 文本终审清洗版）
功能：多源并发聚合 → 参数净化 → 多模板合并 → 最终文件级文本正则修正（彻底解决 []interface{} 数组灾难）
"""

import os
import re
import base64
import logging
import yaml
import requests
from concurrent.futures import ThreadPoolExecutor

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 日志模块
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("clash-aggregator")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 集中管理的可配置参数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONFIG = {
    "urls_file": "urls.txt",
    "template_dir": "template",      # 模板文件夹
    "output_dir": "output",          # 输出文件夹
    "request_timeout": 10,
    "max_workers": 4,
    "user_agent": "clash.meta",
    "target_regions": [
        "香港", "HK", "HongKong", "Hong Kong",
        "新加坡", "SG", "Singapore",
        "日本", "JP", "Japan",
        "美国", "US", "United States", "UnitedStates",
        "台湾", "TW", "Taiwan", "Formosa",
        "🇭🇰", "🇸🇬", "🇯🇵", "🇺🇸", "🇹🇼",
    ],
    "proxy_group_name": "Proxy",
    "duplicates_dir": "TEMP",
}

TASKS = [
    ("template.yaml", "config.yaml", "summary.txt"),
    ("template-smart.yaml", "config-smart.yaml", "summary-smart.txt")
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 工具函数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def build_target_regex(keywords: list[str]) -> re.Pattern:
    escaped = [re.escape(kw) for kw in keywords]
    pattern = "|".join(escaped)
    return re.compile(pattern, re.IGNORECASE)

TARGET_REG = build_target_regex(CONFIG["target_regions"])

def try_decode_base64(text: str) -> str:
    cleaned = text.strip()
    if "proxies:" in cleaned or "- name:" in cleaned:
        return cleaned
    try:
        decoded = base64.b64decode(cleaned).decode("utf-8")
        if "proxies:" in decoded or "- name:" in decoded:
            return decoded
    except Exception:
        pass
    return cleaned

def extract_source_label(url: str) -> str:
    parts = url.rstrip("/").split("/")
    source = parts[-1] if parts else "Unknown"
    if "github" in url.lower() and len(parts) >= 5:
        source = f"{parts[3]}_{parts[-1]}"
    if len(source) > 40:
        source = source[:37] + "..."
    return source

def load_sub_urls(file_path: str) -> list[str]:
    if not os.path.exists(file_path):
        log.warning(f"找不到链接列表文件 {file_path}")
        return []
    urls = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    log.info(f"从 {file_path} 加载了 {len(urls)} 个订阅链接")
    return urls

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 下载并解析单个订阅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_single_sub(url: str) -> tuple[list[dict], str]:
    source_name = extract_source_label(url)
    headers = {"User-Agent": CONFIG["user_agent"]}
    timeout = CONFIG["request_timeout"]

    try:
        log.info(f"正在下载: {url}")
        res = requests.get(url, headers=headers, timeout=timeout)
        res.raise_for_status()

        res_text = try_decode_base64(res.text)
        data = yaml.safe_load(res_text)

        if not data or not isinstance(data, dict):
            msg = f"⚠️ `{source_name}`: 返回内容不是有效 YAML 结构，已跳过"
            log.warning(msg)
            return [], msg

        proxies = data.get("proxies", [])
        if not isinstance(proxies, list):
            msg = f"⚠️ `{source_name}`: 未找到有效 proxies 列表，已跳过"
            log.warning(msg)
            return [], msg

        count_before = len(proxies)
        valid_proxies = []

        for p in proxies:
            if not isinstance(p, dict):
                continue
            name = str(p.get("name", "")).strip()
            server = str(p.get("server", "")).strip()
            port = str(p.get("port", "")).strip()
            if name and server and port:
                if TARGET_REG.search(name):
                    
                    ptype = p.get("type", "")
                    is_tls = p.get("tls", False)
                    network = p.get("network", "")

                    # 1. 拦截非 TLS 节点的脏参数
                    if ptype in ["vless", "trojan", "ss"] and not is_tls:
                        if "reality-opts" in p: del p["reality-opts"]
                        if "client-fingerprint" in p: del p["client-fingerprint"]
                    
                    # 2. 彻底清洗非 REALITY 传输层的垃圾配置
                    if network in ["ws", "grpc"] or p.get("ws-opts") or p.get("grpc-opts"):
                        if "reality-opts" in p: del p["reality-opts"]
                    
                    # 3. 严格数据级清洗 short-id
                    if "reality-opts" in p and isinstance(p["reality-opts"], dict):
                        ro = p["reality-opts"]
                        if "short-id" in ro:
                            sid_raw = ro["short-id"]
                            
                            # 如果此时已经是列表/数组结构，强行解包取第一个元素
                            if isinstance(sid_raw, list):
                                sid_raw = sid_raw[0] if sid_raw else ""
                                
                            sid = str(sid_raw).strip()
                            
                            if "." in sid: sid = sid.split(".")[0]
                            if "e+" in sid.lower():
                                try: sid = f"{int(float(sid)):x}"
                                except: pass

                            # 移除非法十六进制
                            if len(sid) % 2 != 0 or not re.match(r"^[0-9a-fA-F]*$", sid):
                                p["reality-opts"]["short-id"] = ""
                            else:
                                p["reality-opts"]["short-id"] = str(sid)

                    p["_source_key"] = f"{server}:{port}"
                    valid_proxies.append(p)

        msg = f"📦 `{source_name}`: 匹配 *{len(valid_proxies)}* 个 / 源码共 {count_before} 个"
        log.info(msg)
        return valid_proxies, msg

    except Exception as e:
        msg = f"❌ `{source_name}`: 错误 ({str(e)[:50]})"
        log.error(msg)
        return [], msg

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 核心聚合与重命名逻辑（包含序号逻辑）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_and_parse_nodes(sub_urls: list[str]) -> tuple[list[dict], list[str], int]:
    all_raw_proxies: list[dict] = []
    summary_lines: list[str] = []

    with ThreadPoolExecutor(max_workers=CONFIG["max_workers"]) as pool:
        futures = [pool.submit(fetch_single_sub, url) for url in sub_urls]
        for future in futures:
            proxies, msg = future.result()
            summary_lines.append(msg)
            all_raw_proxies.extend(proxies)

    log.info(f"并发下载完成，共收集 {len(all_raw_proxies)} 个候选节点，开始处理与重命名...")

    final_proxies: list[dict] = []
    seen_servers: set[str] = set()
    seen_names: set[str] = set()
    duplicate_count = 0
    duplicate_nodes: list[dict] = []
    
    node_idx = 1

    for p in all_raw_proxies:
        # 纯字典干净复制，防止任何对象引用导致的生成错乱
        node_copy = {k: (v if k != "reality-opts" else v.copy()) for k, v in p.items()}
        server_key = node_copy.get("_source_key", "")
        name = node_copy.get("name", "")

        is_dup_server = False
        if server_key in seen_servers:
            duplicate_count += 1
            is_dup_server = True
            duplicate_nodes.append({"name": name, "server_key": server_key})

        temp_name = f"{name} [复用]" if is_dup_server else name
        final_name = f"[{node_idx:03d}] {temp_name}"

        base_name = final_name
        counter = 1
        while final_name in seen_names:
            final_name = f"{base_name} #{counter}"
            counter += 1

        if "_source_key" in node_copy:
            del node_copy["_source_key"]

        node_copy["name"] = final_name
        final_proxies.append(node_copy)
        
        seen_servers.add(server_key)
        seen_names.add(final_name)
        node_idx += 1

    if duplicate_nodes:
        temp_dir = CONFIG["duplicates_dir"]
        os.makedirs(temp_dir, exist_ok=True)
        dup_file = os.path.join(temp_dir, "duplicates.txt")
        with open(dup_file, "w", encoding="utf-8") as f:
            for node in duplicate_nodes:
                f.write(f"{node['name']} | {node['server_key']}\n")

    return final_proxies, summary_lines, duplicate_count

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 节点注入
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def inject_into_proxy_groups(config: dict, new_proxies: list[dict]) -> None:
    groups = config.get("proxy-groups", [])
    if not groups or not new_proxies:
        return
    new_names = [p["name"] for p in new_proxies]
    target_name = CONFIG["proxy_group_name"]
    injected = 0
    for group in groups:
        if not isinstance(group, dict): continue
        gname = group.get("name", "")
        if gname == target_name or "代理" in gname or "proxy" in gname.lower():
            existing = set(group.get("proxies", []))
            for n in new_names:
                if n not in existing:
                    group.setdefault("proxies", []).append(n)
                    injected += 1
            if injected:
                log.info(f"已向分组 [{gname}] 注入 {injected} 个新节点")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 主逻辑
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    os.makedirs(CONFIG["output_dir"], exist_ok=True)
    os.makedirs(CONFIG["template_dir"], exist_ok=True)

    sub_urls = load_sub_urls(CONFIG["urls_file"])
    if not sub_urls:
        log.error("没有可用的订阅链接！")
        return

    filtered_proxies, base_summary_lines, duplicate_count = fetch_and_parse_nodes(sub_urls)

    for template_name, output_name, summary_name in TASKS:
        template_path = os.path.join(CONFIG["template_dir"], template_name)
        output_path = os.path.join(CONFIG["output_dir"], output_name)
        summary_path = os.path.join(CONFIG["output_dir"], summary_name)

        if not os.path.exists(template_path):
            log.error(f"跳过任务：找不到模板文件 {template_path}")
            continue

        log.info(f"▶️ 正在基于模板 {template_name} 生成配置...")
        
        with open(template_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        # 彻底阻断任何指针和引用，重新序列化一组最纯粹的基础字典
        current_proxies = yaml.safe_load(yaml.dump(filtered_proxies))
        inject_into_proxy_groups(config, current_proxies)
        config["proxies"] = current_proxies

        if "global-client-fingerprint" in config:
            del config["global-client-fingerprint"]
            log.info("🧹 已成功从输出配置中剥离废弃的全局 `global-client-fingerprint` 属性")

        # 1. 转化为初步的 YAML 字符串文本
        final_yaml_text = yaml.dump(
            config, 
            allow_unicode=True, 
            sort_keys=False, 
            default_flow_style=False, 
            width=4096
        )

        # ━━━━━━━ 🛑 最终文本审查官（核心大杀器） ━━━━━━━
        # 无论前面 PyYAML 怎么作妖、把它变成了数组还是别的
        # 只要最终文本里有 short-id 开头的配置，通过正则强制统一格式化为：short-id: "xxxx"
        
        # 修复情况 A: 处理已经被弄成数组列表的 short-id: \n    - "xxxx"
        final_yaml_text = re.sub(
            r'short-id:\s*\n\s*-\s*["\']?([0-9a-fA-F]+)["\']?', 
            r'short-id: "\1"', 
            final_yaml_text
        )
        
        # 修复情况 B: 处理没加引号或者加单引号的单行 short-id: 473277e2
        final_yaml_text = re.sub(
            r'(\s+)short-id:\s*["\']?([0-9a-fA-F]+)["\']?\b', 
            r'\1short-id: "\2"', 
            final_yaml_text
        )
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        # 2. 最终写入安全可用的配置文件
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(final_yaml_text)
        log.info(f"🟢 配置文件已成功保存至: {output_path}")

        task_summary = base_summary_lines.copy()
        if duplicate_count > 0:
            task_summary.append(f"🔁 统计到 *{duplicate_count}* 个服务器重复的节点（已重命名并保留，详见 TEMP/duplicates.txt）")
        task_summary.append(f"🔥 *基于模板 [{template_name}] 聚合完成！共包含 {len(current_proxies)} 个节点。*")

        with open(summary_path, "w", encoding="utf-8") as sf:
            SF_TEXT = "\n".join(task_summary)
            # 对摘要输出也进行一次净化，保证日志和电报通知漂亮整洁
            SF_TEXT = re.sub(r'short-id:.*', '', SF_TEXT)
            sf.write(SF_TEXT)
            
        print(f"\n--- 任务 [{template_name}] 摘要输出 ---")
        print("\n".join(task_summary))
        print("-" * 35)

if __name__ == "__main__":
    main()
