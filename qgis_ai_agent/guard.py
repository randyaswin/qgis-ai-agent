#!/usr/bin/env python3
"""Process guardian that guarantees child AI CLI process is terminated if QGIS closes or crashes.

This script runs as a lightweight supervisory wrapper around the AI CLI.
It monitors the parent QGIS process via:
1. Linux kernel hook: PR_SET_PDEATHSIG (sends SIGKILL the instant parent QGIS dies).
2. Cross-platform watchdog thread: monitors parent PID liveness every 300ms.
3. Signal forwarding: SIGTERM, SIGINT, SIGHUP -> kills child process group.
"""
import ctypes
import os
import signal
import subprocess
import sys
import threading
import time


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
        if proc and proc.poll() is None:
            try:
                # Ensure we NEVER kill the parent QGIS process group under any circumstance
                if hasattr(os, "killpg") and proc.pid:
                    child_pgid = os.getpgid(proc.pid)
                    parent_pgid = os.getpgid(parent_pid) if parent_pid > 0 else -1
                    if child_pgid != parent_pgid and child_pgid != os.getpid():
                        os.killpg(child_pgid, signal.SIGKILL)
                    else:
                        proc.kill()
                else:
                    proc.kill()
            except Exception:
                try:
                    proc.kill()
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
        ret = proc.wait()
        sys.exit(ret)
    except Exception as e:
        sys.stderr.write(f"Guard process error: {e}\n")
        sys.exit(1)
    finally:
        kill_child_tree()


if __name__ == "__main__":
    main()

