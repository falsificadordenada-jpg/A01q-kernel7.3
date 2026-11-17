#!/bin/sh
# SPDX-License-Identifier: GPL-2.0
# Drop every non-Qualcomm SoC platform from the generic arm64 defconfig.
# Cuts build time a lot and shrinks the image. Run after "make defconfig a01q.config",
# then finish with "make olddefconfig".
set -e
cd "$(dirname "$0")/.."
for sym in $(sed -n 's/^\(CONFIG_ARCH_[A-Z0-9_]*\)=y$/\1/p' .config); do
	case "$sym" in
	CONFIG_ARCH_QCOM|CONFIG_ARCH_HAS_*|CONFIG_ARCH_SUPPORTS_*|CONFIG_ARCH_MMAP_*|CONFIG_ARCH_WANT_*|CONFIG_ARCH_USE_*|CONFIG_ARCH_ENABLE_*|CONFIG_ARCH_FORCE_*|CONFIG_ARCH_KEEP_*|CONFIG_ARCH_STACKWALK|CONFIG_ARCH_MHP_*|CONFIG_ARCH_PROC_*|CONFIG_ARCH_FLATMEM_*|CONFIG_ARCH_SPARSEMEM_*|CONFIG_ARCH_INLINE_*|CONFIG_ARCH_HIBERNATION_*|CONFIG_ARCH_SELECT_*|CONFIG_ARCH_NR_*|CONFIG_ARCH_DMA_*|CONFIG_ARCH_MEMORY_*) ;;
	*) scripts/config --disable "${sym#CONFIG_}" ;;
	esac
done
