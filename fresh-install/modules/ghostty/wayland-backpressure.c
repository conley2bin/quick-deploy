// Native-Wayland Ghostty workaround for GTK4's fatal-on-EAGAIN event source.
// GTK 4.14.5 exits(1) when wl_display_flush sees *any* error. libwayland can
// also latch EAGAIN *before* that call while marshaling across its output ring.
// Interpose the underlying nonblocking sendmsg instead: on a tracked Wayland
// socket, wait briefly for writable and retry the exact unsent message. All
// other sends and non-EAGAIN errors retain their original behavior.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <poll.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

struct wl_display;
#define MAX_DISPLAYS 16
#ifndef MAX_WAIT_MS
#define MAX_WAIT_MS 10000
#endif
static atomic_int wayland_fds[MAX_DISPLAYS]; // fd+1; zero = unused
static pthread_once_t sendmsg_once = PTHREAD_ONCE_INIT;
static pthread_once_t wayland_once = PTHREAD_ONCE_INIT;
static ssize_t (*next_sendmsg)(int, const struct msghdr *, int);
static struct wl_display *(*next_connect)(const char *);
static struct wl_display *(*next_connect_to_fd)(int);
static int (*next_get_fd)(struct wl_display *);
static void (*next_disconnect)(struct wl_display *);

static void resolve_sendmsg(void) {
    next_sendmsg = dlsym(RTLD_NEXT, "sendmsg");
}

// A child may send network messages before dlopen-ing Wayland. Defer these
// lookups until its first actual Wayland connection; otherwise NULL sticks.
static void resolve_wayland(void) {
    next_connect = dlsym(RTLD_NEXT, "wl_display_connect");
    next_connect_to_fd = dlsym(RTLD_NEXT, "wl_display_connect_to_fd");
    next_get_fd = dlsym(RTLD_NEXT, "wl_display_get_fd");
    next_disconnect = dlsym(RTLD_NEXT, "wl_display_disconnect");
}

static void report(const char *message, int ms) {
    char buf[180];
    int n = snprintf(buf, sizeof buf, "ghostty-wayland-backpressure: %s (%d ms)\n", message, ms);
    if (n > 0 && n < (int)sizeof buf) {
        ssize_t ignored = write(STDERR_FILENO, buf, (size_t)n);
        (void)ignored;
    }
}

static int tracked(int fd) {
    for (int i = 0; i < MAX_DISPLAYS; ++i)
        if (atomic_load(&wayland_fds[i]) == fd + 1) return 1;
    return 0;
}

static int register_display(struct wl_display *display) {
    if (!display) return 0;
    if (!next_get_fd) { errno = ENOSYS; return -1; }
    int fd = next_get_fd(display);
    if (fd < 0) return -1;
    if (tracked(fd)) return 0;
    for (int i = 0; i < MAX_DISPLAYS; ++i) {
        int empty = 0;
        if (atomic_compare_exchange_strong(&wayland_fds[i], &empty, fd + 1)) return 0;
    }
    errno = EMFILE;
    return -1; // refuse an unprotected connection instead of silently falling back
}

static void unregister_display(struct wl_display *display) {
    if (!display || !next_get_fd) return;
    int fd = next_get_fd(display);
    for (int i = 0; i < MAX_DISPLAYS; ++i) {
        int expected = fd + 1;
        atomic_compare_exchange_strong(&wayland_fds[i], &expected, 0);
    }
}

struct wl_display *wl_display_connect(const char *name) {
    pthread_once(&wayland_once, resolve_wayland);
    if (!next_connect || !next_disconnect) { errno = ENOSYS; return NULL; }
    struct wl_display *display = next_connect(name);
    if (register_display(display) != 0) {
        int saved = errno;
        report("cannot track Wayland display", 0);
        if (display) next_disconnect(display);
        errno = saved;
        return NULL;
    }
    return display;
}

struct wl_display *wl_display_connect_to_fd(int fd) {
    pthread_once(&wayland_once, resolve_wayland);
    if (!next_connect_to_fd || !next_disconnect) { errno = ENOSYS; return NULL; }
    struct wl_display *display = next_connect_to_fd(fd);
    if (register_display(display) != 0) {
        int saved = errno;
        report("cannot track Wayland display", 0);
        if (display) next_disconnect(display);
        errno = saved;
        return NULL;
    }
    return display;
}

void wl_display_disconnect(struct wl_display *display) {
    pthread_once(&wayland_once, resolve_wayland);
    unregister_display(display);
    if (next_disconnect) next_disconnect(display);
}

static int elapsed_ms(const struct timespec *start) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (int)((now.tv_sec - start->tv_sec) * 1000 +
                 (now.tv_nsec - start->tv_nsec) / 1000000);
}

ssize_t sendmsg(int fd, const struct msghdr *message, int flags) {
    pthread_once(&sendmsg_once, resolve_sendmsg);
    if (!next_sendmsg) { errno = ENOSYS; return -1; }
    ssize_t result = next_sendmsg(fd, message, flags);
    if (result != -1 || errno != EAGAIN || !tracked(fd)) return result;

    struct timespec start;
    clock_gettime(CLOCK_MONOTONIC, &start);
    struct pollfd pfd = {.fd = fd, .events = POLLOUT};
    for (;;) {
        int left = MAX_WAIT_MS - elapsed_ms(&start);
        if (left <= 0) {
            report("Wayland socket remained blocked; original EAGAIN propagates", MAX_WAIT_MS);
            errno = EAGAIN;
            return -1;
        }
        pfd.revents = 0;
        int ready = poll(&pfd, 1, left);
        if (ready < 0) {
            if (errno == EINTR) continue;
            report("Wayland socket poll failed", elapsed_ms(&start));
            return -1;
        }
        if (!ready) continue;
        if (pfd.revents & (POLLERR | POLLHUP | POLLNVAL)) {
            report("Wayland socket closed while waiting", elapsed_ms(&start));
            errno = (pfd.revents & POLLNVAL) ? EBADF : EPIPE;
            return -1;
        }
        if (!(pfd.revents & POLLOUT)) continue;
        result = next_sendmsg(fd, message, flags);
        if (result != -1 || errno != EAGAIN) {
            if (result >= 0) report("recovered transient Wayland backpressure", elapsed_ms(&start));
            return result;
        }
    }
}
