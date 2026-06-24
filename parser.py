#!/usr/bin/env python3
"""
Clash 节点物理清洗器 (v15 重新开始版)
功能：并发抓取订阅 ──> 强力净化脏参数 ──> 文本级强制修复 short-id ──> 输出纯节点 YAML
"""
import os
import re
import base64
import logging
import yaml
import requests
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("node-cleaner")

CONFIG = {
    "urls_file": "urls.txt",
    "output_dir": "output",
    "request_timeout": 10,
    "max_workers": 4,
    "user_agent": "clash.meta",
    # 筛选你想要的区域节点
    "target_regions": ["香港", "HK", "HongKong", "新加坡", "SG", "Singapore", "日本", "JP", "Japan", "美国", "US", "台湾", "TW", "Taiwan", "🇭🇰", "🇸🇬", "🇯🇵", "🇺🇸", "🇹🇼"],
}

def build_target_regex(keywords: list[str]) -> re.Pattern:
    return re.compile("|".join([re.escape(kw) for kw in keywords]), re.IGNORECASE)

TARGET_REG = build_target_regex(CONFIG["target_regions"])

def try_decode_base64(text: str) -> str:
    cleaned = text.strip()
    if "proxies:" in cleaned or "- name:" in cleaned: return cleaned
    try:
        decoded = base64.b64decode(cleaned).decode("utf-8")
        if "proxies:" in decoded or "- name:" in decoded: return decoded
    except: pass
    return cleaned

def fetch_single_sub(url: str) -> list[dict]:
    headers = {"User-Agent": CONFIG["user_agent"]}
    try:
        res = requests.get(url, headers=headers, timeout=CONFIG["request_timeout"])
        res.raise_for_status()
        res_text = try_decode_base64(res.text)
        data = yaml.safe_load(res_text)
        if not data or not isinstance(data, dict): return []
        proxies = data.get("proxies", [])
        if not isinstance(proxies, list): return []

        valid_proxies = []
        for p in proxies:
            if not isinstance(p, dict): continue
            name, server, port = str(p.get("name", "")).strip(), str(p.get("server", "")).strip(), str(p.get("port", "")).strip()
            if name and server and port and TARGET_REG.search(name):
                ptype = p.get("type", "")
                is_tls = p.get("tls", False)
                network = p.get("network", "")

                # 🧼 降维打击：剥离非 TLS 节点的现实（Reality）脏残留
                if network in ["ws", "grpc"] or p.get("ws-opts") or p.get("grpc-opts"):
                    if "reality-opts" in p: del p["reality-opts"]
                if ptype in ["vless", "trojan", "ss"] and not is_tls:
                    if "reality-opts" in p: del p["reality-opts"]
                    if "client-fingerprint" in p: del p["client-fingerprint"]
                
                # 🛑 核心防崩溃：解决短 ID 被解析为列表/数组的灾难
                if "reality-opts" in p and isinstance(p["reality-opts"], dict):
                    ro = p["reality-opts"]
                    if "short-id" in ro:
                        sid_raw = ro["short-id"]
                        if isinstance(sid_raw, list): sid_raw = sid_raw[0] if sid_raw else ""
                        sid = str(sid_raw).strip()
                        if "." in sid: sid = sid.split(".")[0]
                        if "e+" in sid.lower():
                            try: sid = f"{int(float(sid)):x}"
                            except: pass
                        if len(sid) % 2 != 0 or not re.match(r"^[0-9a-fA-F]*$", sid): p["reality-opts"]["short-id"] = ""
                        else: p["reality-opts"]["short-id"] = str(sid)

                p["_source_key"] = f"{server}:{port}"
                valid_proxies.append(p)
        return valid_proxies
    except Exception as e:
        log.error(f"❌ 下载/解析失败: {url[:30]}... 错误: {str(e)[:30]}")
        return []

def main():
    os.makedirs(CONFIG["output_dir"], exist_ok=True)
    if not os.path.exists(CONFIG["urls_file"]):
        log.error(f"找不到 {CONFIG["urls_file"]} 文件！")
        return

    urls = []
    with open(CONFIG["urls_file"], "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    all_raw_proxies = []
    with ThreadPoolExecutor(max_workers=CONFIG["max_workers"]) as pool:
        for res in pool.map(fetch_single_sub, urls): all_raw_proxies.extend(res)

    final_proxies, seen_servers, seen_names, node_idx = [], set(), set(), 1
    for p in all_raw_proxies:
        node_copy = {k: (v if k != "reality-opts" else v.copy()) for k, v in p.items()}
        server_key = node_copy.get("_source_key", "")
        name = node_copy.get("name", "")
        
        is_dup = server_key in seen_servers
        temp_name = f"{name} [复用]" if is_dup else name
        final_name = f"[{node_idx:03d}] {temp_name}"
        
        base_name = final_name
        counter = 1
        while final_name in seen_names:
            final_name = f"{base_name} #{counter}"
            counter += 1

        if "_source_key" in node_copy: del node_copy["_source_key"]
        node_copy["name"] = final_name
        final_proxies.append(node_copy)
        seen_servers.add(server_key)
        seen_names.add(final_name)
        node_idx += 1

    # 只导出纯节点列表
    config = {"proxies": final_proxies}
    final_yaml_text = yaml.dump(config, allow_unicode=True, sort_keys=False, default_flow_style=False, width=4096)
    
    # 🔒 终审文本清洗：通过正则强制确保 short-id 带有双引号且不换行
    final_yaml_text = re.sub(r'short-id:\s*\n\s*-\s*["\']?([0-9a-fA-F]+)["\']?', r'short-id: "\1"', final_yaml_text)
    final_yaml_text = re.sub(r'(\s+)short-id:\s*["\']?([0-9a-fA-F]+)["\']?\b', r'\1short-id: "\2"', final_yaml_text)

    with open(os.path.join(CONFIG["output_dir"], "nodes.yaml"), "w", encoding="utf-8") as f:
        f.write(final_yaml_text)
    log.info(f"🟢 成功洗白 {len(final_proxies)} 个纯净节点并写入 output/nodes.yaml")

if __name__ == "__main__":
    main()
