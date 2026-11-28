#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
#
# Build the Meizu 22 (M2582 / SM8735 "tuna") boot image from the sibling
# repositories, in one go:
#
#   ../linux       kernel source (the branch with meizu_m2582_defconfig)
#   ../initramfs   PID 1, rescue tools, BusyBox/qbootctl/mke2fs builders
#   ../rootfs      the distro root filesystem (delivered separately, not packed
#                  into boot.img)
#   ../firmware    vendor blobs, staged into ./firmware before the build
#
# What it does, in order:
#   1. initramfs/build-busybox.sh   static aarch64 BusyBox
#   2. initramfs/build-qbootctl.sh  A/B slot tool for the initramfs
#   3. initramfs/build-mke2fs.sh    mke2fs for the rescue shell
#   4. fetch-firmware.py --from-repo <firmware>   stage the blobs into ./firmware
#   5. build-kernel.sh              Image with the initramfs embedded
#   6. print the pack-boot.py command (packing needs your stock boot.img)
#
# Environment:
#   LINUX_DIR=../linux        kernel checkout
#   INITRAMFS_DIR=../initramfs initramfs checkout
#   ROOTFS_DIR=../rootfs      rootfs checkout (checked, not built here)
#   FIRMWARE_REPO=../firmware firmware checkout or git URL to stage from
#   FIRMWARE=<dir>            staged firmware to build with (default ./firmware)
#   JOBS=n, CLEAN=1, INITRAMFS=0, VERBOSE=1, EXTRA=..., FEATURES=..., DISABLE=...,
#   M2582_EUD=1               passed through to build-kernel.sh
#
# Usage: sh build-all.sh [--check]
#   --check   validate the prerequisites and print the resolved paths, build nothing
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
linux_dir=${LINUX_DIR:-$root/../linux}
initramfs_dir=${INITRAMFS_DIR:-$root/../initramfs}
rootfs_dir=${ROOTFS_DIR:-$root/../rootfs}
firmware_repo=${FIRMWARE_REPO:-$root/../firmware}
firmware=${FIRMWARE:-$root/firmware}

check_only=0
for arg in "$@"; do
	case "$arg" in
	--check) check_only=1 ;;
	-h|--help) sed -n '3,31p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
	*) echo "build-all: unknown argument: $arg (try --help)" >&2; exit 2 ;;
	esac
done

die() { echo "build-all: $*" >&2; exit 1; }
say() { echo; echo "== $*"; }
abs() { CDPATH= cd -- "$1" 2>/dev/null && pwd; }
need_file() { [ -f "$1" ] || die "$2
  expected at: $1"; }
need_tool() { command -v "$1" >/dev/null 2>&1 || die "$2
  missing host tool: $1"; }

# --- fail early: everything below is a precondition, not a build step --------
need_tool python3 "python3 is required"
need_tool make "make is required to build the kernel"
need_tool git "git is required (firmware staging, tool builds)"
need_tool curl "curl is required (downloading pinned sources)"
need_tool clang "clang/LLVM is required: the kernel is built with LLVM=1
  install clang, lld and llvm; do not use a vendor GCC for the kernel"
need_tool ld.lld "LLD is required: the kernel is built with LLVM=1"
cross=${CROSS_COMPILE:-aarch64-linux-gnu-}
need_tool "${cross}gcc" "an aarch64 cross compiler is required for BusyBox,
  qbootctl and mke2fs (default prefix: aarch64-linux-gnu-; set CROSS_COMPILE
  to use another one)"

resolved=$(abs "$linux_dir") || die "kernel repository not found: $linux_dir
  clone https://github.com/MEIZU-22-SM8735-Development/linux next to this
  repository, or set LINUX_DIR."
linux_dir=$resolved
need_file "$linux_dir/arch/arm64/configs/meizu_m2582_defconfig" \
	"$linux_dir is not the Meizu 22 kernel checkout
  (no arch/arm64/configs/meizu_m2582_defconfig)."

resolved=$(abs "$initramfs_dir") || die "initramfs repository not found: $initramfs_dir
  clone https://github.com/MEIZU-22-SM8735-Development/initramfs next to this
  repository, or set INITRAMFS_DIR."
initramfs_dir=$resolved
for f in build-busybox.sh build-qbootctl.sh build-mke2fs.sh build-initramfs.py; do
	need_file "$initramfs_dir/$f" "$initramfs_dir is not the initramfs checkout
  (no $f)."
done

# The firmware may come from a local checkout or a git URL; only a local one is
# checked here (a URL is cloned by fetch-firmware.py).
case "$firmware_repo" in
*://*|git@*) : ;;
*)
	resolved=$(abs "$firmware_repo") || die "firmware repository not found: $firmware_repo
  clone https://github.com/MEIZU-22-SM8735-Development/firmware next to this
  repository, or set FIRMWARE_REPO=<path or URL>."
	firmware_repo=$resolved
	need_file "$firmware_repo/manifest.json" "$firmware_repo is not the firmware checkout
  (no manifest.json)."
	;;
esac

if [ -n "${FIRMWARE:-}" ]; then
	resolved=$(abs "$FIRMWARE") || die "staged firmware directory not found: $FIRMWARE
  unset FIRMWARE to have the firmware repository staged into ./firmware."
	firmware=$resolved
else
	firmware=$root/firmware
	if [ ! -d "$firmware" ]; then
		echo "build-all: $firmware does not exist yet, it will be staged" >&2
	fi
fi

# The rootfs is flashed on its own, so a missing checkout is not fatal here.
if [ -d "$rootfs_dir" ]; then
	if resolved=$(abs "$rootfs_dir"); then rootfs_dir=$resolved; fi
else
	echo "build-all: warning: rootfs repository not found at $rootfs_dir" >&2
	echo "  boot.img does not contain it, but flashing a usable system needs the" >&2
	echo "  rootfs payload from there (set ROOTFS_DIR, or clone rootfs next to this" >&2
	echo "  repository)." >&2
fi

say "repositories"
echo "  tools:      $root"
echo "  linux:      $linux_dir"
echo "  initramfs:  $initramfs_dir"
echo "  rootfs:     $rootfs_dir"
echo "  firmware:   $firmware_repo"
echo "  staging:    $firmware"

if [ "$check_only" = 1 ]; then
	echo
	echo "build-all: prerequisites ok (--check, nothing built)"
	exit 0
fi

# --- build -------------------------------------------------------------------
before=$(date +%s)
step() {
	echo
	echo "== $1"
	echo "   (elapsed: $(( $(date +%s) - before ))s)"
}

step "1/5 static aarch64 BusyBox"
sh "$initramfs_dir/build-busybox.sh"

step "2/5 qbootctl (A/B slot tool)"
sh "$initramfs_dir/build-qbootctl.sh"

step "3/5 mke2fs (rescue shell)"
sh "$initramfs_dir/build-mke2fs.sh"

step "4/5 firmware staging"
if [ "$(abs "$firmware_repo")" = "$firmware" ]; then
	echo "   firmware repository used in place: $firmware"
else
	python3 "$root/fetch-firmware.py" --from-repo "$firmware_repo" --out "$firmware"
fi
# A blob that blocks the build should stop the run here, not 20 minutes into
# the kernel build.
python3 "$root/fetch-firmware.py" --check --out "$firmware" >/dev/null || \
	die "firmware staging is incomplete: run
  python3 fetch-firmware.py --check --out $firmware"

step "5/5 kernel + initramfs"
FIRMWARE=$firmware LINUX_DIR=$linux_dir INITRAMFS_DIR=$initramfs_dir \
	sh "$root/build-kernel.sh"

cat <<EOF

== done ($(( $(date +%s) - before ))s)
   kernel:  $linux_dir/out-meizu/arch/arm64/boot/Image
   next, pack the boot image.  pack-boot.py needs the stock boot.img from your
   own factory package (same firmware release as the phone) as the AVB
   header/footer donor; nothing is packed without it:

     python3 $root/pack-boot.py --original /path/to/stock-boot.img \\
             --output $root/out/boot.img

   then flash both slots of the unlocked device:

     fastboot devices
     fastboot flash boot_a $root/out/boot.img
     fastboot flash boot_b $root/out/boot.img
     fastboot reboot

   the rootfs payload comes from the rootfs repository; the vendor blobs from
   the staging directory are already embedded in the initramfs.
EOF
