#!/usr/bin/env python3
"""
调用本地 subconverter API，将 urls.txt 中的订阅源转换为 Clash 配置。
"""
import os
import sys
import time
import logging
import urllib.parse
import requests
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("converter")

SUBCONVERTER_URL = "http://127.0.0.1:25500"

# ── 可选：自定义远程规则配置（ACL4SSR）──────────────────────
# 留空则使用 subconverter 默认规则
# 常用选项：
#   ACL4SSR_Online       — 标准分组
#   ACL4SSR_Online_Full  — 完整分组（含流媒体、AI、Telegram 等）
REMOTE_CONFIG = ""


def wait_for_backend(url, timeout=30):
    """等待 subconverter 启动"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{url}/version", timeout=2)
            if r.status_code == 200:
                log.info(f"subconverter 已就绪 (v{r.text.strip()})")
                return True
        except requests.ConnectionError:
            pass
        time.sleep(1)
    return False


def read_urls(path="urls.txt"):
    """读取 urls.txt"""
    with open(path, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    return urls


def convert(urls, target="clash"):
    """调用 subconverter 转换订阅"""
    params = {
        "target": target,
        "url": "|".join(urls),
        "emoji": "true",
        "clash.doh": "true",
        "udp": "true",
        "filename": "clash",
    }

    if REMOTE_CONFIG:
        params["config"] = REMOTE_CONFIG

    log.info(f"转换中：{len(urls)} 个源 → {target}")
    resp = requests.get(
        f"{SUBCONVERTER_URL}/sub",
        params=params,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.text


def validate_yaml(text):
    """简单验证输出是否为合法 YAML 且包含 proxies"""
    try:
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            return False, "输出不是合法的字典结构"
        if "proxies" not in data:
            return False, "输出中没有 proxies 字段"
        return True, f"{len(data['proxies'])} 个节点"
    except yaml.YAMLError as e:
        return False, f"YAML 解析错误: {e}"


def main():
    log.info(f"工作目录: {os.getcwd()}")

    # 1. 读取订阅源
    if not os.path.exists("urls.txt"):
        log.error("urls.txt 不存在")
        sys.exit(1)

    urls = read_urls()
    if not urls:
        log.error("urls.txt 中没有有效链接")
        sys.exit(1)
    log.info(f"读取到 {len(urls)} 个订阅源")

    # 2. 等待 subconverter 就绪
    if not wait_for_backend(SUBCONVERTER_URL):
        log.error("subconverter 未在规定时间内启动")
        sys.exit(1)

    # 3. 执行转换
    result = convert(urls)

    # 4. 验证输出
    ok, msg = validate_yaml(result)
    if not ok:
        log.error(f"输出验证失败: {msg}")
        log.debug(f"原始响应前 500 字:\n{result[:500]}")
        sys.exit(1)
    log.info(f"输出验证通过: {msg}")

    # 5. 保存文件
    os.makedirs("output", exist_ok=True)
    out_path = os.path.join("output", "clash.yaml")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(result)
    log.info(f"✅ 已保存至 {out_path} ({len(result)} bytes)")


if __name__ == "__main__":
    main()
