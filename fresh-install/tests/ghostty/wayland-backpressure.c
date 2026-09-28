#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

struct wl_display;
struct wl_proxy;
struct wl_interface { const char *name; int version, method_count; void *methods; int event_count; void *events; };
extern const struct wl_interface wl_registry_interface;
extern struct wl_display *wl_display_connect_to_fd(int);
extern int wl_display_get_error(struct wl_display *);
extern int wl_display_flush(struct wl_display *);
extern void wl_display_disconnect(struct wl_display *);
extern struct wl_proxy *wl_proxy_marshal_constructor(struct wl_proxy *, unsigned, const struct wl_interface *, ...);
extern void wl_proxy_destroy(struct wl_proxy *);

static unsigned char wire[65536];
static size_t wire_len;
static int received_fd_count;
static int waitms(int ms) { struct timespec t={.tv_sec=ms/1000,.tv_nsec=(ms%1000)*1000000L}; return nanosleep(&t, NULL); }
static void *drain(void *ctx) {
    int fd=*(int *)ctx;
    waitms(300);
    if (getenv("NO_DRAIN")) return NULL;
    for(int i=0;i<30;i++) {
        ssize_t n=recv(fd, wire+wire_len, sizeof wire-wire_len, MSG_DONTWAIT);
        if (n > 0) wire_len += (size_t)n;
        else if(n<0 && errno!=EAGAIN) break;
        waitms(20);
    }
    return NULL;
}
static void *drain_fds(void *ctx) {
    int fd=*(int *)ctx;
    waitms(300);
    for (int i=0;i<30;i++) {
        char buf[16384], control[CMSG_SPACE(sizeof(int))];
        struct iovec io={.iov_base=buf,.iov_len=sizeof buf};
        struct msghdr msg={.msg_iov=&io,.msg_iovlen=1,.msg_control=control,.msg_controllen=sizeof control};
        ssize_t n=recvmsg(fd,&msg,MSG_DONTWAIT);
        if (n<0 && errno!=EAGAIN) break;
        if (n<=0) { waitms(20); continue; }
        for (struct cmsghdr *c=CMSG_FIRSTHDR(&msg); c; c=CMSG_NXTHDR(&msg,c)) {
            if (c->cmsg_level==SOL_SOCKET && c->cmsg_type==SCM_RIGHTS) {
                int received;
                memcpy(&received,CMSG_DATA(c),sizeof received);
                close(received);
                received_fd_count++;
            }
        }
        waitms(20);
    }
    return NULL;
}
static size_t fill(int fd) {
    char buf[4096]={0};size_t total=0;ssize_t n;
    while((n=send(fd,buf,sizeof buf,MSG_NOSIGNAL|MSG_DONTWAIT))>=0) total+=(size_t)n;
    if(errno!=EAGAIN){perror("fill");exit(2);}
    printf("filled_socket_bytes=%zu\n",total);
    return total;
}
int main(int argc, char **argv) {
    (void)argc;(void)argv;
    int unrelated[2]; if(socketpair(AF_UNIX, SOCK_STREAM,0,unrelated)) return 2;
    int sz=4096;setsockopt(unrelated[0],SOL_SOCKET,SO_SNDBUF,&sz,sizeof sz);fill(unrelated[0]);
    struct msghdr msg={0};char c='a';struct iovec io={.iov_base=&c,.iov_len=1}; msg.msg_iov=&io; msg.msg_iovlen=1;
    struct timespec start,end;clock_gettime(CLOCK_MONOTONIC,&start);
    int unrelated_ret=sendmsg(unrelated[0],&msg,MSG_DONTWAIT|MSG_NOSIGNAL),unrelated_errno=errno;
    clock_gettime(CLOCK_MONOTONIC,&end);
    long unrelated_ms=(end.tv_sec-start.tv_sec)*1000+(end.tv_nsec-start.tv_nsec)/1000000;
    printf("unrelated ret=%d errno=%d elapsed_ms=%ld\n",unrelated_ret,unrelated_errno,unrelated_ms);
    if(unrelated_ret!=-1||unrelated_errno!=EAGAIN||unrelated_ms>150)return 3;
    close(unrelated[0]);close(unrelated[1]);
    int closed[2];if(socketpair(AF_UNIX, SOCK_STREAM,0,closed))return 2;
    close(closed[1]);
    int epipe_ret=sendmsg(closed[0],&msg,MSG_DONTWAIT|MSG_NOSIGNAL),epipe_errno=errno;
    printf("closed_peer ret=%d errno=%d\n",epipe_ret,epipe_errno);
    close(closed[0]);
    if(epipe_ret!=-1||epipe_errno!=EPIPE)return 4;

    int fds[2];if(socketpair(AF_UNIX, SOCK_STREAM,0,fds))return 2;
    setsockopt(fds[0],SOL_SOCKET,SO_SNDBUF,&sz,sizeof sz);
    size_t fill_bytes=fill(fds[0]);
    struct wl_display *d=wl_display_connect_to_fd(fds[0]); if(!d){perror("connect_to_fd");return 2;}
    pthread_t p;if(pthread_create(&p,NULL,drain,&fds[1]))return 2;
    clock_gettime(CLOCK_MONOTONIC,&start);
    int e=0,n=0;
    for (int i=0;i<600;i++) {
        struct wl_proxy *proxy=wl_proxy_marshal_constructor((struct wl_proxy *)d,1,&wl_registry_interface,NULL);
        if(proxy) wl_proxy_destroy(proxy);
        n++;
        e=wl_display_get_error(d);
        if(e)break;
    }
    int ret=wl_display_flush(d),err=errno;
    clock_gettime(CLOCK_MONOTONIC,&end);
    long ms=(end.tv_sec-start.tv_sec)*1000+(end.tv_nsec-start.tv_nsec)/1000000;
    pthread_join(p,NULL);
    printf("requests=%d flush_ret=%d errno=%d display_error=%d elapsed_ms=%ld\n",n,ret,err,e,ms);
    if (getenv("EXPECT_WIRE")) {
        const size_t message_bytes=12;
        if (wire_len != fill_bytes + 600*message_bytes) {
            fprintf(stderr,"wire length mismatch: %zu\n", wire_len); return 7;
        }
        for (int i=0;i<600;i++) {
            uint32_t object, header, newid;
            const unsigned char *p=wire+fill_bytes+i*message_bytes;
            memcpy(&object,p,4);memcpy(&header,p+4,4);memcpy(&newid,p+8,4);
            if (object!=1 || header!=((12u<<16)|1u) || newid!=(uint32_t)(i+2)) {
                fprintf(stderr,"wire request %d incorrect: %u %u %u\n",i,object,header,newid);
                return 8;
            }
        }
        puts("wire: 600 ordered complete get_registry requests");
    }
    wl_display_disconnect(d);close(fds[1]);
    if (getenv("EXPECT_WIRE")) {
        int ancillary[2],pipefd[2];
        if (socketpair(AF_UNIX,SOCK_STREAM,0,ancillary) || pipe(pipefd)) return 9;
        setsockopt(ancillary[0],SOL_SOCKET,SO_SNDBUF,&sz,sizeof sz);
        fill(ancillary[0]);
        struct wl_display *second=wl_display_connect_to_fd(ancillary[0]);
        if (!second) return 10;
        pthread_t receiver;
        if (pthread_create(&receiver,NULL,drain_fds,&ancillary[1])) return 11;
        char control[CMSG_SPACE(sizeof(int))]={0};
        struct msghdr fdmsg={.msg_iov=&io,.msg_iovlen=1,.msg_control=control,.msg_controllen=sizeof control};
        struct cmsghdr *cmsg=CMSG_FIRSTHDR(&fdmsg);
        cmsg->cmsg_level=SOL_SOCKET;cmsg->cmsg_type=SCM_RIGHTS;cmsg->cmsg_len=CMSG_LEN(sizeof(int));
        memcpy(CMSG_DATA(cmsg),&pipefd[0],sizeof(int));
        ssize_t sent=sendmsg(ancillary[0],&fdmsg,MSG_DONTWAIT|MSG_NOSIGNAL);
        pthread_join(receiver,NULL);
        close(pipefd[0]);close(pipefd[1]);
        wl_display_disconnect(second);close(ancillary[1]);
        printf("fd_transport=sent:%zd received:%d\n",sent,received_fd_count);
        if (sent!=1 || received_fd_count!=1) return 12;
    }
    return 0;
}
