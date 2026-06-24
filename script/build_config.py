#!/usr/bin/env python3
"""
从 output/clash.yaml 提取节点，注入到模板中，生成最终配置文件。
模板中 proxy-groups 的 proxies 列表里写 __PROXY_LIST__ 占位符，
脚本会自动将其替换为所有节点名称。
"""
import os
import sys
import time
import logging
import copy
import requests
import yaml
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("builder")

BJT = timezone(timedelta(hours=8))

# ── 模板配置：模板文件名 → 输出文件名 ──
TEMPLATES = {
    "template.yaml": "config.yaml",
    "template-smart.yaml": "config-smart.yaml",
}

# 占位符，写在模板 proxy-groups 的 proxies 列表中
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
    """从 clash.yaml 提取 proxies 列表"""
    with open(clash_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "proxies" not in data:
        return []
    return data["proxies"]


def build_config(template_path, proxies):
    """
    读取模板，注入 proxies，替换 proxy-groups 中的占位符。
    返回最终的 YAML 字典。
    """
    with open(template_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not isinstance(config, dict):
        raise ValueError(f"模板格式错误: {template_path}")

    # 获取所有节点名称
    proxy_names = [p["name"] for p in proxies if "name" in p]

    # 注入 proxies
    config["proxies"] = proxies

    # 替换 proxy-groups 中的占位符
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

    return config


def save_yaml(data, path):
    """保存 YAML 文件，保持 key 顺序"""
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


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

    # ── 2. 逐模板生成配置 ─────────────────────────────────
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

    # ── 3. GitHub Actions 输出变量 ────────────────────────
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as gh:
            gh.write(f"node_count={len(proxies)}\n")

    # ── 4. Telegram 通知 ──────────────────────────────────
    file_lines = ""
    for output_name, file_kb in results:
        raw_url = get_raw_url(output_name)
        file_lines += f"📄 <b>{output_name}</b> ({file_kb} KB)\n"
        file_lines += f"    <a href=\"{raw_url}\">点击下载</a>\n"

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
        f"📦 生成文件:\n"
        f"{file_lines}"
        f"{error_lines}"
    )
    send_tg_notify(msg)


if __name__ == "__main__":
    main()
