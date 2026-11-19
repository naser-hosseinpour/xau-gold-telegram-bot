#!/usr/bin/env python3
"""Keep the analysis and channel bots running: restart one that stops, and report outages to Telegram.

Only one supervisor can run at a time. It places no orders and starts nothing else.
"""
import json
import os
import socket
import subprocess
import sys
import time

import xau

HERE = os.path.dirname(os.path.abspath(__file__))
BOTS = {"تحلیل طلا": "analysis.py", "اعلام کانال‌ها": "channels.py"}
HEARTBEAT = os.path.join(xau.HOME, "supervisor-heartbeat.json")
CHILDREN = os.path.join(xau.HOME, "supervisor-children.json")
LOCK_PORT = 48917
LOOK_EVERY = 30
OUTAGE = 5 * 60      # a longer silence than this counts as an outage
RESTART_WAIT = 60
NO_WINDOW = 0x08000000


def outage_message(last_seen, now):
    """The line to post on startup, or None when the supervisor was only just running."""
    if not last_seen or now - last_seen < OUTAGE:
        return None
    return "⚠️ ربات‌ها حدود %d دقیقه قطع بودند و دوباره روشن شدند. در این مدت تحلیل یا اعلامی ارسال نشده است." % ((now - last_seen) // 60)


def restart_message(name):
    return "⚠️ ربات %s متوقف شد و دوباره اجرا می‌شود." % name


def due_for_restart(stopped_at, now):
    return now - stopped_at >= RESTART_WAIT


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, data):
    os.makedirs(xau.HOME, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def notify(cfg, text):
    try:
        xau.send_telegram(cfg, text)
    except Exception as e:
        print("Could not notify: %s" % type(e).__name__, flush=True)


def stop_old_children():
    """End bots a killed supervisor left behind, checking each one is still a Python process."""
    for pid in read_json(CHILDREN) or []:
        listed = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                                capture_output=True, text=True, creationflags=NO_WINDOW).stdout.lower()
        if "python" in listed:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=NO_WINDOW)


def start(script):
    log = open(os.path.join(xau.HOME, script.replace(".py", ".log")), "a", encoding="utf-8", errors="replace")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    return subprocess.Popen([sys.executable, os.path.join(HERE, script), "run"], cwd=HERE, stdout=log, stderr=log,
                            env=env, creationflags=NO_WINDOW)


def run():
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        print("Another supervisor is already running.", flush=True)
        return 0
    cfg = xau.telegram_config()
    stop_old_children()
    seen = read_json(HEARTBEAT) or {}
    message = outage_message(seen.get("at"), time.time())
    if message:
        notify(cfg, message)
    running = {name: start(script) for name, script in BOTS.items()}
    stopped = {}
    while True:
        now = time.time()
        for name, script in BOTS.items():
            if running[name].poll() is None:
                continue
            if name not in stopped:
                stopped[name] = now
                notify(cfg, restart_message(name))
            if due_for_restart(stopped[name], now):
                running[name] = start(script)
                del stopped[name]
        write_json(HEARTBEAT, {"at": now})
        write_json(CHILDREN, [p.pid for p in running.values()])
        time.sleep(LOOK_EVERY)


def main():
    os.makedirs(xau.HOME, exist_ok=True)
    if sys.stdout is None or sys.executable.lower().endswith("pythonw.exe"):
        sys.stdout = sys.stderr = open(os.path.join(xau.HOME, "supervisor.log"), "a", encoding="utf-8", errors="replace")
    try:
        return run()
    except xau.XauError as e:
        print("supervisor.py: %s" % e, flush=True)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
