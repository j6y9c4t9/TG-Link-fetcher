#!/usr/bin/env python3
"""
从 output/clash.yaml 提取节点，注入到模板中，生成最终配置文件。
模板中 proxy-groups 的 proxies 列表里写 __PROXY_LIST__ 占位符，
脚本会自动将其替换为所有节点名称。
"""
import os
import sys
import re
import glob
import copy
import time
import logging
import requests
import yaml
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("builder")

BJT = timezone(timedelta(hours=8))

# ── 防止 YAML 把 "473277e2" 当作科学计数法 ─────────────────
class CleanLoader(yaml.SafeLoader):
    """保留科学计数法格式的字符串，不转为浮点数"""
    pass

def _clean_float(loader, node):
    value = loader.construct_scalar(node)
    if re.match(r'^[0-9a-fA-F]+[eE][0-9a-fA-F]+$', value):
        return value
    return float(value)

CleanLoader.add_constructor('tag:yaml.org,2002:float', _clean_float)


class SafeStrDumper(yaml.SafeDumper):
    """输出时给会被误解析的字符串加引号"""
    pass

def _represent_str(dumper, data):
    if re.match(r'^[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?$', data):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')
    return dumper.represent_str(data)

SafeStrDumper.add_representer(str, _represent_str)
# ─────────────────────────────────────────────────────────

# ── 模板配置：模板文件名 → 输出文件名 ──
TEMPLATES = {
    "template.yaml": "config.yaml",
    "template-smart.yaml": "config-smart.yaml",
}

# ── 输出文件名 → 通知中的显示名称和图标 ──
OUTPUT_LABELS = {
    "config.yaml": {"icon": "🔹", "label": "标准版"},
    "config-smart.yaml": {"icon": "🔸", "label": "Smart版"},
}

PLACEHOLDER = "__PROXY_LIST__"


def get_bjt_now():
    return datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S")


def get_raw_url(filename):
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo:
        return f"{server}/{repo}/raw/main/output/{filename}"
    return ""


def extract_proxies(clash_path):
    with open(clash_path, "r", encoding="utf-8") as f:
        data = yaml.load(f.read(), Loader=CleanLoader)
    if not isinstance(data, dict) or "proxies" not in data:
        return []
    return data["proxies"]


def build_config(template_path, proxies):
    with open(template_path, "r", encoding="utf-8") as f:
        config = yaml.load(f.read(), Loader=CleanLoader)

    if not isinstance(config, dict):
        raise ValueError(f"模板格式错误: {template_path}")

    proxy_names = [p["name"] for p in proxies if "name" in p]

    config["proxies"] = proxies

    if "proxy-groups" in config:
        for group in config["proxy-groups"]:
            if not isinstance(group, dict) or "proxies" not in group:
                continue
            new_list = []
            for item in group["proxies"]:
                if item == PLACEHOLDER:
                    new_list.extend(proxy_names)
                else:
                    new_list.append(item)
            group["proxies"] = new_list

    # 最终清理
    config = cleanup_config(config)

    return config


def cleanup_config(config):
    """清理配置中不合规的内容"""

    # 移除已废弃的全局字段
    deprecated_keys = [
        "global-client-fingerprint",
        "global-client-fingerprint",
    ]
    for key in deprecated_keys:
        if key in config:
            log.info(f"移除废弃字段: {key}")
            del config[key]

    if "proxies" not in config:
        return config

    # 验证每个代理节点
    valid = []
    removed = 0

    for p in config["proxies"]:
        name = p.get("name", "unknown")

        if not p.get("server") or not p.get("port"):
            log.warning(f"  过滤 [{name}]: 缺少 server/port")
            removed += 1
            continue

        # 移除代理级别的废弃字段
        for dk in deprecated_keys:
            if dk in p:
                del p[dk]

        # 验证 REALITY short-id
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

    config["proxies"] = valid

    if removed:
        log.info(f"配置验证: 过滤掉 {removed} 个不合规节点")

    # ── 构建合法名称集合：包含所有节点名 + 所有组名 ──
    valid_names = {p["name"] for p in valid}

    if "proxy-groups" in config:
        for group in config["proxy-groups"]:
            if isinstance(group, dict) and "name" in group:
                valid_names.add(group["name"])

    # 特殊内置名称始终保留
    valid_names.update(["DIRECT", "REJECT", "GLOBAL"])

    # 同步清理 proxy-groups 中的无效引用
    if "proxy-groups" in config:
        for group in config["proxy-groups"]:
            if "proxies" in group and isinstance(group["proxies"], list):
                original_len = len(group["proxies"])
                group["proxies"] = [
                    name for name in group["proxies"]
                    if not isinstance(name, str) or name in valid_names
                ]
                cleaned = original_len - len(group["proxies"])
                if cleaned:
                    log.info(f"  proxy-group [{group.get('name')}]: 移除了 {cleaned} 个无效引用")

    return config


def cleanup_output():
    """只清理本脚本会生成的文件，不动 clash.yaml"""
    os.makedirs("output", exist_ok=True)
    for output_name in TEMPLATES.values():
        path = os.path.join("output", output_name)
        if os.path.exists(path):
            os.remove(path)
            log.info(f"已清理旧文件: {path}")


def save_yaml(data, path):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, Dumper=SafeStrDumper, allow_unicode=True, default_flow_style=False, sort_keys=False)


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

    # ── 1. 读取节点源 ─────────────────────────────────────
    clash_path = os.path.join("output", "clash.yaml")
    if not os.path.exists(clash_path):
        msg = (
            f"❌ <b>配置生成失败</b>\n"
            f"🕐 {now} (北京时间)\n"
            f"原因: {clash_path} 不存在，请先运行订阅转换"
        )
        send_tg_notify(msg)
        sys.exit(1)

    proxies = extract_proxies(clash_path)
    if not proxies:
        msg = (
            f"❌ <b>配置生成失败</b>\n"
            f"🕐 {now} (北京时间)\n"
            f"原因: clash.yaml 中无节点"
        )
        send_tg_notify(msg)
        sys.exit(1)
    log.info(f"提取到 {len(proxies)} 个节点")

    # ── 2. 清理旧输出 ─────────────────────────────────────
    cleanup_output()

    # ── 3. 逐模板生成配置 ─────────────────────────────────
    results = []
    errors = []

    for template_name, output_name in TEMPLATES.items():
        template_path = os.path.join("template", template_name)
        if not os.path.exists(template_path):
            msg_text = f"模板不存在: {template_path}"
            log.warning(msg_text)
            errors.append(msg_text)
            continue

        try:
            config = build_config(template_path, copy.deepcopy(proxies))
            out_path = os.path.join("output", output_name)
            save_yaml(config, out_path)
            file_kb = round(os.path.getsize(out_path) / 1024, 1)
            results.append((output_name, file_kb))
            log.info(f"✅ 已生成 {out_path} ({file_kb} KB)")
        except Exception as e:
            msg_text = f"生成 {output_name} 失败: {e}"
            log.error(msg_text)
            errors.append(msg_text)

    if not results:
        msg = (
            f"❌ <b>配置生成失败</b>\n"
            f"🕐 {now} (北京时间)\n"
            f"原因: 所有模板均处理失败\n"
            + "\n".join(f"· {e}" for e in errors)
        )
        send_tg_notify(msg)
        sys.exit(1)

    elapsed = round(time.time() - start_time, 1)

    # ── 4. GitHub Actions 输出变量 ────────────────────────
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as gh:
            gh.write(f"node_count={len(proxies)}\n")

    # ── 5. Telegram 通知 ──────────────────────────────────
    link_lines = ""
    for output_name, file_kb in results:
        raw_url = get_raw_url(output_name)
        meta = OUTPUT_LABELS.get(output_name, {"icon": "📄", "label": output_name})
        link_lines += f"{meta['icon']} <a href=\"{raw_url}\">点击查看【{meta['label']}】Raw配置</a>\n"

    error_lines = ""
    if errors:
        error_lines = f"\n⚠️ 警告:\n" + "\n".join(f"· {e}" for e in errors)

    msg = (
        f"✅ <b>配置生成完成</b>\n"
        f"🕐 {now} (北京时间)\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🔗 节点数: <b>{len(proxies)}</b> 个\n"
        f"⏱️ 耗时: <b>{elapsed}</b> 秒\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🔗 订阅链接:\n"
        f"{link_lines}"
        f"{error_lines}"
    )
    send_tg_notify(msg)


if __name__ == "__main__":
    main()
