#!/usr/bin/env python3
"""Stage the firmware the build needs from the firmware repository or the
user's own factory partition images.

Nothing vendor-signed is distributed with this project, so every blob has to
be staged into firmware/ (next to this tool) before a build.  The script
reports exactly what is still missing, so a fresh checkout can go from
"I cloned the sibling repos" to a buildable tree.

Usage:
    fetch-firmware.py --check                    # report only
    fetch-firmware.py --from-repo ../firmware    # copy from a firmware checkout
    fetch-firmware.py --from-repo                # clone the default firmware URL
    fetch-firmware.py --images <dir>             # extract from factory images
    fetch-firmware.py --images <dir> --force     # overwrite existing files

The images are the ones `fastboot` writes, e.g. vendor.img, modem.img and
bluetooth.img from the phone's factory package.  Extraction needs fsck.erofs
(erofs-utils) for vendor.img and mtools (mdir/mcopy) for the FAT partitions.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT
ARTIFACTS = Path(os.environ.get('M2582_ARTIFACTS', ROOT / 'firmware'))
MANIFEST = TOOLS / 'firmware-manifest.json'
DEFAULT_REPO = 'https://github.com/MEIZU-22-SM8735-Development/firmware'

# (source path inside vendor.img's /firmware, destination under the staging dir)
VENDOR = (
    ('vpu30_2v.mbn', 'video-fw/vpu30_2v.mbn'),
    ('synaptics/firmware.img', 'touch-fw/firmware.img'),
    ('gen80000_sqe.fw', 'gpu-fw/gen80000_sqe.fw'),
    ('gen80000_aqe.fw', 'gpu-fw/gen80000_aqe.fw'),
    ('gen80600_gmu.bin', 'gpu-fw/gen80600_gmu.bin'),
    ('gen80000_zap.mbn', 'gpu-fw/gen80000_zap.mbn'),
)

# (source name in modem.img ::/image/kiwi, destination)
MODEM = (
    ('amss20.bin', 'wifi-fw/amss20.bin'),
    ('phy_ucode20.elf', 'wifi-fw/phy_ucode20.elf'),
    ('bdwlan_m2582.elf', 'wifi-fw/bdwlan_m2582.elf'),
    ('board-2.bin', 'wifi-fw/board-2.bin'),
    ('regdb.bin', 'wifi-fw/regdb.bin'),
)

# Only the video firmware blocks a build; the rest merely lose a feature.
REQUIRED = ('video-fw/vpu30_2v.mbn',)


def die(msg):
    raise SystemExit('fetch-firmware: ' + msg)


def have(tool):
    return shutil.which(tool) is not None


def writable(dest, force):
    """True when dest is to be written: missing, or --force."""
    return force or not dest.exists()


def verified_versions(strict):
    """Compare the staged blobs with the versions the board was validated on."""
    if not MANIFEST.is_file():
        return
    data = json.loads(MANIFEST.read_text())
    bad = []
    for rel, meta in data.get('files', {}).items():
        p = ARTIFACTS / rel
        if not p.is_file():
            continue
        if hashlib.sha256(p.read_bytes()).hexdigest() != meta['sha256']:
            bad.append(rel)
            print(json.dumps({'version_differs': rel, 'source': meta.get('source'),
                              'tested_build': data.get('tested_build')}))
    if bad:
        msg = ('firmware differs from the validated build %s: %s' %
               (data.get('tested_build'), ', '.join(bad)))
        if strict:
            die(msg + ' (pass --strict only with the tested factory release)')
        print('fetch-firmware: WARNING ' + msg)


def report():
    """Print the staged assets and return the required ones that are missing."""
    print('%-38s %-8s %s' % ('asset', 'status', 'size'))
    missing = []
    for dest in [d for _, d in VENDOR] + [d for _, d in MODEM]:
        p = ARTIFACTS / dest
        ok = p.is_file()
        if not ok and dest in REQUIRED:
            missing.append(dest)
        print('%-38s %-8s %s' % (dest, 'ok' if ok else 'MISSING',
                                 p.stat().st_size if ok else '-'))
    bt_dir = ARTIFACTS / 'bt-fw'
    bt = sorted(bt_dir.glob('*')) if bt_dir.is_dir() else []
    print('%-38s %-8s %d file(s)' % ('bt-fw/', 'ok' if bt else 'MISSING', len(bt)))
    return missing


def check(missing):
    """Fail on missing required assets and say how to get them."""
    if missing:
        die('required firmware missing: ' + ', '.join(missing) +
            '\n  stage it with --from-repo <../firmware checkout> or --images <factory images dir>')


def copy_file(src, dest, force):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not writable(dest, force):
        print(json.dumps({'kept': str(dest)}))
        return
    shutil.copy2(src, dest)
    print(json.dumps({'copied': str(dest), 'bytes': dest.stat().st_size}))


def extract_vendor(images, force):
    img = images / 'vendor.img'
    if not img.is_file():
        die('vendor.img not found in ' + str(images))
    if not have('fsck.erofs'):
        die('fsck.erofs not found; install erofs-utils')
    with tempfile.TemporaryDirectory(prefix='m2582-vendor-') as tmp:
        out = Path(tmp) / 'firmware'
        # --extract creates the target and roots the tree at --path, so the
        # files come out directly under out/.
        r = subprocess.run(['fsck.erofs', '--extract=' + str(out),
                            '--path=/firmware', '--no-preserve', str(img)],
                           capture_output=True, text=True)
        if r.returncode or not out.is_dir():
            die('fsck.erofs failed on vendor.img: ' + (r.stderr or r.stdout).strip())
        for src, dest in VENDOR:
            p = out / src
            if p.is_file():
                copy_file(p, ARTIFACTS / dest, force)
            else:
                print(json.dumps({'missing_in_image': src}))


def extract_modem(images, force):
    img = images / 'modem.img'
    if not img.is_file():
        print(json.dumps({'modem': 'modem.img not found, skipping WiFi firmware'}))
        return
    if not have('mcopy'):
        die('mcopy not found; install mtools')
    for name, dest in MODEM:
        dest_path = ARTIFACTS / dest
        if not writable(dest_path, force):
            continue
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(['mcopy', '-n', '-i', str(img),
                            '::/image/kiwi/' + name, str(dest_path)],
                           capture_output=True, text=True)
        if r.returncode:
            print(json.dumps({'missing_in_image': name}))
        else:
            print(json.dumps({'copied': str(dest_path),
                              'bytes': dest_path.stat().st_size}))


def extract_bluetooth(images):
    img = images / 'bluetooth.img'
    if not img.is_file():
        print(json.dumps({'bluetooth': 'bluetooth.img not found, skipping'}))
        return
    r = subprocess.run([sys.executable, str(TOOLS / 'extract-bt-fw.py'),
                        '--image', str(img),
                        '--out', str(ARTIFACTS / 'bt-fw')],
                       capture_output=True, text=True)
    if r.returncode:
        print(json.dumps({'bluetooth': (r.stderr or r.stdout).strip()}))
    else:
        print(r.stdout.strip())


def fetch_from_repo(src, force):
    """Copy the firmware from a checkout (or a git URL) of the firmware repo."""
    with tempfile.TemporaryDirectory(prefix='m2582-fw-') as tmp:
        if '://' in src or src.startswith('git@'):
            print(json.dumps({'cloning': src}))
            r = subprocess.run(['git', 'clone', '--depth', '1', src, tmp + '/repo'],
                               capture_output=True, text=True)
            if r.returncode:
                die('git clone failed: ' + (r.stderr or r.stdout).strip())
            repo = Path(tmp) / 'repo'
        else:
            repo = Path(src).expanduser().resolve()
        if not repo.is_dir():
            die('not a directory: %s\n'
                '  clone the firmware repository next to this one:\n'
                '    git clone https://github.com/MEIZU-22-SM8735-Development/firmware ../firmware\n'
                '  or pass --from-repo with no argument to clone the default URL' % repo)
        manifest_path = repo / 'manifest.json'
        if not manifest_path.is_file():
            die('manifest.json not found in %s (is this the firmware repo?)' % repo)
        manifest = json.loads(manifest_path.read_text())
        copied = 0
        for rel, meta in manifest.get('files', {}).items():
            src_file = repo / rel
            if not src_file.is_file():
                print(json.dumps({'missing_in_repo': rel}))
                continue
            dest = ARTIFACTS / rel
            if not writable(dest, force):
                continue
            data = src_file.read_bytes()
            if hashlib.sha256(data).hexdigest() != meta['sha256']:
                die('hash mismatch in repository for %s' % rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            copied += 1
        print(json.dumps({'firmware_repo': str(repo), 'files_copied': copied,
                          'tested_build': manifest.get('tested_build')}))


def main():
    global ARTIFACTS
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--images', type=Path, default=None,
                    help='directory holding the factory partition images '
                         '(vendor.img, modem.img, bluetooth.img)')
    ap.add_argument('--out', type=Path, default=ARTIFACTS,
                    help='firmware staging directory (default: firmware/ next to this tool)')
    ap.add_argument('--check', action='store_true',
                    help='only report what is present/missing')
    ap.add_argument('--force', action='store_true',
                    help='overwrite files that already exist')
    ap.add_argument('--strict', action='store_true',
                    help='fail if a blob differs from the validated version')
    ap.add_argument('--from-repo', nargs='?', const=DEFAULT_REPO, default=None,
                    metavar='PATH_OR_URL',
                    help='copy the firmware from the firmware repository '
                         '(default URL: %s)' % DEFAULT_REPO)
    args = ap.parse_args()
    ARTIFACTS = args.out
    if args.check:
        check(report())
        verified_versions(args.strict)
        return 0
    if not args.from_repo and args.images is None:
        die('nothing to stage: pass --images <factory images dir> or '
            '--from-repo [PATH_OR_URL] (or --check to report only)')
    if args.from_repo:
        fetch_from_repo(args.from_repo, args.force)
    else:
        if not args.images.is_dir():
            die('image directory not found: %s (pass --images, or use --from-repo)'
                % args.images)
        extract_vendor(args.images, args.force)
        extract_modem(args.images, args.force)
        extract_bluetooth(args.images)
    report()
    verified_versions(args.strict)
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
