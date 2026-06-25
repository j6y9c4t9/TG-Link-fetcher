#!/usr/bin/env python3
"""
调用本地 subconverter，使用指定的 INI 模板将 urls.txt 中的订阅源转换为完整的 Clash 配置。
支持本地 INI 文件或远程 INI 链接，并发送带有地区节点统计的 Telegram 通知。
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
from urllib.parse import unquote, quote, urlparse
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("converter")

SUBCONVERTER_URL = "http://127.0.0.1:25500"

# ── INI 模板配置 ──────────────────────────────────────────
# 如果你使用的是本地的 INI 文件，请确保该文件在工作目录下（例如本地有模板名为 config.ini）
# 如果是本地文件，这里写相对路径，例如: "config.ini"
# 如果是远程文件，继续保持 URL 格式
REMOTE_CONFIG = "https://raw.githubusercontent.com/j6y9c4t9/myclashrule/refs/heads/main/AlvinDad_NEW.ini"

BJT = timezone(timedelta(hours=8))

# ── 防止 YAML 把类似 "473277e2" 的节点 ID 当作科学计数法 ─────────────────
class CleanLoader(yaml.SafeLoader):
    pass

def _clean_float(loader, node):
    value = loader.construct_scalar(node)
    if re.match(r'^[0-9a-fA-F]+[eE][0-9a-fA-F]+$', value):
        return value
    return float(value)

CleanLoader.add_constructor('tag:yaml.org,2002:float', _clean_float)

class SafeStrDumper(yaml.SafeDumper):
    pass

def _represent_str(dumper, data):
    if re.match(r'^[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?$', data):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')
    return dumper.represent_str(data)

SafeStrDumper.add_representer(str, _represent_str)
# ─────────────────────────────────────────────────────────

# ── 地区统计关键字（仅用于 Telegram 通知中的数据统计） ──────────────────
REGION_KEYWORDS = {
    "日本": ["日本", "jp", "japan", "jpn", "东京", "大阪", "tokyo", "osaka", "🇯🇵"],
    "新加坡": ["新加坡", "sg", "singapore", "sgp", "狮城", "🇸🇬"],
    "美国": ["美国", "us", "united states", "unitedstates", "usa", "america", "🇺🇸"],
    "香港": ["香港", "hk", "hongkong", "hong kong", "hkg", "🇭🇰"],
    "台湾": ["台湾", "tw", "taiwan", "formosa", "tpe", "台北", "🇹🇼"],
}

def get_bjt_now():
    return datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S")

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

def extract_proxies_count(clash_yaml_text):
    """解析 Clash 配置文件并返回节点总数及各地区节点数量"""
    try:
        data = yaml.load(clash_yaml_text, Loader=CleanLoader)
        if isinstance(data, dict) and "proxies" in data and isinstance(data["proxies"], list):
            proxies = data["proxies"]
            total = len(proxies)
            
            # 统计各地区
            stats = {}
            for region, keywords in REGION_KEYWORDS.items():
                count = sum(
                    1 for p in proxies
                    if any(kw.lower() in p.get("name", "").lower() for kw in keywords)
                )
                stats[region] = count
            return total, stats
    except Exception as e:
        log.warning(f"解析配置文件节点统计失败: {e}")
    return 0, {r: 0 for r in REGION_KEYWORDS}

def convert_with_ini(urls, target="clash"):
    """将所有 URL 合并，直接调用 subconverter 配合 INI 模板生成完整配置"""
    # 将多个订阅链接用 '|' 拼接
    merged_urls = "|".join(urls)
    
    params = {
        "target": target,
        "url": merged_urls,
        "emoji": "true",
        "clash.doh": "true",
        "udp": "true",
    }
    
    # 判断 REMOTE_CONFIG 是本地路径还是网络 URL
    if REMOTE_CONFIG:
        if REMOTE_CONFIG.startswith("http://") or REMOTE_CONFIG.startswith("https://"):
            params["config"] = REMOTE_CONFIG
        else:
            # 如果是本地 INI 文件，subconverter 要求传入本地绝对路径或相对路径
            # 注意：本地 subconverter 读取本地文件需确保其有访问权限
            params["config"] = REMOTE_CONFIG

    log.info(f"正在请求 subconverter 转换，包含 {len(urls)} 个订阅源...")
    
    resp = requests.get(f"{SUBCONVERTER_URL}/sub", params=params, timeout=120)
    resp.raise_for_status()
    return resp.text.strip()

def cleanup_output():
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


# ═══════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════

def main():
    log.info(f"工作目录: {os.getcwd()}")
    start_time = time.time()
    now = get_bjt_now()

    # 1. 读取订阅源
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

    # 2. 等待后端就绪
    if not wait_for_backend(SUBCONVERTER_URL):
        msg = f"❌ <b>订阅转换失败</b>\n🕐 {now} (北京时间)\n原因: subconverter 未就绪"
        send_tg_notify(msg)
        sys.exit(1)

    # 3. 清理旧输出
    cleanup_output()

    # 4. 调用 subconverter 并结合 INI 生成完整配置
    try:
        full_config_text = convert_with_ini(urls)
    except Exception as e:
        log.error(f"subconverter 转换失败: {e}")
        msg = f"❌ <b>订阅转换失败</b>\n🕐 {now} (北京时间)\n原因: subconverter 核心报错: {str(e)[:100]}"
        send_tg_notify(msg)
        sys.exit(1)

    # 5. 统计节点数据
    node_count, region_counts = extract_proxies_count(full_config_text)
    if node_count == 0:
        msg = f"❌ <b>订阅转换失败</b>\n🕐 {now} (北京时间)\n原因: 转换后的配置文件中未检测到有效节点"
        send_tg_notify(msg)
        sys.exit(1)

    # 6. 保存完整配置文件
    out_path = os.path.join("output", "full_config.yaml")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full_config_text)
    
    elapsed = round(time.time() - start_time, 1)
    file_kb = round(len(full_config_text.encode("utf-8")) / 1024, 1)
    log.info(f"✅ 完整配置已生成至 {out_path}，{node_count} 个节点，{file_kb} KB")

    # 7. GitHub Actions 输出变量
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as gh:
            gh.write(f"node_count={node_count}\n")
            gh.write(f"elapsed={elapsed}\n")
            gh.write(f"file_kb={file_kb}\n")
            gh.write(f"source_count={len(urls)}\n")

    # 8. 整合各地区统计文本
    region_stats = []
    for region, count in region_counts.items():
        region_stats.append(f"  {region}: {count} 个")

    # 9. Telegram 成功通知
    full_url = get_main_url("full_config.yaml")

    msg = (
        f"✅ <b>订阅转换完成</b>\n"
        f"🕐 {now} (北京时间)\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚙️ 使用模板: <code>{os.path.basename(REMOTE_CONFIG)}</code>\n"
        f"🔗 包含订阅源: <b>{len(urls)}</b> 个\n"
        f"🔗 解析总节点: <b>{node_count}</b> 个\n"
        f"📦 文件大小: <b>{file_kb}</b> KB\n"
        f"⏱️ 耗时: <b>{elapsed}</b> 秒\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📊 转换后节点统计:\n"
        + "\n".join(region_stats) + "\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📥 <a href=\"{full_url}\">点击下载完整 Clash 配置</a> ({file_kb} KB)\n"
    )
    send_tg_notify(msg)


if __name__ == "__main__":
    main()
