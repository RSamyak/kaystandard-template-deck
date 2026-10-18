#!/usr/bin/env python3
"""Capture a reveal.js HTML deck to PDF, one reveal step per page.

Opens the HTML in its normal layout. Does not use ?print-pdf.

Usage:
  .venv/bin/python tools/deck_to_pdf.py _output/slides.html "<name>.pdf" [settle_seconds]
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

import pymupdf
import websocket  # websocket-client

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9224
DECK_W = 1280
DECK_H = 720
SCALE = 2
JPEG_QUALITY = 90
DEFAULT_SETTLE = 0.45
MAX_STEPS = 300

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = os.path.join(REPO, "dump", "pdf")


def main():
    html = os.path.abspath(sys.argv[1])
    out = os.path.abspath(sys.argv[2])
    settle = float(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_SETTLE
    if not os.path.exists(CHROME):
        raise RuntimeError(f"Chrome not found: {CHROME}")

    out_dir = os.path.dirname(out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    shutil.rmtree(SCRATCH, ignore_errors=True)
    profile = os.path.join(SCRATCH, "chrome-profile")
    frames = os.path.join(SCRATCH, "frames")
    os.makedirs(profile)
    os.makedirs(frames)

    proc = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-allow-origins=*",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--remote-debugging-port={PORT}",
            f"--user-data-dir={profile}",
            f"--window-size={DECK_W},{DECK_H}",
            "file://" + html,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pngs = []
    try:
        ws_url = None
        for _ in range(60):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                pages = [t for t in tabs if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            time.sleep(0.25)
        if not ws_url:
            raise RuntimeError("Chrome DevTools endpoint never came up")

        ws = websocket.create_connection(ws_url, max_size=None)
        message_id = 0

        def call(method, params=None):
            nonlocal message_id
            message_id += 1
            this_id = message_id
            ws.send(json.dumps({"id": this_id, "method": method, "params": params or {}}))
            while True:
                message = json.loads(ws.recv())
                if message.get("id") == this_id:
                    if "error" in message:
                        raise RuntimeError(f"{method} failed: {message['error']}")
                    return message

        def evaluate(expression):
            result = call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
            return result["result"].get("result", {}).get("value")

        call("Page.enable")
        call("Runtime.enable")
        call("Emulation.setDeviceMetricsOverride", {
            "width": DECK_W,
            "height": DECK_H,
            "deviceScaleFactor": SCALE,
            "mobile": False,
        })

        for _ in range(120):
            if evaluate("typeof Reveal !== 'undefined' && Reveal.isReady && Reveal.isReady()"):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("Reveal never became ready")

        evaluate("Reveal.configure({loop:false, fragments:true}); true")
        evaluate("Reveal.slide(0,0,0); true")
        time.sleep(settle)
        # indexf -1 is the page before the first fragment.
        evaluate("Reveal.slide(0,0); true")
        time.sleep(settle)

        def state():
            return evaluate("JSON.stringify(Reveal.getState())")

        def capture():
            result = call("Page.captureScreenshot", {
                "format": "jpeg",
                "quality": JPEG_QUALITY,
                "fromSurface": True,
                "captureBeyondViewport": False,
                "clip": {"x": 0, "y": 0, "width": DECK_W, "height": DECK_H, "scale": 1},
            })
            path = os.path.join(frames, f"page-{len(pngs):04d}.jpg")
            with open(path, "wb") as handle:
                handle.write(base64.b64decode(result["result"]["data"]))
            pngs.append(path)

        for _ in range(MAX_STEPS):
            capture()
            before = state()
            evaluate("Reveal.next(); true")
            time.sleep(settle)
            if state() == before:
                break

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    if not pngs:
        raise RuntimeError("No pages captured")

    document = pymupdf.open()
    for png in pngs:
        page = document.new_page(width=DECK_W, height=DECK_H)
        page.insert_image(pymupdf.Rect(0, 0, DECK_W, DECK_H), filename=png)
    document.save(out)
    document.close()
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print(f"wrote {out} ({os.path.getsize(out)} bytes, {len(pngs)} pages)")


if __name__ == "__main__":
    main()
