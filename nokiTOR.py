#!/usr/bin/env python3

import curses
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from shutil import which
from typing import Tuple

import requests

SOCKS_PORT = 9050
PROXY_URL = f"socks5://127.0.0.1:{SOCKS_PORT}"
TORRC_CANDIDATES = ("/etc/tor/torrc", "/etc/torrc", "/usr/local/etc/tor/torrc")
SPEED_COUNTRIES = "{de},{nl},{us},{fr},{gb},{ch},{se}"
CHECK_TIMEOUT = 6
AUTO_REFRESH_SEC = 30


class TorProxyApp:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.running = True
        self.proxy = PROXY_URL
        self.proxies = {"http": self.proxy, "https": self.proxy}
        self.auto_enabled = False
        self.next_refresh = 0.0
        self.status_msg = ""
        self.status_color = 0
        self.loading = False
        self.loading_text = ""
        self.tor_active = False
        self.tor_connected = False
        self.real_ip = "—"
        self.tor_ip = "—"
        self.tor_country = ""
        self.optimized = False
        self.lock = threading.Lock()
        self.service_mode, self.tor_service = self._detect_tor_service()
        self.torrc_path = self._detect_torrc_path()

        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_RED, -1)
        curses.init_pair(5, curses.COLOR_WHITE, -1)

        self.stdscr.timeout(100)
        self.stdscr.keypad(True)

    def _run_async(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def _run(self, args, sudo=False):
        cmd = ["sudo", *args] if sudo else args
        return subprocess.run(cmd, capture_output=True, text=True)

    def _detect_tor_service(self):
        if which("systemctl"):
            for name in ("tor.service", "tor@default.service", "tor@default", "tor"):
                probe = self._run(["systemctl", "show", "-p", "LoadState", "--value", name])
                if probe.returncode == 0 and probe.stdout.strip() == "loaded":
                    return "systemctl", name
        if which("service"):
            return "service", "tor"
        return "", ""

    def _detect_torrc_path(self):
        for path in TORRC_CANDIDATES:
            if Path(path).is_file():
                return path
        return TORRC_CANDIDATES[0]

    def _tor_action(self, action: str) -> bool:
        if self.service_mode == "systemctl" and self.tor_service:
            return self._run(["systemctl", action, self.tor_service], sudo=True).returncode == 0
        if self.service_mode == "service":
            return self._run(["service", self.tor_service, action], sudo=True).returncode == 0
        return False

    def is_tor_installed(self) -> bool:
        return which("tor") is not None

    def is_tor_active(self) -> bool:
        if self.service_mode == "systemctl" and self.tor_service:
            return self._run(["systemctl", "is-active", self.tor_service]).stdout.strip() == "active"
        if self.service_mode == "service":
            return self._run(["service", self.tor_service, "status"]).returncode == 0
        return False

    def _get(self, url: str, use_proxy=False, timeout=CHECK_TIMEOUT):
        kwargs = {"timeout": timeout}
        if use_proxy:
            kwargs["proxies"] = self.proxies
        return requests.get(url, **kwargs)

    def verify_tor(self) -> bool:
        try:
            return self._get("https://check.torproject.org/api/ip", use_proxy=True).json().get("IsTor", False)
        except Exception:
            return False

    def check_dns(self) -> bool:
        try:
            self._get("https://am.i.mullvad.net/json", use_proxy=True)
            return True
        except Exception:
            return False

    def get_real_ip(self) -> str:
        try:
            return self._get("https://api.ipify.org?format=json", timeout=4).json().get("ip", "—")
        except Exception:
            return "—"

    def get_tor_ip(self) -> Tuple[str, str]:
        try:
            data = self._get("http://ip-api.com/json/", use_proxy=True).json()
            return data.get("query", "—"), data.get("countryCode", "")
        except Exception:
            return "—", ""

    def _read_torrc(self) -> str:
        result = self._run(["cat", self.torrc_path], sudo=True)
        return result.stdout if result.returncode == 0 else ""

    def _write_torrc(self, content: str, append=True) -> bool:
        args = ["sudo", "tee", *( ["-a"] if append else []), self.torrc_path]
        try:
            proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            proc.communicate(content.encode())
            return proc.returncode == 0
        except Exception:
            return False

    def is_optimized(self) -> bool:
        return "# NokiTorProxy" in self._read_torrc()

    def copy_to_clipboard(self) -> bool:
        for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"], ["wl-copy"]):
            if which(cmd[0]) is None:
                continue
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
                proc.communicate(self.proxy.encode())
                if proc.returncode == 0:
                    return True
            except Exception:
                continue
        return False

    def refresh_data(self):
        def worker():
            with self.lock:
                self.tor_active = self.is_tor_active()
                self.tor_connected = self.verify_tor() if self.tor_active else False
                self.real_ip = self.get_real_ip()
                self.tor_ip, self.tor_country = self.get_tor_ip() if self.tor_active else ("—", "")
                self.optimized = self.is_optimized()

        self._run_async(worker)

    def action_new_ip(self):
        def worker():
            self.loading = True
            self.loading_text = "Getting new IP..."
            ok = self._tor_action("restart")
            for i in range(50):
                self.loading_text = f"Connecting... {i // 10 + 1}s"
                time.sleep(0.1)
            self.loading = False
            if not ok:
                self.show_status("✗ TOR restart failed", 4)
                return
            if self.auto_enabled:
                self.next_refresh = time.time() + AUTO_REFRESH_SEC
            self.refresh_data()
            self.show_status("✓ New IP", 2)

        self._run_async(worker)

    def action_copy(self):
        self.show_status("✓ Copied to clipboard", 2) if self.copy_to_clipboard() else self.show_status(self.proxy, 5)

    def action_optimize(self):
        def worker():
            if self.is_optimized():
                self.show_status("Already optimized", 3)
                return
            self.loading = True
            self.loading_text = "Optimizing..."
            cfg = f"\n# NokiTorProxy\nExitNodes {SPEED_COUNTRIES}\nStrictNodes 0\n"
            if not self._write_torrc(cfg):
                self.loading = False
                self.show_status("✗ Error", 4)
                return
            self.loading_text = "Restarting TOR..."
            if not self._tor_action("restart"):
                self.loading = False
                self.show_status("✗ TOR restart failed", 4)
                return
            time.sleep(5)
            self.loading = False
            self.refresh_data()
            self.show_status("✓ Optimized", 2)

        self._run_async(worker)

    def action_reset(self):
        def worker():
            if not self.is_optimized():
                self.show_status("Not optimized", 3)
                return
            self.loading = True
            self.loading_text = "Resetting..."
            cleaned = re.sub(r"\n?# NokiTorProxy\nExitNodes [^\n]+\nStrictNodes [^\n]+\n?", "", self._read_torrc())
            if not self._write_torrc(cleaned, append=False):
                self.loading = False
                self.show_status("✗ Error", 4)
                return
            self.loading_text = "Restarting TOR..."
            if not self._tor_action("restart"):
                self.loading = False
                self.show_status("✗ TOR restart failed", 4)
                return
            time.sleep(5)
            self.loading = False
            self.refresh_data()
            self.show_status("✓ Reset", 2)

        self._run_async(worker)

    def action_dns_test(self):
        def worker():
            self.loading = True
            self.loading_text = "Testing DNS..."
            tor_ok = self.verify_tor()
            dns_ok = self.check_dns()
            self.loading = False
            if tor_ok and dns_ok:
                self.show_status("✓ TOR OK, DNS protected", 2)
            elif tor_ok:
                self.show_status("⚠ TOR OK, DNS unknown", 3)
            else:
                self.show_status("✗ TOR not connected", 4)

        self._run_async(worker)

    def action_toggle_auto(self):
        self.auto_enabled = not self.auto_enabled
        if self.auto_enabled:
            self.next_refresh = time.time() + AUTO_REFRESH_SEC
            self.show_status(f"✓ Auto ON ({AUTO_REFRESH_SEC}s)", 2)
            return
        self.show_status("○ Auto OFF", 5)

    def show_status(self, msg: str, color: int):
        self.status_msg = msg
        self.status_color = color

        def clear():
            time.sleep(3)
            if self.status_msg == msg:
                self.status_msg = ""

        self._run_async(clear)

    def auto_refresh_worker(self):
        while self.running:
            time.sleep(1)
            if self.auto_enabled and not self.loading and time.time() >= self.next_refresh:
                self.action_new_ip()

    def draw(self):
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        if h < 14 or w < 44:
            self.stdscr.addstr(0, 0, "Terminal too small")
            return

        colors = {k: curses.color_pair(v) for k, v in {"c": 1, "g": 2, "y": 3, "r": 4, "w": 5}.items()}
        box_w, box_h = 44, 13
        start_x, start_y = (w - box_w) // 2, (h - box_h) // 2

        def put(y, x, text, attr=0):
            try:
                self.stdscr.addstr(y, x, text, attr)
            except curses.error:
                pass

        put(start_y, start_x, "╭" + "─" * (box_w - 2) + "╮", colors["c"])
        put(start_y + 1, start_x, "│", colors["c"])
        put(start_y + 1, start_x + (box_w - len("lolz.live/gay1234")) // 2, "lolz.live/gay1234", colors["w"] | curses.A_BOLD)
        put(start_y + 1, start_x + box_w - 1, "│", colors["c"])

        subtitle = "TorProxy manager"
        put(start_y + 2, start_x, "│", colors["c"])
        put(start_y + 2, start_x + (box_w - len(subtitle)) // 2, subtitle, colors["c"])
        put(start_y + 2, start_x + box_w - 1, "│", colors["c"])
        put(start_y + 3, start_x, "├" + "─" * (box_w - 2) + "┤", colors["c"])

        put(start_y + 4, start_x, "│", colors["c"])
        put(start_y + 4, start_x + 2, "Status:", colors["w"])
        if self.loading:
            put(start_y + 4, start_x + 12, "◐ " + self.loading_text, colors["y"])
        elif self.tor_connected:
            put(start_y + 4, start_x + 12, "● Connected", colors["g"])
        elif self.tor_active:
            put(start_y + 4, start_x + 12, "○ Starting", colors["y"])
        else:
            put(start_y + 4, start_x + 12, "○ Offline", colors["r"])
        put(start_y + 4, start_x + box_w - 1, "│", colors["c"])

        put(start_y + 5, start_x, "│", colors["c"])
        put(start_y + 5, start_x + 2, "Real IP:", colors["w"])
        put(start_y + 5, start_x + 12, self.real_ip, colors["w"])
        put(start_y + 5, start_x + box_w - 1, "│", colors["c"])

        put(start_y + 6, start_x, "│", colors["c"])
        put(start_y + 6, start_x + 2, "TOR IP:", colors["w"])
        tor_str = self.tor_ip + (f" [{self.tor_country}]" if self.tor_country else "")
        put(start_y + 6, start_x + 12, tor_str, colors["g"])
        put(start_y + 6, start_x + box_w - 1, "│", colors["c"])

        put(start_y + 7, start_x, "├" + "─" * (box_w - 2) + "┤", colors["c"])
        put(start_y + 8, start_x, "│", colors["c"])
        put(start_y + 8, start_x + 2, "[r]", colors["g"])
        put(start_y + 8, start_x + 5, " Reset IP  ", colors["w"])
        put(start_y + 8, start_x + 14, "[c]", colors["g"])
        put(start_y + 8, start_x + 17, " Copy  ", colors["w"])
        put(start_y + 8, start_x + 24, "[d]", colors["g"])
        put(start_y + 8, start_x + 27, " DNS", colors["w"])
        put(start_y + 8, start_x + box_w - 1, "│", colors["c"])

        put(start_y + 9, start_x, "│", colors["c"])
        put(start_y + 9, start_x + 2, "[o]", colors["g"])
        put(start_y + 9, start_x + 5, " Opti ", colors["w"])
        put(start_y + 9, start_x + 12, "●" if self.optimized else "○", colors["g"] if self.optimized else colors["w"])
        put(start_y + 9, start_x + 14, "[a]", colors["g"])
        put(start_y + 9, start_x + 17, " Auto ", colors["w"])
        put(start_y + 9, start_x + 23, "●" if self.auto_enabled else "○", colors["g"] if self.auto_enabled else colors["w"])
        if self.auto_enabled and not self.loading:
            put(start_y + 9, start_x + 25, f"{max(0, int(self.next_refresh - time.time())):2d}s", colors["c"])
        put(start_y + 9, start_x + box_w - 1, "│", colors["c"])

        put(start_y + 10, start_x, "│", colors["c"])
        put(start_y + 10, start_x + 2, "[x]", colors["g"])
        put(start_y + 10, start_x + 5, " Reset   ", colors["w"])
        put(start_y + 10, start_x + 14, "[q]", colors["g"])
        put(start_y + 10, start_x + 17, " Quit", colors["w"])
        put(start_y + 10, start_x + box_w - 1, "│", colors["c"])

        put(start_y + 11, start_x, "╰" + "─" * (box_w - 2) + "╯", colors["c"])
        if self.status_msg:
            put(start_y + 12, start_x + 2, self.status_msg, curses.color_pair(self.status_color))

        self.stdscr.refresh()

    def run(self):
        if not self.is_tor_installed():
            self.stdscr.addstr(0, 0, "TOR not installed. Install package: tor")
            self.stdscr.refresh()
            self.stdscr.getch()
            return
        if not self.service_mode:
            self.stdscr.addstr(0, 0, "No service manager found for TOR (systemctl/service)")
            self.stdscr.refresh()
            self.stdscr.getch()
            return

        if not self.is_tor_active():
            self.loading = True
            self.loading_text = "Starting TOR..."
            self.draw()
            self._tor_action("start")
            time.sleep(3)
            self.loading = False

        self.refresh_data()
        self._run_async(self.auto_refresh_worker)

        def updater():
            while self.running:
                time.sleep(30)
                if not self.loading:
                    self.refresh_data()

        self._run_async(updater)

        while self.running:
            self.draw()
            try:
                key = self.stdscr.getch()
            except curses.error:
                continue
            if key == -1:
                continue
            ch = chr(key).lower() if 0 <= key < 256 else ""
            if ch == "q":
                self.running = False
            elif ch == "r" and not self.loading:
                self.action_new_ip()
            elif ch == "c":
                self.action_copy()
            elif ch == "o" and not self.loading:
                self.action_optimize()
            elif ch == "x" and not self.loading:
                self.action_reset()
            elif ch == "d" and not self.loading:
                self.action_dns_test()
            elif ch == "a":
                self.action_toggle_auto()


def main(stdscr):
    TorProxyApp(stdscr).run()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
