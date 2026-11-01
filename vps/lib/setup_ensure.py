"""`setup.sh` with no command: converge the configured VPS to the managed node, touching only what is missing.

Decision order: configured target vs committed binding -> original local pending recovery -> one read-only
`ensure-probe` -> exactly one of: skip, refresh local snapshot, existing deploy / deploy_existing, refuse.
A healthy node is never prepared, backed up (backup stops the front), restarted or re-keyed here.

The vps module is passed in (setup.sh runs it as `__main__`; a second import would lose the per-invocation SSH carrier).
Exit codes: 0 converged, 1 refused/unhealthy/failed, 75 waiting for another transaction or the host recovery timer.
"""
import argparse
import contextlib
import fcntl
import os
from pathlib import Path
import sys
import tarfile

import host

WAITING = 75
KINDS = ('absent', 'owned_empty', 'pending', 'foreign', 'owned')
PROBE_KEYS = {'kind', 'owner', 'machine_id', 'node_id', 'config', 'transports', 'tcp_cdn', 'versions', 'lock_match',
              'adapter_sha256', 'health', 'front_drift', 'roster_digest'}
SINGLE, DUAL, TRIPLE = (['tcp-reality'], ['tcp-reality', 'hysteria2'], ['tcp-reality', 'tcp-xhttp-cdn', 'hysteria2'])
CDN_NOTE = 'CDN 入口可选，未自动添加；需要时执行: bash vps/setup.sh cdn --interface <本机物理网卡> --dns-server <权威NS的IPv4>'


def say(tag, text):
    print(f'[{tag}] {text}', flush=True)


def roster_digest(accounts):
    """The one shared algorithm lives in connect_read (the server computes it with the same helper)."""
    import connect_read
    return connect_read.roster_digest(accounts)


@contextlib.contextmanager
def foreground_session(vps, state):
    """Hold manage-jobs/.session.lock for the whole ensure: an active foreground manager excludes us and we exclude it."""
    jobs = vps.private_dir(Path(state) / 'manage-jobs')
    fd = os.open(jobs / '.session.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise host.Error('前台 manage.sh 正在运行并占用此状态目录；先关闭它再运行 setup.sh') from None
        yield
    finally:
        os.close(fd)


def load_document(vps, state):
    path = state / 'state.json'
    if not path.exists():
        if path.is_symlink():
            raise host.Error('Refusing symlink state.json')
        return None
    document = vps.load(path)
    if document.get('format') != host.FORMAT:
        raise host.Error('Unknown local state; no automatic import or regeneration')
    return document


def load_pending(vps, state):
    path = state / 'pending.json'
    return vps.load(path) if path.exists() or path.is_symlink() else None


def checked_probe(probe):
    if not isinstance(probe, dict) or not PROBE_KEYS <= set(probe) or probe['kind'] not in KINDS:
        raise host.Error('服务器返回了无法识别的 ensure-probe 响应；未做任何改动')
    health = probe['health']
    if not isinstance(health, dict) or not {'ok', 'failed_check', 'error'} <= set(health) or type(health['ok']) is not bool:
        raise host.Error('服务器返回了无法识别的 ensure-probe 健康字段；未做任何改动')
    if probe['kind'] == 'owned':
        for key in ('owner', 'machine_id', 'node_id'):
            if not isinstance(probe[key], str) or not probe[key]:
                raise host.Error('服务器 ensure-probe 缺少身份字段 ' + key + '；未做任何改动')
        host.validate_config(probe['config'])
        if not isinstance(probe['transports'], list):
            raise host.Error('服务器 ensure-probe 拓扑字段无效；未做任何改动')
    return probe


def why(probe):
    health = probe['health']
    return ': '.join(part for part in (health.get('failed_check'), health.get('error')) if part) or '无详细原因'


def waiting(message):
    print('vps: ' + message, file=sys.stderr)
    return WAITING


def deploy_args(args):
    return argparse.Namespace(**dict(vars(args), command='deploy'))


def probe_request(vps, document, record):
    request = {'endpoint': record['endpoint'], 'lock': vps.release_lock(),
               'adapter_sha256': host.digest(vps.MODULE / 'lib/host.py')}
    if document:
        request['owner'] = document['owner']
    if record.get('machine_id'):
        request['machine_id'] = record['machine_id']
    return request


def check_local_binding(vps, args, document, pending):
    """Local-only comparison, before any remote I/O or recovery: configured target vs what this state committed."""
    if pending and not document:
        raise host.Error('本地有未完成事务但没有 state.json；保留原样，请改用另一个 --state-dir 或先人工检查')
    if document:
        vps.configuration(args, document['config'])  # a different domain / REALITY target is refused, never migrated
    current = document['current'] if document else None
    record = vps.target_record(args, current)
    bound = [r for r in (current, pending and pending['record']) if r]
    if any(r['endpoint'] != record['endpoint'] for r in bound):
        raise host.Error(vps.DIFFERENT_CONFIGURED_VPS)
    machines = {r['machine_id'] for r in bound if r.get('machine_id')}
    if len(machines) > 1:
        raise host.Error('本地已提交节点与待恢复事务的机器标识不一致；保留原样，不自动恢复')
    return record


def matches_config(args, probe):
    config = probe['config']
    if config['domain'] != args.domain:
        raise host.Error(f"服务器节点的订阅域名 {config['domain']} 与 config 的 DOMAIN 派生值 {args.domain} 不同；不会自动迁移")
    if args.reality_target is not None and config['reality_target'] != args.reality_target:
        raise host.Error(f"服务器节点的 REALITY 目标 {config['reality_target']} 与 config 的 REALITY_TARGET "
                         f'{args.reality_target} 不同；不会自动迁移（删除该行即以服务器为准）')


def cache_is_current(vps, state, document, probe):
    """Checked local archive + native generation equal the live roster/config/node/topology. Never contacts the server."""
    current = document and document.get('current')
    if not current or not document.get('backup'):
        return False
    try:
        backup = Path(document['backup'])
        manifest = vps.checked_backup(document, current, backup)
        if (not manifest.get('published') or manifest['node_id'] != probe['node_id'] or manifest['config'] != probe['config']
                or manifest['owner'] != probe['owner'] or current.get('machine_id') != probe['machine_id']
                or host.transports(manifest) != probe['transports']
                or vps.local_roster_digest(manifest) != probe['roster_digest']):
            return False
        if 'tcp_cdn' in manifest and (probe['tcp_cdn'] or {}).get('domain') != manifest['tcp_cdn']['domain']:
            return False
        digest = host.digest(backup)
        if document.get('native_generation') != digest:
            return False
        vps.read_generation(state, manifest, digest)
    except (host.Error, OSError, ValueError, KeyError, TypeError, tarfile.TarError):
        return False
    return True


def install(vps, state, args, document, record):
    """Absent, or resumable owned-empty: the existing first-install transaction; the REALITY target must come from config."""
    if args.reality_target is None:
        raise host.Error('全新安装需要在 vps/config 增加一行 REALITY_TARGET=<已验证的 TLS1.3+h2 外部域名，如 www.python.org>；'
                         '不会把 DOMAIN 当作伪装目标，未做任何改动')
    vps.resolve_mihomo(args.mihomo)  # real core before the first remote mutation
    if document is None:
        document = vps.state_document(state, args, create=True)
    say('部署', f"{record['endpoint']} 尚无受管节点，执行完整事务（preflight → 安装 → 原生校验 → 异机备份 → 发布）")
    vps.deploy(state, document, deploy_args(args))
    say('提示', CDN_NOTE)
    return 0


def lost_binding(args, document, record):
    backup = document.get('backup') or '<备份文件>'
    raise host.Error(f"{record['endpoint']} 上现在没有受管节点，但本地曾提交过同一地址的部署（重装或丢失）。"
                     '不自动重建（可能复活已吊销的凭据）。若要从已保存的备份还原: '
                     f'bash vps/setup.sh --state-dir <新目录> restore --backup {backup} --ssh {args.ssh}')


def attach(vps, state, args, binary):
    import connect  # lazy: operates on the passed vps module, like manage's auto-connect
    connect.run(vps, state, argparse.Namespace(**dict(vars(args), mihomo=binary)))


def optional_mihomo(vps, args):
    """Path, or None only when the default resolver reports that no native Mihomo is installed.
    An explicit --mihomo that does not resolve stays an error."""
    if args.mihomo is not None:
        return vps.resolve_mihomo(args.mihomo)
    try:
        return vps.resolve_mihomo(None)
    except host.Error:
        return None


def repair_without_state(vps, state, args, probe, record):
    """Front drift on a computer with no local state. connect-pull's archive check rejects exact Caddyfile drift, so
    attach cannot work here; the probe's identity facts drive the existing deploy_existing, and only its verified
    post-repair snapshot is published as local state (remember_commit)."""
    vps.resolve_mihomo(args.mihomo)  # real core before the first remote mutation
    document = {'format': host.FORMAT, 'owner': probe['owner'], 'config': probe['config'],
                'current': dict(record, machine_id=probe['machine_id'], node_id=probe['node_id']),
                'previous': [], 'backup': None}
    record = vps.target_record(args, document['current'])  # carries machine_id, as after a normal attach
    try:
        vps.deploy_existing(state, document, record, deploy_args(args))
    except BaseException:
        discard_untracked_backup_pending(vps, state, record)
        raise


def discard_untracked_backup_pending(vps, state, record):
    """backup_remote writes a backup-only pending.json before state.json exists. Without a state document nothing can
    recover it, and it carries no transaction intent: drop exactly that record. The server's backup-stop stays owned
    by the host recovery timer; the next ensure probe reports it pending (75)."""
    path = state / 'pending.json'
    if (state / 'state.json').exists() or not path.exists():
        return
    pending = vps.load(path)
    if pending.get('mode') == 'backup' and 'request' not in pending and pending.get('record') == record:
        path.unlink()


def owned(vps, state, args, document, probe, record):
    matches_config(args, probe)
    if document:
        if not document['current']:
            raise host.Error('服务器已有已发布节点，但本地状态没有已提交节点；保留原样，请改用另一个 --state-dir')
        import connect
        connect.check_binding(vps, document, record, dict(probe, endpoint=record['endpoint']))
    say('检查', f"所有权/配置一致（节点 {probe['node_id'][:8]}，拓扑 {'+'.join(probe['transports'])}）")
    if probe['lock_match'] is not True:
        raise host.Error(f"服务器固定版本与本地 release-lock 不一致 {probe['versions']}；升级需要显式实现，本次不改动")
    if not host.accepted_adapter_digest(probe['adapter_sha256'], host.digest(vps.MODULE / 'lib/host.py')):
        raise host.Error('服务器已装 adapter 摘要不被接受；不改动')
    if probe['transports'] not in (SINGLE, DUAL, TRIPLE):
        raise host.Error('无法识别的已拥有传输拓扑；不隐式迁移')
    health = probe['health']
    drift = probe['front_drift'] is True and health['failed_check'] in (None, 'front')
    if not health['ok'] and not drift:
        raise host.Error(f'服务器节点运行时不健康，未做任何改动（没有重启或修复）：{why(probe)}')
    legacy = probe['transports'] == SINGLE
    if drift or legacy:
        if not document and legacy:
            binary = vps.resolve_mihomo(args.mihomo)
            say('刷新', '本机尚无状态：先只读附着服务器的当前快照（不停服务）')
            attach(vps, state, args, binary)
            document = load_document(vps, state)
            record = vps.target_record(args, document['current'])
        say('修复', ('旧单传输节点升级为 REALITY+Hysteria2' if legacy else '订阅前端配置漂移') + '：使用既有事务路径')
        if document:
            vps.deploy_existing(state, document, record, deploy_args(args))
        else:
            repair_without_state(vps, state, args, probe, record)
        return 0
    say('检查', '运行时健康：原生账号/投影、TLS、监听进程、前端均通过')
    if cache_is_current(vps, state, document, probe):
        say('跳过', '服务器与本地已校验快照一致；未连接备份、未 prepare、未重启、未改动任何密钥')
        return 0
    binary = optional_mihomo(vps, args)
    if binary is None:
        say('跳过', '服务器健康，无需改动；本地未附着（未找到原生 Mihomo）。安装后运行 bash vps/manage.sh 即可附着')
        return 0
    say('刷新', '本地快照缺失或已过期：只读拉取并校验（服务器不停服务、不改动）')
    attach(vps, state, args, binary)
    say('跳过', '服务器健康，无需改动；本地快照已刷新')
    return 0


def decide(vps, state, args, document, probe, record):
    kind = probe['kind']
    if kind == 'pending':
        return waiting(f'服务器上有进行中/待收尾的事务（{why(probe)}）；不执行回滚，稍后重跑 setup.sh')
    if kind == 'foreign':
        raise host.Error(f'服务器上存在不属于本部署的内容，拒绝接管：{why(probe)}')
    if kind in ('absent', 'owned_empty') and document and document['current']:
        lost_binding(args, document, record)
    if kind == 'owned_empty' and (not document or document['owner'] != probe['owner']
                                  or record.get('machine_id') not in (None, probe['machine_id'])):
        raise host.Error('服务器上有他处创建的空部署所有权标记，本机没有匹配的本地状态；拒绝在其上再创建所有者')
    if kind in ('absent', 'owned_empty'):
        return install(vps, state, args, document, record)
    return owned(vps, state, args, document, probe, record)


def ensure(vps, state, args):
    document, pending = load_document(vps, state), load_pending(vps, state)
    say('检查', f'config: VPS_SSH={args.ssh}, 订阅域名 {args.domain}')
    record = check_local_binding(vps, args, document, pending)
    recovered = False
    if pending:
        say('恢复', '发现本地未完成事务：按原记录对账（不重放设备变更）')
        vps.recover_local(state, document, args.mihomo)
        if (state / 'pending.json').exists():
            return waiting('原事务仍在排队/等待服务器收尾；本地记录已保留，稍后重跑 setup.sh')
        document, recovered = load_document(vps, state), True
        record = vps.target_record(args, document['current'] if document else None)
    probe = checked_probe(vps.remote(record, 'ensure-probe', probe_request(vps, document, record)))
    if recovered and probe['kind'] == 'pending':
        return waiting(f'恢复后服务器仍有事务进行中（{why(probe)}）；稍后重跑 setup.sh')
    return decide(vps, state, args, document, probe, record)


def run(vps, state, args):
    with foreground_session(vps, state):
        return ensure(vps, state, args)
