#!/usr/bin/env python3
"""
抓取 urls.txt 中的订阅源，按订阅源分组保存原始节点，
再合并过滤指定地区节点，发送 Telegram 通知。
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
import json
from urllib.parse import unquote, urlparse
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("converter")

SUBCONVERTER_URL = "http://127.0.0.1:25500"
REMOTE_CONFIG = "https://raw.githubusercontent.com/j6y9c4t9/myclashrule/refs/heads/main/AlvinDad_NEW.ini"

BJT = timezone(timedelta(hours=8))

# ── YAML 安全处理 ──────────────────────────────────────────
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

# ── 地区过滤配置 ───────────────────────────────────────────
DEFAULT_REGION_KEYWORDS = {}

def load_region_keywords(json_path="regions.json"):
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                log.info(f"✅ 成功从 {json_path} 加载了 {len(data)} 个地区的过滤规则")
                return data
            else:
                log.warning(f"⚠️ {json_path} 格式不正确，使用默认过滤规则")
        except Exception as e:
            log.error(f"❌ 读取 {json_path} 失败: {e}，使用默认过滤规则")
    else:
        log.info(f"ℹ️ 未检测到 {json_path}，使用默认过滤规则")
    return DEFAULT_REGION_KEYWORDS

REGION_KEYWORDS = load_region_keywords("regions.json")

# ── subconverter 懒加载 ────────────────────────────────────
_backend_ready = None  # None = 未检查, True/False = 已检查

def ensure_backend():
    """只在第一次需要时检查 subconverter 是否就绪，成功后缓存结果"""
    global _backend_ready
    if _backend_ready is True:
        return True
    if _backend_ready is False:
        return False

    log.info("检测 subconverter 是否可用...")
    for _ in range(10):
        try:
            r = requests.get(f"{SUBCONVERTER_URL}/version", timeout=2)
            if r.status_code == 200:
                log.info(f"subconverter 已就绪 (v{r.text.strip()})")
                _backend_ready = True
                return True
        except requests.ConnectionError:
            pass
        time.sleep(1)

    log.warning("subconverter 不可用，将仅使用本地解析")
    _backend_ready = False
    return False


def get_bjt_now():
    return datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S")


def get_raw_url(filename):
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    return f"{server}/{repo}/raw/main/output/raw/{filename}" if repo else ""


def get_main_url(filename):
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    return f"{server}/{repo}/raw/main/output/{filename}" if repo else ""


def read_urls(path="urls.txt"):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


# ═══════════════════════════════════════════════════════════
#  URI 解析器
# ═══════════════════════════════════════════════════════════

def parse_vless_uri(uri):
    try:
        name = ""
        if "#" in uri:
            uri, name = uri.rsplit("#", 1)
            name = unquote(name)
        raw = uri[len("vless://"):]
        if "?" in raw:
            main_part, query_str = raw.split("?", 1)
        else:
            main_part, query_str = raw, ""
        uuid, server_port = main_part.split("@", 1)
        if server_port.startswith("["):
            end = server_port.index("]")
            server = server_port[1:end]
            port = int(server_port[end + 2:]) if server_port[end + 1:] else 443
        elif ":" in server_port:
            server, port = server_port.rsplit(":", 1)
            port = int(port)
        else:
            server, port = server_port, 443
        params = {}
        if query_str:
            for p in query_str.split("&"):
                if "=" in p:
                    k, v = p.split("=", 1)
                    params[k] = unquote(v)
        proxy = {
            "name": name or f"vless-{server}",
            "type": "vless",
            "server": server,
            "port": port,
            "uuid": uuid,
            "udp": True,
        }
        security = params.get("security", "none")
        if security in ("tls", "reality"):
            proxy["tls"] = True
            if params.get("sni"):
                proxy["servername"] = params["sni"]
            if params.get("fp"):
                proxy["client-fingerprint"] = params["fp"]
            if params.get("alpn"):
                proxy["alpn"] = params["alpn"].split(",")
        if security == "reality":
            ro = {}
            if params.get("pbk"):
                ro["public-key"] = params["pbk"]
            if params.get("sid"):
                ro["short-id"] = params["sid"]
            if ro:
                proxy["reality-opts"] = ro
        if params.get("flow"):
            proxy["flow"] = params["flow"]
        if params.get("fragment"):
            proxy["fragment"] = params["fragment"]
        if params.get("ech"):
            proxy["ech-opts"] = {"enable": True}
        transport = params.get("type", "tcp")
        if transport == "ws":
            proxy["network"] = "ws"
            ws = {}
            if params.get("path"):
                ws["path"] = params["path"]
            if params.get("host"):
                ws["headers"] = {"Host": params["host"]}
            if ws:
                proxy["ws-opts"] = ws
        elif transport == "grpc":
            proxy["network"] = "grpc"
            if params.get("serviceName"):
                proxy["grpc-opts"] = {"grpc-service-name": params["serviceName"]}
        elif transport == "h2":
            proxy["network"] = "h2"
            h2 = {}
            if params.get("host"):
                h2["host"] = [params["host"]]
            if params.get("path"):
                h2["path"] = params["path"]
            if h2:
                proxy["h2-opts"] = h2
        elif transport == "quic":
            proxy["network"] = "quic"
            if params.get("quicSecurity"):
                proxy["quic-opts"] = {
                    "security": params["quicSecurity"],
                    "key": params.get("key", ""),
                }
        elif transport == "tcp":
            if params.get("headerType") == "http":
                proxy["network"] = "tcp"
                proxy["tcp-opts"] = {
                    "header": {
                        "type": "http",
                        "request": {
                            "path": [params.get("path", "/")],
                            "headers": {"Host": [params.get("host", "")]},
                        },
                    },
                }
        return proxy
    except Exception as e:
        log.debug(f"解析 vless 失败: {e}")
        return None


def parse_vmess_uri(uri):
    try:
        raw = uri[len("vmess://"):]
        missing_padding = len(raw) % 4
        if missing_padding:
            raw += "=" * (4 - missing_padding)
        info = yaml.load(base64.b64decode(raw).decode("utf-8"), Loader=CleanLoader)
        if not isinstance(info, dict):
            return None
        proxy = {
            "name": info.get("ps", "vmess-node"),
            "type": "vmess",
            "server": info.get("add", ""),
            "port": int(info.get("port", 443)),
            "uuid": info.get("id", ""),
            "alterId": int(info.get("aid", 0)),
            "cipher": info.get("scy", "auto"),
            "udp": True,
        }
        if info.get("tls") == "tls":
            proxy["tls"] = True
            if info.get("sni"):
                proxy["servername"] = info["sni"]
        net = info.get("net", "tcp")
        if net == "ws":
            proxy["network"] = "ws"
            ws = {}
            if info.get("path"):
                ws["path"] = info["path"]
            if info.get("host"):
                ws["headers"] = {"Host": info["host"]}
            if ws:
                proxy["ws-opts"] = ws
        elif net == "grpc":
            proxy["network"] = "grpc"
            if info.get("path"):
                proxy["grpc-opts"] = {"grpc-service-name": info["path"]}
        elif net == "h2":
            proxy["network"] = "h2"
            h2 = {}
            if info.get("host"):
                h2["host"] = [info["host"]]
            if info.get("path"):
                h2["path"] = info["path"]
            if h2:
                proxy["h2-opts"] = h2
        return proxy
    except Exception as e:
        log.debug(f"解析 vmess 失败: {e}")
        return None


def parse_ss_uri(uri):
    try:
        raw = uri[len("ss://"):]
        name = ""
        if "#" in raw:
            raw, name = raw.rsplit("#", 1)
            name = unquote(name)
        if "@" in raw:
            userinfo, serverinfo = raw.rsplit("@", 1)
            try:
                decoded = base64.b64decode(userinfo + "==").decode("utf-8")
                method, password = decoded.split(":", 1)
            except Exception:
                method, password = userinfo.split(":", 1)
            server, port = serverinfo.rsplit(":", 1)
            port = port.split("?")[0]
        else:
            decoded = base64.b64decode(raw.split("?")[0] + "==").decode("utf-8")
            method_password, serverinfo = decoded.rsplit("@", 1)
            method, password = method_password.split(":", 1)
            server, port = serverinfo.rsplit(":", 1)
        return {
            "name": name or f"ss-{server}",
            "type": "ss",
            "server": server,
            "port": int(port),
            "cipher": method,
            "password": password,
            "udp": True,
        }
    except Exception as e:
        log.debug(f"解析 ss 失败: {e}")
        return None


def parse_trojan_uri(uri):
    try:
        raw = uri[len("trojan://"):]
        name = ""
        if "#" in raw:
            raw, name = raw.rsplit("#", 1)
            name = unquote(name)
        params = {}
        if "?" in raw:
            raw, query = raw.split("?", 1)
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        userinfo, serverinfo = raw.rsplit("@", 1)
        password = userinfo
        server, port = serverinfo.rsplit(":", 1)
        port = port.split("?")[0]
        proxy = {
            "name": name or f"trojan-{server}",
            "type": "trojan",
            "server": server,
            "port": int(port),
            "password": password,
            "udp": True,
        }
        if params.get("sni"):
            proxy["sni"] = params["sni"]
        if params.get("peer"):
            proxy["sni"] = proxy.get("sni", params["peer"])
        net = params.get("type", "tcp")
        if net == "ws":
            proxy["network"] = "ws"
            ws = {}
            if params.get("path"):
                ws["path"] = unquote(params["path"])
            if params.get("host"):
                ws["headers"] = {"Host": params["host"]}
            if ws:
                proxy["ws-opts"] = ws
        return proxy
    except Exception as e:
        log.debug(f"解析 trojan 失败: {e}")
        return None


PARSERS = {
    "vless://": parse_vless_uri,
    "vmess://": parse_vmess_uri,
    "ss://": parse_ss_uri,
    "trojan://": parse_trojan_uri,
}


def parse_uri_list(content):
    proxies = []
    for line in content.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        for prefix, parser in PARSERS.items():
            if line.startswith(prefix):
                proxy = parser(line)
                if proxy:
                    proxies.append(proxy)
                break
    return proxies


# ═══════════════════════════════════════════════════════════
#  验证、提取、转换
# ═══════════════════════════════════════════════════════════

def validate_proxies(proxies):
    """返回新的列表，不修改原始数据"""
    valid = []
    removed = 0
    for p in proxies:
        name = p.get("name", "unknown")
        if not p.get("server") or not p.get("port"):
            log.warning(f"  过滤 [{name}]: 缺少 server/port")
            removed += 1
            continue
        port = p["port"]
        if not (1 <= port <= 65535):
            log.warning(f"  过滤 [{name}]: 端口越界 {port}")
            removed += 1
            continue
        # 深拷贝避免修改原始 dict
        p = dict(p)
        reality_opts = p.get("reality-opts", {})
        if reality_opts:
            sid = str(reality_opts.get("short-id", ""))
            pk = reality_opts.get("public-key", "")
            if sid and not re.match(r'^[0-9a-fA-F]{1,64}$', sid):
                log.warning(f"  过滤 [{name}]: REALITY short-id 不合法: {sid}")
                removed += 1
                continue
            if not pk:
                log.warning(f"  节点 [{name}]: 移除无效 reality-opts（缺少 public-key）")
                p = {k: v for k, v in p.items() if k != "reality-opts"}
        valid.append(p)
    if removed:
        log.info(f"节点验证: 过滤掉 {removed} 个不合规节点")
    return valid


def extract_proxies_from_text(text):
    """尝试从文本中提取节点列表，支持 Clash YAML / base64 / URI 列表"""
    # 尝试 base64 解码
    try:
        decoded = base64.b64decode(text).decode("utf-8").strip()
        if any(decoded.startswith(p) for p in PARSERS.keys()) or "proxies:" in decoded:
            text = decoded
    except Exception:
        pass

    # 尝试 Clash YAML
    try:
        data = yaml.load(text, Loader=CleanLoader)
        if isinstance(data, dict) and "proxies" in data and isinstance(data["proxies"], list):
            return data["proxies"]
    except yaml.YAMLError:
        pass

    # 尝试 URI 列表
    proxies = parse_uri_list(text)
    if proxies:
        return proxies

    return []


def fetch_proxies(url, max_retries=2):
    """
    抓取并解析节点列表，直接返回 list[dict]。
    策略：直接抓取 → 本地解析 → (可选) subconverter 回退
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
    }

    # 策略 1：直接抓取 + 本地解析
    content = None
    for attempt in range(1, max_retries + 1):
        try:
            log.info(f"  直接抓取 (第 {attempt} 次)")
            resp = requests.get(url, timeout=30, headers=headers)
            resp.raise_for_status()
            content = resp.text.strip()
            break
        except Exception as e:
            log.warning(f"  抓取失败: {e}")
            if attempt < max_retries:
                time.sleep(2)

    if content is not None:
        proxies = extract_proxies_from_text(content)
        if proxies:
            proxies = validate_proxies(proxies)
            log.info(f"  ✅ 本地解析成功: {len(proxies)} 个节点")
            return proxies

    # 策略 2：subconverter 回退（懒加载）
    if ensure_backend():
        try:
            log.info("  回退到 subconverter")
            params = {
                "target": "clash",
                "url": url,
                "emoji": "true",
                "clash.doh": "true",
                "udp": "true",
            }
            if REMOTE_CONFIG:
                params["config"] = REMOTE_CONFIG
            resp = requests.get(f"{SUBCONVERTER_URL}/sub", params=params, timeout=120)
            resp.raise_for_status()
            data = yaml.load(resp.text, Loader=CleanLoader)
            if isinstance(data, dict) and "proxies" in data:
                proxies = validate_proxies(data["proxies"])
                log.info(f"  ✅ subconverter 成功: {len(proxies)} 个节点")
                return proxies
        except Exception as e:
            log.warning(f"  subconverter 失败: {e}")

    raise RuntimeError("所有策略均失败")


# ═══════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════

def url_to_filename(index, url):
    try:
        parsed = urlparse(url)
        domain = re.sub(r"[^a-zA-Z0-9.\-]", "_", parsed.hostname or "unknown")
        return f"{index:02d}_{domain}.yaml"
    except Exception:
        return f"{index:02d}_source.yaml"


def deduplicate_proxies(proxies, seen_names):
    """去重名，返回 (unique_list, dup_count)"""
    unique = []
    dup = 0
    for p in proxies:
        name = p.get("name", "")
        if name in seen_names:
            suffix = 2
            while f"{name}_{suffix}" in seen_names:
                suffix += 1
            p["name"] = f"{name}_{suffix}"
            dup += 1
        seen_names.add(p["name"])
        unique.append(p)
    return unique, dup


def filter_by_region(proxies):
    all_keywords = []
    for keywords in REGION_KEYWORDS.values():
        all_keywords.extend([kw.lower() for kw in keywords])

    if not all_keywords:
        log.info("无地区过滤规则，保留全部节点")
        return proxies

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
            if any(kw.lower() in p.get("name", "").lower() for kw in keywords)
        )
        log.info(f"  {region}: {count} 个")
    return filtered


def save_yaml(data, path):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, Dumper=SafeStrDumper, allow_unicode=True,
                  default_flow_style=False, sort_keys=False)


def cleanup_output():
    os.makedirs("output", exist_ok=True)
    for old_file in glob.glob(os.path.join("output", "*.yaml")):
        os.remove(old_file)
    raw_dir = os.path.join("output", "raw")
    if os.path.exists(raw_dir):
        for old_file in glob.glob(os.path.join(raw_dir, "*.yaml")):
            os.remove(old_file)
    log.info("已清理旧输出文件")


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

    # 2. 清理旧输出
    cleanup_output()
    raw_dir = os.path.join("output", "raw")
    os.makedirs(raw_dir, exist_ok=True)

    # 3. 逐个抓取并分组保存
    all_proxies = []
    source_stats = []
    seen_names = set()

    for idx, url in enumerate(urls, 1):
        filename = url_to_filename(idx, url)
        out_path = os.path.join(raw_dir, filename)
        try:
            log.info(f"[{idx}/{len(urls)}] 抓取: {url}")
            proxies = fetch_proxies(url)                    # 直接返回 list[dict]
            count = len(proxies)
            unique, dup = deduplicate_proxies(proxies, seen_names)
            save_yaml({"proxies": unique}, out_path)
            source_stats.append({
                "index": idx, "url": url, "filename": filename,
                "count": count, "dup": dup, "status": "ok",
            })
            all_proxies.extend(unique)
            log.info(f"  ✅ {count} 个节点（{dup} 个重名已处理）→ raw/{filename}")
        except Exception as e:
            status = "超时" if isinstance(e, requests.exceptions.Timeout) else str(e)[:50]
            log.error(f"  ❌ {status}")
            source_stats.append({
                "index": idx, "url": url, "filename": filename,
                "count": 0, "dup": 0, "status": status,
            })

    raw_total = len(all_proxies)
    if raw_total == 0:
        msg = f"❌ <b>订阅转换失败</b>\n🕐 {now} (北京时间)\n原因: 所有源均未获取到节点"
        send_tg_notify(msg)
        sys.exit(1)
    log.info(f"原始节点合计: {raw_total} 个")

    # 4. 地区过滤
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

    # 5. 保存合并后的过滤结果
    result_text = yaml.dump(
        {"proxies": filtered_proxies},
        Dumper=SafeStrDumper, allow_unicode=True,
        default_flow_style=False, sort_keys=False,
    )
    node_count = len(filtered_proxies)
    out_path = os.path.join("output", "clash.yaml")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(result_text)
    elapsed = round(time.time() - start_time, 1)
    file_kb = round(len(result_text.encode("utf-8")) / 1024, 1)
    log.info(f"✅ 已保存至 {out_path}，{node_count} 个节点，{file_kb} KB")

    # 6. GitHub Actions 输出变量
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as gh:
            gh.write(f"node_count={node_count}\n")
            gh.write(f"elapsed={elapsed}\n")
            gh.write(f"file_kb={file_kb}\n")
            gh.write(f"source_count={len(urls)}\n")

    # 7. 各地区统计
    region_stats = []
    for region, keywords in REGION_KEYWORDS.items():
        count = sum(
            1 for p in filtered_proxies
            if any(kw.lower() in p.get("name", "").lower() for kw in keywords)
        )
        region_stats.append(f"  {region}: {count} 个")

    # 8. Telegram 通知
    source_lines = ""
    for s in source_stats:
        if s["status"] == "ok":
            raw_url = get_raw_url(s["filename"])
            source_lines += f'  📡 <a href="{raw_url}">源 {s["index"]}</a>: {s["count"]} 个节点\n'
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
        f'📥 <a href="{main_url}">点击下载节点列表</a> ({file_kb} KB)\n'
    )
    send_tg_notify(msg)


if __name__ == "__main__":
    main()
