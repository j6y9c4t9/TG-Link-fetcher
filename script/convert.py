#!/usr/bin/env python3
"""
调用本地 subconverter，将 urls.txt 中的订阅源转换为 Clash 配置。
转换完成后发送 Telegram 通知。
"""
import os
import sys
import time
import logging
import requests
import yaml
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("converter")

SUBCONVERTER_URL = "http://127.0.0.1:25500"

# 可选：自定义远程规则配置（ACL4SSR）
# 留空使用 subconverter 默认规则
# 常用选项：
#   https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/config/ACL4SSR_Online.ini
#   https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/config/ACL4SSR_Online_Full.ini
REMOTE_CONFIG = ""

# 北京时间时区
BJT = timezone(timedelta(hours=8))


def get_bjt_now():
    """获取北京时间字符串"""
    return datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S")


def get_raw_url():
    """拼接 output/clash.yaml 的 raw 下载地址"""
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo:
        return f"{server}/{repo}/raw/main/output/clash.yaml"
    return ""


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
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


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
    resp = requests.get(f"{SUBCONVERTER_URL}/sub", params=params, timeout=120)
    resp.raise_for_status()
    return resp.text


def validate_yaml(text):
    """验证输出是否为合法 YAML 且包含 proxies"""
    try:
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            return False, "输出不是合法的字典结构", 0
        if "proxies" not in data:
            return False, "输出中没有 proxies 字段", 0
        return True, "OK", len(data["proxies"])
    except yaml.YAMLError as e:
        return False, f"YAML 解析错误: {e}", 0


def send_tg_notify(message):
    """发送 Telegram 通知，未配置时静默跳过"""
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
    raw_url = get_raw_url()

    # ── 1. 读取订阅源 ──────────────────────────────────────
    if not os.path.exists("urls.txt"):
        msg = (
            f"❌ <b>订阅转换失败</b>\n"
            f"🕐 {now} (北京时间)\n"
            f"原因: urls.txt 不存在"
        )
        send_tg_notify(msg)
        sys.exit(1)

    urls = read_urls()
    if not urls:
        msg = (
            f"❌ <b>订阅转换失败</b>\n"
            f"🕐 {now} (北京时间)\n"
            f"原因: urls.txt 中无有效链接"
        )
        send_tg_notify(msg)
        sys.exit(1)
    log.info(f"读取到 {len(urls)} 个订阅源")

    # ── 2. 等待后端就绪 ────────────────────────────────────
    if not wait_for_backend(SUBCONVERTER_URL):
        msg = (
            f"❌ <b>订阅转换失败</b>\n"
            f"🕐 {now} (北京时间)\n"
            f"原因: subconverter 未就绪"
        )
        send_tg_notify(msg)
        sys.exit(1)

    # ── 3. 执行转换 ───────────────────────────────────────
    try:
        result = convert(urls)
    except Exception as e:
        msg = (
            f"❌ <b>订阅转换失败</b>\n"
            f"🕐 {now} (北京时间)\n"
            f"原因: {e}"
        )
        send_tg_notify(msg)
        sys.exit(1)

    # ── 4. 验证输出 ───────────────────────────────────────
    ok, reason, node_count = validate_yaml(result)
    if not ok:
        msg = (
            f"❌ <b>订阅转换失败</b>\n"
            f"🕐 {now} (北京时间)\n"
            f"原因: {reason}"
        )
        send_tg_notify(msg)
        sys.exit(1)

    # ── 5. 保存文件 ───────────────────────────────────────
    os.makedirs("output", exist_ok=True)
    out_path = os.path.join("output", "clash.yaml")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(result)

    elapsed = round(time.time() - start_time, 1)
    file_kb = round(len(result.encode("utf-8")) / 1024, 1)
    log.info(f"✅ 已保存至 {out_path}，{node_count} 个节点，{file_kb} KB")

    # ── 6. 写入 GitHub Actions 输出变量 ───────────────────
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as gh:
            gh.write(f"node_count={node_count}\n")
            gh.write(f"elapsed={elapsed}\n")
            gh.write(f"file_kb={file_kb}\n")
            gh.write(f"source_count={len(urls)}\n")

    # ── 7. Telegram 成功通知 ──────────────────────────────
    msg = (
        f"✅ <b>订阅转换完成</b>\n"
        f"🕐 {now} (北京时间)\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📡 订阅源: <b>{len(urls)}</b> 个\n"
        f"🔗 节点数: <b>{node_count}</b> 个\n"
        f"📦 文件大小: <b>{file_kb}</b> KB\n"
        f"⏱️ 耗时: <b>{elapsed}</b> 秒\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📥 订阅地址:\n"
        f"<code>{raw_url}</code>"
    )
    send_tg_notify(msg)


if __name__ == "__main__":
    main()
