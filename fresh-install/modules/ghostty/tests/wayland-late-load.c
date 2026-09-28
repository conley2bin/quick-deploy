// A terminal child can call sendmsg before loading a GUI library. The preload
// must not cache missing libwayland symbols during that unrelated early send.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>

struct wl_display;
int main(void) {
    int fds[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, fds) != 0) return 2;
    char c='X';
    struct iovec iov={.iov_base=&c,.iov_len=1};
    struct msghdr msg={.msg_iov=&iov,.msg_iovlen=1};
    if (sendmsg(fds[0], &msg, MSG_NOSIGNAL) != 1) return 3;

    void *lib = dlopen("libwayland-client.so.0", RTLD_NOW | RTLD_GLOBAL);
    if (!lib) { fprintf(stderr, "%s\n", dlerror()); return 4; }
    struct wl_display *(*connect_to_fd)(int) = dlsym(RTLD_DEFAULT, "wl_display_connect_to_fd");
    void (*disconnect)(struct wl_display *) = dlsym(RTLD_DEFAULT, "wl_display_disconnect");
    if (!connect_to_fd || !disconnect) return 5;
    struct wl_display *display=connect_to_fd(fds[0]);
    if (!display) { perror("late Wayland connect"); return 6; }
    disconnect(display);
    close(fds[1]);
    dlclose(lib);
    puts("late-loaded Wayland connection works after unrelated sendmsg");
    return 0;
}
