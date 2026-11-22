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
		for (int i = cnt > 40 ? cnt - 40 : 0; i < cnt; i++) printf("%.88s\n", lines[i]);
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
	if (freopen("/dev/console", "w", stdout)) setvbuf(stdout, NULL, _IONBF, 0);

	for (;;) {
		sleep(round ? 1 : 15); /* let the deferred probes finish first */
		summary(buf, sizeof buf, round++);
		sleep(8);
		log_pages(buf, sizeof buf);
	}
}
