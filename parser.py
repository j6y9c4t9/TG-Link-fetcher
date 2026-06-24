#!/usr/bin/env python3
"""
Clash 订阅聚合脚本 — 多模板多输出版（修复版 v10 — 强制字符串引号版）
功能：多源并发聚合 → 彻底修复 Reality short-id 科学计数法转换问题 → 过滤脏节点参数 → 多模板输出
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

# 自定义解析器，防止 YAML 自动把形如 473277e2 的文本转成科学计数法浮点数
class SafeLoaderWithQuotedStrings(yaml.SafeLoader):
    pass

# 覆盖 SafeLoader 对可能引发科学计数法误判的数字规则
SafeLoaderWithQuotedStrings.add_implicit_resolver(
    'tag:yaml.org,2002:float',
    re.compile(r'''^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+]?[0-9]+)?
                    |[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)
                    |\.[0-9_]+(?:[eE][-+]?[0-9]+)?
                    |[-+]?\.inf|[-+]?\.NaN)$''', re.X),
    list('-+0123456789.')
)

# 强制让所有带有特定字符的 short-id 导出时必须以带单/双引号的字符串形式展现
def represent_quoted_str(dumper, data):
    # 只要包含了字符或者长得像十六进制的，一律加引号包裹
    if re.match(r'^[0-9a-fA-F]+$', data):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)

yaml.SafeDumper.add_representer(str, represent_quoted_str)


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
        
        # 使用自定义的解析器读取，阻断科学计数法对 short-id 的自动吞噬
        data = yaml.load(res_text, Loader=SafeLoaderWithQuotedStrings)

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

                    # 1. 非 TLS 节点参数清洗
                    if ptype in ["vless", "trojan", "ss"] and not is_tls:
                        if "reality-opts" in p:
                            del p["reality-opts"]
                        if "client-fingerprint" in p:
                            del p["client-fingerprint"]
                    
                    # 2. 严格校正和强转 REALITY 节点的 short-id 格式
                    if "reality-opts" in p and isinstance(p["reality-opts"], dict):
                        ro = p["reality-opts"]
                        if "short-id" in ro:
                            # 转换为纯小写字符串，排除浮点数转字符串后带点（如 "4.7327e+07"）的干扰
                            raw_sid = ro["short-id"]
                            if isinstance(raw_sid, float) or isinstance(raw_sid, int):
                                # 如果已经被不幸识别成了数字，尝试还原成纯文本形式
                                sid = f"{raw_sid:g}"
                            else:
                                sid = str(raw_sid).strip()
                            
                            # 如果包含科学计数法残余符号，尝试修复
                            if "e+" in sid.lower():
                                try:
                                    sid = f"{int(float(sid)):x}"
                                except:
                                    pass

                            # 校验十六进制
                            if len(sid) % 2 != 0 or not re.match(r"^[0-9a-fA-F]*$", sid):
                                log.debug(f"修正节点 [{name}] 的非法 short-id: '{sid}' -> ''")
                                p["reality-opts"]["short-id"] = ""
                            else:
                                p["reality-opts"]["short-id"] = str(sid)
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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
        # 使用自定义的 Loader 进行深拷贝，防止转换丢失
        node_str = yaml.dump(p)
        node_copy = yaml.load(node_str, Loader=SafeLoaderWithQuotedStrings)
        
        server_key = node_copy.get("_source_key", "")
        name = node_copy.get("name", "")

        is_dup_server = False
        if server_key in seen_servers:
            duplicate_count += 1
            is_dup_server = True
            duplicate_nodes.append({
                "name": name,
                "server_key": server_key,
            })

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
            config = yaml.load(f, Loader=SafeLoaderWithQuotedStrings) or {}

        # 深度复制节点保证转换安全
        node_str = yaml.dump(filtered_proxies)
        current_proxies = yaml.load(node_str, Loader=SafeLoaderWithQuotedStrings)
        
        inject_into_proxy_groups(config, current_proxies)
        config["proxies"] = current_proxies

        if "global-client-fingerprint" in config:
            del config["global-client-fingerprint"]
            log.info("🧹 已成功从输出配置中剥离废弃的全局 `global-client-fingerprint` 属性")

        # 采用 SafeDumper 写入文件，此时自定义的引述器（representer）会强制给 short-id 加双引号
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False, width=4096)
        log.info(f"🟢 配置文件已成功保存至: {output_path}")

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
