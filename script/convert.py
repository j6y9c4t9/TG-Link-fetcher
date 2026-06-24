#!/usr/bin/env python3
"""
调用本地 subconverter，将 urls.txt 中的订阅源转换为 Clash 配置。
过滤指定地区节点后保存，并发送 Telegram 通知。
"""
import os
import sys
import glob
import time
import logging
import requests
import yaml
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("converter")

SUBCONVERTER_URL = "http://127.0.0.1:25500"

# 可选：自定义远程规则配置（ACL4SSR）
REMOTE_CONFIG = ""

BJT = timezone(timedelta(hours=8))

# ── 地区过滤配置 ───────────────────────────────────────────
# 匹配节点名称中的关键词（不区分大小写）
# 满足任意一组关键词即保留，全部不匹配则过滤掉
REGION_KEYWORDS = {
    "日本": ["日本", "JP", "Japan","🇯🇵"],
    "新加坡": ["新加坡", "SG", "Singapore","🇸🇬"],
    "美国": ["美国", "US", "United States", "UnitedStates","🇺🇸"],
    "香港": ["香港", "HK", "HongKong", "Hong Kong","🇭🇰"],
    "台湾": ["台湾", "TW", "Taiwan", "Formosa","🇹🇼"],
}


def get_bjt_now():
    return datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S")


def get_raw_url(filename):
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo:
        return f"{server}/{repo}/raw/main/output/{filename}"
    return ""


def wait_for_backend(url, timeout=30):
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
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def convert(urls, target="clash"):
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
    resp = requests.get(f"{SUBCONVERTER_URL}/sub", params=params, timeout=120)
    resp.raise_for_status()
    return resp.text


def validate_yaml(text):
    try:
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            return False, "输出不是合法的字典结构", 0
        if "proxies" not in data:
            return False, "输出中没有 proxies 字段", 0
        return True, "OK", len(data["proxies"])
    except yaml.YAMLError as e:
        return False, f"YAML 解析错误: {e}", 0


def filter_by_region(proxies):
    """
    按地区过滤节点。
    节点名称中包含配置中任意关键词的保留，否则过滤掉。
    """
    all_keywords = []
    for keywords in REGION_KEYWORDS.values():
        all_keywords.extend(keywords)

    filtered = []
    removed = 0

    for p in proxies:
        name = p.get("name", "").lower()
        if any(kw in name for kw in all_keywords):
            filtered.append(p)
        else:
            removed += 1

    log.info(f"地区过滤: {len(proxies)} → {len(filtered)} 个节点 (过滤掉 {removed} 个)")

    # 打印各地区统计
    for region, keywords in REGION_KEYWORDS.items():
        count = sum(
            1 for p in filtered
            if any(kw in p.get("name", "").lower() for kw in keywords)
        )
        log.info(f"  {region}: {count} 个")

    return filtered


def cleanup_output():
    """清理 output 目录中的旧 yaml 文件"""
    os.makedirs("output", exist_ok=True)
    for old_file in glob.glob(os.path.join("output", "*.yaml")):
        os.remove(old_file)
        log.info(f"已清理旧文件: {old_file}")


def send_tg_notify(message):
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.info("未配置 TELEGRAM_TOKEN / TELEGRAM_CHAT_ID，跳过通知")
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            log.info("Telegram 通知已发送")
        else:
            log.warning(f"Telegram 通知失败: {resp.status_code} {resp.text}")
    except Exception as e:
        log.warning(f"Telegram 通知异常: {e}")


def main():
    log.info(f"工作目录: {os.getcwd()}")
    start_time = time.time()
    now = get_bjt_now()
    raw_url = get_raw_url("clash.yaml")

    # ── 1. 读取订阅源 ──────────────────────────────────────
    if not os.path.exists("urls.txt"):
        msg = f"❌ <b>订阅转换失败</b>\n🕐 {now} (北京时间)\n原因: urls.txt 不存在"
        send_tg_notify(msg)
        sys.exit(1)

    urls = read_urls()
    if not urls:
        msg = f"❌ <b>订阅转换失败</b>\n🕐 {now} (北京时间)\n原因: urls.txt 中无有效链接"
        send_tg_notify(msg)
        sys.exit(1)
    log.info(f"读取到 {len(urls)} 个订阅源")

    # ── 2. 等待后端就绪 ────────────────────────────────────
    if not wait_for_backend(SUBCONVERTER_URL):
        msg = f"❌ <b>订阅转换失败</b>\n🕐 {now} (北京时间)\n原因: subconverter 未就绪"
        send_tg_notify(msg)
        sys.exit(1)

    # ── 3. 执行转换 ───────────────────────────────────────
    try:
        result = convert(urls)
    except Exception as e:
        msg = f"❌ <b>订阅转换失败</b>\n🕐 {now} (北京时间)\n原因: {e}"
        send_tg_notify(msg)
        sys.exit(1)

    # ── 4. 验证输出 ───────────────────────────────────────
    ok, reason, raw_count = validate_yaml(result)
    if not ok:
        msg = f"❌ <b>订阅转换失败</b>\n🕐 {now} (北京时间)\n原因: {reason}"
        send_tg_notify(msg)
        sys.exit(1)

    # ── 5. 地区过滤 ───────────────────────────────────────
    data = yaml.safe_load(result)
    filtered_proxies = filter_by_region(data["proxies"])

    if not filtered_proxies:
        msg = (
            f"❌ <b>订阅转换失败</b>\n"
            f"🕐 {now} (北京时间)\n"
            f"原因: 过滤后无剩余节点\n"
            f"原始节点 {raw_count} 个，均不匹配目标地区"
        )
        send_tg_notify(msg)
        sys.exit(1)

    data["proxies"] = filtered_proxies

    # 同时更新 proxy-groups 中引用的节点
    if "proxy-groups" in data:
        filtered_names = {p["name"] for p in filtered_proxies}
        for group in data["proxy-groups"]:
            if "proxies" in group and isinstance(group["proxies"], list):
                group["proxies"] = [
                    name for name in group["proxies"]
                    if not isinstance(name, str) or name in filtered_names
                ]

    result = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
    node_count = len(filtered_proxies)

    # ── 6. 清理旧文件并保存 ───────────────────────────────
    cleanup_output()
    out_path = os.path.join("output", "clash.yaml")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(result)

    elapsed = round(time.time() - start_time, 1)
    file_kb = round(len(result.encode("utf-8")) / 1024, 1)
    log.info(f"✅ 已保存至 {out_path}，{node_count} 个节点，{file_kb} KB")

    # ── 7. GitHub Actions 输出变量 ────────────────────────
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as gh:
            gh.write(f"node_count={node_count}\n")
            gh.write(f"elapsed={elapsed}\n")
            gh.write(f"file_kb={file_kb}\n")
            gh.write(f"source_count={len(urls)}\n")

    # ── 8. 各地区统计 ─────────────────────────────────────
    region_stats = []
    for region, keywords in REGION_KEYWORDS.items():
        count = sum(
            1 for p in filtered_proxies
            if any(kw in p.get("name", "").lower() for kw in keywords)
        )
        region_stats.append(f"  {region}: {count} 个")

    # ── 9. Telegram 成功通知 ──────────────────────────────
    msg = (
        f"✅ <b>订阅转换完成</b>\n"
        f"🕐 {now} (北京时间)\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📡 订阅源: <b>{len(urls)}</b> 个\n"
        f"🔗 原始节点: <b>{raw_count}</b> 个\n"
        f"🔗 过滤后: <b>{node_count}</b> 个\n"
        f"📦 文件大小: <b>{file_kb}</b> KB\n"
        f"⏱️ 耗时: <b>{elapsed}</b> 秒\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📊 地区统计:\n"
        + "\n".join(region_stats) + "\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📥 <a href=\"{raw_url}\">点击下载 clash.yaml</a>"
    )
    send_tg_notify(msg)


if __name__ == "__main__":
    main()
