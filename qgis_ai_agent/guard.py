#!/usr/bin/env python3
"""Process guardian that guarantees child AI CLI process is terminated if QGIS closes or crashes.

This script runs as a lightweight supervisory wrapper around the AI CLI.
It monitors the parent QGIS process via:
1. Linux kernel hook: PR_SET_PDEATHSIG (sends SIGKILL the instant parent QGIS dies).
2. Cross-platform watchdog thread: monitors parent PID liveness every 300ms.
3. Signal forwarding: SIGTERM, SIGINT, SIGHUP -> kills child process tree.
4. PID tracking: writes child CLI PID to QGIS_AI_PID_FILE for instant direct targeting.
"""
import ctypes
import glob
import os
import signal
import subprocess
import sys
import threading
import time


def _get_process_tree(root_pid, protected_pids):
    """Recursively discover all descendant PIDs of root_pid."""
    descendants = set()
    to_visit = [root_pid]
    while to_visit:
        curr = to_visit.pop(0)
        found = set()
        # 1. Linux /proc filesystem (multi-threaded task check)
        for child_file in glob.glob(f"/proc/{curr}/task/*/children"):
            try:
                with open(child_file, "r") as f:
                    for c in f.read().split():
                        if c.isdigit():
                            found.add(int(c))
            except Exception:
                pass
        # 2. pgrep -P fallback (cross-unix / macOS)
        try:
            out = subprocess.check_output(["pgrep", "-P", str(curr)], stderr=subprocess.DEVNULL, text=True)
            for c in out.split():
                if c.isdigit():
                    found.add(int(c))
        except Exception:
            pass
        for c in found:
            if c not in descendants and c not in protected_pids:
                descendants.add(c)
                to_visit.append(c)
    return list(descendants)


def main():
    if len(sys.argv) < 4:
        sys.stderr.write("Usage: guard.py <parent_pid> <cwd> <cmd> [args...]\n")
        sys.exit(1)

    try:
        parent_pid = int(sys.argv[1])
    except ValueError:
        parent_pid = os.getppid()

    cwd = sys.argv[2]
    child_cmd = sys.argv[3:]
    pid_file = os.environ.get("QGIS_AI_PID_FILE")

    # Ensure guard process runs in its own process group, completely separated from QGIS
    if hasattr(os, "setpgrp"):
        try:
            os.setpgrp()
        except Exception:
            pass

    # 1. Linux PR_SET_PDEATHSIG: kernel sends SIGKILL if parent dies, even on kill -9 or segfault
    if sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL(None)
            PR_SET_PDEATHSIG = 1
            libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
        except Exception:
            pass

    proc = None

    def kill_child_tree():
        nonlocal proc
        if not proc:
            return
        c_pid = proc.pid
        if not c_pid:
            return

        protected = {parent_pid, os.getpid()}
        try:
            protected.add(os.getppid())
        except Exception:
            pass

        all_pids = set([c_pid])
        if sys.platform.startswith("linux") or sys.platform == "darwin":
            try:
                for d in _get_process_tree(c_pid, protected):
                    all_pids.add(d)
            except Exception:
                pass

        # First wave: SIGTERM to processes and process groups
        for p in all_pids:
            if p not in protected:
                try:
                    os.kill(p, signal.SIGTERM)
                except Exception:
                    pass
                try:
                    if hasattr(os, "getpgid") and hasattr(os, "killpg"):
                        pg = os.getpgid(p)
                        if pg not in protected and pg > 0:
                            os.killpg(pg, signal.SIGTERM)
                except Exception:
                    pass

        time.sleep(0.06)

        # Second wave: SIGKILL to guarantee all processes terminate
        for p in all_pids:
            if p not in protected:
                try:
                    os.kill(p, signal.SIGKILL)
                except Exception:
                    pass
                try:
                    if hasattr(os, "getpgid") and hasattr(os, "killpg"):
                        pg = os.getpgid(p)
                        if pg not in protected and pg > 0:
                            os.killpg(pg, signal.SIGKILL)
                except Exception:
                    pass

        # Windows tree termination fallback
        if sys.platform == "win32":
            try:
                subprocess.call(
                    ["taskkill", "/F", "/T", "/PID", str(c_pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                pass

    def on_signal(signum, frame):
        kill_child_tree()
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, on_signal)

    # 2. Watchdog thread: checks if QGIS is still alive
    def parent_watchdog():
        while True:
            # If QGIS died, getppid() will change (reparented to 1/systemd), or os.kill(pid, 0) fails
            if os.getppid() != parent_pid:
                kill_child_tree()
                os._exit(0)
            try:
                os.kill(parent_pid, 0)
            except OSError:
                kill_child_tree()
                os._exit(0)
            time.sleep(0.3)

    watchdog = threading.Thread(target=parent_watchdog, daemon=True)
    watchdog.start()

    # 3. Spawn child in its own process group
    preexec = os.setsid if hasattr(os, "setsid") else None
    creationflags = 0
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        proc = subprocess.Popen(
            child_cmd,
            cwd=cwd,
            preexec_fn=preexec,
            creationflags=creationflags
        )

        # Write child PID to PID tracking file so runner can immediately target it directly
        if pid_file:
            try:
                with open(pid_file, "w") as f:
                    f.write(f"{proc.pid}\n")
            except Exception:
                pass

        ret = proc.wait()
        sys.exit(ret)
    except Exception as e:
        sys.stderr.write(f"Guard process error: {e}\n")
        sys.exit(1)
    finally:
        kill_child_tree()


if __name__ == "__main__":
    main()
