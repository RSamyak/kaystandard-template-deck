#!/usr/bin/env python3
"""Capture a reveal.js HTML deck to PDF, one reveal step per page.

Opens the HTML in its normal layout.  Does not use ?print-pdf.
Each page is a vector print of that screen.

Usage:
  .venv/bin/python scripts/deck_to_pdf.py _output/slides.html "<title>.pdf" [extra_seconds]
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pymupdf
import websocket  # websocket-client

DECK_W = 1280
DECK_H = 720
MAX_STEPS = 300
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = os.path.join(REPO, "dump", "pdf")
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
PREPARE = """
(async () => {
  await document.fonts.ready;
  await Promise.all([...document.images].map(async (image) => {
    if (!image.complete) {
      await new Promise((resolve) => {
        image.addEventListener("load", resolve, {once: true});
        image.addEventListener("error", resolve, {once: true});
      });
    }
    if (image.decode) {
      try { await image.decode(); } catch (_) {}
    }
  }));
  Reveal.configure({
    loop: false,
    fragments: true,
    transition: "none",
    backgroundTransition: "none",
    autoAnimate: false,
  });
  Reveal.slide(0, 0, 0);
  Reveal.slide(0, 0);
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  return true;
})()
"""
ADVANCE = """
(async () => {
  Reveal.next();
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  return true;
})()
"""


def find_chrome():
    chosen = os.environ.get("CHROME")
    if chosen:
        return chosen
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("Chrome not found.  Set CHROME to the browser path.")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    html = os.path.abspath(sys.argv[1])
    out = os.path.abspath(sys.argv[2])
    extra = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    chrome = find_chrome()
    port = free_port()
    out_dir = os.path.dirname(out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    shutil.rmtree(SCRATCH, ignore_errors=True)
    profile = os.path.join(SCRATCH, "chrome-profile")
    os.makedirs(profile)
    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-allow-origins=*",
            "--hide-scrollbars",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            f"--window-size={DECK_W},{DECK_H}",
            "file://" + html,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    document = pymupdf.open()
    page_count = 0
    try:
        ws_url = None
        for _ in range(60):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                pages = [tab for tab in tabs if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl")]
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
                if message.get("id") != this_id:
                    continue
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error']}")
                return message["result"]

        def evaluate(expression, await_promise=False):
            result = call("Runtime.evaluate", {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            })
            return result.get("result", {}).get("value")

        call("Page.enable")
        call("Runtime.enable")
        call("Emulation.setDeviceMetricsOverride", {
            "width": DECK_W,
            "height": DECK_H,
            "deviceScaleFactor": 1,
            "mobile": False,
        })
        call("Emulation.setEmulatedMedia", {"media": "screen"})

        for _ in range(120):
            if evaluate("typeof Reveal !== 'undefined' && Reveal.isReady && Reveal.isReady()"):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("Reveal never became ready")

        evaluate(PREPARE, await_promise=True)

        def state():
            return evaluate("JSON.stringify(Reveal.getState())")

        for _ in range(MAX_STEPS):
            printed = call("Page.printToPDF", {
                "printBackground": True,
                "displayHeaderFooter": False,
                "paperWidth": DECK_W / 96,
                "paperHeight": DECK_H / 96,
                "marginTop": 0,
                "marginBottom": 0,
                "marginLeft": 0,
                "marginRight": 0,
                "pageRanges": "1",
                "preferCSSPageSize": False,
            })
            page_pdf = pymupdf.open(stream=base64.b64decode(printed["data"]), filetype="pdf")
            # Chrome prints 96 CSS pixels per inch.  PDF points are 72 per inch,
            # so a 1280×720 screen arrives as 960×540.  Place it at deck size.
            page = document.new_page(width=DECK_W, height=DECK_H)
            page.show_pdf_page(pymupdf.Rect(0, 0, DECK_W, DECK_H), page_pdf, 0)
            page_pdf.close()
            page_count += 1

            before = state()
            evaluate(ADVANCE, await_promise=True)
            deadline = time.time() + 2
            while state() == before and time.time() < deadline:
                time.sleep(0.05)
            if state() == before:
                break
            if extra:
                time.sleep(extra)

        ws.close()
        document.save(out, garbage=4, deflate=True)
        print(f"wrote {out} ({os.path.getsize(out)} bytes, {page_count} pages)")
    finally:
        document.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(SCRATCH, ignore_errors=True)

    if not page_count:
        raise RuntimeError("No pages captured")


if __name__ == "__main__":
    main()
