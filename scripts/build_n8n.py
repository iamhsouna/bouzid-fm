#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bouzid FM 2.0 — generate the n8n news automation workflow (Google-only).

Emits a single importable workflow:
    n8n/bouzid-news-google-tts.json

Pipeline:
    [Schedule] -> 4x RSS Read -> Merge -> Filter (recent, de-dupe)
              -> Build bulletin (Gemini text HTTP) -> Extract text
              -> Gemini TTS HTTP -> PCM->WAV (Function) -> Save WAV
              -> ffmpeg WAV->MP3 (Execute Command) -> done

All model / key / voice settings come from n8n environment variables so no
secrets are written into the workflow JSON:
    GEMINI_API_KEY, GEMINI_TEXT_MODEL, GEMINI_TTS_MODEL, GEMINI_TTS_VOICE
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "n8n")

# n8n expression snippets (kept as plain strings so we never confuse them with
# anything else). In n8n, a field value that starts with "=" is an expression
# and may contain  ...  interpolations.
EXPR_API_KEY = " $env.GEMINI_API_KEY "
EXPR_TEXT_MODEL = " $env.GEMINI_TEXT_MODEL || 'gemini-3.5-flash' "
EXPR_TTS_MODEL = " $env.GEMINI_TTS_MODEL || 'gemini-3.1-flash-tts-preview' "
EXPR_STAMP = " $now.toFormat('yyyyLLdd_HHmm') "

FEEDS = [
    "https://www.babnet.net/feed.php",
    "https://www.mosaiquefm.net/ar/rss",
    "https://www.jawharafm.net/ar/rss/showrss/12",
    "https://news.google.com/rss/search?q=%D8%AA%D9%88%D9%86%D8%B3&hl=ar&gl=TN&ceid=TN:ar",
]


def _node(name, ntype, x, y, params, extra=None):
    node = {
        "parameters": params,
        "name": name,
        "type": ntype,
        "typeVersion": 1,
        "position": [x, y],
    }
    if extra:
        node.update(extra)
    return node


def workflow_google():
    nodes = []
    conns = {}

    def link(a, b):
        conns.setdefault(a, {"main": [[]]})
        conns[a]["main"][0].append({"node": b, "type": "main", "index": 0})

    sched = _node("Every 6h", "n8n-nodes-base.scheduleTrigger", 80, 300, {
        "rule": {"interval": [{"field": "hours", "hoursInterval": 6}]}})
    nodes.append(sched)

    rss_names = []
    for i, url in enumerate(FEEDS):
        n = _node("RSS %d" % (i + 1), "n8n-nodes-base.rssFeedRead",
                  320, 120 + i * 130, {"url": url})
        nodes.append(n)
        rss_names.append(n["name"])
        link(sched["name"], n["name"])

    merge = _node("Merge", "n8n-nodes-base.merge", 560, 300, {"mode": "append"})
    nodes.append(merge)
    for rn in rss_names:
        link(rn, merge["name"])

    filt = _node("Filter & de-dupe", "n8n-nodes-base.code", 780, 300, {
        "jsCode": (
            "const seen = new Set();\n"
            "const cutoff = Date.now() - 1000*60*60*24;\n"
            "const out = [];\n"
            "for (const item of $input.all()) {\n"
            "  const j = item.json;\n"
            "  const title = (j.title||'').trim();\n"
            "  if (!title || seen.has(title)) continue;\n"
            "  seen.add(title);\n"
            "  const ts = Date.parse(j.isoDate||j.pubDate||'')||Date.now();\n"
            "  if (ts < cutoff) continue;\n"
            "  out.push({json:{title, link:j.link||''}});\n"
            "}\n"
            "return out.slice(0, 15);")})
    nodes.append(filt)
    link(merge["name"], filt["name"])

    collect = _node("Collect headlines", "n8n-nodes-base.code", 1000, 300, {
        "jsCode": (
            "const lines = $input.all().map(i => '- ' + i.json.title);\n"
            "const prompt = \"أنت محرر نشرة أخبار إذاعية بالعربية الفصحى لإذاعة بوزيد إف إم. "
            "اكتب نشرة موجزة واضحة ومترابطة من العناوين التالية، بدون رموز ولا قوائم:\\n\\n\" + lines.join('\\n');\n"
            "return [{json:{prompt}}];")})
    nodes.append(collect)
    link(filt["name"], collect["name"])

    gen = _node("Gemini text", "n8n-nodes-base.httpRequest", 1220, 300, {
        "method": "POST",
        "url": ("=https://generativelanguage.googleapis.com/v1beta/models/"
                + EXPR_TEXT_MODEL + ":generateContent"),
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "x-goog-api-key", "value": "=" + EXPR_API_KEY},
            {"name": "Content-Type", "value": "application/json"}]},
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({contents:[{parts:[{text:$json.prompt}]}]}) }}",
        "options": {}})
    nodes.append(gen)
    link(collect["name"], gen["name"])

    extract = _node("Extract bulletin", "n8n-nodes-base.code", 1440, 300, {
        "jsCode": (
            "const j = $json;\n"
            "let text='';\n"
            "try { text = j.candidates[0].content.parts.map(p=>p.text||'').join(' '); } catch(e){}\n"
            "text = (text||'').trim();\n"
            "return [{json:{bulletin:text}}];")})
    nodes.append(extract)
    link(gen["name"], extract["name"])

    tts = _node("Gemini TTS", "n8n-nodes-base.httpRequest", 1660, 300, {
        "method": "POST",
        "url": ("=https://generativelanguage.googleapis.com/v1beta/models/"
                + EXPR_TTS_MODEL + ":generateContent"),
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "x-goog-api-key", "value": "=" + EXPR_API_KEY},
            {"name": "Content-Type", "value": "application/json"}]},
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": (
            "={{ JSON.stringify({contents:[{parts:[{text:$json.bulletin}]}],"
            "generationConfig:{responseModalities:['AUDIO'],"
            "speechConfig:{voiceConfig:{prebuiltVoiceConfig:"
            "{voiceName:($env.GEMINI_TTS_VOICE||'Kore')}}}}}) }}"),
        "options": {}})
    nodes.append(tts)
    link(extract["name"], tts["name"])

    pcm = _node("PCM base64 -> WAV", "n8n-nodes-base.code", 1880, 300, {
        "jsCode": (
            "const j = $json;\n"
            "let b64='';\n"
            "try { b64 = j.candidates[0].content.parts[0].inlineData.data; } catch(e){}\n"
            "const pcm = Buffer.from(b64, 'base64');\n"
            "const sr=24000, ch=1, bps=16;\n"
            "const blockAlign = ch*bps/8, byteRate = sr*blockAlign;\n"
            "const h = Buffer.alloc(44);\n"
            "h.write('RIFF',0); h.writeUInt32LE(36+pcm.length,4); h.write('WAVE',8);\n"
            "h.write('fmt ',12); h.writeUInt32LE(16,16); h.writeUInt16LE(1,20);\n"
            "h.writeUInt16LE(ch,22); h.writeUInt32LE(sr,24); h.writeUInt32LE(byteRate,28);\n"
            "h.writeUInt16LE(blockAlign,32); h.writeUInt16LE(bps,34);\n"
            "h.write('data',36); h.writeUInt32LE(pcm.length,40);\n"
            "const wav = Buffer.concat([h, pcm]);\n"
            "return [{json:{}, binary:{data:{data:wav.toString('base64'),"
            "mimeType:'audio/wav', fileName:'bulletin.wav'}}}];")})
    nodes.append(pcm)
    link(tts["name"], pcm["name"])

    save = _node("Save WAV", "n8n-nodes-base.writeBinaryFile", 2100, 300, {
        "fileName": "=/data/news/bulletin_" + EXPR_STAMP + ".wav",
        "dataPropertyName": "data"})
    nodes.append(save)
    link(pcm["name"], save["name"])

    mp3 = _node("WAV -> MP3 (ffmpeg)", "n8n-nodes-base.executeCommand", 2320, 300, {
        "command": (
            "=ffmpeg -y -loglevel error -i /data/news/bulletin_" + EXPR_STAMP
            + ".wav -codec:a libmp3lame -qscale:a 2 /data/news/bulletin_"
            + EXPR_STAMP + ".mp3")})
    nodes.append(mp3)
    link(save["name"], mp3["name"])

    return {
        "name": "Bouzid FM — News (Google TTS)",
        "nodes": nodes,
        "connections": conns,
        "active": False,
        "settings": {},
        "tags": ["bouzid-fm", "google"],
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "bouzid-news-google-tts.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(workflow_google(), f, ensure_ascii=False, indent=2)
    print("Wrote", path)


if __name__ == "__main__":
    main()
