# Notices

The scripts and Python tooling in this repository are licensed under
GPL-2.0-only; see `LICENSE`.

## Vendored third-party tools (`vendor/`)

- `vendor/mkbootimg/` — AOSP `system/tools/mkbootimg`, Apache-2.0.
- `vendor/avbtool.py` — AOSP `external/avb`, Apache-2.0.

Only the files needed at run time are vendored; upstream tests and build
files are not included. Both files carry their own copyright headers.

## External material fetched at build time

- Firmware blobs come from the organisation's `firmware` repository and are
  not part of this repository.
- The Arch Linux ARM rootfs, Mesa source and build dependencies are fetched by
  the `rootfs` and `initramfs` repositories with pinned sha256 sums.
