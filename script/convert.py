#!/usr/bin/env python3
"""
调用本地 subconverter，将 urls.txt 中的订阅源转换为 Clash 配置。
按订阅源分组保存原始节点，再合并过滤指定地区节点，发送 Telegram 通知。
过滤后通过 subconverter 加载自定义 INI 模板生成完整的 Clash 配置。
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
from urllib.parse import unquote, quote
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("converter")

SUBCONVERTER_URL = "http://127.0.0.1:25500"

REMOTE_CONFIG = "https://raw.githubusercontent.com/j6y9c4t9/myclashrule/refs/heads/main/AlvinDad_NEW.ini"

BJT = timezone(timedelta(hours=8))

# ── 防止 YAML 把 "473277e2" 当作科学计数法 ─────────────────
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
#  通过 Subconverter 加载 INI 模板生成完整 Clash 配置
# ═══════════════════════════════════════════════════════════

def generate_full_config_via_subconverter(filtered_clash_yaml_text, target="clash"):
    """
    将过滤后的纯节点 yaml 内容保存为临时本地文件，然后通过 file:// 协议提供给 subconverter，
    以此防止节点过多导致 414 URI Too Long 错误。
    """
    if not filtered_clash_yaml_text:
        log.warning("过滤节点文本为空，跳过完整配置生成")
        return None

    log.info("借助 subconverter 及自定义 INI 模板生成完整配置...")
    
    # 建立临时节点文件存放过滤后的纯 proxies 列表
    tmp_nodes_path = os.path.abspath(os.path.join("output", "filtered_nodes.yaml"))
    with open(tmp_nodes_path, "w", encoding="utf-8") as f:
        f.write(filtered_clash_yaml_text)

    # 转换为本地绝对路径的 file:// URL，规避 URL 长度限制限制
    file_url = f"file://{tmp_nodes_path}"

    params = {
        "target": target,
        "url": file_url,
        "emoji": "true",
        "clash.doh": "true",
        "udp": "true",
    }
    if REMOTE_CONFIG:
        params["config"] = REMOTE_CONFIG

    try:
        resp = requests.get(f"{SUBCONVERTER_URL}/sub", params=params, timeout=120)
        resp.raise_for_status()
        result = resp.text.strip()
        log.info("✅ 成功使用自定义 INI 模板生成完整配置")
        return result
    except Exception as e:
        log.error(f"❌ 配合 INI 模板转换失败: {e}")
        raise
    finally:
        # 转换完成后，清理掉生成的临时纯节点文件
        if os.path.exists(tmp_nodes_path):
            os.remove(tmp_nodes_path)


# ═══════════════════════════════════════════════════════════
#  验证、提取、转换
# ═══════════════════════════════════════════════════════════

def validate_proxies(proxies):
    valid = []
    removed = 0
    for p in proxies:
        name = p.get("name", "unknown")
        if not p.get("server") or not p.get("port"):
            log.warning(f"  过滤 [{name}]: 缺少 server/port")
            removed += 1
            continue
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
                del p["reality-opts"]
        valid.append(p)
    if removed:
        log.info(f"节点验证: 过滤掉 {removed} 个不合规节点")
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
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    content = None

    # 策略 1：直接抓取
    try:
        log.info("  策略1: 直接抓取")
        fetch_resp = requests.get(url, timeout=30, headers=headers)
        fetch_resp.raise_for_status()
        content = fetch_resp.text.strip()
        log.info("  ✅ 直接抓取成功")
    except Exception as e:
        log.warning(f"  直接抓取失败: {e}")

    # 策略 2：回退到 subconverter
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
            data = yaml.load(result, Loader=CleanLoader)
            if isinstance(data, dict) and "proxies" in data:
                log.info(f"  ✅ subconverter 成功: {len(data['proxies'])} 个节点")
                return result
        except Exception as e:
            log.warning(f"  subconverter 失败: {e}")

    # 策略 3：解析抓取到的内容
    if content is not None:
        try:
            decoded = base64.b64decode(content).decode("utf-8").strip()
            if any(decoded.startswith(p) for p in PARSERS.keys()):
                content = decoded
            elif "proxies:" in decoded:
                content = decoded
        except Exception:
            pass
        try:
            data = yaml.load(content, Loader=CleanLoader)
            if isinstance(data, dict) and "proxies" in data:
                log.info(f"  ✅ 已是 Clash YAML: {len(data['proxies'])} 个节点")
                return content
        except yaml.YAMLError:
            pass
        proxies = parse_uri_list(content)
        if proxies:
            proxies = validate_proxies(proxies)
            log.info(f"  ✅ 本地解析成功: {len(proxies)} 个节点")
            return yaml.dump(
                {"proxies": proxies},
                Dumper=SafeStrDumper,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )

    raise RuntimeError("所有策略均失败")


# ═══════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════

def url_to_filename(index, url):
    try:
        parsed = urlparse(url)
        domain = parsed.hostname or "unknown"
        domain = re.sub(r"[^a-zA-Z0-9.\-]", "_", domain)
        return f"{index:02d}_{domain}.yaml"
    except Exception:
        return f"{index:02d}_source.yaml"


def sanitize_name(name, seen):
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
    all_keywords = []
    for keywords in REGION_KEYWORDS.values():
        all_keywords.extend([kw.lower() for kw in keywords])
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
        yaml.dump(data, f, Dumper=SafeStrDumper, allow_unicode=True, default_flow_style=False, sort_keys=False)


def cleanup_output():
    os.makedirs("output", exist_ok=True)
    for old_file in glob.glob(os.path.join("output", "*.yaml")):
        try:
            os.remove(old_file)
            log.info(f"已清理旧文件: {old_file}")
        except Exception:
            pass
    raw_dir = os.path.join("output", "raw")
    if os.path.exists(raw_dir):
