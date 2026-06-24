#!/usr/bin/env python3
"""
Clash 订阅聚合脚本 — 稳定修复版 v7.1 (支持节点自动数字编号)
"""

import os
import re
import base64
import logging
import copy  # 导入深拷贝模块，防止多模板冲突
import yaml
import requests
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("clash-aggregator")

CONFIG = {
    "urls_file": "urls.txt",
    "template_dir": "template",
    "output_dir": "output",
    "request_timeout": 10,
    "max_workers": 4,
    "user_agent": "clash.meta",
    "disable_reality": True,   # ⭐关键开关（建议开）
    "target_regions": [
        "香港","HK","HongKong","Hong Kong",
        "新加坡","SG","Singapore",
        "日本","JP","Japan",
        "美国","US","United States",
        "台湾","TW","Taiwan",
        "🇭🇰","🇸🇬","🇯🇵","🇺🇸","🇹🇼",
    ],
    "proxy_group_name": "Proxy",
    "duplicates_dir": "TEMP",
}

TASKS = [
    ("template.yaml", "config.yaml", "summary.txt"),
    ("template-smart.yaml", "config-smart.yaml", "summary-smart.txt")
]

TARGET_REG = re.compile("|".join(map(re.escape, CONFIG["target_regions"])), re.I)

# ━━━━━━━━━━━━━━━━━━━ 工具 ━━━━━━━━━━━━━━━━━━━

def try_decode_base64(text: str) -> str:
    try:
        decoded = base64.b64decode(text.strip()).decode("utf-8")
        if "proxies:" in decoded:
            return decoded
    except:
        pass
    return text

def is_valid_hex(s: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]*", s))

def validate_proxy(p: dict):
    ptype = p.get("type", "").lower()

    if ptype not in ["vmess", "trojan", "ss", "hysteria2", "vless"]:
        return False, "协议不支持"

    # ⭐ 禁用 reality（最稳）
    if CONFIG["disable_reality"] and ptype == "vless":
        if "reality-opts" in p:
            return False, "禁用reality"

    # ⭐ reality 校验
    if ptype == "vless":
        ropts = p.get("reality-opts") or {}
        sid = ropts.get("short-id")

        if sid is not None:
            if isinstance(sid, list):
                return False, "short-id数组"

            sid = str(sid).strip()

            if len(sid) > 32:
                return False, "short-id过长"

            if not is_valid_hex(sid):
                return False, "short-id非法"

    return True, ""

# ━━━━━━━━━━━━━━━━━━━ 下载 ━━━━━━━━━━━━━━━━━━━

def fetch_single_sub(url: str):
    try:
        headers = {"User-Agent": CONFIG["user_agent"]}
        res = requests.get(url, timeout=CONFIG["request_timeout"], headers=headers)
        text = try_decode_base64(res.text)
        data = yaml.safe_load(text)

        proxies = data.get("proxies", []) if isinstance(data, dict) else []

        valid = []
        for p in proxies:
            if not isinstance(p, dict):
                continue

            ok, reason = validate_proxy(p)
            if not ok:
                log.debug(f"丢弃: {p.get('name')} - {reason}")
                continue

            name = str(p.get("name", "")).strip()
            server = str(p.get("server", "")).strip()
            port = str(p.get("port", "")).strip()

            if name and server and port and TARGET_REG.search(name):
                p["_key"] = f"{server}:{port}"
                valid.append(p)

        return valid, f"✔ {url} -> {len(valid)}"

    except Exception as e:
        return [], f"❌ {url} error"

# ━━━━━━━━━━━━━━━━━━━ 聚合 ━━━━━━━━━━━━━━━━━━━

def fetch_all(urls):
    all_nodes = []
    summary = []

    with ThreadPoolExecutor(max_workers=CONFIG["max_workers"]) as ex:
        for nodes, msg in ex.map(fetch_single_sub, urls):
            summary.append(msg)
            all_nodes.extend(nodes)

    final = []
    seen = set()

    for p in all_nodes:
        key = p["_key"]
        name = p["name"]

        if key in seen:
            name += " [复用]"

        p["name"] = name
        p.pop("_key", None)

        final.append(p)
        seen.add(key)

    return final, summary

# ━━━━━━━━━━━━━━━━━━━ 注入 ━━━━━━━━━━━━━━━━━━━

def inject(config, proxies):
    names = [p["name"] for p in proxies]

    for g in config.get("proxy-groups", []):
        if CONFIG["proxy_group_name"] in g.get("name", ""):
            # 保持原有策略组节点的合并逻辑
            g["proxies"] = list(set(g.get("proxies", []) + names))

# ━━━━━━━━━━━━━━━━━━━ 主函数 ━━━━━━━━━━━━━━━━━━━

def main():
    os.makedirs(CONFIG["output_dir"], exist_ok=True)

    if not os.path.exists(CONFIG["urls_file"]):
        log.error(f"找不到订阅源链接文件: {CONFIG['urls_file']}")
        return

    with open(CONFIG["urls_file"], encoding="utf-8") as f:
        urls = [x.strip() for x in f if x.strip()]

    nodes, summary = fetch_all(urls)
    total_nodes_count = len(nodes)

    # 动态计算编号所需位数（如超过999个节点则用4位，默认3位：001）
    width = max(3, len(str(total_nodes_count)))

    for tpl, out, summ in TASKS:
        path = os.path.join(CONFIG["template_dir"], tpl)
        if not os.path.exists(path):
            log.warning(f"模板文件不存在，跳过: {path}")
            continue

        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        # ⭐ 删除旧字段
        config.pop("global-client-fingerprint", None)

        # ⭐ 核心修改：深拷贝一份原始节点，并添加排序数字前缀
        task_nodes = copy.deepcopy(nodes)
        for index, p in enumerate(task_nodes, start=1):
            # 前缀格式形如: [001] 
            prefix = f"[{str(index).zfill(width)}] "
            p["name"] = prefix + p["name"]

        # 注入策略组与添加节点列表
        inject(config, task_nodes)
        config["proxies"] = task_nodes

        # 写入配置文件
        output_config_path = os.path.join(CONFIG["output_dir"], out)
        with open(output_config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, sort_keys=False)

        # 写入日志摘要
        output_summary_path = os.path.join(CONFIG["output_dir"], summ)
        with open(output_summary_path, "w", encoding="utf-8") as f:
            f.write("\n".join(summary))

        log.info(f"完成 {out}，节点数: {total_nodes_count}")

if __name__ == "__main__":
    main()
