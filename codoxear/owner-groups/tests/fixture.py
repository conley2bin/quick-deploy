"""Synthetic canonical registry, built by the real CLI in a temporary root only."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parents[1]
CLI = HERE.parents[1] / 'pi-agent/skills/responsibility-registry/scripts/registry.py'
spec = importlib.util.spec_from_file_location('projection', HERE / 'registry_projection.py')
projection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(projection)


def build(root):
    def cli(*args, payload=None):
        result = subprocess.run([sys.executable, str(CLI), '--root', str(root), *args],
                                input=json.dumps(payload, ensure_ascii=False) if payload else None,
                                text=True, capture_output=True, timeout=10)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return json.loads(result.stdout)

    cli('init')
    def owner(oid, name, role, parent='owner:ops', pi=None, source='current-session', task=None, project='示例评测项目'):
        identity = {'pi_session_id': pi, 'intercom_id': 'synthetic-contact-' + oid if source != 'unavailable' else None,
                    'source': source, 'unknown_reason': '合成：尚未核验 Pi 会话身份' if pi is None else ''}
        data = {'capacity': 'accountable-owner', 'appointment': '合成演示：明确任命',
                'goal_source': {'issuer': 'user', 'source': '合成演示任命'},
                'owner': {'id': oid, 'name': name, 'role': role, 'parent': parent, 'identity': identity}}
        if task:
            data['assignment'] = {'id': oid + '-scope', 'project': {'name': project, 'path': '/example/release' if project == '示例发布项目' else '/example/shared'},
                'repo': {'path': '/example/shared/.git'}, 'task': task,
                'objective': 'PRIVATE synthetic objective', 'acceptance': 'PRIVATE synthetic acceptance', 'depends_on': []}
        cli('register', '--actor', oid, payload=data)
    owner('ops', '运营总负责人', '跨项目协调', parent='user', pi='pi-synthetic-ops', task='总协调')
    owner('legacy', '前任 office', '原场景负责人', pi='pi-synthetic-legacy', task='office 场景')
    owner('office', 'Office 方向', 'office 场景评测负责人', pi='pi-synthetic-office')
    cli('handover', '--actor', 'legacy', '--assignment', 'legacy-scope', '--to-owner', 'office',
        '--new-assignment', 'office-scope', '--expected-revision', '3', '--reason', '合成：用户批准交接',
        '--pending-work', '合成：无运行子任务', '--reporting-change', '合成：仍向运营总负责人汇报')
    owner('kitchen', 'Kitchen 方向', 'kitchen 场景评测负责人', source='intercom-status', task='kitchen 场景')
    owner('web', '发布管理', 'web 发布负责人', pi='pi-synthetic-web', task='Web 发布', project='示例发布项目')
    owner('qa', '验收负责人', 'Office 验收', parent='owner:office', pi='pi-synthetic-qa', task='验收')
    owner('unknown', '待核验负责人', '接入方向', source='unavailable')
    owner('collision-a', '冲突甲', '冲突演示', pi='pi-synthetic-duplicate')
    owner('collision-b', '冲突乙', '冲突演示', pi='pi-synthetic-duplicate')
    owner('other', '另一顶层负责人', '独立项目', parent='user', pi='pi-synthetic-other', task='同库另一方向')
    owner('orphan', '待确认上级方向', '未登记上级演示', parent='owner:not-registered', pi='pi-synthetic-orphan', task='同库独立方向')
    return json.loads((root / 'registry.json').read_text())


def sessions():
    def row(broker, pi, **kwargs):
        return {'session_id': broker, 'thread_id': pi, 'agent_backend': 'pi',
                'log_path': '/example/synthetic.jsonl', 'log_exists': True, **kwargs}
    return {'sessions': [row('demo-office-opaque-123', 'pi-synthetic-office'),
        row('demo-legacy', 'pi-synthetic-legacy'), row('demo-web', 'pi-synthetic-web'),
        row('demo-qa', 'pi-synthetic-qa'), row('demo-conflict', 'pi-synthetic-duplicate'),
        row('demo-unrelated', 'pi-synthetic-unrelated', title='Office 方向', cwd='/example/shared'),
        row('demo-alias-only', '', title='Office 方向', cwd='/example/shared'),
        row('demo-wrong-backend', 'pi-synthetic-other', agent_backend='codex')]}


def demo():
    with tempfile.TemporaryDirectory(prefix='owner-groups-demo-') as temp:
        state = build(Path(temp))
        return {'owners': projection.project(state), 'sessions': sessions()}


if __name__ == '__main__':
    print(json.dumps(demo(), ensure_ascii=False))
