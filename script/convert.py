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
from urllib.parse import unquote, quote, urlparse
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

# ── 地区过滤配置 ─────────────────────────────────────────
# 设定默认兜底配置，防止本地 regions.json 不存在时导致节点全被过滤
DEFAULT_REGION_KEYWORDS = {
    "香港": ["HK", "Hong Kong", "香港", "川日", "🇭🇰"],
    "日本": ["JP", "Japan", "日本", "东京", "大阪", "🇯🇵"],
    "新加坡": ["SG", "Singapore", "新加坡", "狮城", "🇸🇬"],
    "美国": ["US", "United States", "美国", "洛杉矶", "圣克拉拉", "🇺🇸"]
}

def load_region_keywords(json_path="regions.json"):
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                log.info(f"✅ 成功从 {json_path} 加载了 {len(data)} 个地区的过滤规则")
                return data
            else:
                log.warning(f"⚠️ {json_path} 格式不正确（应为键值对字典），将激活默认过滤规则")
        except Exception as e:
            log.error(f"❌ 读取 {json_path} 失败: {e}，将激活默认过滤规则")
    else:
        log.info(f"ℹ️ 未检测到 {json_path}，使用脚本默认过滤规则")
    return DEFAULT_REGION_KEYWORDS

REGION_KEYWORDS = load_region_keywords("regions.json")


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
    if not os.path.exists(path):
        return []
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
    # 自动识别并解密被整体打包成一条 Base64 字符串的 txt 订阅
    try:
        clean_content = content.strip().replace("\n", "").replace("\r", "")
        if len(clean_content) % 4 == 0 and re.match(r"^[A-Za-z0-9+/=]+$", clean_content):
            content = base64.b64decode(clean_content).decode("utf-8", errors="ignore")
    except Exception:
        pass

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
    valid = []
    removed = 0
    for p in proxies:
        name = p.get("name", "unknown")
        server = p.get("server")
        port = p.get("port")
        
        # 1. 基础字段验证
        if not server or port is None:
            log.warning(f"  过滤 [{name}]: 缺少 server 或 port")
            removed += 1
            continue
            
        # 2. 端口合法性与范围校验 (防内核崩溃)
        try:
            port_num = int(port)
            if not (1 <= port_num <= 65535):
                log.warning(f"  过滤 [{name}]: 端口越界 ({port})，必须在 1-65535 之间")
                removed += 1
                continue
            p["port"] = port_num  # 规范化为整型
        except (ValueError, TypeError):
            log.warning(f"  过滤 [{name}]: 端口格式非法 ({port})，无法转换为数字")
            removed += 1
            continue

        # 3. REALITY 安全验证
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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 ClashforWindows/0.20.39",
        "Accept": "*/*",
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

    # 策略 3：本地解析
    if content is not None:
        try:
            decoded = base64.b64decode(content).decode("utf-8", errors="ignore").strip()
            if any(decoded.startswith(p) for p in PARSERS.keys()) or "proxies:" in decoded:
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
    
    # 策略打底：如果关键词库彻底为空，放行全部节点，防止脚本中断
    if not all_keywords:
        log.warning("⚠️ 未配置任何有效的地区关键词，默认不过滤，直接放行全部节点")
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
        for old_file in glob.glob(os.path.join(raw_dir, "*.yaml")):
            try:
                os.remove(old_file)
                log.info(f"已清理旧文件: {old_file}")
            except Exception:
                pass


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
            timeout=15,
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
    urls = read_urls()
    if not urls:
        msg = f"❌ <b>订阅转换失败</b>\n🕐 {now} (北京时间)\n原因: urls.txt 不存在或无有效内容"
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
    raw_dir = os.path.join("output", "raw")
    os.makedirs(raw_dir, exist_ok=True)

    # 4. 逐个抓取并分组保存
    all_proxies = []
    source_stats = []
    seen_names = set()

    for idx, url in enumerate(urls, 1):
        filename = url_to_filename(idx, url)
        out_path = os.path.join(raw_dir, filename)
        try:
            log.info(f"[{idx}/{len(urls)}] 抓取: {url}")
            text = convert_single(url)
            proxies = validate_proxies(extract_proxies(text))
            count = len(proxies)
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
            save_yaml({"proxies": unique}, out_path)
            source_stats.append({
                "index": idx, "url": url, "filename": filename,
                "count": count, "dup": dup, "status": "ok",
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

    # 5. 地区过滤
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

    # 6. 保存合并后的过滤结果
    result_text = yaml.dump(
        {"proxies": filtered_proxies},
        Dumper=SafeStrDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    node_count = len(filtered_proxies)
    out_path = os.path.join("output", "clash.yaml")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(result_text)
    elapsed = round(time.time() - start_time, 1)
    file_kb = round(len(result_text.encode("utf-8")) / 1024, 1)
    log.info(f"✅ 已保存至 {out_path}，{node_count} 个节点，{file_kb} KB")

    # 7. GitHub Actions 输出变量
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as gh:
            gh.write(f"node_count={node_count}\n")
            gh.write(f"elapsed={elapsed}\n")
            gh.write(f"file_kb={file_kb}\n")
            gh.write(f"source_count={len(urls)}\n")

    # 8. 各地区统计
    region_stats = []
    for region, keywords in REGION_KEYWORDS.items():
        count = sum(
            1 for p in filtered_proxies
            if any(kw.lower() in p.get("name", "").lower() for kw in keywords)
        )
        region_stats.append(f"  {region}: {count} 个")

    # 9. Telegram 成功通知
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
        f"📥 <a href=\"{main_url}\">点击下载节点列表</a> ({file_kb} KB)\n"
    )
    send_tg_notify(msg)


if __name__ == "__main__":
    main()
