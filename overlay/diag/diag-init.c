/*
 * a01q boot diagnostic init (PID 1). Boot with  rdinit=/diag-init  on the kernel command line.
 *
 * It replaces the Android first-stage init for bring-up tests: it never reboots, and it prints on the
 * screen (fbcon on /dev/console) what the kernel found: block devices, MMC hosts and the kernel
 * messages that mention storage, pinctrl, regulators, RPM/SMD. The screen is redrawn every 5 s.
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

static void crash_to_save_log(void)
{
	/*
	 * pmsg gets flooded by TWRP, so the log is saved the way that already works: a deliberate panic.
	 * pstore keeps the kernel log up to the panic as dmesg-ramoops-0 (the whole boot fits in it).
	 */
	printf("\n*** Saving the log: forced panic in 5 s. Then hold Power ~10 s, boot TWRP and copy\n"
	       "*** /sys/fs/pstore/dmesg-ramoops-0 ***\n");
	sleep(5);
	int fd = open("/proc/sysrq-trigger", O_WRONLY);
	if (fd >= 0) { write(fd, "c", 1); close(fd); }
	printf("(sysrq crash failed: no /proc/sysrq-trigger)\n");
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

int main(void)
{
	static char buf[1 << 18];
	int round = 0;

	mkdir("/proc", 0555); mkdir("/sys", 0555);
	mount("proc", "/proc", "proc", 0, NULL);
	mount("sysfs", "/sys", "sysfs", 0, NULL);
	mount("devtmpfs", "/dev", "devtmpfs", 0, NULL);
	if (freopen("/dev/console", "w", stdout)) setvbuf(stdout, NULL, _IONBF, 0);

	for (;;) {
		sleep(round ? 5 : 15); /* let the deferred probes finish first */
		printf("\033[2J\033[H");
		printf("A01Q DIAG round %d\n", round++);
		cat("/proc/uptime", 1);
		printf("== /proc/partitions ==\n"); cat("/proc/partitions", 14);
		printf("== /sys/class/mmc_host ==\n"); ls("/sys/class/mmc_host");
		printf("== /sys/block ==\n"); ls("/sys/block");
		printf("== regulators ==\n"); regulator_names();
		printf("== kernel messages (storage/pinctrl/rpm/...) ==\n");
		int n = klogctl(3, buf, sizeof buf - 1);
		if (n > 0) {
			char *lines[4000]; int cnt = 0;
			buf[n] = 0;
			for (char *p = strtok(buf, "\n"); p && cnt < 4000; p = strtok(NULL, "\n"))
				if (interesting(p)) lines[cnt++] = p;
			for (int i = cnt > 45 ? cnt - 45 : 0; i < cnt; i++) printf("%.88s\n", lines[i]);
		}
		if (round == 8) crash_to_save_log(); /* about 50 s after boot */
	}
}
