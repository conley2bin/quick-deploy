#!/usr/bin/env python3
"""manage.sh: foreground, auto-connecting VPS device manager.

Default run = read the configured VPS (`connect`, unless a local transaction is pending), serve the
signed management API on the fixed private loopback port and print the clickable file:// page address.
A repeat start finds the lock held, verifies the running manager by signed query and reuses its page without connecting.
The page works for exactly this process's lifetime; Ctrl+C stops everything. No service unit, no browser launch.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
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
REUSED = '已在运行，复用现有管理页面；原终端负责生命周期'
BOOTSTRAP = re.compile(r'<script type="application/json" id="manage-bootstrap">(.*?)</script>', re.S)
STAY = '保持此窗口运行；Ctrl+C 关闭；页面刷新有效，无固定时限'


def parser():
    p = argparse.ArgumentParser(
        prog='manage.sh', description='Foreground VPS device manager: connects to the VPS from config, then serves the page until Ctrl+C.',
        epilog='commands: connect (default) | devices {list,add,rename,reset,remove} | export | dashboard | status | recover | backup. '
               'Setup/deployment is setup.sh.')
    p.add_argument('words', nargs='*', metavar='COMMAND', help=argparse.SUPPRESS)
    p.add_argument('--state-dir', type=Path, default=vps.DEFAULT_STATE)
    p.add_argument('--ssh-proxy', type=vps.proxy_argument, help='invocation-only independent loopback SOCKS5/HTTP SSH route; default direct')
    p.add_argument('--independent-carrier', action='store_true', help='with --ssh-proxy: acknowledge this route is independent of EVERY managed VPS entry (enables changes in the page)')
    p.add_argument('--ssh', help='override VPS_SSH from config: user@host or SSH alias')
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


def desired_target(args):
    """The VPS this invocation means: --ssh, else VPS_SSH from config. Local only: nothing is contacted."""
    return target_config.ssh_target(args.ssh) if args.ssh else target_config.default_ssh(vps.MODULE)


def refresh(args, state, err, target=None):
    """connect to the configured VPS; True when state was refreshed, False when skipped for a pending transaction."""
    target = target or desired_target(args)
    if pending(state):
        # Skipping connect must not hide a different intent: the original binding is the only target recovery may serve.
        path = state / 'state.json'
        current = vps.load(path).get('current') if path.exists() else None
        if isinstance(current, dict) and current.get('target') != target:
            raise host.Error('本地有未完成的事务，原绑定目标为 %s，与本次要求的 %s 不同；未连接。先用页面“恢复”或 manage.sh recover 处理，再换目标' % (
                current.get('target'), target))
        print('本地有未完成的事务：未连接 VPS。页面将使用现有绑定，需要显式恢复（页面“恢复”或 manage.sh recover）。', file=err)
        return False
    print('正在连接 %s 并读取当前设备…' % target, file=err)
    code = vps.main(connect_argv(args, state, target))
    if code:
        raise host.Error('连接 VPS 失败（退出码 %d）；未启动管理页面' % code)
    return True


def entry_page(state):
    """The private entry file, only if its embedded origin and bootstrap key belong to THIS state's manager."""
    config, master = manage_auth.load_state(state)
    expected = {'version': 1, 'origin': 'http://127.0.0.1:%d' % config['port'],
                'bootstrap_key': manage_auth.bootstrap_key(master).hex()}
    path = vps.MODULE / 'index.html'
    try:
        raw = vps.read_private(path)
        found = BOOTSTRAP.search(raw.decode('utf-8'))
        ok = raw.startswith(b'<!doctype html>' + manage.MARKER) and found and json.loads(found.group(1)) == expected
    except (host.Error, ValueError):
        ok = False
    if not ok:
        raise host.Error('管理器在运行，但入口页面 %s 缺失、损坏或不属于它，不输出可能失效的链接；请回到原窗口 Ctrl+C 后重新运行 manage.sh' % path)
    return path


def reuse(args, state, target, out):
    """The session lock is held: serve nothing, connect to nothing; show the running manager's page if it is provably this VPS."""
    import manage_service
    try:
        snap = manage_service.query_state(state)
    except manage_auth.AuthError as exc:
        raise host.Error('manager_active：会话锁被另一进程持有，但本机管理器私钥/配置不可用，无法验证它：%s。未终止进程、未接管锁、未删除文件' % exc) from exc
    except manage_service.ServiceError as exc:
        cause = exc.__cause__
        if isinstance(cause, TimeoutError):
            why = '3 秒内无响应：它可能正在连接/启动（原终端尚未 ready），请稍后重试；长期如此再检查该进程'
        elif isinstance(cause, ConnectionRefusedError):
            why = '锁仍被持有但管理端口未监听（尚未绑定或已异常）'
        else:
            why = '响应没有通过签名状态验证（不是本管理器、密钥不匹配或返回异常：%s）' % exc
        raise host.Error('manager_active：会话锁被另一进程持有，%s。未终止进程、未接管锁、未删除文件；未发送任何修改请求，请到原窗口确认' % why) from exc
    mode = snap.get('service_mode') if isinstance(snap, dict) else None
    if mode == 'persistent':
        # An old systemd-style service owns the lock: there is no foreground terminal to hand lifecycle to, and we never stop it.
        try:
            print('旧版常驻服务页面（非前台）: ' + hyperlink(entry_page(state).as_uri(), out.isatty()), file=out)
        except host.Error:
            pass
        raise host.Error('legacy_service_conflict：会话锁由旧版常驻管理服务持有（通常是 systemd 用户单元 %s），不是前台管理器，没有“原终端”。'
                         '未停止、未接管、未连接；确认其空闲后由你自行停止该服务，再运行 manage.sh'
                         % manage_service.UNIT_NAME)
    if mode != 'foreground':
        raise host.Error('manager_active：已有进程通过签名验证，但无法识别其运行模式；未复用、未改动')
    bound = snap.get('target')
    legacy = False
    if not (isinstance(bound, dict) and isinstance(bound.get('target'), str) and isinstance(bound.get('endpoint'), str)
            and isinstance(bound.get('ssh_user'), str) and type(bound.get('ssh_port')) is int):
        # Older foreground manager (no startup binding). The binding must then be PROVEN from the signed live endpoint plus this
        # state-root's committed record. The requested target / config is the question here, never evidence about the running one.
        legacy = True
        model = snap.get('model')
        live = model['snapshot'].get('endpoint') if isinstance(model, dict) and isinstance(model.get('snapshot'), dict) else None
        try:
            record = vps.load(state / 'state.json').get('current')
        except (host.Error, OSError):
            record = None
        if not (isinstance(live, str) and isinstance(record, dict) and record.get('endpoint') == live
                and isinstance(record.get('target'), str) and isinstance(record.get('user'), str) and type(record.get('port')) is int):
            raise host.Error('manager_active：这是旧版前台管理器（不声明启动目标），当前%s，无法证明它连接的是 %s；未复用、未改动。'
                             '等任务结束后在原窗口 Ctrl+C，用当前版本重新运行 manage.sh 即可获得可复用的绑定'
                             % ('正在执行任务（状态不含端点）' if model is None else '签名端点与本地记录不一致', target))
        bound = {'target': record['target'], 'endpoint': live, 'ssh_user': record['user'], 'ssh_port': record['port']}
    busy = snap.get('busy') is not False
    where = '现有管理器的目标是 %s（%s@%s:%d）' % (bound['target'], bound['ssh_user'], bound['endpoint'], bound['ssh_port'])
    later = ('它正在执行任务，等任务结束且空闲时再在原窗口 Ctrl+C 后用新参数重启' if busy else
             '原窗口空闲，需要换成本次参数时请在该窗口 Ctrl+C 后重新运行；本次不会替你关闭它')
    # Identity is the locally resolved (endpoint, user, port) triple (ssh -G: no network), never the raw string alone, so an
    # alias and user@host that resolve identically are the same VPS and ~/.ssh/config drift in user/port is not ignored.
    differs = None
    try:
        record = vps.target_record(argparse.Namespace(ssh=target, port=args.port, identity=None), None)
        triple = (record['endpoint'], record['user'], record['port'])
    except host.Error as exc:
        if bound['target'] != target:
            raise host.Error('manager_active：%s；目标标识 %s 与之不同且无法在本地解析，未确认同机，未复用。新参数未应用；%s' % (where, target, later)) from exc
        raise host.Error('manager_active：无法在本地解析 %s，不能确认与现有管理器同机：%s' % (target, exc)) from exc
    if triple != (bound['endpoint'], bound['ssh_user'], bound['ssh_port']):
        if triple[0] != bound['endpoint']:
            differs = '本次的 %s 解析到 %s，不是同一台 VPS' % (target, triple[0])
        else:
            differs = '本次的 %s 端点相同，但解析出的 SSH 用户/端口为 %s@…:%d，与其不同（可能是 ~/.ssh/config 变更）' % (target, triple[1], triple[2])
    if differs is None:
        given = [flag for flag, value in (('--ssh-proxy', args.ssh_proxy), ('--independent-carrier', args.independent_carrier or None),
                                          ('--identity', args.identity), ('--mihomo', args.mihomo)) if value is not None]
        if given:
            differs = '本次的 %s 无法与其启动时的路由/身份/Mihomo 比对，不能声称已生效' % ' '.join(given)
    if differs:
        raise host.Error('manager_active：%s；%s。新参数未应用；%s' % (where, differs, later))
    launcher = entry_page(state)
    print('管理页面: ' + hyperlink(launcher.as_uri(), out.isatty()), file=out)
    print(REUSED, file=out)
    mode = (snap.get('carrier') or {}).get('mode')
    print('沿用原窗口的 SSH 路由（%s）；本次未连接、未刷新、未修改任何内容' % {'direct': '直连', 'proxy': '代理'}.get(mode, '只读/未配置'), file=out)
    if legacy:
        print('注意：这是旧版管理器，目标由签名端点与本地记录相符推断（非启动时绑定）；升级后重启可获得更强确认', file=out)
    if busy:
        print('该管理器正在执行任务；页面显示其进度，本次不会刷新或改动', file=out)
    elif (snap.get('pending') or {}).get('present'):
        print('该管理器有未完成的本地事务，请在页面中恢复', file=out)
    out.flush()
    return 0


def serve(args, state, out, err, target):
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
    try:
        return manage.run_foreground(vps, state, carrier, ready, prepare=lambda: refresh(args, state, err, target),
                                     mihomo=mihomo and str(mihomo))
    except manage.Refusal as exc:
        if exc.code != 'manager_active':
            raise
        return reuse(args, state, target, out)


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
            return serve(args, state, out, err, desired_target(args))  # chosen before the lock check; nothing contacted
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
