##!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, time, threading, random, hashlib, io, re
import requests, soundfile as sf, sounddevice as sd, urllib3, xml.etree.ElementTree as ET
from obswebsocket import obsws, requests as obs_requests

# —— 关闭 HTTPS 警告 —— 
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

print("▶ obs_newsflash_cloned.py loaded (嘴动修正版)")

# —— 路径自动定位 —— 
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
NEWS_FILE  = os.path.join(BASE_DIR, "newsflash.txt")
AUDIO_FILE = os.path.join(BASE_DIR, "news.wav")

# —— TTS 配置 —— 
ELEVEN_KEY   = "sk_0c277a4b585365f68ca7dcbb8ebb6793b1ca7bf2cd2a0d32"
VOICE_ID     = "rxBF5ZexDzHsfd4beXzc"
TTS_URL      = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"
HEADERS_TTS  = {"xi-api-key":ELEVEN_KEY, "Content-Type":"application/json"}

# —— OBS WebSocket 配置 —— 
OBS_HOST     = "localhost"
OBS_PORT     = 4455
OBS_PASSWORD = "123456"
SCENE_NAME   = "场景"
TEXT_SOURCE  = "news_subtitle"

# —— 四帧图层名 —— 
SRC_IDLE_OPEN    = "cz_closed_open"   # 闲置：嘴闭+眼开
SRC_IDLE_CLOSED  = "cz_closed_closed" # 闲置：嘴闭+眼闭
SRC_TALK_OPEN    = "cz_open_open"     # 讲话：嘴开+眼开
SRC_TALK_CLOSED  = "cz_open_closed"   # 讲话：嘴开+眼闭 （仅作浏览器示例，实际不再用）

# —— 全局状态 —— 
last_hash_local = ""
last_hash_pa    = ""
last_hash_wu    = ""
last_hash_od    = ""
is_speaking     = False

def fetch_panewslab_flash():
    rss = "https://rss.panewslab.com/zh/tvsq/rss"
    try:
        r = requests.get(rss, timeout=8, verify=False); r.raise_for_status()
        root = ET.fromstring(r.content)
        it = root.find("./channel/item")
        return it.find("title").text.strip() if it is not None else None
    except Exception as e:
        print("🔍 PANewsLab 抓取/解析失败：", e)
        return None

def fetch_wublock_flash():
    url = "https://www.wublock123.com/"
    try:
        r = requests.get(url, timeout=8, verify=False); r.raise_for_status()
        m = re.search(r'最新文章[\s\S]*?【\d+†([^】]+)】', r.text)
        if m: return m.group(1).strip()
        for L in r.text.splitlines():
            if L.strip().startswith("【") and "†" in L and "】" in L:
                return L.split("†",1)[1].rstrip("】").strip()
    except Exception as e:
        print("🔍 吴说 抓取失败：", e)
    return None

def fetch_odaily_flash():
    url = "https://www.odaily.news/v1/openapi/feeds"
    try:
        r = requests.get(url, timeout=8, verify=False); r.raise_for_status()
        for it in r.json().get("data",{}).get("arr_news",[]):
            if it.get("type")=="newsflashes":
                return it.get("title","").strip()
    except Exception as e:
        print("🔍 ODaily API 失败：", e)
    return None

# —— 连接 OBS WebSocket —— 
ws = obsws(OBS_HOST, OBS_PORT, OBS_PASSWORD); ws.connect()
res = ws.call(obs_requests.GetSceneItemList(sceneName=SCENE_NAME))
id_map = {it["sourceName"]: it["sceneItemId"] for it in res.getSceneItems()}

def show(src):
    for name, sid in id_map.items():
        if name in (SRC_IDLE_OPEN, SRC_IDLE_CLOSED, SRC_TALK_OPEN, SRC_TALK_CLOSED):
            ws.call(obs_requests.SetSceneItemEnabled(
                sceneName=SCENE_NAME,
                sceneItemId=sid,
                sceneItemEnabled=(name == src)
            ))

def blink_loop():
    show(SRC_IDLE_OPEN)
    while True:
        if is_speaking:
            # — 讲话时：嘴动（闭→开→闭…），眼睛保持睁开 —
            show(SRC_TALK_OPEN)
            time.sleep(random.uniform(0.1,0.15))
            show(SRC_IDLE_OPEN)
            time.sleep(random.uniform(0.1,0.15))
        else:
            # — 闲置时：缓慢眨眼（睁眼 5–12s，闭眼 0.2s）—
            show(SRC_IDLE_OPEN)
            time.sleep(random.uniform(5.0,12.0))
            show(SRC_IDLE_CLOSED)
            time.sleep(0.2)

threading.Thread(target=blink_loop, daemon=True).start()

def update_subtitle(txt):
    ws.call(obs_requests.SetInputSettings(
        inputName=TEXT_SOURCE,
        inputSettings={"text": txt}
    ))

def clear_subtitle():
    update_subtitle("")

def clone_and_play(txt):
    global is_speaking
    is_speaking = True
    show(SRC_TALK_OPEN); update_subtitle(txt)
    try:
        r = requests.post(
            TTS_URL,
            json={"text":txt,"voice_settings":{"stability":0.75,"similarity_boost":0.85}},
            headers=HEADERS_TTS, stream=True, timeout=15, verify=False
        )
        r.raise_for_status()
    except Exception as e:
        print("❌ TTS 请求失败：", e)
        is_speaking = False; clear_subtitle(); show(SRC_IDLE_OPEN)
        return

    buf = io.BytesIO()
    for chunk in r.iter_content(4096): buf.write(chunk)
    with open(AUDIO_FILE, "wb") as f: f.write(buf.getvalue())

    data, fs = sf.read(AUDIO_FILE, dtype="float32")
    sd.play(data, fs); sd.wait()

    clear_subtitle()
    is_speaking = False
    show(SRC_IDLE_OPEN)

def main_loop():
    global last_hash_local, last_hash_pa, last_hash_wu, last_hash_od
    print("🔄 监听 → 本地 / PANewsLab / 吴说 / ODaily")
    while True:
        # —— 本地优先 —— 
        if os.path.exists(NEWS_FILE):
            txt = open(NEWS_FILE, encoding="utf-8").read().strip()
            if txt:
                h = hashlib.md5(f"local:{txt}".encode()).hexdigest()
                if h != last_hash_local:
                    last_hash_local = h
                    print("🆕 [Local]  ", txt)
                    clone_and_play(txt)
                    time.sleep(3)
                    continue
        # —— PANewsLab —— 
        pa = fetch_panewslab_flash()
        if pa:
            h = hashlib.md5(pa.encode()).hexdigest()
            if h != last_hash_pa:
                last_hash_pa = h
                print("🆕 [PANewsLab]", pa)
                clone_and_play(pa)
        # —— 吴说 —— 
        wu = fetch_wublock_flash()
        if wu:
            h = hashlib.md5(wu.encode()).hexdigest()
            if h != last_hash_wu:
                last_hash_wu = h
                print("🆕 [WuSay]  ", wu)
                clone_and_play(wu)
        # —— ODaily —— 
        od = fetch_odaily_flash()
        if od:
            h = hashlib.md5(od.encode()).hexdigest()
            if h != last_hash_od:
                last_hash_od = h
                print("🆕 [ODaily] ", od)
                clone_and_play(od)
        time.sleep(5)

if __name__=="__main__":
    main_loop()
