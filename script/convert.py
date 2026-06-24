#!/usr/bin/env python3
"""
调用本地 subconverter，将 urls.txt 中的订阅源转换为 Clash 配置。
按订阅源分组保存原始节点，再合并过滤指定地区节点，发送 Telegram 通知。
"""
import os
import sys
import re
import glob
import time
import base64  
import logging
import requests
import yaml
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("converter")

SUBCONVERTER_URL = "http://127.0.0.1:25500"

REMOTE_CONFIG = ""

BJT = timezone(timedelta(hours=8))

# ── 地区过滤配置 ───────────────────────────────────────────
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
        return f"{server}/{repo}/raw/main/output/raw/{filename}"
    return ""


def get_main_url(filename):
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


def convert_single(url, target="clash"):
    """转换单个订阅源：优先自己抓取，失败则回退到 subconverter"""

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    content = None

    # ── 策略 1：直接抓取（轻量快速）────────────────────────
    try:
        log.info("  策略1: 直接抓取")
        fetch_resp = requests.get(url, timeout=30, headers=headers)
        fetch_resp.raise_for_status()
        content = fetch_resp.text.strip()
        log.info("  ✅ 直接抓取成功")
    except Exception as e:
        log.warning(f"  直接抓取失败: {e}")

    # ── 策略 2：回退到 subconverter 内置抓取 ────────────────
    if content is None:
        log.info("  策略2: 回退到 subconverter")
        try:
            params = {
                "target": target,
                "url": url,
                "emoji": "true",
                "clash.doh": "true",
                "udp": "true",
            }
            if REMOTE_CONFIG:
                params["config"] = REMOTE_CONFIG

            resp = requests.get(f"{SUBCONVERTER_URL}/sub", params=params, timeout=120)
            resp.raise_for_status()
            result = resp.text.strip()

            data = yaml.safe_load(result)
            if isinstance(data, dict) and "proxies" in data:
                log.info(f"  ✅ subconverter 成功: {len(data['proxies'])} 个节点")
                return result
            else:
                log.warning("  subconverter 返回无 proxies")
        except Exception as e:
            log.warning(f"  subconverter 失败: {e}")

    # ── 策略 3：对抓取到的内容做格式转换 ────────────────────
    if content is not None:
        # 尝试 base64 解码
        try:
            decoded = base64.b64decode(content).decode("utf-8").strip()
            if any(decoded.startswith(p) for p in (
                "vless://", "vmess://", "ss://", "trojan://",
                "hysteria", "hy2://", "tuic://", "ssr://",
            )):
                content = decoded
            elif "proxies:" in decoded:
                content = decoded
        except Exception:
            pass

        # 如果已经是 Clash YAML，直接返回
        try:
            data = yaml.safe_load(content)
            if isinstance(data, dict) and "proxies" in data:
                log.info(f"  ✅ 已是 Clash YAML: {len(data['proxies'])} 个节点")
                return content
        except yaml.YAMLError:
            pass

        # URI 列表 → 交给 subconverter 转换格式
        log.info("  内容是 URI 列表，交 subconverter 转换格式")
        encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        params = {
            "target": target,
            "url": encoded,
            "emoji": "true",
            "clash.doh": "true",
            "udp": "true",
        }
        if REMOTE_CONFIG:
            params["config"] = REMOTE_CONFIG

        resp = requests.get(f"{SUBCONVERTER_URL}/sub", params=params, timeout=120)
        resp.raise_for_status()
        return resp.text

    # 所有策略都失败
    raise RuntimeError("所有抓取策略均失败")


def extract_proxies(text):
    """从 YAML 文本中提取 proxies"""
    try:
        data = yaml.safe_load(text)
        if isinstance(data, dict) and "proxies" in data and isinstance(data["proxies"], list):
            return data["proxies"]
    except yaml.YAMLError:
        pass
    return []


def url_to_filename(index, url):
    """根据 URL 生成可读的文件名"""
    try:
        parsed = urlparse(url)
        domain = parsed.hostname or "unknown"
        # 只保留域名中的字母数字和点、横杠
        domain = re.sub(r"[^a-zA-Z0-9.\-]", "_", domain)
        return f"{index:02d}_{domain}.yaml"
    except Exception:
        return f"{index:02d}_source.yaml"


def sanitize_name(name, seen):
    """处理重名节点"""
    if name not in seen:
        seen.add(name)
        return name
    suffix = 2
    while f"{name}_{suffix}" in seen:
        suffix += 1
    new_name = f"{name}_{suffix}"
    seen.add(new_name)
    return new_name


def filter_by_region(proxies):
    """按地区过滤节点"""
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

    for region, keywords in REGION_KEYWORDS.items():
        count = sum(
            1 for p in filtered
            if any(kw in p.get("name", "").lower() for kw in keywords)
        )
        log.info(f"  {region}: {count} 个")

    return filtered


def save_yaml(data, path):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def cleanup_output():
    """清理 output 目录"""
    os.makedirs("output", exist_ok=True)
    # 清理 output 根目录的 yaml
    for old_file in glob.glob(os.path.join("output", "*.yaml")):
        os.remove(old_file)
        log.info(f"已清理旧文件: {old_file}")
    # 清理 raw 子目录
    raw_dir = os.path.join("output", "raw")
    if os.path.exists(raw_dir):
        for old_file in glob.glob(os.path.join(raw_dir, "*.yaml")):
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

    # ── 3. 清理旧输出 ─────────────────────────────────────
    cleanup_output()
    raw_dir = os.path.join("output", "raw")
    os.makedirs(raw_dir, exist_ok=True)

    # ── 4. 逐个抓取并分组保存 ─────────────────────────────
    all_proxies = []
    source_stats = []
    seen_names = set()

    for idx, url in enumerate(urls, 1):
        filename = url_to_filename(idx, url)
        out_path = os.path.join(raw_dir, filename)

        try:
            log.info(f"[{idx}/{len(urls)}] 抓取: {url}")
            text = convert_single(url)
            proxies = extract_proxies(text)
            count = len(proxies)

            # 去重处理
            unique = []
            dup = 0
            for p in proxies:
                name = p.get("name", "")
                if name in seen_names:
                    p["name"] = sanitize_name(name, seen_names)
                    dup += 1
                else:
                    seen_names.add(name)
                unique.append(p)

            # 保存该源的原始节点
            save_yaml({"proxies": unique}, out_path)
            source_stats.append({
                "index": idx,
                "url": url,
                "filename": filename,
                "count": count,
                "dup": dup,
                "status": "ok",
            })
            all_proxies.extend(unique)
            log.info(f"  ✅ {count} 个节点（{dup} 个重名已处理）→ raw/{filename}")

        except requests.exceptions.Timeout:
            log.error(f"  ❌ 超时，跳过")
            source_stats.append({"index": idx, "url": url, "filename": filename, "count": 0, "dup": 0, "status": "超时"})
        except requests.exceptions.HTTPError as e:
            log.error(f"  ❌ HTTP 错误 {e.response.status_code}")
            source_stats.append({"index": idx, "url": url, "filename": filename, "count": 0, "dup": 0, "status": f"HTTP {e.response.status_code}"})
        except Exception as e:
            log.error(f"  ❌ 未知错误: {e}")
            source_stats.append({"index": idx, "url": url, "filename": filename, "count": 0, "dup": 0, "status": str(e)[:50]})

    raw_total = len(all_proxies)
    if raw_total == 0:
        msg = f"❌ <b>订阅转换失败</b>\n🕐 {now} (北京时间)\n原因: 所有源均未获取到节点"
        send_tg_notify(msg)
        sys.exit(1)

    log.info(f"原始节点合计: {raw_total} 个")

    # ── 5. 地区过滤 ───────────────────────────────────────
    filtered_proxies = filter_by_region(all_proxies)

    if not filtered_proxies:
        msg = (
            f"❌ <b>订阅转换失败</b>\n"
            f"🕐 {now} (北京时间)\n"
            f"原因: 过滤后无剩余节点\n"
            f"原始节点 {raw_total} 个，均不匹配目标地区"
        )
        send_tg_notify(msg)
        sys.exit(1)

    # ── 6. 保存合并后的过滤结果 ───────────────────────────
    final_data = {"proxies": filtered_proxies}
    result_text = yaml.dump(final_data, allow_unicode=True, default_flow_style=False, sort_keys=False)
    node_count = len(filtered_proxies)

    # 同步更新 proxy-groups 中的引用（如果有）
    filtered_names = {p["name"] for p in filtered_proxies}
    # 注意：合并后的结果不包含 rules/proxy-groups，只有 proxies
    # 所以这里不需要处理 proxy-groups

    out_path = os.path.join("output", "clash.yaml")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(result_text)

    elapsed = round(time.time() - start_time, 1)
    file_kb = round(len(result_text.encode("utf-8")) / 1024, 1)
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
    source_lines = ""
    for s in source_stats:
        if s["status"] == "ok":
            raw_url = get_raw_url(s["filename"])
            source_lines += f"  📡 <a href=\"{raw_url}\">源 {s['index']}</a>: {s['count']} 个节点\n"
        else:
            source_lines += f"  📡 源 {s['index']}: ❌ {s['status']}\n"

    main_url = get_main_url("clash.yaml")

    msg = (
        f"✅ <b>订阅转换完成</b>\n"
        f"🕐 {now} (北京时间)\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🔗 原始节点: <b>{raw_total}</b> 个\n"
        f"🔗 过滤后: <b>{node_count}</b> 个\n"
        f"📦 文件大小: <b>{file_kb}</b> KB\n"
        f"⏱️ 耗时: <b>{elapsed}</b> 秒\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📊 地区统计:\n"
        + "\n".join(region_stats) + "\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📋 各源明细:\n"
        f"{source_lines}"
        f"━━━━━━━━━━━━━━━━\n"
        f"📥 <a href=\"{main_url}\">点击下载 clash.yaml</a>"
    )
    send_tg_notify(msg)


if __name__ == "__main__":
    main()
