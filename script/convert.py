#!/usr/bin/env python3
"""
调用本地 subconverter，将 urls.txt 中的订阅源转换为 Clash 配置。
按订阅源分组保存原始节点，再合并过滤指定地区节点，发送 Telegram 通知。
过滤后通过 subconverter POST 生成含 rules 的完整 Clash 配置。
"""
import os
import sys
import re
import glob
import time
import json
import base64
import logging
import requests
import yaml
from urllib.parse import unquote, quote
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("converter")

SUBCONVERTER_URL = "http://127.0.0.1:25500"
REMOTE_CONFIG = "https://raw.githubusercontent.com/j6y9c4t9/myclashrule/refs/heads/main/AlvinDad_NEW.ini"
BJT = timezone(timedelta(hours=8))


# ── YAML 安全加载/输出 ─────────────────────────────────────
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
REGION_KEYWORDS = {
    "日本": ["日本", "jp", "japan", "jpn", "东京", "大阪", "tokyo", "osaka", "🇯🇵"],
    "新加坡": ["新加坡", "sg", "singapore", "sgp", "狮城", "🇸🇬"],
    "美国": ["美国", "us", "united states", "unitedstates", "usa", "america", "🇺🇸"],
    "香港": ["香港", "hk", "hongkong", "hong kong", "hkg", "🇭🇰"],
    "台湾": ["台湾", "tw", "taiwan", "formosa", "tpe", "台北", "🇹🇼"],
}


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


# ═══════════════════════════════════════════════════════════
#  重名处理（统一管理 seen_names）
# ═══════════════════════════════════════════════════════════

def ensure_unique_name(name, seen):
    """
    确保 name 在 seen 中唯一。
    调用方负责将原始 name 传入，函数内部完成查重 + 加后缀 + 注册 seen。
    """
    if name not in seen:
        seen.add(name)
        return name
    suffix = 2
    while f"{name}_{suffix}" in seen:
        suffix += 1
    new_name = f"{name}_{suffix}"
    seen.add(new_name)
    return new_name


# ═══════════════════════════════════════════════════════════
#  URI → Clash proxy 解析器
# ═══════════════════════════════════════════════════════════

def parse_vless_uri(uri):
    try:
        name = ""
        if "#" in uri:
            uri, name = uri.rsplit("#", 1)
            name = unquote(name)
        raw = uri[len("vless://"):]
        main_part, query_str = (raw.split("?", 1) + [""])[:2] if "?" in raw else (raw, "")
        uuid, server_port = main_part.split("@", 1)
        if server_port.startswith("["):
            end = server_port.index("]")
            server = server_port[1:end]
            port = int(server_port[end + 2:]) if len(server_port) > end + 1 else 443
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
            "name": name or f"vless-{server}", "type": "vless",
            "server": server, "port": port, "uuid": uuid, "udp": True,
        }
        security = params.get("security", "none")
        if security in ("tls", "reality"):
            proxy["tls"] = True
            if params.get("sni"): proxy["servername"] = params["sni"]
            if params.get("fp"): proxy["client-fingerprint"] = params["fp"]
            if params.get("alpn"): proxy["alpn"] = params["alpn"].split(",")
        if security == "reality":
            ro = {}
            if params.get("pbk"): ro["public-key"] = params["pbk"]
            if params.get("sid"): ro["short-id"] = params["sid"]
            if ro: proxy["reality-opts"] = ro
        if params.get("flow"): proxy["flow"] = params["flow"]
        if params.get("fragment"): proxy["fragment"] = params["fragment"]
        if params.get("ech"): proxy["ech-opts"] = {"enable": True}
        transport = params.get("type", "tcp")
        if transport == "ws":
            proxy["network"] = "ws"
            ws = {}
            if params.get("path"): ws["path"] = params["path"]
            if params.get("host"): ws["headers"] = {"Host": params["host"]}
            if ws: proxy["ws-opts"] = ws
        elif transport == "grpc":
            proxy["network"] = "grpc"
            if params.get("serviceName"): proxy["grpc-opts"] = {"grpc-service-name": params["serviceName"]}
        elif transport == "h2":
            proxy["network"] = "h2"
            h2 = {}
            if params.get("host"): h2["host"] = [params["host"]]
            if params.get("path"): h2["path"] = params["path"]
            if h2: proxy["h2-opts"] = h2
        return proxy
    except Exception as e:
        log.debug(f"解析 vless 失败: {e}")
        return None


def parse_vmess_uri(uri):
    try:
        raw = uri[len("vmess://"):]
        if len(raw) % 4: raw += "=" * (4 - len(raw) % 4)
        info = yaml.load(base64.b64decode(raw).decode("utf-8"), Loader=CleanLoader)
        if not isinstance(info, dict): return None
        proxy = {
            "name": info.get("ps", "vmess-node"), "type": "vmess",
            "server": info.get("add", ""), "port": int(info.get("port", 443)),
            "uuid": info.get("id", ""), "alterId": int(info.get("aid", 0)),
            "cipher": info.get("scy", "auto"), "udp": True,
        }
        if info.get("tls") == "tls":
            proxy["tls"] = True
            if info.get("sni"): proxy["servername"] = info["sni"]
        net = info.get("net", "tcp")
        if net == "ws":
            proxy["network"] = "ws"
            ws = {}
            if info.get("path"): ws["path"] = info["path"]
            if info.get("host"): ws["headers"] = {"Host": info["host"]}
            if ws: proxy["ws-opts"] = ws
        return proxy
    except Exception as e:
        log.debug(f"解析 vmess 失败: {e}")
        return None


def parse_ss_uri(uri):
    """解析 ss:// URI"""
    try:
        raw = uri[len("ss://"):]
        name = ""
        if "#" in raw:
            raw, name = raw.rsplit("#", 1)
            name = unquote(name)
        if "@" in raw:
            userinfo, serverinfo = raw.rsplit("@", 1)
            # 健壮的 base64 padding
            missing = len(userinfo) % 4
            if missing:
                userinfo += "=" * (4 - missing)
            try:
                decoded = base64.b64decode(userinfo).decode("utf-8")
                method, password = decoded.split(":", 1)
            except Exception:
                method, password = userinfo.split(":", 1)
            server, port = serverinfo.rsplit(":", 1)
            port = port.split("?")[0]
        else:
            raw_clean = raw.split("?")[0]
            missing = len(raw_clean) % 4
            if missing:
                raw_clean += "=" * (4 - missing)
            decoded = base64.b64decode(raw_clean).decode("utf-8")
            method_password, serverinfo = decoded.rsplit("@", 1)
            method, password = method_password.split(":", 1)
            server, port = serverinfo.rsplit(":", 1)
        return {
            "name": name or f"ss-{server}", "type": "ss",
            "server": server, "port": int(port),
            "cipher": method, "password": password, "udp": True,
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
        server, port = serverinfo.rsplit(":", 1)
        proxy = {
            "name": name or f"trojan-{server}", "type": "trojan",
            "server": server, "port": int(port), "password": userinfo, "udp": True,
        }
        if params.get("sni"): proxy["sni"] = params["sni"]
        net = params.get("type", "tcp")
        if net == "ws":
            proxy["network"] = "ws"
            ws = {}
            if params.get("path"): ws["path"] = unquote(params["path"])
            if params.get("host"): ws["headers"] = {"Host": params["host"]}
            if ws: proxy["ws-opts"] = ws
        return proxy
    except Exception as e:
        log.debug(f"解析 trojan 失败: {e}")
        return None


PARSERS = {
    "vless://": parse_vless_uri, "vmess://": parse_vmess_uri,
    "ss://": parse_ss_uri, "trojan://": parse_trojan_uri,
}


def parse_uri_list(content):
    proxies = []
    for line in content.strip().splitlines():
        line = line.strip()
        if not line: continue
        for prefix, parser in PARSERS.items():
            if line.startswith(prefix):
                proxy = parser(line)
                if proxy: proxies.append(proxy)
                break
    return proxies


# ═══════════════════════════════════════════════════════════
#  Clash proxy → URI 反向编码
# ═══════════════════════════════════════════════════════════

def proxy_to_uri(proxy):
    handlers = {
        "vless": _vless_to_uri, "vmess": _vmess_to_uri,
        "ss": _ss_to_uri, "trojan": _trojan_to_uri,
    }
    handler = handlers.get(proxy.get("type", ""))
    return handler(proxy, proxy.get("name", "")) if handler else None

def _vless_to_uri(p, name):
    uuid, server, port = p.get("uuid", ""), p.get("server", ""), p.get("port", 443)
    params = {}
    ro = p.get("reality-opts", {})
    params["security"] = "reality" if ro.get("public-key") else ("tls" if p.get("tls") else "none")
    transport = p.get("network", "tcp")
    if transport != "tcp": params["type"] = transport
    ws = p.get("ws-opts", {})
    if ws.get("path"): params["path"] = ws["path"]
    if ws.get("headers", {}).get("Host"): params["host"] = ws["headers"]["Host"]
    if p.get("servername"): params["sni"] = p["servername"]
    if p.get("client-fingerprint"): params["fp"] = p["client-fingerprint"]
    if p.get("alpn"): params["alpn"] = ",".join(p["alpn"]) if isinstance(p["alpn"], list) else p["alpn"]
    if p.get("flow"): params["flow"] = p["flow"]
    if ro.get("public-key"): params["pbk"] = ro["public-key"]
    if ro.get("short-id"): params["sid"] = str(ro["short-id"])
    if p.get("fragment"): params["fragment"] = p["fragment"]
    grpc = p.get("grpc-opts", {})
    if grpc.get("grpc-service-name"): params["serviceName"] = grpc["grpc-service-name"]
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
    return f"vless://{uuid}@{server}:{port}?{query}#{quote(name, safe='')}"

def _vmess_to_uri(p, name):
    ws = p.get("ws-opts", {})
    info = {
        "v": "2", "ps": name, "add": p.get("server", ""),
        "port": str(p.get("port", 443)), "id": p.get("uuid", ""),
        "aid": str(p.get("alterId", 0)), "net": p.get("network", "tcp"),
        "type": "none", "host": ws.get("headers", {}).get("Host", ""),
        "path": ws.get("path", ""), "tls": "tls" if p.get("tls") else "",
        "sni": p.get("servername", ""), "scy": p.get("cipher", "auto"),
    }
    return f"vmess://{base64.b64encode(json.dumps(info, ensure_ascii=False).encode()).decode()}"

def _ss_to_uri(p, name):
    userinfo = base64.b64encode(f"{p.get('cipher', '')}:{p.get('password', '')}".encode()).decode()
    return f"ss://{userinfo}@{p.get('server', '')}:{p.get('port', '')}#{quote(name, safe='')}"

def _trojan_to_uri(p, name):
    password, server, port = p.get("password", ""), p.get("server", ""), p.get("port", "")
    params = {}
    if p.get("sni"): params["sni"] = p["sni"]
    transport = p.get("network", "tcp")
    if transport != "tcp": params["type"] = transport
    ws = p.get("ws-opts", {})
    if ws.get("path"): params["path"] = ws["path"]
    if ws.get("headers", {}).get("Host"): params["host"] = ws["headers"]["Host"]
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
    return f"trojan://{password}@{server}:{port}?{query}#{quote(name, safe='')}" if query else f"trojan://{password}@{server}:{port}#{quote(name, safe='')}"


# ═══════════════════════════════════════════════════════════
#  通过 subconverter POST 生成完整 Clash 配置
# ═══════════════════════════════════════════════════════════

def generate_full_config(proxies):
    uri_list = []
    for p in proxies:
        uri = proxy_to_uri(p)
        if uri: uri_list.append(uri)
    if not uri_list:
        log.warning("无有效 URI，跳过完整配置生成")
        return None

    log.info(f"反向编码完成: {len(uri_list)} 个 URI，生成完整配置")

    # 写入文件（调试用）
    uri_file = os.path.join("output", "uri_list.txt")
    with open(uri_file, "w", encoding="utf-8") as f:
        f.write("\n".join(uri_list))

    params = {"target": "clash", "emoji": "true", "clash.doh": "true", "udp": "true", "filename": "full_config"}
    if REMOTE_CONFIG: params["config"] = REMOTE_CONFIG

    resp = requests.post(
        f"{SUBCONVERTER_URL}/sub", params=params,
        data="\n".join(uri_list).encode("utf-8"),
        headers={"Content-Type": "text/plain"}, timeout=120,
    )
    if resp.status_code != 200:
        log.error(f"subconverter 返回 {resp.status_code}: {resp.text[:500]}")
        resp.raise_for_status()

    data = yaml.load(resp.text, Loader=CleanLoader)
    if isinstance(data, dict):
        if "global-client-fingerprint" in data: del data["global-client-fingerprint"]
        if "proxies" in data: data["proxies"] = validate_proxies(data["proxies"])
        return yaml.dump(data, Dumper=SafeStrDumper, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return resp.text


# ═══════════════════════════════════════════════════════════
#  验证、提取、转换
# ═══════════════════════════════════════════════════════════

def validate_proxies(proxies):
    valid, removed = [], 0
    for p in proxies:
        name = p.get("name", "unknown")
        if not p.get("server") or not p.get("port"):
            log.warning(f"  过滤 [{name}]: 缺少 server/port"); removed += 1; continue
        ro = p.get("reality-opts", {})
        if ro:
            sid = str(ro.get("short-id", ""))
            if sid and not re.match(r'^[0-9a-fA-F]{1,64}$', sid):
                log.warning(f"  过滤 [{name}]: REALITY short-id 不合法: {sid}"); removed += 1; continue
            if not ro.get("public-key"):
                log.warning(f"  节点 [{name}]: 移除无效 reality-opts"); del p["reality-opts"]
        valid.append(p)
    if removed: log.info(f"节点验证: 过滤掉 {removed} 个不合规节点")
    return valid


def extract_proxies(text):
    try:
        data = yaml.load(text, Loader=CleanLoader)
        if isinstance(data, dict) and "proxies" in data and isinstance(data["proxies"], list):
            return data["proxies"]
    except yaml.YAMLError:
        pass
    return []


def convert_single(url, target="clash"):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    content = None

    # 策略 1：直接抓取原始内容
    try:
        log.info("  策略1: 直接抓取")
        resp = requests.get(url, timeout=30, headers=headers)
        resp.raise_for_status()
        content = resp.text.strip()
        log.info("  ✅ 直接抓取成功")
    except Exception as e:
        log.warning(f"  直接抓取失败: {e}")

    # 策略 2：回退到 subconverter 内置抓取
    if content is None:
        log.info("  策略2: 回退到 subconverter")
        try:
            params = {"target": target, "url": url, "emoji": "true", "clash.doh": "true", "udp": "true"}
            if REMOTE_CONFIG: params["config"] = REMOTE_CONFIG
            resp = requests.get(f"{SUBCONVERTER_URL}/sub", params=params, timeout=120)
            resp.raise_for_status()
            data = yaml.load(resp.text.strip(), Loader=CleanLoader)
            if isinstance(data, dict) and "proxies" in data:
                log.info(f"  ✅ subconverter 成功: {len(data['proxies'])} 个节点")
                return resp.text.strip()
        except Exception as e:
            log.warning(f"  subconverter 失败: {e}")

    # 策略 3：解析抓取到的内容
    if content is None:
        raise RuntimeError("所有抓取策略均失败")

    # 3a: 尝试 base64 解码
    try:
        decoded = base64.b64decode(content).decode("utf-8").strip()
        # 只有解码后看起来像代理内容才替换
        if any(decoded.startswith(p) for p in PARSERS.keys()) or "proxies:" in decoded:
            content = decoded
            log.info("  内容已 base64 解码")
    except Exception:
        pass

    # 3b: 已是 Clash YAML？
    try:
        data = yaml.load(content, Loader=CleanLoader)
        if isinstance(data, dict) and "proxies" in data:
            log.info(f"  ✅ 已是 Clash YAML: {len(data['proxies'])} 个节点")
            return content
    except yaml.YAMLError:
        pass

    # 3c: URI 列表 → 本地解析
    proxies = parse_uri_list(content)
    if proxies:
        proxies = validate_proxies(proxies)
        log.info(f"  ✅ 本地解析成功: {len(proxies)} 个节点")
        return yaml.dump(
            {"proxies": proxies}, Dumper=SafeStrDumper,
            allow_unicode=True, default_flow_style=False, sort_keys=False,
        )

    raise RuntimeError("无法解析抓取到的内容（非 YAML 也非 URI 列表）")


# ═══════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════

def url_to_filename(index, url):
    try:
        domain = urlparse(url).hostname or "unknown"
        clean_domain = re.sub(r'[^a-zA-Z0-9.\-]', '_', domain)
        return f"{index:02d}_{clean_domain}.yaml"
    except:
        return f"{index:02d}_source.yaml"


def filter_by_region(proxies):
    all_kw = [kw.lower() for kws in REGION_KEYWORDS.values() for kw in kws]
    filtered, removed = [], 0
    for p in proxies:
        name = p.get("name", "").lower()
        if any(kw in name for kw in all_kw): filtered.append(p)
        else: removed += 1
    log.info(f"地区过滤: {len(proxies)} → {len(filtered)} (过滤 {removed})")
    for region, keywords in REGION_KEYWORDS.items():
        count = sum(1 for p in filtered if any(kw.lower() in p.get("name", "").lower() for kw in keywords))
        log.info(f"  {region}: {count}")
    return filtered


def save_yaml(data, path):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, Dumper=SafeStrDumper, allow_unicode=True, default_flow_style=False, sort_keys=False)


def cleanup_output():
    os.makedirs("output", exist_ok=True)
    for f in glob.glob(os.path.join("output", "*.yaml")): os.remove(f)
    raw_dir = os.path.join("output", "raw")
    if os.path.exists(raw_dir):
        for f in glob.glob(os.path.join(raw_dir, "*.yaml")): os.remove(f)


def send_tg_notify(message):
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.info("未配置 TELEGRAM_TOKEN/TELEGRAM_CHAT_ID，跳过通知"); return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
        if resp.status_code == 200: log.info("Telegram 通知已发送")
        else: log.warning(f"Telegram 通知失败: {resp.status_code}")
    except Exception as e:
        log.warning(f"Telegram 通知异常: {e}")


# ═══════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════

def main():
    log.info(f"工作目录: {os.getcwd()}")
    start_time = time.time()
    now = get_bjt_now()

    if not os.path.exists("urls.txt"):
        send_tg_notify(f"❌ <b>订阅转换失败</b>\n🕐 {now} (北京时间)\n原因: urls.txt 不存在")
        sys.exit(1)
    urls = read_urls()
    if not urls:
        send_tg_notify(f"❌ <b>订阅转换失败</b>\n🕐 {now} (北京时间)\n原因: urls.txt 中无有效链接")
        sys.exit(1)
    log.info(f"读取到 {len(urls)} 个订阅源")

    if not wait_for_backend(SUBCONVERTER_URL):
        send_tg_notify(f"❌ <b>订阅转换失败</b>\n🕐 {now} (北京时间)\n原因: subconverter 未就绪")
        sys.exit(1)

    cleanup_output()
    os.makedirs(os.path.join("output", "raw"), exist_ok=True)

    # ── 逐个抓取 ──────────────────────────────────────────
    all_proxies, source_stats, seen_names = [], [], set()

    for idx, url in enumerate(urls, 1):
        filename = url_to_filename(idx, url)
        try:
            log.info(f"[{idx}/{len(urls)}] 抓取: {url}")
            raw_proxies = validate_proxies(extract_proxies(convert_single(url)))

            # 统一使用 ensure_unique_name 管理 seen_names
            unique, dup = [], 0
            for p in raw_proxies:
                old_name = p.get("name", "")
                new_name = ensure_unique_name(old_name, seen_names)
                if new_name != old_name:
                    dup += 1
                p["name"] = new_name
                unique.append(p)

            save_yaml({"proxies": unique}, os.path.join("output", "raw", filename))
            source_stats.append({"index": idx, "filename": filename, "count": len(raw_proxies), "dup": dup, "status": "ok"})
            all_proxies.extend(unique)
            log.info(f"  ✅ {len(raw_proxies)} 个节点（{dup} 个重名已处理）→ raw/{filename}")

        except requests.exceptions.Timeout:
            log.error(f"  ❌ 超时")
            source_stats.append({"index": idx, "filename": filename, "count": 0, "dup": 0, "status": "超时"})
        except requests.exceptions.HTTPError as e:
            log.error(f"  ❌ HTTP {e.response.status_code}")
            source_stats.append({"index": idx, "filename": filename, "count": 0, "dup": 0, "status": f"HTTP {e.response.status_code}"})
        except Exception as e:
            log.error(f"  ❌ {e}")
            source_stats.append({"index": idx, "filename": filename, "count": 0, "dup": 0, "status": str(e)[:50]})

    raw_total = len(all_proxies)
    if raw_total == 0:
        send_tg_notify(f"❌ <b>订阅转换失败</b>\n🕐 {now} (北京时间)\n原因: 所有源均未获取到节点")
        sys.exit(1)
    log.info(f"原始节点合计: {raw_total}")

    # ── 地区过滤 ──────────────────────────────────────────
    filtered = filter_by_region(all_proxies)
    if not filtered:
        send_tg_notify(f"❌ <b>订阅转换失败</b>\n🕐 {now} (北京时间)\n原因: 过滤后无节点")
        sys.exit(1)

    # ── 保存节点列表 ──────────────────────────────────────
    result_text = yaml.dump({"proxies": filtered}, Dumper=SafeStrDumper, allow_unicode=True, default_flow_style=False, sort_keys=False)
    node_count = len(filtered)
    out_path = os.path.join("output", "clash.yaml")
    with open(out_path, "w", encoding="utf-8") as f: f.write(result_text)
    elapsed = round(time.time() - start_time, 1)
    file_kb = round(len(result_text.encode("utf-8")) / 1024, 1)
    log.info(f"✅ {out_path}，{node_count} 个节点，{file_kb} KB")

    # ── 生成完整配置 ──────────────────────────────────────
    full_config_kb, full_config_ok = 0, False
    try:
        fc = generate_full_config(filtered)
        if fc:
            fp = os.path.join("output", "full_config.yaml")
            with open(fp, "w", encoding="utf-8") as f: f.write(fc)
            full_config_kb = round(len(fc.encode("utf-8")) / 1024, 1)
            full_config_ok = True
            log.info(f"✅ 完整配置: {fp} ({full_config_kb} KB)")
    except Exception as e:
        log.warning(f"生成完整配置失败: {e}")

    # ── GitHub Actions 输出变量 ──────────────────────────
    gh = os.environ.get("GITHUB_OUTPUT", "")
    if gh:
        with open(gh, "a") as f:
            f.write(f"node_count={node_count}\nelapsed={elapsed}\nfile_kb={file_kb}\nsource_count={len(urls)}\n")

    # ── 地区统计 ──────────────────────────────────────────
    region_stats = []
    for region, keywords in REGION_KEYWORDS.items():
        count = sum(1 for p in filtered if any(kw.lower() in p.get("name", "").lower() for kw in keywords))
        region_stats.append(f"  {region}: {count} 个")

    # ── Telegram 通知 ────────────────────────────────────
    src_lines = ""
    for s in source_stats:
        if s["status"] == "ok":
            src_lines += f"  📡 <a href=\"{get_raw_url(s['filename'])}\">源 {s['index']}</a>: {s['count']} 个节点\n"
        else:
            src_lines += f"  📡 源 {s['index']}: ❌ {s['status']}\n"

    full_line = f"📄 <a href=\"{get_main_url('full_config.yaml')}\">点击下载完整配置</a> ({full_config_kb} KB)\n" if full_config_ok else ""

    msg = (
        f"✅ <b>订阅转换完成</b>\n"
        f"🕐 {now} (北京时间)\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🔗 原始节点: <b>{raw_total}</b> 个\n"
        f"🔗 过滤后: <b>{node_count}</b> 个\n"
        f"📦 文件大小: <b>{file_kb}</b> KB\n"
        f"⏱️ 耗时: <b>{elapsed}</b> 秒\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📊 地区统计:\n" + "\n".join(region_stats) + "\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📋 各源明细:\n{src_lines}"
        f"━━━━━━━━━━━━━━━━\n"
        f"📥 <a href=\"{get_main_url('clash.yaml')}\">点击下载节点列表</a> ({file_kb} KB)\n"
        f"{full_line}"
    )
    send_tg_notify(msg)


if __name__ == "__main__":
    main()
