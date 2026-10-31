#!/usr/bin/env python3
"""manage.sh: foreground, auto-connecting VPS device manager.

Default run = read the configured VPS (`connect`, unless a local transaction is pending), serve the
signed management API on the fixed private loopback port and print the clickable file:// page address.
The page works for exactly this process's lifetime; Ctrl+C stops everything. No service unit, no browser launch.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import host
import manage
import manage_auth
import ssh_proxy
import target_config
import vps

DELEGATED = {'devices': True, 'export': False, 'dashboard': False, 'status': True, 'backup': True, 'recover': True}  # name -> accepts --ssh-proxy
NO_CONNECT = ('recover',)
RETIRED = ('install', 'start', 'stop', 'restart', 'uninstall', 'rotate', 'carrier', 'service', 'serve', 'open')
STAY = '保持此窗口运行；Ctrl+C 关闭；页面刷新有效，无固定时限'


def parser():
    p = argparse.ArgumentParser(
        prog='manage.sh', description='Foreground VPS device manager: connects to the VPS from config.txt, then serves the page until Ctrl+C.',
        epilog='commands: connect (default) | devices {list,add,rename,reset,remove} | export | dashboard | status | recover | backup. '
               'Setup/deployment is setup.sh.')
    p.add_argument('words', nargs='*', metavar='COMMAND', help=argparse.SUPPRESS)
    p.add_argument('--state-dir', type=Path, default=vps.DEFAULT_STATE)
    p.add_argument('--ssh-proxy', type=vps.proxy_argument, help='invocation-only independent loopback SOCKS5/HTTP SSH route; default direct')
    p.add_argument('--independent-carrier', action='store_true', help='with --ssh-proxy: acknowledge this route is independent of EVERY managed VPS entry (enables changes in the page)')
    p.add_argument('--ssh', help='override VPS_SSH from config.txt: user@host or SSH alias')
    p.add_argument('--identity', type=Path, help='SSH identity for the connect step')
    p.add_argument('--port', type=int, help='SSH port ONLY, for the connect step')
    p.add_argument('--mihomo', help='native core executable for profile validation')
    p.add_argument('--no-open', action='store_true', help='accepted and ignored: no browser is ever opened')
    return p


def hyperlink(uri, tty):
    return '\x1b]8;;%s\x1b\\%s\x1b]8;;\x1b\\' % (uri, uri) if tty else uri


def runtime_carrier(args):
    if args.ssh_proxy is None:
        # Direct SSH is not tunnelled through any managed entry; remote reachability is the user's responsibility.
        return {'ssh_proxy': None, 'independent': True}
    return {'ssh_proxy': args.ssh_proxy.url, 'independent': args.independent_carrier}


def connect_argv(args, state, target):
    argv = ['--state-dir', str(state), 'connect', '--ssh', target]
    for flag, value in (('--identity', args.identity), ('--port', args.port),
                        ('--ssh-proxy', args.ssh_proxy and args.ssh_proxy.url), ('--mihomo', args.mihomo)):
        if value is not None:
            argv += [flag, str(value)]
    return argv


def pending(state):
    path = state / 'pending.json'
    return path.exists() or path.is_symlink()


def refresh(args, state, err):
    """connect to the configured VPS; True when state was refreshed, False when skipped for a pending transaction."""
    if pending(state):
        print('本地有未完成的事务：未连接 VPS。页面将使用现有绑定，需要显式恢复（页面“恢复”或 manage.sh recover）。', file=err)
        return False
    target = target_config.ssh_target(args.ssh) if args.ssh else target_config.default_ssh(vps.MODULE)
    print('正在连接 %s 并读取当前设备…' % target, file=err)
    code = vps.main(connect_argv(args, state, target))
    if code:
        raise host.Error('连接 VPS 失败（退出码 %d）；未启动管理页面' % code)
    return True


def serve(args, state, out, err):
    # connect runs inside run_foreground AFTER the session lock is held, so a second manager refuses first.
    carrier = runtime_carrier(args)

    def ready(launcher, origin):
        import manage_service  # signed hello + state: the page is only announced once the API answers
        manage_service.query_state(state)
        tty = out.isatty()
        print('管理页面: ' + hyperlink(Path(launcher).as_uri(), tty), file=out)
        print(STAY, file=out)
        if carrier['ssh_proxy'] is not None and not carrier['independent']:
            print('提示：--ssh-proxy 未加 --independent-carrier，页面为只读；确认该路径独立于所有受管 VPS 条目后再加。', file=out)
        out.flush()

    mihomo = vps.resolve_mihomo(args.mihomo) if args.mihomo else None
    return manage.run_foreground(vps, state, carrier, ready, prepare=lambda: refresh(args, state, err),
                                 mihomo=mihomo and str(mihomo))


def main(argv=None, out=None, err=None):
    out, err = out or sys.stdout, err or sys.stderr
    args, extra = parser().parse_known_intermixed_args(argv)
    words = args.words
    command = words[0] if words else 'connect'
    if command in RETIRED:
        print('manage.sh 现在是前台运行，不再安装/启动/管理系统服务（%s 已移除）。直接运行：bash vps/manage.sh' % command, file=err)
        return 2
    if command != 'connect' and command not in DELEGATED:
        print('未知命令 %s；可用: connect devices export dashboard status recover backup' % command, file=err)
        return 2
    if command == 'connect' and (len(words) > 1 or extra):
        print('connect 不接受额外参数', file=err)
        return 2
    words = words + extra
    os.umask(0o077)
    state = args.state_dir.expanduser().absolute()
    try:
        if command not in NO_CONNECT and command != 'connect':
            refresh(args, state, err)
        if command == 'connect':
            return serve(args, state, out, err)
        argv = ['--state-dir', str(state), command, *words[1:]]
        if DELEGATED[command] and args.ssh_proxy is not None:
            argv += ['--ssh-proxy', args.ssh_proxy.url]
        if command in ('devices', 'recover', 'backup') and args.mihomo and not (command == 'devices' and words[1:2] == ['list']):
            argv += ['--mihomo', args.mihomo]
        return vps.main(argv)
    except KeyboardInterrupt:
        print('已取消连接', file=err)
        return 130
    except (host.Error, manage_auth.AuthError, manage.Refusal, ssh_proxy.Error, OSError) as exc:
        print('Error: %s' % exc, file=err)
        return 1


if __name__ == '__main__':
    sys.exit(main())
