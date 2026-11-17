# Mainline Linux 7.3 for the Samsung Galaxy A01 (a01q)

Target: regular Brazilian Galaxy A01 (SM-A015*, MSM8937/SDM439 family).

## Status (milestone 1)

Enabled: touchscreen (FocalTech via edt-ft5x06), Wi-Fi (WCNSS, needs stock firmware),
framebuffer (simple-framebuffer, bootloader splash), eMMC, SD card,
volume/power keys, pstore (ramoops), SMEM/RPM, PM8953 regulators.

NOT enabled yet: USB gadget, GPU, display panel
driver, charger/fuel gauge, audio, camera, cpufreq.

## Layout

This repo holds only the a01q changes (~30 KB). The CI downloads mainline
Linux v7.3.1 (gregkh/linux tag), applies `0001-qcom-add-samsung-a01q.patch`
and copies `overlay/` on top.

## Files added

- `overlay/arch/arm64/boot/dts/qcom/msm8937-samsung-a01q.dts`
- `overlay/arch/arm64/configs/a01q.config` and `a01q-touch-wifi.config` (config fragments)
- `overlay/arch/arm64/boot/dts/qcom/msm8937-samsung-a01q-touch-wifi.dtsi` (touch + WCNSS, included by the DTS)
- `overlay/scripts/a01q-trim.sh` (drops non-Qualcomm platforms)
- `.github/workflows/build-a01q.yml` (free GitHub Actions build)
- `0001-qcom-add-samsung-a01q.patch` (Makefile + DT binding entry)

## Build locally (inside a v7.3.1 tree with the patch and overlay applied)

    export ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-
    make defconfig a01q.config a01q-touch-wifi.config
    scripts/a01q-trim.sh && make olddefconfig
    make -j$(nproc) Image.gz dtbs modules
    cat arch/arm64/boot/Image.gz \
        arch/arm64/boot/dts/qcom/msm8937-samsung-a01q.dtb > Image.gz-dtb

Pack `Image.gz-dtb` with your usual mkbootimg parameters (base/offsets/cmdline
from the stock boot.img header; keep the stock `androidboot.*` args).

## Compatibility notes (all regular A01 units)

- The DTS lists board-id subtypes 0xf1..0xf9 so the stock LK matches every HW
  revision; the memory node is filled in by the bootloader (1 GB/2 GB).
- Touch IC differs between units. The DTS describes the FocalTech FT5446 at 0x38
  (driven by edt-ft5x06 through the FT5426 compatible, unverified). Units with a
  Goodix GT1x at 0x14 have no mainline driver and will not get touch yet.
- Wi-Fi needs the stock WCNSS firmware (wcnss.mdt/.mbn from the `modem` partition and
  WCNSS_qcom_wlan_nv.bin) copied under /lib/firmware. The iris chip is a guess
  (qcom,wcn3620); try wcn3660 or wcn3660b if the radio does not come up.

## Known unknowns

- Splash framebuffer pixel format (a8r8g8b8 vs r8g8b8) is unconfirmed.
- The DTS and config were written without a compile check: expect to fix a
  typo or a missing Kconfig symbol on the first build.
- Vendor (4.19) modules such as `fts_ts` or `wlan.ko` do NOT load on a 7.x kernel.
  Use in-tree drivers instead: `edt-ft5x06` (touch) and `wcn36xx` + WCNSS (Wi-Fi).
