#!/usr/bin/env python3
"""push-file.py -- copy a file to the M2582 over the serial console.

The board's only link is a 115200 baud CDC ACM console, so a file has to go in
as text. base64 at 76 columns is the format that survives it: the tty's
canonical-mode line buffer is 4 KB and anything longer is truncated.

The transfer is verified, not trusted: the md5 of the received file is compared
with the md5 of the local one, and a file that does not match is sent again.

Usage:  push-file.py <local> <remote> [--chunk-lines N] [--keep-b64]
"""
import argparse
import base64
import hashlib
import os
import subprocess
import sys

from console import strip_ansi

HERE = os.path.dirname(os.path.abspath(__file__))
CONSOLE = os.path.join(HERE, 'console.py')


def run_cmd(cmd, timeout=120):
    r = subprocess.run(['python3', CONSOLE, '--password', 'm2582',
                        '--timeout', str(timeout), '--cmd', cmd],
                       capture_output=True, text=True, timeout=timeout + 180)
    return r.stdout + r.stderr


def send_chunk(remote_b64, lines, attempt):
    body = '\n'.join(lines)
    # A unique delimiter per attempt: a half-received heredoc must not be
    # closed by the next attempt's terminator.
    tag = 'MZEOF%d' % attempt
    return run_cmd("cat >> %s <<'%s'\n%s\n%s" % (remote_b64, tag, body, tag))


def remote_md5(remote):
    out = run_cmd('md5sum %s 2>/dev/null' % remote, timeout=60)
    for line in strip_ansi(out).splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 32:
            try:
                int(parts[0], 16)
            except ValueError:
                continue
            return parts[0]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('local')
    ap.add_argument('remote')
    # 24 lines (about 1.8 KB of base64) per heredoc.  Larger chunks overrun the
    # device's tty input buffer, which drops bytes and can leave the shell
    # waiting on an unclosed heredoc.
    ap.add_argument('--chunk-lines', type=int, default=24)
    ap.add_argument('--keep-b64', action='store_true')
    args = ap.parse_args()

    data = open(args.local, 'rb').read()
    want = hashlib.md5(data).hexdigest()
    b64 = base64.b64encode(data).decode()
    lines = [b64[i:i + 76] for i in range(0, len(b64), 76)]
    remote_b64 = args.remote + '.b64'

    print('%s -> %s: %d bytes, %d lines, md5 %s'
          % (args.local, args.remote, len(data), len(lines), want))
    sys.stdout.flush()

    for attempt in range(1, 4):
        run_cmd('rm -f %s' % remote_b64, timeout=30)
        for i in range(0, len(lines), args.chunk_lines):
            chunk = lines[i:i + args.chunk_lines]
            send_chunk(remote_b64, chunk, attempt)
            print('  attempt %d: %d/%d lines' % (attempt, min(i + len(chunk),
                                                             len(lines)),
                                                 len(lines)))
            sys.stdout.flush()
        run_cmd('base64 -d %s > %s%s' % (remote_b64, args.remote,
                                         '' if args.keep_b64 else
                                         '; rm -f %s' % remote_b64),
                timeout=90)
        got = remote_md5(args.remote)
        if got == want:
            print('OK %s md5 %s' % (args.remote, got))
            return 0
        print('MISMATCH attempt %d: got %s want %s' % (attempt, got, want))
        sys.stdout.flush()
    print('FAILED after 3 attempts')
    return 1


if __name__ == '__main__':
    sys.exit(main())
