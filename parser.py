#!/usr/bin/env python3
"""
全新的 Clash 节点无损合并器
任务：并发抓取订阅源 ➔ 解码并提取原始 proxies 节点列表 ➔ 汇总输出为大原材料文件
"""
import os
import base64
import logging
import requests
import yaml
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("raw-aggregator")

URLS_FILE = "urls.txt"
OUTPUT_FILE = "output/raw_nodes.yaml"
USER_AGENT = "clash.meta"
TIMEOUT = 12

def decode_source_text(text: str) -> str:
    """自动兼容处理 Base64 编码或明文 YAML 编码"""
    cleaned = text.strip()
    if "proxies:" in cleaned or "- name:" in cleaned:
        return cleaned
    try:
        decoded = base64.b64decode(cleaned).decode("utf-8")
        if "proxies:" in decoded or "- name:" in decoded:
            return decoded
    except:
        pass
    return cleaned

def fetch_source_proxies(url: str) -> list:
    """纯粹的下载与字典提取，不碰任何具体节点的具体参数"""
    url = url.strip()
    if not url or url.startswith("#"):
        return []
    
    headers = {"User-Agent": USER_AGENT}
    try:
        log.info(f"正在抓取源: {url[:50]}...")
        res = requests.get(url, headers=headers, timeout=TIMEOUT)
        res.raise_for_status()
        
        pure_text = decode_source_text(res.text)
        data = yaml.safe_load(pure_text)
        
        if data and isinstance(data, dict) and "proxies" in data:
            proxies_list = data["proxies"]
            if isinstance(proxies_list, list):
                log.info(f"成功获取 {len(proxies_list)} 个原始节点")
                return proxies_list
    except Exception as e:
        log.error(f"抓取失败: {url[:40]} | 原因: {str(e)[:30]}")
    return []

def main():
    os.makedirs("output", exist_ok=True)
    if not os.path.exists(URLS_FILE):
        log.error(f"未找到订阅源列表文件: {URLS_FILE}")
        return

    with open(URLS_FILE, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    log.info(f"开始并发同步 {len(urls)} 个订阅源...")
    collected_proxies = []
    
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(fetch_source_proxies, urls):
            collected_proxies.extend(result)

    # 直接无损导出大原材料包
    raw_config = {"proxies": collected_proxies}
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        yaml.dump(raw_config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        
    log.info(f"🟢 原材料大合流完成，共聚合 {len(collected_proxies)} 个原始节点。已写入 {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
