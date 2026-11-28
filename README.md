# Meizu 22 (M2582 / SM8735) — host-side tools

Host-side tooling for the mainline Linux port to the Meizu 22 (M2582, Qualcomm
SM8735 "tuna"). It builds the kernel with its initramfs, stages the vendor
blobs and produces the flashable `boot.img`; it also drives the board through
the single USB port it exposes (serial console, file transfer, fastboot).

No absolute path from the author's machine is baked in: every input is either
in a sibling repository or dumped from your own phone. See [BUILD.md](BUILD.md)
for the full build recipe.

## Sibling repositories

| repository | contents |
|---|---|
| `linux` | kernel source, device trees, `arch/arm64/configs/meizu_m2582_defconfig` |
| `initramfs` | PID 1, rescue shell, BusyBox / qbootctl / mke2fs builders |
| `rootfs` | Arch Linux ARM root filesystem payload |
| `firmware` | vendor blobs (GPU, video, WiFi, BT, touch, ADSP) + sha256 manifest |
| `tools` | this repository: build, pack, flash, serial console, USB network |

The scripts assume the five checkouts are siblings:

```
work/
  linux/   initramfs/   rootfs/   firmware/   tools/
```

Every path can be overridden, so another layout works too:
`LINUX_DIR`, `INITRAMFS_DIR`, `ROOTFS_DIR`, `FIRMWARE_REPO` (and `FIRMWARE`
for a firmware tree that is already staged).

## Quick start

```sh
sh build-all.sh --check        # validate the sibling checkouts, build nothing
sh build-all.sh                # BusyBox, qbootctl, mke2fs, firmware, kernel
python3 pack-boot.py --original /path/to/stock-boot.img
```

`pack-boot.py` needs a stock `boot.img` from your own factory package (same
firmware release as the phone) as the AVB header/footer donor; it is not
distributed here. Flashing needs an unlocked device with verification
disabled.

## Tools

### Build

| tool | purpose |
|---|---|
| `build-all.sh` | orchestrator: builds everything a boot image needs, then prints the pack command |
| `build-kernel.sh` | build `Image` in `$LINUX_DIR/out-meizu`, embedding the initramfs; verifies the result |
| `fetch-firmware.py` | stage the vendor blobs from the firmware repository or from your own factory images |
| `firmware-manifest.json` | sha256 of the firmware versions the board was validated with |
| `extract-bt-fw.py` | helper for `fetch-firmware.py --images` (reads `bluetooth.img`) |

### Pack and flash

| tool | purpose |
|---|---|
| `pack-boot.py` | wrap `Image` into a boot image, keeping the stock AVB header/footer |
| `reboot-to-fastboot.py` | put the board into fastboot from Linux through the serial console |

### Serial console (115200 8N1, `/dev/ttyACM0`)

| tool | purpose |
|---|---|
| `console.py` | log in and run one command, or follow the boot log |
| `push-file.py` | small files over the console (base64, chunked, md5 on both ends) |

### USB NCM network (CDC NCM gadget, 192.168.42.1/24 on the board)

| tool | purpose |
|---|---|
| `net-file.py` | file transfer over the gadget link (tens of MB/s, md5 on both ends) |
| `net-up.sh` | bring the link up on the host (`up`/`down`/`status`/`speed`) |
| `net-install.sh` | make the link come up by itself after every board reboot |

## Host requirements

Linux x86_64 with `python3` (standard library only), `make`, `git`, `curl`,
clang/LLD and an aarch64 cross compiler (`aarch64-linux-gnu-gcc`, or set
`CROSS_COMPILE`). For `--images` firmware extraction: `erofs-utils` and
`mtools`. The `serial` module (pyserial) is needed only by the console tools.

## Licensing

The scripts are GPL-2.0-only unless a file says otherwise. `vendor/` contains
the AOSP `mkbootimg` and `avbtool.py` (Apache-2.0) used by the packer; see
`vendor/README.md`. Vendor firmware is not distributed here — it is staged from
the `firmware` repository or from your own device.
