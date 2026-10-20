"""Render the Till on Nemotron demo video with no human recording (Nebius x NVIDIA hackathon).

    source ~/.secrets && python3 tools/make_nebius_video.py OUT.mp4

Copied from ~/till-paypal/tools/make_paypal_video.py. Same look and voices as the Oct 15 Till video: light blue
theme, ElevenLabs Brian (narrator), Chris (owner), Eric (Till). No placeholder voices, ever (Von 2026-10-16).

Drives the real voice page against a throwaway Till server that uses the live Nebius Token Factory and Tavily
keys from .env, records the screen with Playwright, and lays the audio on the timeline: a narrator, the bakery
owner asking each question, and Till answering with the exact text it produced on screen.
Needs Playwright in the python3 running this script (the server itself runs from .venv).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8797
NARRATOR, OWNER, TILL = "nPczCjzI2devNBz1zQrb", "iP95p4xoKVk53GoZ742B", "cjVigY5qzO86Huf0OWal"  # Brian, Chris, Eric

# (kind, text shown or spoken, spoken version for the owner when it differs)
BEATS = [
    ("say", "A bakery owner hears flour went up. Can she still make payroll? "
            "Till answers out loud: a voice CFO running on NVIDIA Nemotron through Nebius Token Factory."),
    ("ask", "My flour supplier just raised prices 12 percent. Can I still make payroll in three weeks?",
     "My flour supplier just raised prices twelve percent. Can I still make payroll in three weeks?"),
    ("say", "Nemotron Super chose the tool. Tavily checked today's flour prices live, and a plain "
            "Python cash engine ran the forecast with payroll. Every number comes from the engine, never the model."),
    ("ask", "Give me my morning brief", None),
    ("ask", "Warn me if cash goes under 12 thousand", "Warn me if cash goes under twelve thousand."),
    ("say", "Anything that changes what Till remembers happens only when the owner asks for it."),
    ("say", "Till on Nemotron. Open source, built on Nebius Token Factory and Tavily."),
]

CACHE = ROOT / "tools" / ".tts_cache"


def tts(text, voice, out, model="eleven_multilingual_v2"):
    CACHE.mkdir(exist_ok=True)
    hit = CACHE / (hashlib.sha1(f"{voice}|{model}|{text}".encode()).hexdigest() + ".mp3")
    if hit.exists():
        shutil.copy(hit, out)
        return _dur(out)
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128",
        data=json.dumps({"text": text, "model_id": model}).encode(),
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "content-type": "application/json"})
    out.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    shutil.copy(out, hit)
    return _dur(out)


def _dur(out):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "csv=p=0", str(out)]).decode())


def main(out_mp4):
    tmp = Path(tempfile.mkdtemp(prefix="till_nebius_video_"))
    env = {**os.environ, "PORT": str(PORT), "TILL_DB": str(tmp / "state.db")}
    for line in (ROOT / ".env").read_text().splitlines():          # live Nebius + Tavily keys, never printed
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip("\"'"))
    server = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "till.server"], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fixed = {}
    for i, beat in enumerate(BEATS):
        voice, text = (OWNER, beat[2] or beat[1]) if beat[0] == "ask" else (NARRATOR, beat[1])
        f = tmp / f"fixed{i}.mp3"
        fixed[i] = (f, tts(text, voice, f))
    time.sleep(1.5)
    clips = []  # (start_seconds, path)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1920, "height": 1080},
                                      record_video_dir=str(tmp), record_video_size={"width": 1920, "height": 1080})
            ctx.add_init_script("window.speechSynthesis && (window.speechSynthesis.speak = () => {});")
            page = ctx.new_page()
            t0 = time.monotonic()
            now = lambda: time.monotonic() - t0
            page.goto(f"http://127.0.0.1:{PORT}/?browservoice")
            page.wait_for_function("document.getElementById('agent').textContent !== '…'", timeout=15000)
            agent = page.inner_text("#agent")
            if "nemotron" not in agent.lower():
                raise SystemExit(f"ABORT: header shows {agent!r}, not live Nemotron; check .env")
            time.sleep(1.0)
            answers = 0
            for i, beat in enumerate(BEATS):
                kind = beat[0]
                f, d = fixed[i]
                if kind in ("say", "dashboard"):
                    if kind == "dashboard":
                        page.goto(f"http://127.0.0.1:{PORT}/dashboard")
                        page.wait_for_selector(".kpi", timeout=15000)
                        time.sleep(1.5)   # let the charts finish drawing
                    clips.append((now(), f))
                    time.sleep(d + (2.0 if kind == "dashboard" else 0.5))
                    continue
                shown = beat[1]
                clips.append((now(), f))
                page.click("#q")
                page.type("#q", shown, delay=max(15, int(d * 1000 * 0.85 / len(shown))))
                time.sleep(0.3)
                page.click("button.send")
                answers += 1
                page.wait_for_function(f"document.querySelectorAll('.msg.till').length >= {answers}", timeout=90000)
                appeared = now()
                reply = page.locator(".msg.till").nth(answers - 1).inner_text()
                f = tmp / f"a{i}.mp3"
                d = tts(reply, TILL, f, model="eleven_flash_v2_5")
                clips.append((appeared + 0.3, f))
                d -= now() - appeared - 0.3
                print(f"[{now():6.1f}s] {shown}\n          -> {reply}")
                time.sleep(max(d, 0) + 0.8)
            time.sleep(1.0)
            total = now()
            video = page.video.path()
            ctx.close()
            browser.close()
    finally:
        server.terminate()

    # mix: every clip delayed to its start time, then muxed under the screen recording
    ins, filt = ["-i", str(video)], []
    for n, (start, f) in enumerate(clips, 1):
        ins += ["-i", str(f)]
        ms = int(start * 1000)
        filt.append(f"[{n}:a]adelay={ms}|{ms}[c{n}]")
    filt.append("".join(f"[c{n}]" for n in range(1, len(clips) + 1)) + f"amix=inputs={len(clips)}:normalize=0[a]")
    subprocess.run(["ffmpeg", "-y", *ins, "-filter_complex", ";".join(filt), "-map", "0:v", "-map", "[a]",
                    "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_mp4],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if total > 170:
        print(f"WARNING: {total:.0f}s is close to the 3 minute limit; re-render (answers vary run to run)")
    print(f"{out_mp4}  {total:.1f}s  agent={agent}")


if __name__ == "__main__":
    main(sys.argv[1])
