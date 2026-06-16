#!/usr/bin/env python3
"""
Clash 订阅聚合脚本 — 完整优化版（修复版 v3）
功能：多源并发聚合 → 地区筛选 → 双重去重(物理服务器优先) → 重复节点归档 → 输出配置
"""

import os
import re
import base64
import logging
import yaml
import requests
from concurrent.futures import ThreadPoolExecutor

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 日志模块：替代所有 print，方便控制级别和输出格式
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
    "template_file": "template.yaml",
    "output_file": "config.yaml",
    "summary_file": "summary.txt",
    "request_timeout": 10,
    "max_workers": 4,
    "user_agent": "clash.meta",
    "target_regions": [
        "香港", "HK", "HongKong", "Hong Kong",
        "新加坡", "SG", "Singapore",
        "日本", "JP", "Japan",
        "美国", "US", "United States", "UnitedStates",
        "台湾", "TW", "Taiwan", "Formosa",
    ],
    "proxy_group_name": "Proxy",
    "duplicates_dir": "TEMP",
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 构建正则：直接匹配，与原版行为一致
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def build_target_regex(keywords: list[str]) -> re.Pattern:
    escaped = [re.escape(kw) for kw in keywords]
    pattern = "|".join(escaped)
    return re.compile(pattern, re.IGNORECASE)


TARGET_REG = build_target_regex(CONFIG["target_regions"])

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Base64 解码兼容层
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 辅助函数：从 URL 提取可读的源标识名
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def extract_source_label(url: str) -> str:
    parts = url.rstrip("/").split("/")
    source = parts[-1] if parts else "Unknown"
    if "github" in url.lower() and len(parts) >= 5:
        source = f"{parts[3]}_{parts[-1]}"
    if len(source) > 40:
        source = source[:37] + "..."
    return source


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 加载订阅链接
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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
                    p["_source_key"] = f"{server}:{port}"
                    valid_proxies.append(p)

        msg = f"📦 `{source_name}`: 匹配 *{len(valid_proxies)}* 个 / 源码共 {count_before} 个"
        log.info(msg)
        return valid_proxies, msg

    except requests.exceptions.Timeout:
        msg = f"❌ `{source_name}`: 请求超时 ({timeout}s)"
        log.error(msg)
        return [], msg
    except requests.exceptions.HTTPError as e:
        msg = f"❌ `{source_name}`: HTTP {e.response.status_code}"
        log.error(msg)
        return [], msg
    except yaml.YAMLError as e:
        msg = f"❌ `{source_name}`: YAML 解析失败 ({str(e)[:50]})"
        log.error(msg)
        return [], msg
    except requests.exceptions.RequestException as e:
        msg = f"❌ `{source_name}`: 网络错误 ({str(e)[:50]})"
        log.error(msg)
        return [], msg
    except Exception as e:
        msg = f"❌ `{source_name}`: 未知错误 ({str(e)[:50]})"
        log.error(msg)
        return [], msg


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 核心聚合逻辑：并发下载 + 双重去重 + 重复节点归档
# 【修改】返回值加上 duplicate_count
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_and_parse_nodes(sub_urls: list[str]) -> tuple[list[dict], list[str], int]:
    all_raw_proxies: list[dict] = []
    summary_lines: list[str] = []

    # 并发下载，按提交顺序收集结果保持行为一致
    with ThreadPoolExecutor(max_workers=CONFIG["max_workers"]) as pool:
        futures = [pool.submit(fetch_single_sub, url) for url in sub_urls]
        for future in futures:
            proxies, msg = future.result()
            summary_lines.append(msg)
            all_raw_proxies.extend(proxies)

    log.info(f"并发下载完成，共收集 {len(all_raw_proxies)} 个候选节点，开始去重...")

    # ── 双重去重：物理服务器优先 ──
    final_proxies: list[dict] = []
    seen_servers: set[str] = set()
    seen_names: set[str] = set()
    duplicate_count = 0
    duplicate_nodes: list[dict] = []

    for p in all_raw_proxies:
        server_key = p.pop("_source_key")
        name = p.get("name", "")

        if server_key in seen_servers:
            duplicate_count += 1
            duplicate_nodes.append({
                "name": name,
                "server_key": server_key,
            })
            continue

        # 名字冲突处理：追加 #1、#2 ...
        final_name = name
        counter = 1
        while final_name in seen_names:
            final_name = f"{name} #{counter}"
            counter += 1

        p["name"] = final_name
        final_proxies.append(p)
        seen_servers.add(server_key)
        seen_names.add(final_name)

    log.info(f"去重完成：保留 {len(final_proxies)} 个唯一节点，过滤 {duplicate_count} 个重复")

    # 将重复节点写入 TEMP 目录
    if duplicate_nodes:
        temp_dir = CONFIG["duplicates_dir"]
        os.makedirs(temp_dir, exist_ok=True)
        dup_file = os.path.join(temp_dir, "duplicates.txt")

        with open(dup_file, "w", encoding="utf-8") as f:
            for node in duplicate_nodes:
                f.write(f"{node['name']} | {node['server_key']}\n")

        log.info(f"已将 {len(duplicate_nodes)} 个重复节点写入 {os.path.abspath(dup_file)}")
    else:
        log.info("本轮无重复节点，跳过写入 TEMP 目录")

    return final_proxies, summary_lines, duplicate_count    # 【修改】多返回一个值


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 将新聚合的节点自动注入到 proxy-groups 的指定分组中
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

    if injected == 0:
        log.warning(
            f"未找到名为 [{target_name}] 的代理组，"
            "请检查 template.yaml 中是否配置了对应的 proxy-groups"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 主函数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    template_file = CONFIG["template_file"]
    if not os.path.exists(template_file):
        log.error(f"找不到模板文件 {template_file}")
        return

    sub_urls = load_sub_urls(CONFIG["urls_file"])
    if not sub_urls:
        log.error(f"{CONFIG['urls_file']} 中没有可用的订阅链接！")
        return

    log.info(f"正在读取 {template_file}...")
    with open(template_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 【修改】接收第三个返回值
    filtered_proxies, summary_lines, duplicate_count = fetch_and_parse_nodes(sub_urls)

    inject_into_proxy_groups(config, filtered_proxies)

    config["proxies"] = filtered_proxies

    output_file = CONFIG["output_file"]
    with open(output_file, "w", encoding="utf-8") as f:
        yaml.dump(
            config,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=4096,
        )
    log.info(f"配置文件 {output_file} 已生成")

    # 【新增】重复节点通知，写入摘要
    if duplicate_count > 0:
        dup_msg = f"🔁 去重过滤了 *{duplicate_count}* 个重复节点（详见 TEMP/duplicates.txt）"
        summary_lines.append(dup_msg)

    total_msg = f"🔥 *去重完成！最终保留 {len(filtered_proxies)} 个唯一节点。*"
    summary_lines.append(total_msg)

    summary_file = CONFIG["summary_file"]
    with open(summary_file, "w", encoding="utf-8") as sf:
        sf.write("\n".join(summary_lines))
    log.info(f"摘要文件 {summary_file} 已写入")

    print("\n" + "\n".join(summary_lines))


if __name__ == "__main__":
    main()
