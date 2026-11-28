#!/usr/bin/env python3
"""Put the board into fastboot from Linux, over the serial console.

It writes the reboot mode into the PMK8550's SDAM2 and then triggers a sysrq
reboot, which works from any userspace -- unlike `sh /sbin/m2582-reboot
bootloader`, which only exists while that script is installed:

    0x04 at offset 0x48 of the SDAM2 nvmem, then a sysrq reboot

0x04 is 0x02 << 1: the field is seven bits at <1 7> (see the reboot-mode
comment in meizu-m2582.dts) and 0x02 is mode-bootloader. ABL reads that field
on the way up and enters fastboot instead of Linux.

The write is checked before rebooting: a silent write error means a boot
straight back into Linux, which looks exactly like "the command did nothing".
"""
import argparse
import re
import sys
import time

import serial

PORT = "/dev/ttyACM0"
PASSWORD = "m2582"
NVMem = "/sys/bus/nvmem/devices/spmi_sdam0/nvmem"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args()

    s = serial.Serial(PORT, 115200, timeout=0.5, write_timeout=5)
    s.dtr = True
    s.rts = True
    time.sleep(1)

    def drain():
        out = b""
        try:
            while True:
                c = s.read(512)
                if not c:
                    break
                out += c
        except Exception:
            pass
        return out.decode("utf-8", "replace")

    def wait(pat, t=6.0):
        end = time.time() + t
        buf = ""
        while time.time() < end:
            try:
                c = s.read(256)
            except Exception:
                time.sleep(0.2)
                continue
            if c:
                buf += c.decode("utf-8", "replace")
                if re.search(pat, buf):
                    return buf, True
        return buf, False

    drain()
    s.write(b"\n")
    buf, ok = wait(r"login:|[#$] ", 4)
    if "login:" in buf:
        s.write(b"root\n")
        buf, ok = wait(r"[Pp]assword:|[#$] ", 4)
        if re.search(r"[Pp]assword", buf):
            s.write(PASSWORD.encode() + b"\n")
            buf, ok = wait(r"[#$] ", 5)
    if not ok:
        print("no shell:", repr(buf[-200:]))
        return 1
    s.write(b"echo 1 > /proc/sys/kernel/sysrq\n")
    time.sleep(0.5)
    drain()
    s.write(b"printf '\\004' | dd of=" + NVMem.encode() +
            b" bs=1 seek=72 conv=notrunc\n")
    time.sleep(1.0)
    out = drain()
    if "1 bytes" not in out and "1+0 records out" not in out:
        print("the SDAM write may not have happened:")
        print(out)
    s.write(b"sync\n")
    time.sleep(1.0)
    drain()
    s.write(b"echo b > /proc/sysrq-trigger\n")
    time.sleep(2)
    drain()
    s.close()
    print("reboot triggered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
