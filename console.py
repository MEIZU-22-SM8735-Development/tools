#!/usr/bin/env python3
"""Talk to the Meizu 22's kernel console over the USB cable.

The board's USB gadget exposes a CDC ACM port (the host sees it as
/dev/ttyACM0). The kernel command line carries console=ttyGS0, and the
distro's /etc/inittab spawns an agetty on the same port, so this one wire
gives you both the kernel log and a login prompt:

    === M2582 entering distro root ===
    ...
    m2582 login:

Log in as root with the password m2582 (the rootfs image sets it); change it
with `passwd` once you are in if the cable is not trusted.

Why not plain `cat`: the port accepts a single opener, so setting line
parameters with `stty -F` and then reading with `cat` fights over it, and DTR
has to be asserted for the gadget to transmit. pyserial does both on one file
descriptor.

Usage
-----
    console.py                       # interactive
    console.py --cmd 'id; uname -a'  # run a command and print the result
    console.py --boot                # just dump what arrives for N seconds
    console.py --port /dev/ttyACM1 --baud 115200

Interactive mode: Ctrl-] to quit (like telnet), Ctrl-C also works.
"""

import argparse
import glob
import re
import sys
import time

# agetty appends a cursor-position query (ESC[6n) to the prompt, and shell
# integration (e.g. Arch's bash) writes an OSC 3008 sequence after every
# command, so a line ending in a bare '#' or '$' never matches. The OSC is
# terminated by BEL or ST.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]"
                   r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def strip_ansi(s):
    return _ANSI.sub("", s)


# The board may run a localized agetty, so the prompts are matched in English
# and in CJK form (the escapes below are "login"/"password"); the source stays
# ASCII on purpose.
LOGIN_PROMPT = re.compile(r"(?:login:|[\u767b\u5f55][\uff1a:])$")
PASSWORD_PROMPT = re.compile(r"(?:Password:|[\u5bc6\u7801][\uff1a:])$")


try:
    import serial
except ImportError:
    sys.exit("pyserial is required: pip install pyserial --break-system-packages")


def find_port(explicit=None):
    if explicit:
        return explicit
    for pat in ("/dev/ttyACM*", "/dev/ttyUSB*"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[0]
    sys.exit("no /dev/ttyACM* or /dev/ttyUSB* found -- is the board booted and the cable in?")


def open_port(path, baud):
    s = serial.Serial()
    s.port = path
    s.baudrate = baud
    s.timeout = 0.3
    s.write_timeout = 2
    s.dtr = True          # the gadget will not transmit until DTR is asserted
    s.rts = True
    s.open()
    return s


def pump(ser, seconds, out=sys.stdout):
    """Read and echo whatever arrives for a while."""
    end = time.time() + seconds
    while time.time() < end:
        data = ser.read(4096)
        if data:
            out.write(data.decode("utf-8", "replace"))
            out.flush()


def ensure_shell(ser, password, timeout, out=sys.stdout):
    """Follow actual login prompts; never send commands before seeing a shell."""
    end = time.monotonic() + timeout
    buf = ""
    sent_user = False
    ser.write(b"\n")
    while time.monotonic() < end:
        data = ser.read(4096)
        if not data:
            continue
        chunk = data.decode("utf-8", "replace")
        out.write(chunk)
        out.flush()
        buf = (buf + chunk)[-8192:]
        clean = strip_ansi(buf).replace("\r", "").rstrip()
        if re.search(r"(?:^|\n)[^\n]*[#$]$", clean):
            return True
        if LOGIN_PROMPT.search(clean):
            ser.write(b"root\n")
            sent_user = True
            buf = ""
        elif PASSWORD_PROMPT.search(clean):
            # An earlier invocation may have left an unknown username here.
            ser.write((password.encode() if sent_user else b"") + b"\n")
            sent_user = False
            buf = ""
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial device (default: first /dev/ttyACM*)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--cmd", help="log in, run this command, print the result, exit")
    ap.add_argument("--boot", action="store_true",
                    help="just print everything that arrives for --seconds")
    ap.add_argument("--seconds", type=float, default=10.0,
                    help="how long to listen in --boot mode (default 10)")
    ap.add_argument("--timeout", type=float, default=4.0,
                    help="seconds to wait for a command's output (default 4)")
    ap.add_argument("--login-timeout", type=float, default=25.0,
                    help="seconds to wait for a login prompt (default 25)")
    ap.add_argument("--password", default="m2582",
                    help="root password set in the rootfs image (default: m2582)")
    args = ap.parse_args()

    path = find_port(args.port)
    ser = open_port(path, args.baud)
    print(f"# {path} @ {args.baud} 8N1", file=sys.stderr)

    try:
        if args.boot:
            print(f"# listening for {args.seconds}s", file=sys.stderr)
            pump(ser, args.seconds)
            return 0

        if args.cmd:
            # Never type before a prompt is confirmed: a blind "root" lands on
            # whatever agetty is showing and the login dies with "invalid
            # password for 'UNKNOWN'". agetty also wants a line feed; a bare
            # carriage return leaves it waiting for its 60 s timeout.
            def pump_until_idle(limit, idle=1.5):
                """Read until the device has been silent for `idle` seconds.

                Returns as soon as the output stops, rather than burning the
                whole --timeout like a fixed-duration read would.
                """
                buf = ""
                end = time.time() + limit
                last = time.time()
                while time.time() < end:
                    try:
                        data = ser.read(4096)
                    except serial.SerialException:
                        # A reboot can disconnect after printing useful evidence.
                        sys.stdout.write(buf)
                        sys.stdout.flush()
                        raise
                    if data:
                        buf += data.decode("utf-8", "replace")
                        last = time.time()
                    else:
                        if buf and (time.time() - last) > idle:
                            break
                        time.sleep(0.05)
                return buf

            if not ensure_shell(ser, args.password, args.login_timeout):
                print("# no confirmed shell prompt; command was not sent", file=sys.stderr)
                return 1

            ser.write(args.cmd.encode() + b"\n")
            out = pump_until_idle(args.timeout)
            sys.stdout.write(out)
            sys.stdout.flush()
            return 0

        # interactive: forward stdin to the port and the port to stdout
        print("# interactive -- Ctrl-] to quit", file=sys.stderr)
        import select
        while True:
            r, _, _ = select.select([ser.fileno(), sys.stdin.fileno()], [], [], 0.2)
            if ser.fileno() in r:
                data = ser.read(4096)
                if data:
                    sys.stdout.write(data.decode("utf-8", "replace"))
                    sys.stdout.flush()
            if sys.stdin.fileno() in r:
                line = sys.stdin.readline()
                if not line:
                    break
                if "\x1d" in line:      # Ctrl-]
                    break
                ser.write(line.encode())
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
