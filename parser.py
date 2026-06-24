#!/usr/bin/env python3
import os
import logging
import yaml
import requests
import base64

# 强制开启 DEBUG 日志，这样 Actions 的 Log 里会显示所有细节
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
log = logging.getLogger("debug-aggregator")

def main():
    # 1. 强制检查环境
    log.info(f"当前工作目录: {os.getcwd()}")
    if not os.path.exists("urls.txt"):
        log.error("致命错误: 找不到 urls.txt 文件！")
        return

    # 2. 读取链接
    with open("urls.txt", "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    
    log.info(f"检测到 {len(urls)} 个订阅源")
    
    all_proxies = []
    for url in urls:
        log.info(f"正在尝试抓取: {url}")
        try:
            res = requests.get(url, timeout=15)
            res.raise_for_status()
            
            # 简单的 Base64 检测
            text = res.text.strip()
            if not text.startswith("proxies:"):
                try:
                    text = base64.b64decode(text).decode("utf-8")
                except:
                    pass
            
            data = yaml.safe_load(text)
            if data and isinstance(data, dict) and "proxies" in data:
                all_proxies.extend(data["proxies"])
                log.info(f"✅ 成功从该源获取到节点")
            else:
                log.warning(f"⚠️ 该源无 proxies 字段或格式错误")
        except Exception as e:
            log.error(f"❌ 抓取失败: {e}")

    # 3. 强制写入
    if not os.path.exists("output"):
        os.makedirs("output")
        
    final_path = os.path.join("output", "raw_nodes.yaml")
    with open(final_path, "w", encoding="utf-8") as f:
        yaml.dump({"proxies": all_proxies}, f, allow_unicode=True)
    
    if os.path.exists(final_path):
        log.info(f"🟢 最终文件已生成: {final_path}，包含 {len(all_proxies)} 个节点")
    else:
        log.error("🔴 文件写入失败！")

if __name__ == "__main__":
    main()
