/*
 * a01q boot diagnostic init (PID 1). Boot with  rdinit=/diag-init  on the kernel command line.
 *
 * It replaces the Android first-stage init for bring-up tests: it never reboots, and it prints on the
 * screen (fbcon on /dev/console) what the kernel found: block devices, MMC hosts and the kernel
 * messages that mention storage, pinctrl, regulators, RPM/SMD. After a summary page it cycles through the WHOLE kernel log in pages (PAGE i/N), so a phone
 * video of the screen is enough; no TWRP / pstore needed. It never panics.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <dirent.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/klog.h>

static void cat(const char *path, int max_lines)
{
	char line[200];
	FILE *f = fopen(path, "r");
	if (!f) { printf("  (cannot open %s)\n", path); return; }
	while (max_lines-- > 0 && fgets(line, sizeof line, f)) printf("  %s", line);
	fclose(f);
}

static void ls(const char *path)
{
	struct dirent *e;
	DIR *d = opendir(path);
	if (!d) { printf("  (no %s)\n", path); return; }
	while ((e = readdir(d)))
		if (e->d_name[0] != '.') printf("  %s", e->d_name);
	printf("\n");
	closedir(d);
}

static void regulator_names(void)
{
	char path[96], name[64];
	struct dirent *e;
	DIR *d = opendir("/sys/class/regulator");
	if (!d) return;
	while ((e = readdir(d))) {
		if (e->d_name[0] == '.') continue;
		snprintf(path, sizeof path, "/sys/class/regulator/%s/name", e->d_name);
		FILE *f = fopen(path, "r");
		if (!f) continue;
		if (fgets(name, sizeof name, f)) { name[strcspn(name, "\n")] = 0; printf("  %s=%s", e->d_name, name); }
		fclose(f);
	}
	printf("\n");
	closedir(d);
}

/* Why is a device not probed? Print each fw_devlink supplier link of the consumer and its status
 * ("available" = supplier ready; "dormant"/"consumer probing" = still waiting for that supplier). */
static void devlinks_of(const char *consumer)
{
	char path[300], st[48];
	struct dirent *e;
	size_t cl = strlen(consumer);
	DIR *d = opendir("/sys/class/devlink");
	if (!d) { printf("  (no /sys/class/devlink)\n"); return; }
	while ((e = readdir(d))) {
		size_t n = strlen(e->d_name);
		if (n <= cl || strcmp(e->d_name + n - cl, consumer)) continue;
		snprintf(path, sizeof path, "/sys/class/devlink/%.200s/status", e->d_name);
		FILE *f = fopen(path, "r");
		st[0] = 0;
		if (f) { if (fgets(st, sizeof st, f)) st[strcspn(st, "\n")] = 0; fclose(f); }
		/* "platform:SUPPLIER--platform:CONSUMER": print only the supplier part */
		char *sep = strstr(e->d_name, "--");
		if (sep) *sep = 0;
		printf("  %-14s <- %.50s\n", st, e->d_name);
	}
	closedir(d);
}

static void ls_filter(const char *path, const char *a, const char *b, const char *c)
{
	struct dirent *e;
	DIR *d = opendir(path);
	if (!d) { printf("  (no %s)\n", path); return; }
	while ((e = readdir(d)))
		if (e->d_name[0] != '.' && (strstr(e->d_name, a) || strstr(e->d_name, b) || strstr(e->d_name, c)))
			printf("  %s", e->d_name);
	printf("\n");
	closedir(d);
}

static void why_page(void)
{
	printf("\033[2J\033[H");
	printf("A01Q DIAG  WHY NOT PROBED\n");
	printf("== devices_deferred (debugfs) ==\n");
	cat("/sys/kernel/debug/devices_deferred", 22);
	printf("== sdhci 7824900 (eMMC): suppliers ==\n"); devlinks_of("platform:7824900.sdhci");
	printf("== gpio-keys: suppliers ==\n"); devlinks_of("platform:gpio-keys");
	printf("== i2c 78b7000: suppliers ==\n"); devlinks_of("platform:78b7000.i2c");
	printf("== gpiochips ==\n"); ls("/sys/class/gpio");
	printf("== rpmsg devices ==\n"); ls("/sys/bus/rpmsg/devices");
	printf("== platform devices: rpm/smd/smem/pinctrl/hwlock ==\n");
	ls_filter("/sys/bus/platform/devices", "rpm", "smd", "smem");
	ls_filter("/sys/bus/platform/devices", "pinctrl", "hwlock", "mutex");
	printf("== bound drivers ==\n");
	ls_filter("/sys/bus/platform/drivers", "pinctrl", "smem", "rpm");
	ls_filter("/sys/bus/platform/drivers", "sdhci", "qcom-apcs", "hwspin");
}

static int interesting(const char *l)
{
	static const char *k[] = { "mmc", "sdhci", "pinctrl", "regulator", "rpm", "smd", "smem",
		"scm", "spmi", "pm8937", "hwspinlock", "deferred", "failed", "error", "Kernel panic",
		"BUG", "Oops", NULL };
	if (strstr(l, "+0x") || strstr(l, "sync_state") || strstr(l, "mboxes")) return 0; /* backtraces, noise */
	for (int i = 0; k[i]; i++) if (strstr(l, k[i])) return 1;
	return 0;
}

#define LINES_PER_PAGE 82
#define PAGE_SECS 7

static void summary(char *buf, size_t sz, int round)
{
	printf("\033[2J\033[H");
	printf("A01Q DIAG  SUMMARY  (cycle %d)  uptime: ", round);
	cat("/proc/uptime", 1);
	printf("== /proc/partitions ==\n"); cat("/proc/partitions", 14);
	printf("== /sys/class/mmc_host ==\n"); ls("/sys/class/mmc_host");
	printf("== /sys/block ==\n"); ls("/sys/block");
	printf("== regulators ==\n"); regulator_names();
	printf("== kernel messages (storage/pinctrl/rpm/...) ==\n");
	int n = klogctl(3, buf, sz - 1);
	if (n > 0) {
		static char *lines[8000]; int cnt = 0;
		buf[n] = 0;
		for (char *p = strtok(buf, "\n"); p && cnt < 8000; p = strtok(NULL, "\n"))
			if (interesting(p)) lines[cnt++] = p;
		for (int i = cnt > 30 ? cnt - 30 : 0; i < cnt; i++) printf("%.88s\n", lines[i]);
	}
}

static void log_pages(char *buf, size_t sz)
{
	static char *lines[12000];
	int cnt = 0, n = klogctl(3, buf, sz - 1);
	if (n <= 0) return;
	buf[n] = 0;
	for (char *p = strtok(buf, "\n"); p && cnt < 12000; p = strtok(NULL, "\n"))
		if (!strstr(p, "+0x")) lines[cnt++] = p; /* skip backtrace lines */
	int pages = (cnt + LINES_PER_PAGE - 1) / LINES_PER_PAGE;
	for (int pg = 0; pg < pages; pg++) {
		printf("\033[2J\033[H");
		printf("A01Q LOG  PAGE %d/%d  (lines %d-%d of %d)\n", pg + 1, pages,
		       pg * LINES_PER_PAGE + 1, (pg + 1) * LINES_PER_PAGE < cnt ? (pg + 1) * LINES_PER_PAGE : cnt, cnt);
		for (int i = pg * LINES_PER_PAGE; i < cnt && i < (pg + 1) * LINES_PER_PAGE; i++)
			printf("%.88s\n", lines[i]);
		sleep(PAGE_SECS);
	}
}

int main(void)
{
	static char buf[1 << 19];
	int round = 0;

	mkdir("/proc", 0555); mkdir("/sys", 0555);
	mount("proc", "/proc", "proc", 0, NULL);
	mount("sysfs", "/sys", "sysfs", 0, NULL);
	mount("devtmpfs", "/dev", "devtmpfs", 0, NULL);
	mount("debugfs", "/sys/kernel/debug", "debugfs", 0, NULL);
	if (freopen("/dev/console", "w", stdout)) setvbuf(stdout, NULL, _IONBF, 0);

	for (;;) {
		sleep(round ? 1 : 15); /* let the deferred probes finish first */
		summary(buf, sizeof buf, round++);
		sleep(8);
		why_page();
		sleep(14);
		log_pages(buf, sizeof buf);
	}
}
