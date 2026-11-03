"""Exact frozen API double. Never imports or reads a real registry."""
import copy
import hashlib
import json


class Service:
    def __init__(self, owner='owner-A'):
        self.owner = owner
        self.calls = []
        self.receipts = {}
        self.valid = True
        self.commit_error = None
        self.events = 0
        self.revision = 7
        self.null_document_token_after = False

    def envelope(self, op, status, *, digest=None, plan=None, receipt=None, error=None):
        return dict(schema='responsibility-session-succession/v1', status=status,
                    operation_id=op, intent_digest=digest, plan=plan, receipt=receipt,
                    current_binding_valid=status in ('committed', 'already_committed'),
                    no_owner_valid=status == 'not_applicable', error=error)

    def prepare_owner_session_succession(self, op, source, *, store=None):
        self.calls.append('prepare')
        return self.envelope(op, 'prepared', plan=dict(version=1, operation_id=op,
            store_id='temporary-double', era='era-one', owner_id=self.owner,
            source_pi_session_id=source, document_token='doc-' + str(self.revision),
            observation_revision=self.revision))

    def build_intent_digest(self, plan, target, authority, custody):
        plan = {k: v for k, v in plan.items() if k not in ('document_token', 'observation_revision')}
        raw = json.dumps(dict(version=1, plan=plan, target=target, authority=authority, custody=custody),
                         ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def commit_owner_session_succession(self, plan, target, *, expected_document, authority, custody, store=None):
        self.calls.append('commit')
        op = plan['operation_id']
        digest = self.build_intent_digest(plan, target, authority, custody)
        if self.commit_error:
            error = self.commit_error
            self.commit_error = None
            return self.envelope(op, 'conflict', digest=digest, error=error)
        if op in self.receipts:
            return self.get_owner_session_succession(op, digest)
        before = self.revision
        if plan['owner_id'] is not None:
            self.events += 1
            self.revision += 1
        receipt = dict(version=1, operation_id=op, intent_digest=digest, store_id=plan['store_id'],
            era=plan['era'], owner_id=plan['owner_id'], source_pi_session_id=plan['source_pi_session_id'],
            target_pi_session_id=target['identity']['pi_session_id'],
            outcome='committed' if plan['owner_id'] else 'not_applicable', revision_before=before,
            revision_after=self.revision, transaction_id='tx-one', event_id='event-one' if plan['owner_id'] else None,
            document_token_after=None if self.null_document_token_after else 'doc-' + str(self.revision))
        self.receipts[op] = receipt
        return self.envelope(op, receipt['outcome'], digest=digest, receipt=copy.deepcopy(receipt))

    def get_owner_session_succession(self, op, digest, *, store=None):
        self.calls.append('get')
        receipt = self.receipts.get(op)
        if receipt is None:
            return self.envelope(op, 'not_found', digest=digest)
        status = ('already_committed' if receipt['outcome'] == 'committed' else 'not_applicable') if self.valid else 'superseded'
        return self.envelope(op, status, digest=digest, receipt=copy.deepcopy(receipt))
