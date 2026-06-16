#!/usr/bin/env python3
"""
Clash 订阅聚合脚本 — 优化版
功能：多源聚合 → 地区筛选 → 双重去重(物理服务器优先) → 输出配置
"""

import os
import re
import base64
import logging
import yaml
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 【新增】日志模块：替代所有 print，方便控制级别和输出格式
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("clash-aggregator")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 【新增】集中管理的可配置参数，方便后期维护
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONFIG = {
    "urls_file": "urls.txt",
    "template_file": "template.yaml",
    "output_file": "config.yaml",
    "summary_file": "summary.txt",
    "request_timeout": 10,          # 【修改】超时从 20s 缩短为 10s
    "max_workers": 4,               # 【新增】并发下载线程数
    "user_agent": "clash.meta",
    "target_regions": [             # 【新增】地区关键词抽离为配置，便于扩展
        "香港", "HK", "HongKong", "Hong Kong",
        "新加坡", "SG", "Singapore",
        "日本", "JP", "Japan",
        "美国", "US", "United States", "UnitedStates",
        "台湾", "TW", "Taiwan", "Formosa",
    ],
    "proxy_group_name": "Proxy",    # 【新增】要自动注入的代理组名称
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 【新增】构建正则：长词直接匹配，短缩写(HK/SG/JP/US/TW)加词边界防误判
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def build_target_regex(keywords: list[str]) -> re.Pattern:
    """将关键词列表拆分为短词(≤2字母)和长词，分别用 \\b 包裹或直接拼接"""
    short, long = [], []
    for kw in keywords:
        if len(kw) <= 2:
            short.append(re.escape(kw))
        else:
            long.append(re.escape(kw))
    # 短缩写用词边界保护，防止 "twice" 误匹配 "TW"
    short_part = r"\b(?:" + "|".join(short) + r")\b" if short else ""
    long_part = "|".join(long) if long else ""
    pattern = "|".join(filter(None, [short_part, long_part]))
    return re.compile(pattern, re.IGNORECASE)


TARGET_REG = build_target_regex(CONFIG["target_regions"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 【新增】Base64 解码兼容层 —— 部分机场返回 Base64 编码的 Clash YAML
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def try_decode_base64(text: str) -> str:
    """
    尝试对响应内容做 Base64 解码。
    如果解码成功且内容看起来像 Clash YAML，返回解码结果；
    否则返回原文。
    """
    cleaned = text.strip()
    # 如果已经包含 YAML 特征，直接返回，不浪费时间解码
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
# 【新增】辅助函数：从 URL 提取可读的源标识名
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def extract_source_label(url: str) -> str:
    """从 URL 尾部提取文件名，GitHub 链接额外附带作者名以区分"""
    parts = url.rstrip("/").split("/")
    source = parts[-1] if parts else "Unknown"
    # GitHub 链接通常 /<author>/<repo>/.../file，取 author_file
    if "github" in url.lower() and len(parts) >= 5:
        source = f"{parts[3]}_{parts[-1]}"
    # 截断过长的名字
    if len(source) > 40:
        source = source[:37] + "..."
    return source


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 【修改】加载订阅链接 —— 保持原逻辑，改用 logging 输出
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
# 【新增】下载并解析单个订阅（抽离为独立函数，便于并发调用和错误处理）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_single_sub(url: str) -> tuple[list[dict], str]:
    """
    下载单个订阅，返回 (解析后的 proxies 列表, 日志文本)。
    失败时返回 ([], 错误日志文本)。
    """
    source_name = extract_source_label(url)
    headers = {"User-Agent": CONFIG["user_agent"]}
    timeout = CONFIG["request_timeout"]

    try:
        log.info(f"正在下载: {url}")
        res = requests.get(url, headers=headers, timeout=timeout)
        res.raise_for_status()

        # 【新增】先尝试 Base64 解码兼容
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
                # 【修改】只保留匹配目标地区的节点，将 _meta 信息附上用于后续去重
                if TARGET_REG.search(name):
                    p["_source_key"] = f"{server}:{port}"
                    valid_proxies.append(p)

        msg = f"📦 `{source_name}`: 匹配 *{len(valid_proxies)}* 个 / 源码共 {count_before} 个"
        log.info(msg)
        return valid_proxies, msg

    # 【新增】分类型捕获异常，精准定位问题
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
# 【修改】核心聚合逻辑 —— 并发下载 + 双重去重
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_and_parse_nodes(sub_urls: list[str]) -> tuple[list[dict], list[str]]:
    all_raw_proxies: list[dict] = []
    summary_lines: list[str] = []

    # 【新增】使用线程池并发下载多个订阅源
    with ThreadPoolExecutor(max_workers=CONFIG["max_workers"]) as pool:
        future_map = {pool.submit(fetch_single_sub, url): url for url in sub_urls}
        for future in as_completed(future_map):
            proxies, msg = future.result()
            summary_lines.append(msg)
            all_raw_proxies.extend(proxies)

    log.info(f"并发下载完成，共收集 {len(all_raw_proxies)} 个候选节点，开始去重...")

    # ── 双重去重：物理服务器优先 ──
    final_proxies: list[dict] = []
    seen_servers: set[str] = set()
    seen_names: set[str] = set()
    duplicate_count = 0

    for p in all_raw_proxies:
        server_key = p.pop("_source_key")  # 取出临时标记，不写入最终配置
        name = p.get("name", "")

        if server_key in seen_servers:
            duplicate_count += 1
            continue

        # 名字冲突处理：追加 (1)、(2) ...
        final_name = name
        counter = 1
        while final_name in seen_names:
            final_name = f"{name} ({counter})"
            counter += 1

        p["name"] = final_name
        final_proxies.append(p)
        seen_servers.add(server_key)
        seen_names.add(final_name)

    log.info(f"去重完成：保留 {len(final_proxies)} 个唯一节点，过滤 {duplicate_count} 个重复")

    return final_proxies, summary_lines


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 【新增】将新聚合的节点自动注入到 proxy-groups 的指定分组中
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def inject_into_proxy_groups(config: dict, new_proxies: list[dict]) -> None:
    """
    将新节点的名字追加到 config 中目标 proxy-group 的 proxies 列表里。
    仅处理 template 中已存在的 group，不会创建新 group。
    """
    groups = config.get("proxy-groups", [])
    if not groups or not new_proxies:
        return

    new_names = [p["name"] for p in new_proxies]
    target_name = CONFIG["proxy_group_name"]
    injected = 0

    for group in groups:
        if not isinstance(group, dict):
            continue
        # 匹配指定名称的 group，或包含 "代理"/"Proxy" 关键字的 group
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
# 【修改】主函数 —— 整合所有优化
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    # 检查模板文件
    template_file = CONFIG["template_file"]
    if not os.path.exists(template_file):
        log.error(f"找不到模板文件 {template_file}")
        return

    # 加载订阅链接
    sub_urls = load_sub_urls(CONFIG["urls_file"])
    if not sub_urls:
        log.error(f"{CONFIG['urls_file']} 中没有可用的订阅链接！")
        return

    # 读取模板
    log.info(f"正在读取 {template_file}...")
    with open(template_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 聚合 + 去重
    filtered_proxies, summary_lines = fetch_and_parse_nodes(sub_urls)

    # 【新增】自动注入 proxy-groups
    inject_into_proxy_groups(config, filtered_proxies)

    # 写入 proxies
    config["proxies"] = filtered_proxies

    # 【修改】输出时使用更安全的 dump 设置
    output_file = CONFIG["output_file"]
    with open(output_file, "w", encoding="utf-8") as f:
        yaml.dump(
            config,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,   # 【新增】禁止内联格式，保持可读性
            width=4096,                  # 【新增】防止长行被自动折行
        )
    log.info(f"配置文件 {output_file} 已生成")

    # 写入摘要
    total_msg = f"🔥 *去重完成！最终保留 {len(filtered_proxies)} 个唯一节点。*"
    summary_lines.append(total_msg)

    summary_file = CONFIG["summary_file"]
    with open(summary_file, "w", encoding="utf-8") as sf:
        sf.write("\n".join(summary_lines))
    log.info(f"摘要文件 {summary_file} 已写入")

    # 【新增】同时输出到标准输出，方便 GitHub Actions 读取
    print("\n" + "\n".join(summary_lines))


if __name__ == "__main__":
    main()
