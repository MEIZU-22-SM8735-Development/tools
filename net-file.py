#!/usr/bin/env python3
"""Move a file between the host and the M2582 over the USB gadget network.

The serial console moves 5-10 KB/s; this moves ~37-42 MB/s, so anything past a
few hundred kilobytes belongs here.

It needs no sshd, no sshpass and no key on the board: both ends run the Python
they already have, over one TCP connection, which keeps it working on a fresh
boot before anything on the root filesystem has been set up.

Usage:
    net-file.py put LOCAL REMOTE      # host -> board
    net-file.py get REMOTE LOCAL      # board -> host
    net-file.py put LOCAL REMOTE --port 5700 --timeout 300

Verification is by md5 on both ends, so a truncated transfer cannot pass.

The board is always the TCP client and the host always the server, in both
directions: the host drives the board's half over the serial console, and a
console command cannot be driven while the caller is also blocked sending
data, so whichever half runs on the board has to initiate. Having the board
listen deadlocks: the host sits in the console read waiting for a command that
is itself waiting for a connection the host has not made yet.

Prerequisites:
    the gadget network up on both sides. The board does it by itself at boot;
    the host needs net-install.sh run once as root, after which
    it also comes up by itself.
"""

import argparse
import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time

# Both ends need the other's address. In the "send" role the peer argument is
# the address to connect to; in "recv" it is unused, since the listener binds
# 0.0.0.0. Passing the wrong one is silent -- the connection simply goes to the
# wrong machine and nothing happens -- so they are named separately.
BOARD = os.environ.get("M2582_BOARD", "192.168.42.1")
HOST = os.environ.get("M2582_HOST", "192.168.42.2")
BOARD_PASSWORD = os.environ.get("M2582_PASSWORD", "m2582")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from console import ensure_shell, find_port, open_port, strip_ansi  # noqa: E402

# Runs on the board, which is always the TCP client:
#   recv PEER PORT PATH      -- connect and drain into PATH
#   send PEER PORT PATH      -- connect and push PATH
BOARD_TRANSFER = r'''
import socket, sys, hashlib, time
mode, peer, port, path = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
buf = 1 << 20
total = 0
t0 = time.time()
s = socket.create_connection((peer, port), timeout=60)
s.settimeout(60)
if mode == "recv":
    with open(path, "wb") as f:
        while True:
            d = s.recv(buf)
            if not d:
                break
            f.write(d)
            total += len(d)
    s.close()
else:
    with open(path, "rb") as f:
        while True:
            d = f.read(buf)
            if not d:
                break
            s.sendall(d)
            total += len(d)
    s.shutdown(socket.SHUT_WR)
    s.close()
h = hashlib.md5()
with open(path, "rb") as f:
    for c in iter(lambda: f.read(buf), b""):
        h.update(c)
dt = time.time() - t0
print("%d bytes in %.2fs = %.1f MB/s" % (total, dt, total / dt / 1e6))
print("MD5 " + h.hexdigest())
'''


def board_python(prog, args, timeout=180.0, serial_port=None, baud=115200):
    """Run a Python program on the board over the console and return its output.

    The program is delivered as a base64 heredoc wrapped at 76 columns: it
    contains quotes and newlines, and this channel is a line-oriented tty with a
    4 KB line limit.
    """
    payload = base64.b64encode(prog.encode()).decode()
    lines = [payload[i:i + 76] for i in range(0, len(payload), 76)]

    ser = open_port(find_port(serial_port), baud)
    try:
        if not ensure_shell(ser, BOARD_PASSWORD, 60):
            raise RuntimeError('no confirmed shell; transfer program was not sent')

        ser.write(b"cat > /tmp/mz.py.b64 <<'MZEOF'\n")
        time.sleep(1.0)
        for i in range(0, len(lines), 8):
            ser.write(("\n".join(lines[i:i + 8]) + "\n").encode())
            time.sleep(0.1)
        ser.write(b"MZEOF\n")
        time.sleep(1.0)
        ser.write(b"base64 -d /tmp/mz.py.b64 > /tmp/mz.py\n")
        time.sleep(0.6)

        ser.write(("python3 /tmp/mz.py " + " ".join(args) + "\n").encode())
        buf = ""
        last = time.time()
        end = time.time() + timeout
        while time.time() < end:
            data = ser.read(4096)
            if data:
                buf += data.decode("utf-8", "replace")
                last = time.time()
            elif buf and (time.time() - last) > 3.0:
                break
            else:
                time.sleep(0.05)
        return buf
    finally:
        ser.close()


def host_send(path, port, holder):
    """Wait for the board to connect, then stream the file to it."""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(1)
    s.settimeout(120)
    holder["listening"] = True
    try:
        conn, _ = s.accept()
        conn.settimeout(120)
        t0 = time.time()
        sent = 0
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                conn.sendall(chunk)
                sent += len(chunk)
        conn.close()
        dt = time.time() - t0
        holder["out"] = (f"host sent {sent} bytes in {dt:.2f}s = "
                         f"{sent / dt / 1e6:.1f} MB/s\n")
    except Exception as e:                       # noqa: BLE001
        holder["out"] = f"host send failed: {e}\n"
    finally:
        s.close()


def host_recv(path, port, holder):
    """Wait for the board to connect, then drain it into the file."""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(1)
    s.settimeout(120)
    holder["listening"] = True
    try:
        conn, _ = s.accept()
        conn.settimeout(120)
        t0 = time.time()
        got = 0
        with open(path, "wb") as f:
            while True:
                chunk = conn.recv(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
        conn.close()
        dt = time.time() - t0
        holder["out"] = (f"host received {got} bytes in {dt:.2f}s = "
                         f"{got / dt / 1e6:.1f} MB/s\n")
    except Exception as e:                       # noqa: BLE001
        holder["out"] = f"host receive failed: {e}\n"
    finally:
        s.close()


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_md5(text):
    for line in strip_ansi(text).splitlines():
        if line.startswith("MD5 "):
            return line.split()[1]
    return None


def board_reachable(addr, tries=3):
    """Is the board actually up on the gadget link?

    Opening a socket is not enough: the link can be up with no address on
    either end, and then the connect just hangs until the timeout and the
    failure looks like a transfer problem. Ping first, so an unconfigured link
    fails in three seconds with the commands that fix it.
    """
    try:
        links = json.loads(subprocess.check_output(
            ["ip", "-j", "-4", "address", "show"], text=True, timeout=3))
        interfaces = [link['ifname'] for link in links
                      if 'UP' in link.get('flags', []) and any(
                          item.get('local') == HOST
                          for item in link.get('addr_info', []))]
        if len(interfaces) != 1:
            return False
        # A VPN may answer an unbound ping while the USB link is down.
        r = subprocess.run(["ping", "-I", interfaces[0],
                            "-c", str(tries), "-W", "1", addr],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=tries * 2 + 2)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("direction", choices=("put", "get"))
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--port", type=int, default=5700)
    ap.add_argument("--serial-port", help="console used to drive the board")
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    if not board_reachable(BOARD):
        print(f"# {BOARD} does not answer ping -- fix the link before transferring",
              file=sys.stderr)
        print("#   host:   sudo sh net-up.sh up", file=sys.stderr)
        print("#   board:  ip addr replace 192.168.42.1/24 dev usb0; ip link set usb0 up",
              file=sys.stderr)
        print("#   then:   ping -c 3 192.168.42.1", file=sys.stderr)
        return 2

    holder = {}

    if args.direction == "put":
        local, remote = args.src, args.dst
        # Check the source first. Without this a missing or empty file produces
        # a "successful" transfer of zero bytes and an md5 that matches on both
        # ends, which reads exactly like a working tool.
        if not os.path.isfile(local):
            print(f"# {local} is not a regular file", file=sys.stderr)
            return 1
        size = os.path.getsize(local)
        if size == 0:
            print(f"# {local} is empty -- refusing to 'transfer' nothing",
                  file=sys.stderr)
            return 1
        expect = md5_file(local)
        print(f"# put {local} ({size} bytes, md5 {expect}) -> {remote}")
        # Host is the server and sends; the board connects and receives.
        t = threading.Thread(target=host_send, args=(local, args.port, holder))
        t.start()
        while not holder.get("listening"):
            time.sleep(0.05)
        out = board_python(BOARD_TRANSFER, ["recv", HOST, str(args.port), remote],
                           timeout=max(args.timeout, 300.0),
                           serial_port=args.serial_port)
        t.join(180)
    else:
        remote, local = args.src, args.dst
        print(f"# get {remote} -> {local}")
        # Host is the server and receives; the board connects and sends.
        t = threading.Thread(target=host_recv, args=(local, args.port, holder))
        t.start()
        while not holder.get("listening"):
            time.sleep(0.05)
        out = board_python(BOARD_TRANSFER, ["send", HOST, str(args.port), remote],
                           timeout=args.timeout, serial_port=args.serial_port)
        t.join(180)

    print(out)
    print(holder.get("out", ""))

    # The board's copy is the same file, so its md5 is the reference for both
    # directions: for "put" it is the copy that landed, for "get" it is the
    # original that was read.
    board_md5 = parse_md5(out)
    if args.direction == "get":
        expect = md5_file(local) if os.path.exists(local) else None

    if board_md5 is None or expect is None:
        print("# could not verify: no md5 from one end", file=sys.stderr)
        return 1
    if board_md5 != expect:
        print(f"# MISMATCH: board {board_md5} != local {expect}", file=sys.stderr)
        return 1
    print(f"# verified: md5 {expect}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
