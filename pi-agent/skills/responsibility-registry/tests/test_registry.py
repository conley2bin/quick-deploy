"""Isolated behavioral tests: no real identities, contacts or home-state writes."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/registry.py'
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location('registry', SCRIPT)
registry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registry)


def appointment(oid='alpha', aid='release', parent='user', path='/example/alpha'):
    return {
        'capacity': 'accountable-owner', 'appointment': '演练：任命你为发布负责人',
        'goal_source': {'issuer': 'user', 'source': '合成测试：用户给出目标和任命'},
        'owner': {'id': oid, 'name': '示例' + oid, 'role': '发布负责人', 'parent': parent,
                  'identity': {'pi_session_id': None, 'intercom_id': None, 'source': 'unavailable',
                               'unknown_reason': '合成测试，无真实会话'}},
        'assignment': {'id': aid, 'project': {'name': '示例项目', 'path': path},
                       'repo': {'path': path + '/.git'},
                       'goal_source': {'issuer': 'user', 'source': '合成测试：用户给出目标和任命'}, 'task': '发布',
                       'objective': '组织目标与验收', 'acceptance': '用户接受所有交付',
                       'depends_on': [{'kind': 'task', 'project': 'example:upstream', 'task': '构建'}]}}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.document = self.root / 'RESPONSIBILITIES.md'
        self.cli('init')

    def cli(self, *args, payload=None, ok=True):
        if args[0] == 'rebuild' and '--output' not in args:
            args = (*args, '--output', str(self.root / 'report.md'))
        if args[0] in ['register', 'update-owner', 'update', 'close', 'handover', 'coord-propose', 'coord-ack', 'coord-release']:
            args = (*args, '--expected-document', self.cli('query')['document_token'])
        result = subprocess.run([sys.executable, str(SCRIPT), '--root', str(self.root), '--document', str(self.document), *args],
                                input=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0 if ok else 2, result.stderr + result.stdout)
        if ok and args[0] not in ['query', 'rebuild', 'init']:
            self.cli('rebuild')  # Explicit optional report, never a source of current rows.
        return json.loads(result.stdout) if ok else result.stderr

    def register(self, payload=None, ok=True, actor=None):
        p = payload or appointment()
        return self.cli('register', '--actor', actor or p['owner']['id'], payload=p, ok=ok)

    def state(self):
        return registry.parse_document(self.document.read_text())[0]

    def test_appointment_hierarchy_lookup_update_handover_close(self):
        p = appointment()
        self.register(p)
        first = self.document.read_bytes()
        self.assertTrue(self.register(p)['unchanged'])
        self.assertEqual(first, self.document.read_bytes())
        q = appointment('beta', parent='owner:alpha')
        q.pop('assignment')  # Successor has a genuine appointment, no fake bootstrap task.
        self.register(q)
        result = self.cli('query', '--owner', 'alpha')
        self.assertEqual(result['hierarchy'][0]['children'], ['beta'])
        self.assertEqual(result['relations'][0]['target']['kind'], 'task')
        self.assertEqual(self.cli('query', '--project', 'path:/example/alpha', '--task', '发布')['revision'], 2)
        a = p['assignment']
        a['acceptance'] = '子代理提供证据，负责人审阅，用户接受'
        self.cli('update', '--actor', 'alpha', '--assignment', 'release', '--expected-revision', '2',
                 '--reason', '细化范围内验收', payload=a)
        self.assertTrue(self.cli('update', '--actor', 'alpha', '--assignment', 'release', '--expected-revision', '3',
                                '--reason', '同样验收', payload=a)['unchanged'])
        self.cli('handover', '--actor', 'alpha', '--assignment', 'release', '--expected-revision', '3',
                 '--reason', '演练：用户已批准交接', '--to-owner', 'beta', '--new-assignment', 'release-v2',
                 '--pending-work', '合成测试：已核验无运行 child', '--reporting-change', '用户明确乙接任后仍向甲报告')
        active = self.cli('query', '--owner', 'beta', '--active')['assignments']
        self.assertEqual(list(active), ['release-v2'])
        self.assertEqual(self.state()['assignments']['release']['status'], 'handed-over')
        self.assertEqual(self.state()['assignments']['release']['owner'], 'alpha')
        self.cli('close', '--actor', 'beta', '--assignment', 'release-v2', '--expected-revision', '4',
                 '--reason', '目标已获用户接受')
        o = q['owner']
        o['parent'] = 'user'
        self.cli('update-owner', '--actor', 'beta', '--expected-revision', '5', '--reason', '用户明确改为直报', payload=o)
        state = self.state()
        self.assertEqual(state['revision'], 6)
        self.assertEqual(state['history'][3]['before']['assignments']['release']['status'], 'active')
        self.assertEqual(state['history'][3]['after']['assignments']['release']['successor'], 'release-v2')
        self.assertEqual(state['history'][5]['before']['owners']['beta']['parent'], 'owner:alpha')
        self.assertEqual(self.cli('query', '--active')['assignments'], {})
        self.register(p, ok=False)  # Ended responsibility IDs are never recycled.

    def test_multiple_scopes_and_distinct_same_basename_projects(self):
        p = appointment(path='/example/one/repo')
        self.register(p)
        q = appointment(aid='release-two', path='/example/two/repo')
        self.register(q)
        self.assertEqual(len(self.cli('query', '--owner', 'alpha')['assignments']), 2)
        self.assertEqual(len({a['project']['id'] for a in self.state()['assignments'].values()}), 2)

    def test_conflict_cannot_silently_overwrite_or_create_owner(self):
        self.register()
        before = self.document.read_bytes()
        error = self.register(appointment('beta', 'other'), ok=False)
        self.assertIn('lead conflict', error)
        self.assertIn('handover', error)
        self.assertEqual(before, self.document.read_bytes())
        p = appointment()
        p['owner']['name'] = '冒充替换'
        self.assertIn('update-owner', self.register(p, ok=False))
        self.assertEqual(before, self.document.read_bytes())

    def test_unresolved_parent_then_resolution_and_cycles(self):
        p = appointment(parent='owner:superior')
        self.register(p)
        unresolved = self.cli('query')['hierarchy'][0]
        self.assertFalse(unresolved['parent_resolved'])
        self.assertEqual(unresolved['parent'], 'owner:superior')  # Never promote unresolved parent to user.
        self.assertIn('未登记，待解析', (self.root / 'report.md').read_text())
        q = appointment('superior', parent='owner:alpha')
        q.pop('assignment')
        before = self.document.read_bytes()
        self.assertIn('cycle', self.register(q, ok=False))
        self.assertEqual(before, self.document.read_bytes())
        q['owner']['parent'] = 'user'
        self.register(q)
        self.assertTrue(self.cli('query', '--owner', 'alpha')['hierarchy'][0]['parent_resolved'])
        o = p['owner']
        o['parent'] = 'owner:alpha'
        self.assertIn('cycle', self.cli('update-owner', '--actor', 'alpha', '--expected-revision', '2',
                                      '--reason', '无效自环', payload=o, ok=False))

    def test_stale_or_unauthorized_changes_rejected(self):
        self.register()
        before = self.document.read_bytes()
        self.assertIn('stale', self.cli('close', '--actor', 'alpha', '--assignment', 'release',
                                       '--expected-revision', '0', '--reason', '过期', ok=False))
        self.assertIn('actor', self.cli('close', '--actor', 'other', '--assignment', 'release',
                                       '--expected-revision', '1', '--reason', '未经授权', ok=False))
        self.assertIn('register', self.cli('handover', '--actor', 'alpha', '--assignment', 'release',
                                          '--expected-revision', '1', '--reason', '不猜接任人',
                                          '--to-owner', 'unknown', '--new-assignment', 'next',
                                          '--pending-work', '合成测试：无运行 child', ok=False))
        self.assertEqual(before, self.document.read_bytes())

    def test_identity_unknown_honest_and_helpers_rejected(self):
        p = appointment()
        self.register(p)
        stored = self.cli('query')['owners']['alpha']['identity']
        self.assertIsNone(stored['pi_session_id'])
        self.assertIsNone(stored['intercom_id'])
        self.assertNotIn('live', self.state()['assignments']['release'])
        helper = appointment('helper', 'helper-task', path='/example/helper')
        helper['capacity'] = 'implementation-helper'
        self.assertIn('helpers', self.register(helper, ok=False))
        self.assertNotIn('helper', self.state()['owners'])
        for source, pi, intercom in [('unavailable', None, 'guessed'), ('intercom-status', 'not-proven', 'test-contact')]:
            helper['capacity'] = 'accountable-owner'
            helper['owner']['identity'].update(source=source, pi_session_id=pi, intercom_id=intercom)
            self.register(helper, ok=False)
        helper['owner']['identity'].update(source='intercom-status', pi_session_id=None, intercom_id='synthetic-contact')
        self.register(helper)
        self.assertIsNone(self.state()['owners']['helper']['identity']['pi_session_id'])
        # Current-session evidence may prove two different IDs; never conflate them.
        o = helper['owner']
        o['identity'].update(source='current-session', pi_session_id='synthetic-pi-id', intercom_id='synthetic-alias', unknown_reason='')
        self.cli('update-owner', '--actor', 'helper', '--expected-revision', '2', '--reason', '合成身份核验', payload=o)
        self.assertNotEqual(self.state()['owners']['helper']['identity']['pi_session_id'],
                            self.state()['owners']['helper']['identity']['intercom_id'])

    def test_chinese_quotes_pipes_newlines_safe_readable_dashboard(self):
        p = appointment()
        p['owner']['name'] = '甲“负责人” |\n第二行 <script> & `引号`'
        p['assignment']['objective'] = '范围 | 中文\n下一目标'
        self.register(p)
        document = (self.root / 'report.md').read_text()
        self.assertIn('甲“负责人” &#124;<br>第二行 &lt;script&gt; &amp; &#96;引号&#96;', document)
        self.assertIn('范围 &#124; 中文<br>下一目标', document)
        self.assertNotIn('<script>', document)
        self.assertEqual(self.cli('query')['owners']['alpha']['name'], p['owner']['name'])
        # Every row has the same delimiter count as its own table header.
        expected = None
        for line in document.splitlines():
            if line.startswith('| '):
                if expected is None:
                    expected = line.count('|')
                self.assertEqual(line.count('|'), expected)
            else:
                expected = None

    def test_corrupt_canonical_preserved_no_silent_reset(self):
        self.register()
        good = self.document.read_text()
        malformed = ['{bad', good.replace('Revision: 1', 'Revision: 9'), good.replace('Format: responsibility-registry/v2', 'Format: unknown')]
        old_dashboard = (self.root / 'report.md').read_bytes()
        for broken in malformed:
            self.document.write_text(broken)
            for action in ['rebuild', 'query']:
                self.assertIn('invalid', self.cli(action, ok=False))
                self.assertEqual(self.document.read_text(), broken)
                self.assertEqual((self.root / 'report.md').read_bytes(), old_dashboard)
        self.document.unlink()
        self.assertIn('prior initialization', self.cli('init', ok=False))

    def test_query_does_not_repair_optional_report(self):
        self.register()
        (self.root / 'report.md').write_text('stale')
        self.cli('query')
        self.assertEqual((self.root / 'report.md').read_text(), 'stale')
        self.cli('rebuild')
        self.assertIn('修订 1', (self.root / 'report.md').read_text())

    def test_multiprocess_registration_serializes_and_rejects_shared_stale_token(self):
        token = self.cli('query')['document_token']
        children = []
        for n in range(8):
            p = appointment(f'owner-{n}', f'scope-{n}', path=f'/example/project-{n}')
            process = subprocess.Popen([sys.executable, str(SCRIPT), '--root', str(self.root),
                                        '--document', str(self.document), 'register', '--actor', p['owner']['id'],
                                        '--expected-document', token], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            children.append((process, json.dumps(p)))
        results = [process.communicate(payload, timeout=30) + (process.returncode,) for process, payload in children]
        self.assertEqual(sum(code == 0 for out, err, code in results), 1)
        self.assertTrue(all(code == 0 or (code == 2 and 'stale' in err) for out, err, code in results))
        self.assertEqual(len(self.state()['owners']), 1)
        self.assertEqual(self.document.stat().st_mode & 0o777, 0o600)
        registry.validate(self.state())

    def test_superior_appointment_records_issuer_without_user_bypass(self):
        self.register()
        child = appointment('child', 'direction', parent='owner:alpha', path='/example/child')
        child['goal_source'] = {'issuer': 'owner:alpha', 'source': '合成测试：甲在授权范围内任命下级并下达目标'}
        child['assignment']['goal_source'] = child['goal_source']
        self.register(child, actor='alpha')
        state = self.state()
        self.assertEqual(state['owners']['child']['parent'], 'owner:alpha')
        self.assertEqual(state['owners']['child']['appointment_source']['issuer'], 'owner:alpha')
        self.assertEqual(state['assignments']['direction']['goal_source']['issuer'], 'owner:alpha')
        self.assertEqual(self.cli('query', '--owner', 'child')['hierarchy'][0]['parent'], 'owner:alpha')
        child['owner']['id'] = 'wrong-parent'
        child['owner']['parent'] = 'user'
        child['assignment']['id'] = 'wrong-direction'
        self.assertIn('immediate parent', self.register(child, actor='alpha', ok=False))

    def test_same_repo_directions_need_explicit_agreement_not_sent_message(self):
        p = appointment()
        p['assignment']['task'] = '后端方向'
        self.register(p)
        q = appointment('beta', 'frontend')
        q['assignment']['task'] = '前端方向'
        self.register(q)
        repo = 'repo:/example/alpha/.git'
        self.assertEqual(len(self.cli('query', '--repo', repo)['assignments']), 2)
        agreement = {'id': 'shared-interface-v1',
                     'scopes': {'release': ['backend/**', 'shared/schema.py'],
                                'frontend': ['frontend/**', 'shared/schema.py']},
                     'shared_interfaces': '共同确认 schema v1；共享文件修改按顺序集成，不互相覆盖',
                     'workspace': {'mode': 'isolated-worktrees', 'plan': 'alpha/backend, beta/frontend 分支和 worktree'},
                     'sequencing': '甲先提交 schema；乙基于已接受接口；甲串行集成',
                     'integration_owner': 'alpha', 'acceptance': '甲负责集成验收，双方确认接口兼容后向各自上级报告'}
        self.cli('coord-propose', '--actor', 'alpha', '--expected-revision', '2', '--reason', '已发送提议但尚无同意', payload=agreement)
        view = self.cli('query', '--repo', repo)['coordination'][agreement['id']]
        self.assertFalse(view['agreement_ready'])
        self.assertEqual(view['status'], 'proposed')
        self.assertEqual(view['acknowledgements'], {})
        self.assertIn('explicit acknowledgement missing', ' '.join(view['not_ready_reasons']))
        self.cli('coord-ack', '--actor', 'alpha', '--coordination', agreement['id'], '--expected-revision', '3',
                 '--reason', '甲明确同意完整协议，合成消息 ack-alpha')
        self.assertFalse(self.cli('query')['coordination'][agreement['id']]['agreement_ready'])
        self.cli('coord-ack', '--actor', 'beta', '--coordination', agreement['id'], '--expected-revision', '4',
                 '--reason', '乙明确确认边界、共享接口、隔离与集成顺序，合成消息 ack-beta')
        view = self.cli('query', '--owner', 'beta')['coordination'][agreement['id']]
        self.assertTrue(view['agreement_ready'])
        self.assertEqual(view['status'], 'confirmed')
        self.assertEqual(view['participants'], ['alpha', 'beta'])
        self.assertIn('shared/schema.py', (self.root / 'report.md').read_text())
        # Goal/boundary basis changes make old acknowledgement unusable; no silent reuse.
        updated = copy.deepcopy(p['assignment'])
        updated['objective'] = '方向范围发生改变，需重新协调'
        self.cli('update', '--actor', 'alpha', '--assignment', 'release', '--expected-revision', '5',
                 '--reason', '直属上级确认目标调整', payload=updated)
        self.assertFalse(self.cli('query')['coordination'][agreement['id']]['agreement_ready'])
        self.cli('coord-release', '--actor', 'alpha', '--coordination', agreement['id'], '--expected-revision', '6',
                 '--reason', '边界改变，撤销旧协议并重新协商')
        self.assertEqual(self.cli('query')['coordination'][agreement['id']]['status'], 'released')
        self.assertEqual(self.state()['history'][4]['after']['coordination'][agreement['id']]['status'], 'confirmed')

    def test_transfer_chain_persistent_semantic_history_and_active_work_custody(self):
        manager = appointment('manager')
        manager.pop('assignment')
        self.register(manager)
        a = appointment(parent='owner:manager')
        a['owner']['role'] = 'office场景评测负责人'
        a['assignment']['task'] = 'office评测方向'
        self.register(a)
        b = appointment('beta', 'kitchen', parent='owner:manager')
        b['owner']['role'] = '接任方向负责人'
        b['assignment']['task'] = 'kitchen评测方向'
        self.register(b)
        c = appointment('gamma', parent='owner:manager')
        c.pop('assignment')
        self.register(c)
        agreement = {'id': 'active-shared', 'scopes': {'release': ['office/**', 'shared/config'], 'kitchen': ['kitchen/**', 'shared/config']},
                     'shared_interfaces': '共享配置按确认版本', 'workspace': {'mode': 'shared-cwd', 'plan': '串行 writer，alpha 先 beta 后'},
                     'sequencing': 'alpha 后 beta，由 manager 授权 alpha 集成', 'integration_owner': 'alpha', 'acceptance': '直属上级接受接口'}
        self.cli('coord-propose', '--actor', 'alpha', '--expected-revision', str(self.state()['revision']), '--reason', '明确提案', payload=agreement)
        for oid in ['alpha', 'beta']:
            self.cli('coord-ack', '--actor', oid, '--coordination', 'active-shared',
                     '--expected-revision', str(self.state()['revision']), '--reason', oid + '明确同意所有条款')
        first = ['handover', '--actor', 'alpha', '--assignment', 'release', '--to-owner', 'beta', '--new-assignment', 'office-v2',
                 '--expected-revision', str(self.state()['revision']), '--reason', '经直属上级授权由甲交给乙',
                 '--authority', '合成证据：manager 批准交接', '--occurred-at', '2025-05-06T07:08:09+08:00',
                 '--pending-work', 'child-run-example 仍由 alpha 托管，结果转 beta；不自动换 supervisor',
                 '--coordination-plan', '旧协议释放，beta 与 kitchen 方向重新确认共享配置和集成顺序']
        self.cli(*first)
        self.assertEqual(self.state()['coordination']['active-shared']['status'], 'released')
        self.assertFalse(self.cli('query')['coordination']['active-shared']['agreement_ready'])
        self.cli('handover', '--actor', 'manager', '--assignment', 'office-v2', '--to-owner', 'gamma', '--new-assignment', 'office-v3',
                 '--expected-revision', str(self.state()['revision']), '--reason', '授权直属上级主动记录乙交给丙',
                 '--authority', 'manager 本级目标分工决定', '--occurred-at', '2025-05-06T07:08:09+08:00',
                 '--pending-work', 'child-run-example 仍由 alpha 托管，结果直接转 gamma，beta 汇交现有材料',
                 '--coordination-plan', 'gamma 接手重新确认所有共享配置边界，未确认前不派重叠 mutation')
        before_repeat = self.document.read_bytes()
        self.assertTrue(self.cli(*first)['unchanged'])  # Original revision is stale, original event must not repeat.
        self.assertEqual(before_repeat, self.document.read_bytes())
        self.cli('rebuild')  # Every CLI is a new process: restart/rebuild cannot lose the chain.
        current = self.cli('query', '--task', 'office评测方向', '--active')['current_owners']
        self.assertEqual(list(current), ['office-v3'])
        self.assertEqual(current['office-v3']['owner']['id'], 'gamma')
        self.assertEqual(current['office-v3']['superior'], 'owner:manager')
        obligations = current['office-v3']['handover_obligations']
        self.assertIn('active-shared', obligations['coordination_ids'])
        self.assertIn('child-run-example', obligations['previous_pending_work'])
        state = self.state()
        self.assertTrue({'alpha', 'beta', 'gamma', 'manager'} <= set(state['owners']))
        self.assertEqual(state['assignments']['release']['successor'], 'office-v2')
        self.assertEqual(state['assignments']['office-v2']['successor'], 'office-v3')
        transfers = [e for e in state['history'] if e['action'] == 'handover']
        self.assertEqual([(e['changes'][0]['old_owner']['id'], e['changes'][0]['new_owner']['id']) for e in transfers],
                         [('alpha', 'beta'), ('beta', 'gamma')])
        self.assertEqual(len({e['id'] for e in transfers}), 2)
        for e in transfers:
            self.assertRegex(e['at'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$')
            self.assertEqual(e['occurred'], {'at': '2025-05-06T07:08:09+08:00', 'precision': 'exact'})
            self.assertNotEqual(e['at'], e['occurred']['at'])
        dashboard = (self.root / 'report.md').read_text()
        self.assertIn('责任变更历史', dashboard)
        self.assertIn('alpha / 示例alpha / office场景评测负责人', dashboard)
        self.assertIn('beta / 示例beta / 接任方向负责人', dashboard)
        self.assertIn('gamma / 示例gamma / 发布负责人', dashboard)
        self.assertIn(transfers[0]['id'], dashboard)
        self.assertIn(transfers[1]['id'], dashboard)
        self.assertIsNotNone(state['assignments']['release']['end_time'])
        self.assertIsNotNone(state['assignments']['office-v2']['end_time'])
        self.assertIsNone(state['assignments']['office-v3']['end_time'])

    def test_unknown_and_approximate_occurrence_do_not_fabricate_past_seconds(self):
        p = appointment()
        self.cli('register', '--actor', 'alpha', '--occurrence-precision', 'unknown', payload=p)
        self.assertEqual(self.state()['assignments']['release']['start_time'], {'at': None, 'precision': 'unknown'})
        q = appointment('beta')
        q.pop('assignment')
        self.register(q)
        self.cli('handover', '--actor', 'alpha', '--assignment', 'release', '--to-owner', 'beta', '--new-assignment', 'release-v2',
                 '--expected-revision', '2', '--reason', '补录约略过去交接', '--occurrence-precision', 'approximate',
                 '--occurred-at', '约2025年5月，具体秒未知', '--pending-work', '已核验无运行 child')
        event = self.state()['history'][-1]
        self.assertEqual(event['occurred'], {'at': '约2025年5月，具体秒未知', 'precision': 'approximate'})
        self.assertIn('约略，不保证秒', (self.root / 'report.md').read_text())
        before = self.document.read_bytes()
        self.assertIn('timezone', self.cli('close', '--actor', 'beta', '--assignment', 'release-v2',
                                           '--expected-revision', '3', '--reason', '无时区应拒绝',
                                           '--occurred-at', '2025-05-06T07:08:09', ok=False))
        self.assertEqual(before, self.document.read_bytes())

    def test_legacy_state_read_does_not_invent_repo_goal_or_coordination_facts(self):
        self.register()
        legacy = self.state()
        legacy.pop('coordination')
        legacy['owners']['alpha'].pop('appointment_source')
        legacy['assignments']['release'].pop('repo')
        legacy['assignments']['release'].pop('goal_source')
        legacy['coordination'] = {}
        content = registry.serialize_document(legacy, self.cli('query')['era'])
        self.document.write_text(content)
        result = self.cli('query')
        self.assertEqual(result['coordination'], {})
        self.assertNotIn('repo', result['assignments']['release'])
        self.assertNotIn('goal_source', result['assignments']['release'])
        self.cli('rebuild')
        self.assertIn('历史未录入，待确认', (self.root / 'report.md').read_text())
        self.assertEqual(self.document.read_text(), content)

    def test_project_identity_consistency_and_task_identity_immutable(self):
        p = appointment()
        self.register(p)
        a = copy.deepcopy(p['assignment'])
        a['task'] = '悄悄换范围'
        self.assertIn('identity', self.cli('update', '--actor', 'alpha', '--assignment', 'release',
                                          '--expected-revision', '1', '--reason', '不应替换', payload=a, ok=False))
        q = appointment('beta', 'beta-task')
        q['assignment']['task'] = '构建'
        q['assignment']['project']['name'] = '同项目的不同名字'
        self.assertIn('project identity mismatch', self.register(q, ok=False))


if __name__ == '__main__':
    unittest.main()
