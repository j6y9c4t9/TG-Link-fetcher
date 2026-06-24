#!/usr/bin/env python3
"""
Clash 订阅聚合脚本 — 多模板多输出版（修复版 v10）
功能：多源并发聚合 → 强力清洗（规范 short-id & 纠正 flow 冲突） → 地区筛选 → 按顺序自动加数字编号 → 重复标记 → 遍历多模板 → 输出
"""

import os
import re
import base64
import logging
import yaml
import requests
import copy  # 导入内置深拷贝模块
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

# 任务定义：(模板文件名, 输出文件名, 摘要文件名)
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
            log.debug("检测到 Base64 编码的 Clash YAML，已自动解码")
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
        cleaned_count = 0

        # 正则校验：必须是偶数长度且由 0-9, a-f 组成的合法十六进制字符串
        hex_pattern = re.compile(r"^(?:[0-9a-fA-F]{2})+$")

        for p in proxies:
            if not isinstance(p, dict):
                continue
            
            # 💡【自愈逻辑 1】：强力清洗不合法的 REALITY short-id
            if p.get("type") == "vless" and "reality-opts" in p:
                reality_opts = p["reality-opts"]
                if isinstance(reality_opts, dict) and "short-id" in reality_opts:
                    sid = str(reality_opts["short-id"]).strip()
                    if sid == "" or not hex_pattern.match(sid):
                        del reality_opts["short-id"]
                        cleaned_count += 1

            # 💡【自愈逻辑 2】：修正错误的 flow 配置（防止 tls: false 或 ws/grpc 却带有 vision 流控）
            if p.get("type") == "vless" and "flow" in p:
                is_tls = p.get("tls") is True or "reality-opts" in p
                network_type = str(p.get("network", "tcp")).lower().strip()
                
                # 如果没有开启 TLS/Reality，或者传输协议不是标准的 tcp，则 flow 字段必错，直接删除
                if not is_tls or network_type != "tcp":
                    del p["flow"]
                    cleaned_count += 1

            name = str(p.get("name", "")).strip()
            server = str(p.get("server", "")).strip()
            port = str(p.get("port", "")).strip()
            
            if name and server and port:
                if TARGET_REG.search(name):
                    p["_source_key"] = f"{server}:{port}"
                    valid_proxies.append(p)

        clean_info = f" (自动清洗修正了 {cleaned_count} 处冲突配置)" if cleaned_count > 0 else ""
        msg = f"📦 `{source_name}`: 匹配 *{len(valid_proxies)}* 个 / 源码共 {count_before} 个{clean_info}"
        log.info(msg)
        return valid_proxies, msg

    except Exception as e:
        msg = f"❌ `{source_name}`: 错误 ({str(e)[:50]})"
        log.error(msg)
        return [], msg

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 核心聚合逻辑
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

    # 💡 按最终排出的顺序，自动为节点加 3 位数序号前缀（如 001 | ）
    for idx, p in enumerate(all_raw_proxies, start=1):
        node_copy = copy.deepcopy(p)
        server_key = node_copy.get("_source_key", "")
        raw_name = node_copy.get("name", "")

        numbered_name = f"{idx:03d} | {raw_name}"

        is_dup_server = False
        if server_key in seen_servers:
            duplicate_count += 1
            is_dup_server = True
            duplicate_nodes.append({
                "name": numbered_name,
                "server_key": server_key,
            })

        final_name = f"{numbered_name} [复用]" if is_dup_server else numbered_name

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

    log.info(f"处理完成：最终筛选出 {len(final_proxies)} 个有效节点，包含 {duplicate_count} 个复用服务器")

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
        if not isinstance(group, dict):
            continue
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

    # 1. 统一拉取和解析所有节点
    filtered_proxies, base_summary_lines, duplicate_count = fetch_and_parse_nodes(sub_urls)

    # 2. 遍历任务，分别为不同的模板生成不同的输出文件
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

        # 注入策略组并替换 proxies
        current_proxies = copy.deepcopy(filtered_proxies)
        inject_into_proxy_groups(config, current_proxies)
        config["proxies"] = current_proxies

        # 写入最终配置文件
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False, width=4096)
        log.info(f"🟢 配置文件已成功保存至: {output_path}")

        # 生成该模板对应的专属摘要
        task_summary = base_summary_lines.copy()
        if duplicate_count > 0:
            task_summary.append(f"🔁 统计到 *{duplicate_count}* 个服务器重复的节点（已重命名并保留，详见 TEMP/duplicates.txt）")
        task_summary.append(f"🔥 *基于模板 [{template_name}] 聚合完成！共包含 {len(current_proxies)} 个节点。*")

        with open(summary_path, "w", encoding="utf-8") as sf:
            sf.write("\n".join(task_summary))
            
        print(f"\n--- 任务 [{template_name}] 摘要输出 ---")
        print("\n".join(task_summary))
        print("-" * 35)

if __name__ == "__main__":
    main()
