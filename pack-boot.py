#!/usr/bin/env python3
"""Pack the built kernel into a boot image that keeps the stock AVB metadata.

The OEM descriptor's signature covers the original kernel, so it will not
authenticate the replacement: the result needs an unlocked device with
verification disabled.  Keeping the stock footer/properties leaves rollback
and version metadata untouched.

Inputs come from the sibling kernel repo ($LINUX_DIR, default ../linux):

    out-meizu/arch/arm64/boot/Image
    out-meizu/arch/arm64/boot/dts/qcom/meizu-m2582.dtb  (linked into Image)
    out-meizu/.config                                  (must match the Image)

and, when present, the initramfs is cross-checked against the sibling
initramfs repo ($INITRAMFS_DIR, default ../initramfs).

Usage:
    pack-boot.py --original <stock boot.img>
    pack-boot.py --original <stock boot.img> --output out/boot.img

--original is required: it is only used as the AVB header/footer donor, and it
must come from the same firmware release as the phone it will be flashed to.
"""
import argparse
import gzip
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
LINUX_DIR = Path(os.environ.get('M2582_KERNEL_DIR',
                                os.environ.get('LINUX_DIR', TOOLS.parent / 'linux')))
INITRAMFS_DIR = Path(os.environ.get('INITRAMFS_DIR', TOOLS.parent / 'initramfs'))
K = LINUX_DIR / 'out-meizu'
VENDOR = TOOLS / 'vendor'


def need(cond, msg):
    if not cond:
        raise SystemExit('pack-boot: ' + msg)


def tool(path, what):
    need(path.is_file(), '%s not found at %s (it is vendored in this repository)' % (what, path))
    return path


def initramfs_cpio(explicit):
    """Path of the uncompressed cpio the kernel embedded, if it can be found."""
    candidates = [explicit] if explicit else [
        INITRAMFS_DIR / 'artifacts/initramfs/initramfs.cpio',
    ]
    for path in candidates:
        if path and path.is_file():
            return path
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--original', type=Path, default=None,
                    help='stock boot.img used as the AVB header/footer donor '
                         '(REQUIRED; same firmware release as the phone)')
    ap.add_argument('--output', type=Path, default=TOOLS / 'out/boot.img',
                    help='output boot image (default: out/boot.img in this repo)')
    ap.add_argument('--initramfs', type=Path, default=None,
                    help='uncompressed cpio to cross-check the embedded one '
                         'against (default: ../initramfs/artifacts/initramfs/initramfs.cpio)')
    args = ap.parse_args()

    if args.original is None:
        raise SystemExit(
            'pack-boot: --original is required\n'
            '  pass the stock boot.img from your own factory package (same firmware\n'
            '  release as the phone): it is used only as the AVB header/footer donor.\n'
            '    pack-boot.py --original /path/to/boot.img\n'
            '  Without it the image would carry no AVB footer and the bootloader\n'
            '  would reject it. See BUILD.md "Packing".')
    if not args.original.is_file():
        raise SystemExit(
            'pack-boot: stock boot.img not found at %s\n'
            '  --original must point at the factory boot.img you dumped from the\n'
            '  phone; it is used only as the AVB header/footer donor.\n'
            '  See BUILD.md "Packing".' % args.original)

    mkbootimg = tool(VENDOR / 'mkbootimg/mkbootimg.py', 'mkbootimg.py')
    avbtool = tool(VENDOR / 'avbtool.py', 'avbtool.py')

    image = K / 'arch/arm64/boot/Image'
    config_path = K / '.config'
    need(image.is_file(), 'kernel Image not found at %s (build the kernel first:\n'
         '  LINUX_DIR=%s ./build-kernel.sh)' % (image, LINUX_DIR))
    need(config_path.is_file(), 'build config not found at %s' % config_path)
    config = config_path.read_text()
    actual = subprocess.check_output(
        ['sh', str(LINUX_DIR / 'scripts/extract-ikconfig'), str(image)]).decode()
    need(actual == config, 'Linked Image config differs from the current build config\n'
         '  (stale Image or edited .config; rebuild with build-kernel.sh)')
    for key in ('INITRAMFS_FORCE', 'CMDLINE_FORCE', 'IKCONFIG'):
        need('CONFIG_%s=y\n' % key in config, 'CONFIG_%s is not enabled in %s' % (key, config_path))
    cfg = dict(x.split('=', 1) for x in config.splitlines()
               if x.startswith('CONFIG_') and '=' in x)
    # The stock header carries no cmdline, and CONFIG_CMDLINE_FORCE=y makes the
    # built-in one win regardless.
    cmdline = ''

    embedded = cfg.get('CONFIG_ARM64_MEIZU_M2582_BUILTIN_DTB') == 'y'
    dtb_name = cfg.get('CONFIG_BUILTIN_DTB_NAME', '').strip('"') if embedded else None
    img = image.read_bytes()
    need(img[56:60] == bytes.fromhex('41524d64'), 'Image does not carry the arm64 header')
    comp = (K / 'usr/initramfs_inc_data').read_bytes()
    need(comp in img, 'the embedded initramfs was not linked into the Image')
    cpio = gzip.decompress(comp)
    need(cpio[:6] == b'070701', 'the embedded initramfs is not a newc cpio archive')
    need(b'TRAILER!!!' in cpio, 'the embedded initramfs has no TRAILER!!! entry')
    built_cpio = initramfs_cpio(args.initramfs)
    if built_cpio is not None:
        need(cpio == built_cpio.read_bytes(),
             'the embedded initramfs differs from %s (rebuild with build-kernel.sh)'
             % built_cpio)
    if embedded:
        dtb = K / 'arch/arm64/boot/dts' / (dtb_name + '.dtb')
        need(dtb.is_file(), 'linked DTB not found at %s' % dtb)
        need(dtb.read_bytes() in img, 'the DTB selected by CONFIG_BUILTIN_DTB_NAME was not linked')
        need(b'M2582: embedded mainline DTB' in img, 'the Image has no embedded-DTB marker')
    else:
        need(b'M2582: embedded mainline DTB' not in img,
             'unexpected FDT substitution code in the Image')

    raw = args.original.read_bytes()
    need(raw[:8] == b'ANDROID!', '%s is not a boot image' % args.original)
    need(struct.unpack_from('<I', raw, 40)[0] == 4,
         '%s is not a header v4 boot image; this packer keeps AVB metadata from a v4 stock image'
         % args.original)
    magic, major, _minor, stock_payload, vbmeta_offset, vbmeta_size, _ = \
        struct.unpack('>4sIIQQQ28s', raw[-64:])
    need(magic == b'AVBf' and major == 1, '%s has no AVB footer' % args.original)
    need(stock_payload <= vbmeta_offset < vbmeta_offset + vbmeta_size <= len(raw) - 64,
         'inconsistent AVB footer in %s' % args.original)
    need(raw[vbmeta_offset:vbmeta_offset + 4] == b'AVB0',
         'no AVB descriptor at the offset the footer gives in %s' % args.original)
    stock_child_flags = struct.unpack_from('>I', raw, vbmeta_offset + 120)[0]
    version = struct.unpack_from('<I', raw, 16)[0]
    os_version = '.'.join(str((version >> n) & 127) for n in (25, 18, 11))
    patch = '%04d-%02d' % (2000 + ((version >> 4) & 127), version & 15)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='m2582-pack-', dir=args.output.parent) as tmp:
        payload_path = Path(tmp) / 'boot.payload'
        command = [sys.executable, str(mkbootimg), '--header_version', '4',
                   '--kernel', str(image), '--os_version', os_version,
                   '--os_patch_level', patch, '--cmdline', cmdline,
                   '--output', str(payload_path)]
        subprocess.run(command, check=True)
        payload = payload_path.read_bytes()
    # Use the declared Android payload length; never trim at the last nonzero byte.
    ksize, rsize = struct.unpack_from('<II', payload, 8)
    align = lambda n: (n + 4095) & ~4095
    signature_size = struct.unpack_from('<I', payload, 1580)[0]
    need(rsize == signature_size == 0, 'unexpected ramdisk/signature field in the new header')
    need(len(payload) == 4096 + align(ksize), 'unexpected payload size from mkbootimg')
    need(payload[4096:4096 + ksize] == img, 'mkbootimg did not copy the Image verbatim')
    need(len(payload) <= stock_payload,
         'the new image (%d bytes) does not fit the stock partition payload (%d bytes)'
         % (len(payload), stock_payload))
    output = payload + bytes(stock_payload - len(payload)) + raw[stock_payload:]
    need(len(output) == len(raw), 'output size does not match the stock partition size')
    need(output[stock_payload:] == raw[stock_payload:], 'the stock AVB metadata was not preserved')
    args.output.write_bytes(output)
    info = subprocess.check_output([sys.executable, str(avbtool),
                                    'info_image', '--image', str(args.output)], text=True)
    args.output.with_suffix('.avb.txt').write_text(info)
    report = {'file': str(args.output), 'header': 4, 'kernel_bytes': ksize,
              'payload_bytes': len(payload), 'partition_bytes': len(output),
              'sha256': hashlib.sha256(output).hexdigest(),
              'kernel_sha256': hashlib.sha256(img).hexdigest(),
              'linked_config_sha256': hashlib.sha256(actual.encode()).hexdigest(),
              'builtin_dtb': dtb_name or 'bootloader-firmware-dtb',
              'embedded_dtb_verified': True if embedded else None,
              'embedded_initramfs_verified': True,
              'initramfs_cross_checked': built_cpio is not None,
              'cmdline': cmdline,
              'stock_avb_metadata_preserved': True,
              'boot_child_avb_flags': stock_child_flags,
              'oem_signature_authenticates_new_kernel': False,
              'requires_disabled_verification': True,
              'intended_partition': 'boot_a', 'hardware_tested': False}
    args.output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
