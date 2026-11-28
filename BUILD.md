# Building the Meizu 22 (M2582 / SM8735 "tuna") kernel

This document describes how to go from a fresh set of checkouts to a boot image
that can be flashed to an unlocked Meizu 22. It is written for a third party:
nothing from the author's machine is needed, and nothing vendor-signed is
distributed with the source.

The configuration has exactly one source: `arch/arm64/configs/meizu_m2582_defconfig`
in the kernel repository. Local `out-meizu/` directories are build products and
play no part in a release.

## 1. Repositories

Five checkouts, all siblings:

```
work/
  linux/       kernel source (branch with meizu_m2582_defconfig)
  initramfs/   PID 1, rescue shell, BusyBox/qbootctl/mke2fs builders
  rootfs/      Arch Linux ARM root filesystem payload (flashed separately)
  firmware/    vendor blobs, sha256 manifest, NOTICE
  tools/       this repository: build, pack, flash, console, USB network
```

Clone them next to each other:

```sh
git clone https://github.com/MEIZU-22-SM8735-Development/linux
git clone https://github.com/MEIZU-22-SM8735-Development/initramfs
git clone https://github.com/MEIZU-22-SM8735-Development/rootfs
git clone https://github.com/MEIZU-22-SM8735-Development/firmware
git clone https://github.com/MEIZU-22-SM8735-Development/tools
```

Every path is overridable, so a different layout works as well:

| variable | default | meaning |
|---|---|---|
| `LINUX_DIR` | `../linux` | kernel checkout that is built |
| `INITRAMFS_DIR` | `../initramfs` | initramfs checkout that builds the cpio |
| `ROOTFS_DIR` | `../rootfs` | rootfs checkout (payload only, not packed here) |
| `FIRMWARE_REPO` | `../firmware` | firmware checkout or git URL to stage from |
| `FIRMWARE` | `./firmware` | staged firmware the build actually reads |

## 2. Host requirements

- Linux x86_64, about 16 GB of RAM and 30 GB of free disk
- `make`, `bc`, `bison`, `flex`, `gzip`, `cpio`, `rsync`, `python3` (3.8+)
- clang/LLD/llvm (the kernel is built with `ARCH=arm64 LLVM=1`); kbuild needs a
  reasonably new LLVM, so a distribution's old clang may fail. There is no
  need for an aarch64 GCC to build the kernel itself
- `aarch64-linux-gnu-gcc` for BusyBox, qbootctl and mke2fs (the initramfs
  builds them); set `CROSS_COMPILE` to use another prefix
- `curl` and `git` (the initramfs builders fetch pinned sources,
  `fetch-firmware.py` can clone the firmware repository)
- `erofs-utils` (`fsck.erofs`) and `mtools` (`mdir`/`mcopy`) only for
  `fetch-firmware.py --images`
- `pyserial` only for the console tools (`console.py`, `push-file.py`,
  `net-file.py`, `reboot-to-fastboot.py`)

The Python tools use the standard library only.

## 3. Build

One command builds everything a boot image needs:

```sh
sh build-all.sh --check    # validate checkouts and host tools, build nothing
sh build-all.sh            # full build
```

`build-all.sh` runs, in order:

1. `../initramfs/build-busybox.sh` - static aarch64 BusyBox
2. `../initramfs/build-qbootctl.sh` - A/B slot tool used by the initramfs
3. `../initramfs/build-mke2fs.sh` - `mke2fs` for the rescue shell
4. `fetch-firmware.py --from-repo ../firmware` - stage the vendor blobs into
   `./firmware`
5. `build-kernel.sh` - seed `.config`, build the initramfs, build `Image`
6. print the `pack-boot.py` command (packing needs your own stock `boot.img`)

It refuses to start when a checkout or a host tool is missing and says what to
install or clone. The kernel is never built concurrently: do not run two builds
against the same `$LINUX_DIR`.

### 3.1 build-kernel.sh

```sh
sh build-kernel.sh
```

It does five things:

1. seeds `out-meizu/.config` from `arch/arm64/configs/meizu_m2582_defconfig`
   (first build or `CLEAN=1`)
2. builds the initramfs through the sibling checkout and embeds it
   (`CONFIG_INITRAMFS_SOURCE` points at `../initramfs/artifacts/initramfs/initramfs.cpio`)
3. writes the build stamp and the command line into the built-in cmdline
   (`CONFIG_CMDLINE`, forced)
4. builds `Image`
5. verifies the result: the config embedded in `Image` must be byte-identical
   to `out-meizu/.config`, and the config must still carry the board
   essentials (display, framebuffer console, USB DWC3, qcom clocks). The
   script refuses to produce an image that lost them - such an image still
   links and boots to a dark screen, which is indistinguishable from the bug
   being chased.

Environment:

| variable | effect |
|---|---|
| `JOBS=n` | parallel jobs (default: `nproc`) |
| `CLEAN=1` | delete `out-meizu/` first and rebuild from the defconfig |
| `INITRAMFS=0` | skip the initramfs (kernel-only iteration) |
| `VERBOSE=1` | `initcall_debug` and full loglevel on the panel console |
| `EXTRA="..."` | extra kernel command line words (must be a single line) |
| `FEATURES=a,b` / `DISABLE=a,b` | probe only / skip the named `m2582,feature` subsystems |
| `M2582_EUD=1` | build the EUD debug port (off by default) |
| `M2582_ARTIFACTS=<dir>` | where the extracted-config baseline is written (default: the kernel out dir) |
| `M2582_KERNEL_DIR=<dir>` | kernel checkout, overrides `LINUX_DIR` (old-layout compatibility) |
| `SAVEDEFCONFIG=1` | write `out-meizu/defconfig` back to the kernel tree and exit |

Products:

```
$LINUX_DIR/out-meizu/arch/arm64/boot/Image
$LINUX_DIR/out-meizu/arch/arm64/boot/dts/qcom/meizu-m2582.dtb   (linked into Image)
../initramfs/artifacts/initramfs/initramfs.cpio                 (embedded)
```

### 3.2 Changing the configuration

- Runtime change: edit `$LINUX_DIR/out-meizu/.config`; the next build keeps it.
- Back into the repository:
  `SAVEDEFCONFIG=1 sh build-kernel.sh` in this repository, then review
  `arch/arm64/configs/meizu_m2582_defconfig` in the kernel repository before
  committing. The build stamp, the command line and the initramfs path are
  stripped automatically - never write them into the defconfig by hand.
- **Never run `make O=out-meizu <target>` yourself.** Without `ARCH`/
  `CROSS_COMPILE`, kconfig rewrites `.config` for x86 and silently drops the
  qcom and arm64 symbols; `Image` still links, with no msm driver in it. The
  build script checks for that and refuses to build; if it happens anyway,
  delete `out-meizu/.config` and let the defconfig seed it again.

## 4. Firmware staging

Vendor firmware is not distributed with the source. It is staged into
`./firmware` (override with `--out` or `M2582_ARTIFACTS`) from either the
firmware repository or your own factory images.

### 4.1 From the firmware repository (default)

```sh
python3 fetch-firmware.py --from-repo ../firmware   # local checkout
python3 fetch-firmware.py --from-repo               # clone the default URL
python3 fetch-firmware.py --check                   # report what is staged
python3 fetch-firmware.py --check --strict          # fail on a version mismatch
```

Every file is checked against `manifest.json` in the firmware repository before
it is copied, so a truncated clone cannot silently reach the image.

### 4.2 From your own factory images

```sh
python3 fetch-firmware.py --images /path/to/factory/images
```

The images are the ones `fastboot` writes: `vendor.img` (EROFS), `modem.img`
and `bluetooth.img` (FAT). Extraction needs `fsck.erofs` and `mtools`.

### 4.3 What is needed for what

| staged path | needed for | source |
|---|---|---|
| `video-fw/vpu30_2v.mbn` | video decode/encode (Iris); **fatal** if missing | `vendor.img` `/firmware/` |
| `gpu-fw/gen80000_{sqe,aqe}.fw`, `gen80600_gmu.bin`, `gen80000_zap.mbn` | Adreno 825 | `vendor.img` `/firmware/` |
| `touch-fw/firmware.img` | Synaptics TCM touch | `vendor.img` `/firmware/synaptics/` |
| `wifi-fw/{amss20.bin,phy_ucode20.elf,bdwlan_m2582.elf,regdb.bin}` | ath12k WiFi | `modem.img` `/image/kiwi/` |
| `bt-fw/` | Bluetooth (btqca) | `bluetooth.img` |
| `adsp-fw/` | ADSP (pushed to the rootfs later, not embedded) | device dump |

Missing files other than the video firmware are reported by
`build-initramfs.py --check` and only cost the corresponding feature. BusyBox,
qbootctl and mke2fs are tools, not firmware: the initramfs repository builds
them from pinned sources.

### 4.4 Firmware versions

`firmware-manifest.json` in this repository records the sha256 of the versions
the board was validated with. A different factory release usually ships
different blobs; `fetch-firmware.py` prints `version_differs` for those and
`--strict` turns that into an error. Use it when a bug may be firmware-related,
but only the manifest build is a known-good reference.

## 5. Packing the boot image

```sh
python3 pack-boot.py --original /path/to/stock-boot.img
```

`--original` is required and must be a header-v4 `boot.img` from the same
firmware release as the phone. It is used only as the AVB header/footer donor:
keeping the stock descriptors leaves rollback and version metadata untouched.
Without it the image has no AVB footer and the bootloader rejects it, so the
packer refuses to run instead of producing a brick.

Output: `out/boot.img` (override with `--output`), plus `out/boot.avb.txt` and
`out/boot.json` describing what was packed.

The packer verifies before it writes: the `Image` config matches
`out-meizu/.config`, the embedded initramfs matches the one the initramfs
repository just built, the selected DTB is linked into the `Image`, and the new
payload fits the stock partition size.

The output is signed with nothing: the stock descriptor still covers the
original kernel. The device must be unlocked, with verification disabled
(`fastboot flashing unlock`, `--disable-verity --disable-verification` on
`vbmeta`).

## 6. Flashing and checking

```sh
python3 reboot-to-fastboot.py          # from Linux, over the serial console
until timeout 3 fastboot devices | grep -q .; do sleep 1; done
fastboot flash boot_a out/boot.img
fastboot flash boot_b out/boot.img
fastboot reboot
```

The kernel is flashed to both slots; the slot A/B retry counters are handled by
the initramfs (qbootctl), so no manual slot switching is needed in normal use.

After boot (the serial console is `/dev/ttyACM0`, 115200 8N1, user `root`,
password `m2582` unless the rootfs was built with another one):

```sh
grep -o "meizu.build=[0-9-]*" /proc/cmdline   # must match the stamp of the build
cat /sys/class/drm/card0-DSI-1/status          # connected + enabled
ip -br a                                       # wlp1s0 / usb0
ls /sys/class/bluetooth/                       # hci0
```

The root filesystem is not part of `boot.img`: it is delivered by the `rootfs`
repository as a cpio stream and unpacked on the board. See that repository's
README for the installation steps, and the initramfs rescue mode
(`m2582.rootfs=none` on the command line) if the system no longer boots.

## 7. Known limits

- Two machines building the same configuration do not produce byte-identical
  `Image`s (the build embeds timestamps and paths). What is reproducible is the
  configuration and the behaviour: `CLEAN=1` rebuilds differ only in
  `CONFIG_CMDLINE` and `CONFIG_INITRAMFS_SOURCE`.
- Vendor firmware and AVB metadata are the vendor's; they are never committed
  here.
- The embedded DTB depends on firmware-owned memory regions that come from the
  factory `vendor.img`; building does not need them, booting does.
- `CONFIG_MODULES` is off in the board configuration, so kernel changes need a
  full rebuild plus a reflash.
