#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
#
# Build the Meizu 22 (M2582 / SM8735 "tuna") arm64 kernel with the diagnostic
# initramfs embedded in it.
#
# The kernel lives in a sibling checkout (default ../linux) and the initramfs
# in another (default ../initramfs); see BUILD.md.  The configuration is the
# one in the kernel tree (arch/arm64/configs/meizu_m2582_defconfig); this
# script only adds what a defconfig cannot carry: the build stamp, the command
# line it is forced into and the initramfs path.
#
# Environment:
#   LINUX_DIR=../linux        kernel checkout to build
#   INITRAMFS_DIR=../initramfs initramfs checkout that builds the cpio
#   FIRMWARE=<dir>            staged firmware (default: ./firmware, else ../firmware)
#   JOBS=n                    parallel jobs (default: nproc)
#   CLEAN=1                   remove out-meizu/ first (full rebuild from defconfig)
#   INITRAMFS=0               skip rebuilding the initramfs (kernel-only iteration)
#   VERBOSE=1                 initcall_debug and full loglevel on the panel console
#   EXTRA="..."               extra kernel command line words (single line)
#   FEATURES=a,b              probe only these m2582 subsystems (default: all)
#   DISABLE=a,b               leave these m2582 subsystems unprobed
#   M2582_EUD=1               build the EUD debug port (off by default)
#   SAVEDEFCONFIG=1           write out-meizu/defconfig back to the tree and exit
#   M2582_KERNEL_DIR=<dir>    kernel checkout (overrides LINUX_DIR; kept for
#                             compatibility with the old workspace layout)
#   M2582_ARTIFACTS=<dir>     where the extracted-config baseline is written
#                             (default: the kernel out dir)
#
# It builds in $LINUX_DIR/out-meizu and never calls make outside that out dir.
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
kernel=${M2582_KERNEL_DIR:-${LINUX_DIR:-$root/../linux}}
initramfs_dir=${INITRAMFS_DIR:-$root/../initramfs}
defconfig=meizu_m2582_defconfig

die() { echo "build-kernel: $*" >&2; exit 1; }

# Every path handed to another script is absolute: the initramfs builder is
# invoked from a different directory.
abs() { CDPATH= cd -- "$1" 2>/dev/null && pwd; }

resolved=$(abs "$kernel") || die "kernel tree not found: $kernel
  clone https://github.com/MEIZU-22-SM8735-Development/linux next to this
  repository (or set LINUX_DIR)."
kernel=$resolved
[ -f "$kernel/arch/arm64/configs/$defconfig" ] || \
	die "missing arch/arm64/configs/$defconfig in $kernel
  this is not the Meizu 22 kernel branch (Linux-7.2-MEIZU_22-M2582)."

resolved=$(abs "$initramfs_dir") || die "initramfs repository not found: $initramfs_dir
  clone https://github.com/MEIZU-22-SM8735-Development/initramfs next to this
  repository (or set INITRAMFS_DIR)."
initramfs_dir=$resolved
[ -f "$initramfs_dir/build-initramfs.py" ] || \
	die "missing build-initramfs.py in $initramfs_dir
  this is not the initramfs repository (or set INITRAMFS_DIR)."

# The initramfs builder reads the firmware from here; a staged copy in this
# repository wins, the sibling firmware checkout is the fallback.
if [ -z "${FIRMWARE:-}" ]; then
	if [ -d "$root/firmware" ]; then
		FIRMWARE=$root/firmware
	elif [ -d "$root/../firmware" ]; then
		FIRMWARE=$root/../firmware
	else
		die "no firmware found: expected $root/firmware or $root/../firmware
  stage it with 'python3 fetch-firmware.py --from-repo ../firmware' (see
  BUILD.md), or set FIRMWARE=<dir>."
	fi
fi
resolved=$(abs "$FIRMWARE") || die "firmware directory not found: $FIRMWARE"
FIRMWARE=$resolved
export FIRMWARE

artifacts=${M2582_ARTIFACTS:-$kernel/out-meizu}
initramfs=$initramfs_dir/artifacts/initramfs/initramfs.cpio

jobs=$(printenv JOBS || true)
[ -n "$jobs" ] || jobs=$(nproc)
case "$jobs" in ''|*[!0-9]*|0) die "JOBS must be a positive integer";; esac

cd "$kernel"
[ "${CLEAN:-0}" = 1 ] && rm -rf out-meizu
if [ ! -f out-meizu/.config ]; then
	echo "build-kernel: seeding out-meizu/.config from $defconfig"
	make -j"$jobs" O=out-meizu ARCH=arm64 LLVM=1 "$defconfig"
fi

if [ "${SAVEDEFCONFIG:-0}" = 1 ]; then
	make -j"$jobs" O=out-meizu ARCH=arm64 LLVM=1 savedefconfig
	# The command line and the initramfs path are build inputs, not board
	# configuration; keep them out of the committed defconfig.
	grep -v -e '^CONFIG_CMDLINE=' -e '^CONFIG_INITRAMFS_SOURCE=' \
		out-meizu/defconfig > "arch/arm64/configs/$defconfig"
	printf '%s\n' 'CONFIG_CMDLINE="console=tty0 loglevel=7 fbcon=font:TER16x32 mem_sleep_default=s2idle panic=0 log_buf_len=8M"' >> "arch/arm64/configs/$defconfig"
	echo "build-kernel: wrote arch/arm64/configs/$defconfig (review it before committing)"
	exit 0
fi

# gen_init_cpio comes from this kernel build; the initramfs repository only
# assembles the archive.
make -j"$jobs" O=out-meizu ARCH=arm64 LLVM=1 usr_gen_init_cpio
if [ "${INITRAMFS:-1}" = 1 ]; then
	if [ ! -x "$initramfs_dir/artifacts/busybox/busybox" ]; then
		echo 'build-kernel: busybox missing, building it'
		sh "$initramfs_dir/build-busybox.sh" || \
			die 'busybox build failed; see ../initramfs/build-busybox.sh (it needs
  aarch64-linux-gnu-gcc, curl and a writable artifacts/ directory)'
	fi
	# FIRMWARE and GEN_INIT_CPIO are the contract of the initramfs repository;
	# its other defaults (busybox, qbootctl, mke2fs, output dir) apply as-is.
	KERNEL_DIR=$kernel GEN_INIT_CPIO=$kernel/out-meizu/usr/gen_init_cpio \
		python3 "$initramfs_dir/build-initramfs.py" || \
		die "initramfs build failed (run $initramfs_dir/build-initramfs.py --check)"
	[ -f "$initramfs" ] || \
		die "initramfs not found: $initramfs
  the initramfs repository writes artifacts/initramfs/initramfs.cpio;
  use INITRAMFS=0 for a kernel-only build."
fi

stamp=$(date +%m%d-%H%M)
epoch=$(date +%s)
cmdline="systemd.log_target=kmsg systemd.log_level=info console=tty0 loglevel=7 fbcon=font:TER16x32 mem_sleep_default=s2idle rdinit=/init panic=0 softlockup_panic=1 hung_task_panic=1 log_buf_len=8M meizu.build=$stamp meizu.built=$epoch"
[ -z "${VERBOSE:-}" ] || cmdline="$cmdline initcall_debug loglevel=8 ignore_loglevel hardlockup_panic=1 nmi_watchdog=1 consoleblank=0"
[ -z "${EXTRA:-}" ] || cmdline="$cmdline $EXTRA"
[ -z "${FEATURES:-}" ] || cmdline="$cmdline m2582.features=$FEATURES"
[ -z "${DISABLE:-}" ] || cmdline="$cmdline m2582.disable=$DISABLE"

# A newline in these makes scripts/config write a half CONFIG_CMDLINE, and the
# next syncconfig then silently drops ARCH_QCOM/DRM*.
lines=$(printf '%s%s%s' "${EXTRA:-}" "${FEATURES:-}" "${DISABLE:-}" | wc -l)
[ "$lines" -eq 0 ] || die "EXTRA/FEATURES/DISABLE must be a single line"

scripts/config --file out-meizu/.config \
	--set-str CMDLINE "$cmdline" --enable CMDLINE_FORCE
if [ "${INITRAMFS:-1}" = 1 ]; then
	scripts/config --file out-meizu/.config \
		--set-str INITRAMFS_SOURCE "$initramfs" --enable INITRAMFS_FORCE \
		--enable INITRAMFS_COMPRESSION_GZIP
fi
if [ "${M2582_EUD:-0}" = 1 ]; then
	scripts/config --file out-meizu/.config --enable EXTCON --enable USB_QCOM_EUD
else
	scripts/config --file out-meizu/.config --disable EXTCON --disable USB_QCOM_EUD
fi

make -j"$jobs" O=out-meizu ARCH=arm64 LLVM=1 olddefconfig
# Promptless symbols cannot be expressed in a defconfig and kconfig drops them
# on every syncconfig; put them back afterwards.
for sym in FB_DEFERRED_IO; do
	grep -q "^CONFIG_${sym}=y" out-meizu/.config || \
		printf 'CONFIG_%s=y\n' "$sym" >> out-meizu/.config
done

# Refuse an image that lost the board essentials: a missing display driver
# still links and still boots to a dark screen, which here is indistinguishable
# from the bug being chased.
missing=
for sym in ARCH_QCOM DRM DRM_MSM DRM_MSM_MDSS DRM_MSM_DPU DRM_MSM_DSI \
	   DRM_PANEL_MEIZU_M2582 FRAMEBUFFER_CONSOLE FONT_TER16x32 \
	   FB_DEFERRED_IO SM_DISPCC_TUNA USB_DWC3_MSM; do
	grep -q "^CONFIG_${sym}=y" out-meizu/.config || missing="$missing $sym"
done
[ -z "$missing" ] || \
	die ".config lost:$missing (delete out-meizu/.config to re-seed it from $defconfig)"

make -j"$jobs" O=out-meizu ARCH=arm64 LLVM=1 Image
mkdir -p "$artifacts"
sh scripts/extract-ikconfig out-meizu/arch/arm64/boot/Image > "$artifacts/current-image-extracted.config"
cmp out-meizu/.config "$artifacts/current-image-extracted.config"

printf '\nbuild %s\n' "$stamp"
ls -l out-meizu/arch/arm64/boot/Image out-meizu/arch/arm64/boot/dts/qcom/meizu-m2582.dtb
