import os
import re
import urllib.request
import urllib.error
import ssl
import socket
import base64
import random
import sys
import datetime

# 从环境配置中读取 TG 频道
channel_username = os.environ.get('TG_CHANNEL', 'freeVPNjd')
URL = f"https://t.me/s/{channel_username}"
SUBSCRIBE_REGEX = r'https?://[^\s"\'<>]+token=[a-zA-Z0-9]+'

# 🎯 已经帮你把私人备用链接死死固化在这里了
BACKUP_LINK = "https://d.zrfme.com/vless-all"

def test_tcp_port(server, port, timeout=2.5):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((server, int(port)))
        sock.close()
        return True
    except Exception:
        return False

def is_subscription_alive(link, ssl_context):
    try:
        req = urllib.request.Request(link, headers={'User-Agent': 'Mihomo'})
        with urllib.request.urlopen(req, context=ssl_context, timeout=8) as res:
            raw_data = res.read().decode('utf-8', errors='ignore').strip()

        if not raw_data:
            return False

        decoded_text = raw_data
        if re.match(r'^[a-zA-Z0-9+/=\s]+$', raw_data) and len(raw_data) > 30:
            try:
                missing_padding = len(raw_data) % 4
                if missing_padding:
                    raw_data += '=' * (4 - missing_padding)
                decoded_text = base64.b64decode(raw_data).decode('utf-8', errors='ignore')
                print("   🔓 成功识别并完成 Base64 本地解密。")
            except Exception:
                pass

        servers = re.findall(r'server:\s*([^\s\'",]+)', decoded_text)
        ports = re.findall(r'port:\s*(\d+)', decoded_text)
        
        if not servers:
            ip_port_pairs = re.findall(r'([a-zA-Z0-9][-a-zA-Z0-9]{0,62}(?:\.[a-zA-Z0-9][-a-zA-Z0-9]{0,62})+):(\d+)', decoded_text)
            servers = [item[0] for item in ip_port_pairs]
            ports = [item[1] for item in ip_port_pairs]

        if not servers or not ports:
            print("   ❌ 解析失败：未能从数据中提取到任何节点。")
            return False

        valid_pairs = []
        for s, p in zip(servers, ports):
            s_clean = s.strip("'\" ,\r\n\t").split(',')[0].split('"')[0].split("'")[0]
            try:
                p_clean = int(p)
            except:
                continue
            if s_clean and p_clean:
                if not any(x in s_clean.lower() for x in ["127.0.0.1", "localhost", "github", "google", "网址", "官网", "频道", "公告"]):
                    valid_pairs.append((s_clean, p_clean))

        if not valid_pairs:
            print("   ❌ 过滤干扰项后，该订阅内无可用的有效代理节点。")
            return False

        print(f"   📦 成功清洗出 {len(valid_pairs)} 个节点，开始抽检连通性...")

        sample_size = min(3, len(valid_pairs))
        sample_pairs = random.sample(valid_pairs, sample_size)
        
        for srv, prt in sample_pairs:
            print(f"      ⚡ 正在检测节点: {srv}:{prt} ...")
            if test_
