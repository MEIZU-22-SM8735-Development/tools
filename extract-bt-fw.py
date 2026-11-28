#!/usr/bin/env python3
"""Extract the Bluetooth firmware from the factory `bluetooth` partition.

That partition is a FAT16 image (not EROFS, so fsck.erofs cannot read it) with a
single `image/` directory holding the WCN7750's rampatch/NVM patch stream and one
NVM file per board id.  mainline's btqca asks for exactly those names under
/lib/firmware/qca, so this is a copy job, not a translation job.

Both factory extraction and the firmware repository stage the same file set,
so this helper is only needed with `fetch-firmware.py --images`.

Usage:
    extract-bt-fw.py --image <bluetooth.img> [--out <dir>]
    extract-bt-fw.py --image <bluetooth.img> --list   # just show what is there
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IMG = ROOT / 'firmware/bluetooth.img'
OUT = Path(os.environ.get('M2582_ARTIFACTS', ROOT / 'firmware')) / 'bt-fw'


def list_firmware():
    r = subprocess.run(['mdir', '-i', str(IMG), '::/image'], capture_output=True,
                       text=True)
    if r.returncode:
        sys.exit('extract-bt-fw: mdir failed on %s: %s' % (IMG, r.stderr))
    files = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[-1]
        if not name.startswith(('hmtbtfw', 'hmtnv')):
            continue
        # The size is the last all-digit field before the name: mdir prints a
        # date and a time, and the date is absent for some entries, so the
        # column is not at a fixed index.
        size = 0
        for field in reversed(parts[:-1]):
            if field.isdigit():
                size = int(field)
                break
        files.append((name, size))
    return files


def main():
    global IMG, OUT
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--list', action='store_true',
                    help='list the firmware in the partition without copying')
    ap.add_argument('--image', type=Path, default=IMG,
                    help='factory bluetooth.img dumped from the phone '
                         '(not distributed with this repo)')
    ap.add_argument('--out', type=Path, default=OUT,
                    help='output directory for the firmware files')
    args = ap.parse_args()
    IMG, OUT = args.image, args.out
    if not IMG.is_file():
        sys.exit('extract-bt-fw: bluetooth image not found: %s (pass --image)' % IMG)
    for tool in ('mdir', 'mcopy'):
        if not shutil.which(tool):
            sys.exit('extract-bt-fw: %s not found; install mtools' % tool)
    files = list_firmware()
    if args.list or not files:
        for name, size in sorted(files):
            print('%-24s %8d' % (name, size))
        print('%d files, %d bytes' % (len(files), sum(s for _, s in files)))
        return
    OUT.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name, _size in sorted(files):
        dest = OUT / name
        if dest.is_file():
            continue
        r = subprocess.run(['mcopy', '-n', '-i', str(IMG),
                            '::/image/%s' % name, str(dest)],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit('extract-bt-fw: mcopy %s failed: %s' % (name, r.stderr))
        copied += 1
    total = sum(f.stat().st_size for f in OUT.iterdir())
    print(json.dumps({'copied': copied, 'dir': str(OUT), 'bytes': total}))


if __name__ == '__main__':
    main()
