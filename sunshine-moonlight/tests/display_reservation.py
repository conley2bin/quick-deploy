#!/usr/bin/env python3
"""Portable reservation evidence: real private temp files/spawned processes only.

Windows byte-lock and ACL tests are explicitly Linux-hosted API fixtures, NOT
native Windows acceptance. No host inventory, services, HOME, network or GUI.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from dataclasses import replace
import errno
import json
import multiprocessing as mp
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "display-control"
sys.path.insert(0, str(SOURCE))
import reservation as r
from protocol import Refusal

HOST = r.HostIdentity("01234567-89ab-4def-8123-456789abcdef", 47989)
GENERATION = r.ServiceGeneration("sunshine.service", "boot-1", 431, "birth-111")
BINDING = r.RuntimeBinding(HOST, GENERATION, "a" * 64, "b" * 64)
DESKTOP = r.PositiveDesktop(958645192, "c" * 64)
CEREMONY = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
CANCEL = "ffffffff-eeee-4ddd-8ccc-bbbbbbbbbbbb"


def idle(binding=BINDING):
    return r.IdleRuntime(binding, 0)


def active(ticket):
    return r.DesktopActive(ticket.status.binding, DESKTOP, DESKTOP.app_id,
                           ticket.status.attempt_revision)


def running(ticket):
    return r.DesktopRunning(ticket.status.binding, DESKTOP, DESKTOP.app_id)


def owned_running(ticket):
    return r.OwnedDesktopRunning(r.OwnershipBinding.from_runtime(ticket.status.binding), DESKTOP, DESKTOP.app_id)


def closure(ticket):
    return r.DesktopClosure(r.OwnershipBinding.from_runtime(ticket.status.binding), DESKTOP, 0, ticket.status.revision, CANCEL)


def contend(path, revision, barrier, output):
    """Each child opens its own engine and real lock; no shared mock state."""
    barrier.wait(timeout=15)
    try:
        ticket = r.ReservationStore(Path(path), HOST).acquire(revision, idle())
        output.put(("owned", ticket))  # Private test IPC; repr suppresses capability.
    except Refusal as exc:
        output.put(("refused", exc.code))


def hold_kernel_lock(path, ready, stop):
    with r.ReservationStore(Path(path), HOST)._transaction():
        ready.send("locked")
        stop.recv()


def crash_after_initial_link(path):
    original_publish = r._Directory.publish

    def publish_then_exit(directory, source, absent):
        original_publish(directory, source, absent)
        if absent:
            os._exit(23)  # No finally/cleanup: simulate interruption before publication completes.

    with mock.patch.object(r._Directory, "publish", publish_then_exit):
        r.ReservationStore(Path(path), HOST).reconcile(0, r.ExclusiveOnboarding(idle(), CEREMONY))


def owner_then_wait(path, revision, ready, stop):
    store = r.ReservationStore(Path(path), HOST)
    ticket = store.acquire(revision, idle())
    ticket = store.begin_prepare(ticket, idle())
    ticket = store.prepared(ticket, r.PreparedRuntime(BINDING, BINDING, DESKTOP, 0))
    ticket = store.attempt_launch(ticket, idle())
    ready.send(ticket)  # Persisted before caller could issue launch.
    stop.recv()


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="display-reservation-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.store = r.ReservationStore(self.path, HOST)

    def refuse(self, code, function, *args, **kwargs):
        with self.assertRaises(Refusal) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def onboard(self):
        return self.store.reconcile(self.store.status().revision,
                                    r.ExclusiveOnboarding(idle(), CEREMONY))

    def acquire(self):
        status = self.onboard()
        return self.store.acquire(status.revision, idle())

    def prepared(self):
        ticket = self.store.begin_prepare(self.acquire(), idle())
        return self.store.prepared(ticket, r.PreparedRuntime(BINDING, BINDING, DESKTOP, 0))

    def connecting(self):
        return self.store.attempt_launch(self.prepared(), idle())

    def owned_active(self):
        ticket = self.connecting()
        return self.store.observe_active(ticket, active(ticket))


class LifecycleTests(StoreCase):
    def test_missing_state_requires_explicit_onboarding(self):
        status = self.store.status()
        self.assertEqual(status.phase, r.Phase.RECONCILIATION_REQUIRED)
        self.assertEqual(status.revision, 0)
        self.refuse("phase_mismatch", self.store.acquire, 0, idle())
        self.refuse("invalid_evidence", self.store.reconcile, 0, {"verified": True, "force": True})
        self.refuse("application_mismatch", self.store.reconcile, 0,
                    r.ExclusiveOnboarding(r.IdleRuntime(BINDING, DESKTOP.app_id), CEREMONY))
        self.refuse("invalid_evidence", self.store.reconcile, 0,
                    r.ExclusiveOnboarding(idle(), "yes"))
        self.refuse("generation_mismatch", self.store.reconcile, 0,
                    r.ServiceTermination(GENERATION, idle()))
        self.assertEqual(self.onboard().phase, r.Phase.AVAILABLE)

    def test_complete_launch_reconnect_release_survives_reopen(self):
        ticket = self.owned_active()
        token = ticket.capability
        self.assertTrue(ticket.status.active_observed)
        self.assertEqual(r.ReservationStore(self.path, HOST).status(), ticket.status)
        ticket = self.store.attempt_reconnect(ticket, running(ticket))
        self.assertEqual(ticket.status.phase, r.Phase.RECONNECTING)
        self.refuse("phase_mismatch", self.store.begin_release, ticket, owned_running(ticket))
        ticket = self.store.observe_active(ticket, active(ticket))
        ticket = self.store.begin_release(ticket, owned_running(ticket))
        status = self.store.release(ticket, closure(ticket))
        self.assertEqual(status.phase, r.Phase.AVAILABLE)
        self.assertFalse(status.launch_attempted)
        self.assertEqual(self.store.status(), status)
        second = self.store.acquire(status.revision, idle())
        self.assertTrue(second.capability.value != token.value)
        self.refuse("not_owner", self.store.abort, ticket, r.PrelaunchAbort(BINDING, 0))

    def test_positive_desktop_contract_rejects_zero_id_and_uninspected_definition(self):
        for app_id in (0, -1, True, 1 << 31, "12"):
            self.refuse("invalid_evidence", r.PositiveDesktop, app_id, "c" * 64)
        self.refuse("invalid_evidence", r.PositiveDesktop, 1, "not-a-definition-digest")

    def test_clean_prelaunch_abort_is_distinct_from_attempt(self):
        for stage in ("selecting", "preparing", "prepared"):
            with self.subTest(stage=stage):
                if stage == "selecting":
                    ticket = self.acquire()
                else:
                    ticket = self.store.begin_prepare(self.acquire(), idle())
                    if stage == "prepared":
                        ticket = self.store.prepared(ticket, r.PreparedRuntime(BINDING, BINDING, DESKTOP, 0))
                status = self.store.abort(ticket, r.PrelaunchAbort(BINDING, 0))
                self.assertEqual(status.phase, r.Phase.AVAILABLE)
                # A later reservation does not reuse the old capability.
                if stage != "prepared":
                    status = self.store.require_reconciliation(status.revision, r.Uncertainty.CONTROL_LOST)
        ticket = self.store.acquire(self.store.status().revision, idle())
        ticket = self.store.begin_prepare(ticket, idle())
        ticket = self.store.prepared(ticket, r.PreparedRuntime(BINDING, BINDING, DESKTOP, 0))
        ticket = self.store.attempt_launch(ticket, idle())
        self.refuse("reconciliation_required", self.store.abort, ticket, r.PrelaunchAbort(BINDING, 0))
        status = self.store.status()
        self.assertEqual(status.phase, r.Phase.RECONCILIATION_REQUIRED)
        self.assertTrue(status.launch_attempted)
        self.refuse("phase_mismatch", self.store.acquire, status.revision, idle())
        self.assertIsNotNone(json.loads((self.path / r.STATE_FILE).read_bytes())["owner_digest"])

    def test_preparation_must_precede_any_attempt(self):
        ticket = self.acquire()
        self.refuse("phase_mismatch", self.store.attempt_launch, ticket, idle())
        ticket = self.store.begin_prepare(ticket, idle())
        self.refuse("phase_mismatch", self.store.attempt_launch, ticket, idle())
        self.refuse("phase_mismatch", self.store.observe_active, ticket,
                    r.DesktopActive(BINDING, DESKTOP, DESKTOP.app_id, ticket.status.revision))
        ticket = self.store.prepared(ticket, r.PreparedRuntime(BINDING, BINDING, DESKTOP, 0))
        self.refuse("phase_mismatch", self.store.prepared, ticket,
                    r.PreparedRuntime(BINDING, BINDING, DESKTOP, 0))

    def test_restart_requires_exact_retired_generation_and_unchanged_topology(self):
        ticket = self.store.begin_prepare(self.acquire(), idle())
        replacement = replace(BINDING, generation=replace(GENERATION, start_id="birth-222"),
                              config_digest="d" * 64)
        self.refuse("generation_mismatch", self.store.prepared, ticket,
                    r.PreparedRuntime(BINDING, replacement, DESKTOP, 0))
        self.refuse("generation_mismatch", self.store.prepared, ticket,
                    r.PreparedRuntime(BINDING, replacement, DESKTOP, 0, replace(GENERATION, pid=987)))
        ticket = self.store.prepared(ticket, r.PreparedRuntime(BINDING, replacement, DESKTOP, 0, GENERATION))
        self.assertEqual(ticket.status.binding, replacement)
        ticket = self.store.attempt_launch(ticket, idle(replacement))
        ticket = self.store.observe_active(ticket, active(ticket))
        self.assertEqual(ticket.status.phase, r.Phase.ACTIVE)

    def test_prepared_topology_change_cannot_rebind_selection(self):
        ticket = self.store.begin_prepare(self.acquire(), idle())
        self.refuse("binding_mismatch", self.store.prepared, ticket,
                    r.PreparedRuntime(BINDING, replace(BINDING, topology_digest="d" * 64), DESKTOP, 0))
        self.assertEqual(self.store.status().phase, r.Phase.RECONCILIATION_REQUIRED)

    def test_wrong_tokens_and_revision_replay_never_replace_owner(self):
        ticket = self.acquire()
        wrong = replace(ticket, capability=r.Capability("0" * 64))
        before = (self.path / r.STATE_FILE).read_bytes()
        self.refuse("not_owner", self.store.begin_prepare, wrong, idle())
        self.assertEqual((self.path / r.STATE_FILE).read_bytes(), before)
        new = self.store.begin_prepare(ticket, idle())
        self.refuse("stale_revision", self.store.begin_prepare, ticket, idle())
        self.refuse("binding_mismatch", self.store.prepared,
                    replace(new, status=replace(new.status, binding=replace(BINDING, config_digest="e" * 64))),
                    r.PreparedRuntime(BINDING, BINDING, DESKTOP, 0))
        self.refuse("phase_mismatch", self.store.acquire, new.status.revision, idle())
        self.assertEqual(self.store.status(), new.status)

    def test_all_owner_phases_keep_durable_exclusive_ownership(self):
        ticket = self.acquire()
        for step in (lambda t: self.store.begin_prepare(t, idle()),
                     lambda t: self.store.prepared(t, r.PreparedRuntime(BINDING, BINDING, DESKTOP, 0)),
                     lambda t: self.store.attempt_launch(t, idle()),
                     lambda t: self.store.observe_active(t, active(t)),
                     lambda t: self.store.attempt_reconnect(t, running(t)),
                     lambda t: self.store.observe_active(t, active(t)),
                     lambda t: self.store.begin_release(t, owned_running(t))):
            self.refuse("phase_mismatch", self.store.acquire, ticket.status.revision, idle())
            self.assertEqual(r.ReservationStore(self.path, HOST).status(), ticket.status)
            ticket = step(ticket)
        self.refuse("phase_mismatch", self.store.acquire, ticket.status.revision, idle())

    def test_binding_changes_fail_closed_for_owner_not_other_caller(self):
        variants = [replace(BINDING, topology_digest="d" * 64),
                    replace(BINDING, config_digest="e" * 64),
                    replace(BINDING, generation=replace(GENERATION, boot_id="boot-2")),
                    replace(BINDING, generation=replace(GENERATION, start_id="birth-222")),
                    replace(BINDING, host=replace(HOST, base_port=48000))]
        for variant in variants:
            with self.subTest(binding=variant):
                ticket = self.acquire()
                wrong = replace(ticket, capability=r.Capability("0" * 64))
                self.refuse("not_owner", self.store.begin_prepare, wrong, idle(variant))
                self.assertEqual(self.store.status(), ticket.status)
                self.refuse("runtime_changed", self.store.begin_prepare, ticket, idle(variant))
                self.assertEqual(self.store.status().uncertainty, r.Uncertainty.RUNTIME_CHANGED)
                self.assertEqual(self.store.status().binding, BINDING)

    def test_available_baseline_generation_change_requires_reconciliation(self):
        status = self.onboard()
        changed = replace(BINDING, generation=replace(GENERATION, pid=900))
        self.refuse("generation_mismatch", self.store.acquire, status.revision, idle(changed))
        status = self.store.status()
        self.assertEqual(status.phase, r.Phase.RECONCILIATION_REQUIRED)
        status = self.store.reconcile(status.revision, r.ServiceTermination(GENERATION, idle(changed)))
        self.assertEqual(self.store.acquire(status.revision, idle(changed)).status.binding, changed)

    def test_available_snapshot_refresh_does_not_adopt_a_session(self):
        status = self.onboard()
        changed = replace(BINDING, topology_digest="d" * 64, config_digest="e" * 64)
        self.refuse("application_mismatch", self.store.acquire, status.revision,
                    r.IdleRuntime(changed, DESKTOP.app_id))
        ticket = self.store.acquire(status.revision, idle(changed))
        self.assertEqual(ticket.status.binding, changed)

    def test_host_uuid_port_are_immutable_across_store_instances(self):
        self.acquire()
        for host in (replace(HOST, base_port=48000), replace(HOST, uuid=CEREMONY)):
            self.refuse("host_mismatch", r.ReservationStore(self.path, host).status)
        self.refuse("host_mismatch", self.store.acquire, self.store.status().revision,
                    idle(replace(BINDING, host=replace(HOST, base_port=48000))))

    def test_active_observation_must_match_latest_attempt_and_desktop(self):
        ticket = self.connecting()
        for observation in (replace(active(ticket), attempt_revision=ticket.status.revision - 1),
                            replace(active(ticket), attempt_revision=True)):
            self.refuse("stale_observation", self.store.observe_active, ticket, observation)
        self.refuse("application_mismatch", self.store.observe_active, ticket,
                    replace(active(ticket), application_id=0))
        self.refuse("application_mismatch", self.store.observe_active, ticket,
                    replace(active(ticket), desktop=replace(DESKTOP, definition_digest="d" * 64)))
        ticket = self.store.observe_active(ticket, active(ticket))
        old_observation = active(ticket)
        ticket = self.store.attempt_reconnect(ticket, running(ticket))
        self.refuse("stale_observation", self.store.observe_active, ticket, old_observation)
        self.assertEqual(ticket.status.phase, r.Phase.RECONNECTING)

    def test_attempt_without_positive_owned_active_cannot_normally_release(self):
        ticket = self.connecting()
        self.refuse("phase_mismatch", self.store.begin_release, ticket, owned_running(ticket))
        self.refuse("phase_mismatch", self.store.release, ticket, closure(ticket))
        self.refuse("invalid_evidence", self.store.release, ticket, {"exit_code": 0, "state": "FREE"})
        self.assertEqual(self.store.status().phase, r.Phase.CONNECTING)
        status = self.store.require_reconciliation(ticket.status.revision, r.Uncertainty.LAUNCH_UNCONFIRMED)
        self.refuse("phase_mismatch", self.store.acquire, status.revision, idle())

    def test_release_requires_correlated_cancel_and_confirmed_idle(self):
        ticket = self.owned_active()
        self.refuse("phase_mismatch", self.store.release, ticket, closure(ticket))
        self.refuse("application_mismatch", self.store.begin_release, ticket,
                    replace(owned_running(ticket), application_id=0))
        ticket = self.store.begin_release(ticket, owned_running(ticket))
        for evidence in (replace(closure(ticket), cancel_transaction="exit0"),
                         replace(closure(ticket), cancel_transaction="")):
            self.refuse("invalid_evidence", self.store.release, ticket, evidence)
        self.refuse("application_mismatch", self.store.release, ticket,
                    replace(closure(ticket), application_id=DESKTOP.app_id))
        self.refuse("application_mismatch", self.store.release, ticket,
                    replace(closure(ticket), desktop=replace(DESKTOP, app_id=12)))
        self.refuse("stale_observation", self.store.release, ticket,
                    replace(closure(ticket), release_revision=ticket.status.revision - 1))
        self.assertEqual(self.store.status().phase, r.Phase.RELEASING)
        status = self.store.require_reconciliation(ticket.status.revision, r.Uncertainty.CLOSURE_UNCONFIRMED)
        self.refuse("phase_mismatch", self.store.release,
                    replace(ticket, status=status), replace(closure(ticket), release_revision=status.revision))
        self.refuse("phase_mismatch", self.store.acquire, status.revision, idle())

    def test_control_loss_and_free_never_expire_or_clear_owner(self):
        ticket = self.owned_active()
        owner = json.loads((self.path / r.STATE_FILE).read_bytes())["owner_digest"]
        status = self.store.require_reconciliation(ticket.status.revision, r.Uncertainty.CONTROL_LOST)
        os.utime(self.path / r.STATE_FILE, (1, 1))  # Age is not an input to lifecycle decisions.
        for _ in range(3):
            self.refuse("phase_mismatch", self.store.acquire, status.revision, idle())
            self.assertEqual(self.store.status(), status)
        self.assertEqual(json.loads((self.path / r.STATE_FILE).read_bytes())["owner_digest"], owner)
        self.refuse("phase_mismatch", self.store.begin_release, replace(ticket, status=status), owned_running(ticket))

    def test_reconciliation_requires_exact_dead_service_and_verified_replacement(self):
        ticket = self.connecting()
        status = self.store.require_reconciliation(ticket.status.revision, r.Uncertainty.OWNER_LOST)
        fresh = replace(BINDING, generation=replace(GENERATION, start_id="birth-222"))
        for proof in (r.ServiceTermination(replace(GENERATION, pid=999), idle(fresh)),
                      r.ServiceTermination(GENERATION, idle()),
                      r.ServiceTermination(GENERATION, idle(replace(fresh, generation=replace(fresh.generation, service_id="other"))))):
            self.refuse("generation_mismatch", self.store.reconcile, status.revision, proof)
        self.refuse("application_mismatch", self.store.reconcile, status.revision,
                    r.ServiceTermination(GENERATION, r.IdleRuntime(fresh, DESKTOP.app_id)))
        self.refuse("stale_revision", self.store.reconcile, status.revision - 1,
                    r.ServiceTermination(GENERATION, idle(fresh)))
        status = self.store.reconcile(status.revision, r.ServiceTermination(GENERATION, idle(fresh)))
        self.assertEqual(status.phase, r.Phase.AVAILABLE)
        self.refuse("not_owner", self.store.observe_active, ticket, active(ticket))
        self.assertEqual(self.store.acquire(status.revision, idle(fresh)).status.binding, fresh)

    def test_explicit_operator_reconciliation_clears_only_uncertain_state(self):
        ticket = self.acquire()
        self.refuse("phase_mismatch", self.store.reconcile, ticket.status.revision,
                    r.ExclusiveOnboarding(idle(), CEREMONY))
        status = self.store.require_reconciliation(ticket.status.revision, r.Uncertainty.OWNER_LOST)
        status = self.store.reconcile(status.revision, r.ExclusiveOnboarding(idle(), CEREMONY))
        self.assertEqual(status.phase, r.Phase.AVAILABLE)
        self.refuse("not_owner", self.store.abort, ticket, r.PrelaunchAbort(BINDING, 0))

    def test_selecting_refresh_and_cancel_publish_truthful_current_topology(self):
        ticket = self.acquire()
        changed = replace(BINDING, topology_digest="d" * 64)
        refreshed = self.store.refresh_selection(ticket, idle(changed))
        self.assertEqual(refreshed.capability, ticket.capability)
        self.assertEqual(refreshed.status.binding, changed)
        self.assertEqual(refreshed.status.revision, ticket.status.revision + 1)
        self.refuse("stale_revision", self.store.begin_prepare, ticket, idle())
        changed_again = replace(changed, topology_digest="e" * 64)
        status = self.store.abort(refreshed, r.PrelaunchAbort(changed_again, 0))
        self.assertEqual(status.phase, r.Phase.AVAILABLE)
        self.assertEqual(status.binding, changed_again)
        self.assertIsNone(json.loads((self.path / r.STATE_FILE).read_bytes())["owner_digest"])

    def test_selecting_refresh_is_owner_only_exact_process_config_and_idle(self):
        for changed in (replace(BINDING, config_digest="d" * 64),
                        replace(BINDING, generation=replace(GENERATION, pid=999)),
                        replace(BINDING, host=replace(HOST, base_port=48000))):
            ticket = self.acquire()
            self.refuse("not_owner", self.store.refresh_selection,
                        replace(ticket, capability=r.Capability("0" * 64)), idle(changed))
            self.assertEqual(self.store.status(), ticket.status)
            self.refuse("runtime_changed", self.store.refresh_selection, ticket, idle(changed))
            self.assertEqual(self.store.status().phase, r.Phase.RECONCILIATION_REQUIRED)
            self.assertEqual(self.store.status().binding, BINDING)
        ticket = self.acquire()
        self.refuse("application_mismatch", self.store.refresh_selection, ticket,
                    r.IdleRuntime(BINDING, DESKTOP.app_id))
        self.refuse("invalid_evidence", self.store.refresh_selection, ticket, {"verified": True})
        self.assertEqual(self.store.status(), ticket.status)

    def test_selecting_refresh_exception_cannot_extend_to_preparing_or_attempts(self):
        ticket = self.store.begin_prepare(self.acquire(), idle())
        self.refuse("phase_mismatch", self.store.refresh_selection, ticket, idle())
        self.refuse("phase_mismatch", self.store.abort, ticket,
                    r.SelectionAbort(r.OwnershipBinding.from_runtime(BINDING), 0))
        self.refuse("runtime_changed", self.store.abort, ticket,
                    r.PrelaunchAbort(replace(BINDING, topology_digest="d" * 64), 0))
        self.assertEqual(self.store.status().phase, r.Phase.RECONCILIATION_REQUIRED)

    def test_selecting_cancel_can_use_only_truthful_ownership_without_topology(self):
        ticket = self.acquire()
        evidence = r.SelectionAbort(r.OwnershipBinding.from_runtime(BINDING), 0)
        status = self.store.abort(ticket, evidence)
        self.assertEqual(status.phase, r.Phase.AVAILABLE)
        self.assertEqual(status.binding, BINDING)  # retained historical full snapshot
        self.refuse("not_owner", self.store.abort, ticket, evidence)

    def test_cleanup_projection_has_no_capture_topology_and_requires_own_record(self):
        ticket = self.owned_active()
        self.refuse("invalid_evidence", self.store.begin_release, ticket, running(ticket))
        changed_topology = replace(BINDING, topology_digest="d" * 64)
        owned = r.OwnedDesktopRunning(r.OwnershipBinding.from_runtime(changed_topology), DESKTOP, DESKTOP.app_id)
        self.assertFalse(hasattr(owned.binding, "topology_digest"))
        ticket = self.store.begin_release(ticket, owned)
        evidence = r.DesktopClosure(r.OwnershipBinding.from_runtime(changed_topology), DESKTOP, 0,
                                    ticket.status.revision, CANCEL)
        status = self.store.release(ticket, evidence)
        self.assertEqual(status.phase, r.Phase.AVAILABLE)
        # Retained full binding is historical selection, not fresh topology.
        self.assertEqual(status.binding, BINDING)
        self.assertEqual(self.store.acquire(status.revision, idle(changed_topology)).status.binding, changed_topology)

    def test_owned_cleanup_still_pins_host_generation_config_and_desktop(self):
        for changed in (replace(BINDING, config_digest="d" * 64),
                        replace(BINDING, generation=replace(GENERATION, start_id="new-birth")),
                        replace(BINDING, host=replace(HOST, uuid=CEREMONY))):
            ticket = self.owned_active()
            proof = r.OwnedDesktopRunning(r.OwnershipBinding.from_runtime(changed), DESKTOP, DESKTOP.app_id)
            self.refuse("runtime_changed", self.store.begin_release, ticket, proof)
            self.assertEqual(self.store.status().phase, r.Phase.RECONCILIATION_REQUIRED)
            self.assertIsNotNone(json.loads((self.path / r.STATE_FILE).read_bytes())["owner_digest"])
        ticket = self.owned_active()
        proof = replace(owned_running(ticket), desktop=replace(DESKTOP, definition_digest="d" * 64))
        self.refuse("application_mismatch", self.store.begin_release, ticket, proof)
        ticket = self.store.begin_release(ticket, owned_running(ticket))
        changed = replace(closure(ticket), binding=replace(closure(ticket).binding, config_digest="d" * 64))
        self.refuse("runtime_changed", self.store.release, ticket, changed)
        self.assertEqual(self.store.status().phase, r.Phase.RECONCILIATION_REQUIRED)

    def test_resume_retains_full_capture_binding_guard(self):
        ticket = self.owned_active()
        changed = replace(running(ticket), binding=replace(BINDING, topology_digest="d" * 64))
        self.refuse("runtime_changed", self.store.attempt_reconnect, ticket, changed)
        self.assertEqual(self.store.status().phase, r.Phase.RECONCILIATION_REQUIRED)

    def test_prepared_abort_requires_actual_prepared_transition_and_revision(self):
        ticket = self.store.begin_prepare(self.acquire(), idle())
        evidence = r.PreparedAbort(r.OwnershipBinding.from_runtime(BINDING), DESKTOP, 0, ticket.status.revision)
        self.refuse("preparation_unconfirmed", self.store.abort, ticket, evidence)
        self.assertEqual(self.store.status(), ticket.status)
        ticket = self.store.prepared(ticket, r.PreparedRuntime(BINDING, BINDING, DESKTOP, 0))
        self.refuse("stale_observation", self.store.abort, ticket, evidence)
        evidence = replace(evidence, prepared_revision=ticket.status.revision)
        self.refuse("preparation_unconfirmed", self.store.abort, ticket,
                    replace(evidence, desktop=replace(DESKTOP, definition_digest="d" * 64)))
        status = self.store.abort(ticket, evidence)
        self.assertEqual(status.phase, r.Phase.AVAILABLE)
        self.assertEqual(status.binding, BINDING)  # historical full snapshot, not invented current topology

    def test_prepared_abort_cannot_clear_reconciliation_or_issued_attempt(self):
        ticket = self.prepared()
        evidence = r.PreparedAbort(r.OwnershipBinding.from_runtime(BINDING), DESKTOP, 0, ticket.status.revision)
        status = self.store.require_reconciliation(ticket.status.revision, r.Uncertainty.OWNER_LOST)
        self.refuse("phase_mismatch", self.store.abort, replace(ticket, status=status), evidence)
        ticket = self.prepared()
        evidence = replace(evidence, prepared_revision=ticket.status.revision)
        ticket = self.store.attempt_launch(ticket, idle())
        self.refuse("reconciliation_required", self.store.abort, ticket, evidence)
        self.assertTrue(self.store.status().launch_attempted)
        self.assertIsNotNone(json.loads((self.path / r.STATE_FILE).read_bytes())["owner_digest"])

    def test_untrusted_dicts_and_boolean_revision_are_not_evidence(self):
        ticket = self.acquire()
        self.refuse("invalid_evidence", self.store.begin_prepare, ticket, {"binding": BINDING, "application_id": 0})
        self.refuse("invalid_evidence", self.store.abort, ticket, {"verified": True})
        self.refuse("invalid_evidence", self.store.require_reconciliation, ticket.status.revision, "owner_lost")
        self.refuse("stale_revision", self.store.require_reconciliation, True, r.Uncertainty.OWNER_LOST)
        self.refuse("invalid_evidence", self.store.require_reconciliation, ticket.status.revision,
                    r.Uncertainty.NEEDS_ONBOARDING)
        self.refuse("application_mismatch", self.store.begin_prepare, ticket, r.IdleRuntime(BINDING, False))

    def test_raw_capability_never_persisted_or_in_public_repr(self):
        ticket = self.acquire()
        data = (self.path / r.STATE_FILE).read_bytes()
        self.assertTrue(ticket.capability.value.encode("ascii") not in data)
        self.assertTrue(ticket.capability.value not in repr(ticket))
        self.assertTrue(ticket.capability.value not in str(ticket.capability))
        self.assertFalse(hasattr(self.store.status(), "owner_digest"))
        self.assertFalse(hasattr(self.store.status(), "capability"))
        self.assertEqual(len(json.loads(data)["owner_digest"]), 64)
        self.assertEqual(set(p.name for p in self.path.iterdir()), {r.STATE_FILE, r.LOCK_FILE})
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE((self.path / r.STATE_FILE).stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((self.path / r.LOCK_FILE).stat().st_mode), 0o600)


class PersistentArtifactTests(StoreCase):
    def test_corrupt_state_cannot_acquire_abort_release_or_reconcile(self):
        ticket = self.owned_active()
        state_file = self.path / r.STATE_FILE
        valid = json.loads(state_file.read_bytes())
        invalid = [b"{", b"\xff", b"[]", b" " * (r.MAX_STATE + 1),
                   b'{"schema":1,"schema":1}',
                   json.dumps({**valid, "extra": 1}).encode(),
                   json.dumps({**valid, "revision": True}).encode(),
                   json.dumps({**valid, "active_observed": 1}).encode(),
                   json.dumps({**valid, "owner_digest": None}).encode(),
                   json.dumps({**valid, "phase": "available"}).encode(),
                   json.dumps({**valid, "attempt_revision": valid["revision"] + 1}).encode(),
                   json.dumps({**valid, "binding": None}).encode(),
                   json.dumps({**valid, "uncertainty": "owner_lost"}).encode(),
                   json.dumps({**valid, "revision": float("nan")}).encode(),
                   b'[' * 1100 + b']' * 1100]
        for data in invalid:
            with self.subTest(case=data[:25]):
                state_file.write_bytes(data)
                self.refuse("reservation_corrupt", self.store.status)
                self.refuse("reservation_corrupt", self.store.acquire, ticket.status.revision, idle())
                self.refuse("reservation_corrupt", self.store.abort, ticket, r.PrelaunchAbort(BINDING, 0))
                self.refuse("reservation_corrupt", self.store.release, ticket, closure(ticket))
                self.refuse("reservation_corrupt", self.store.reconcile, ticket.status.revision,
                            r.ExclusiveOnboarding(idle(), CEREMONY))
                self.assertEqual(state_file.read_bytes(), data)
        nested_duplicate = json.dumps(valid).replace('"pid": 431', '"pid": 431, "pid": 431').encode()
        state_file.write_bytes(nested_duplicate)
        self.refuse("reservation_corrupt", self.store.status)

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture requires unprivileged symlink support")
    def test_symlink_hardlink_nonregular_state_and_lock_refused(self):
        self.onboard()
        for name in (r.STATE_FILE, r.LOCK_FILE):
            artifact = self.path / name
            valid = artifact.read_bytes()
            artifact.unlink()
            target = self.path / "target"
            target.write_bytes(valid)
            target.chmod(0o600)
            artifact.symlink_to(target)
            self.refuse("reservation_unsafe", self.store.status)
            self.assertEqual(target.read_bytes(), valid)
            artifact.unlink()
            os.link(target, artifact)
            self.refuse("reservation_unsafe", self.store.status)
            artifact.unlink()
            artifact.mkdir(mode=0o700)
            self.refuse("reservation_unsafe", self.store.status)
            artifact.rmdir()
            os.mkfifo(artifact, 0o600)
            self.refuse("reservation_unsafe", self.store.status)  # Must not block opening FIFO.
            artifact.unlink()
            target.unlink()
            artifact.write_bytes(valid)
            artifact.chmod(0o600)

    @unittest.skipIf(os.name == "nt", "POSIX permissions/UID evidence")
    def test_insecure_permissions_and_foreign_uid_refused(self):
        self.onboard()
        for artifact in (self.path / r.STATE_FILE, self.path / r.LOCK_FILE, self.path):
            previous = stat.S_IMODE(artifact.stat().st_mode)
            artifact.chmod(previous | 0o020)
            self.refuse("reservation_unsafe", self.store.status)
            artifact.chmod(previous)
        real_uid = os.geteuid()
        with mock.patch.object(r.os, "geteuid", return_value=real_uid + 1):
            self.refuse("reservation_unsafe", self.store.status)

    @unittest.skipIf(os.name == "nt", "POSIX directory-fd anchoring fixture")
    def test_linked_or_replaced_state_directory_refused(self):
        self.onboard()
        link = self.path / "linked"
        real = self.path / "real"
        real.mkdir(mode=0o700)
        link.symlink_to(real, target_is_directory=True)
        self.refuse("reservation_unsafe", r.ReservationStore(link, HOST).status)
        moved = self.path.with_name(self.path.name + "-moved")
        with r._Directory(self.path) as directory:
            self.path.rename(moved)
            self.path.mkdir(mode=0o700)
            try:
                self.refuse("reservation_unsafe", directory.check)
            finally:
                self.path.rmdir()
                moved.rename(self.path)

    def test_reparse_point_metadata_fixture_is_refused(self):
        info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_nlink=1,
                               st_uid=getattr(os, "geteuid", lambda: 0)(), st_file_attributes=0x400)
        self.refuse("reservation_unsafe", r._metadata, info)

    def test_missing_previously_owned_state_is_unknown_not_available(self):
        self.acquire()
        (self.path / r.STATE_FILE).unlink()
        status = self.store.status()
        self.assertEqual(status.phase, r.Phase.RECONCILIATION_REQUIRED)
        self.refuse("phase_mismatch", self.store.acquire, status.revision, idle())

    def test_invalid_lock_is_never_deleted_or_reinitialized(self):
        self.onboard()
        lock = self.path / r.LOCK_FILE
        lock.write_bytes(b"")
        self.refuse("reservation_unsafe", self.store.status)
        self.assertEqual(lock.read_bytes(), b"")

    @unittest.skipIf(os.name == "nt", "POSIX directory-fsync ordering; Windows has no directory fsync")
    def test_initial_publication_syncs_final_one_link_state(self):
        self.store.status()  # Create only the lock, not a reservation state.
        self.assertFalse((self.path / r.STATE_FILE).exists())
        original_sync, original_unlink = r._Directory.sync, r._Directory.unlink
        events = []

        def unlink(directory, name):
            original_unlink(directory, name)
            events.append(("unlink", (self.path / r.STATE_FILE).stat().st_nlink))

        def sync(directory):
            events.append(("sync", (self.path / r.STATE_FILE).stat().st_nlink))
            original_sync(directory)  # Real directory fsync, not a success-only stub.

        with mock.patch.object(r._Directory, "unlink", unlink), mock.patch.object(r._Directory, "sync", sync):
            status = self.store.reconcile(0, r.ExclusiveOnboarding(idle(), CEREMONY))
        self.assertEqual(events, [("unlink", 1), ("sync", 1)])
        self.assertEqual(self.store.status(), status)
        self.assertEqual(set(p.name for p in self.path.iterdir()), {r.STATE_FILE, r.LOCK_FILE})

    @unittest.skipIf(os.name == "nt", "POSIX final-directory-sync failure fixture")
    def test_initial_publication_final_sync_failure_surfaces_and_cleans_alias(self):
        self.store.status()
        self.assertFalse((self.path / r.STATE_FILE).exists())
        original_sync = r._Directory.sync
        failures = []

        def fail_final_sync(directory):
            if (self.path / r.STATE_FILE).stat().st_nlink == 1:
                failures.append("final one-link sync")
                raise OSError(errno.EIO, "fixture final sync failed")
            original_sync(directory)

        with mock.patch.object(r._Directory, "sync", fail_final_sync):
            self.refuse("persistence_uncertain", self.store.reconcile, 0,
                        r.ExclusiveOnboarding(idle(), CEREMONY))
        self.assertEqual(failures, ["final one-link sync"])
        self.assertEqual((self.path / r.STATE_FILE).stat().st_nlink, 1)
        self.assertEqual(set(p.name for p in self.path.iterdir()), {r.STATE_FILE, r.LOCK_FILE})
        # Publication may already have happened despite the refusal; do not assume no state.
        self.assertEqual(self.store.status().phase, r.Phase.AVAILABLE)

    def test_atomic_publication_failure_retains_state_and_cleans_scratch(self):
        self.onboard()
        before = (self.path / r.STATE_FILE).read_bytes()
        revision = self.store.status().revision
        with mock.patch.object(r._Directory, "publish", side_effect=OSError(errno.EIO, "fixture")):
            self.refuse("persistence_uncertain", self.store.acquire, revision, idle())
        self.assertEqual((self.path / r.STATE_FILE).read_bytes(), before)
        self.assertEqual(set(p.name for p in self.path.iterdir()), {r.STATE_FILE, r.LOCK_FILE})

    def test_prepublication_fsync_failure_never_returns_owner_and_cleans_scratch(self):
        self.onboard()
        before = (self.path / r.STATE_FILE).read_bytes()
        revision = self.store.status().revision
        with mock.patch.object(r.os, "fsync", side_effect=OSError(errno.EIO, "fixture")):
            self.refuse("persistence_uncertain", self.store.acquire, revision, idle())
        self.assertEqual((self.path / r.STATE_FILE).read_bytes(), before)
        self.assertEqual(set(p.name for p in self.path.iterdir()), {r.STATE_FILE, r.LOCK_FILE})

    def test_failed_postpublication_sync_can_leave_owner_but_never_silently_succeeds(self):
        self.onboard()
        revision = self.store.status().revision
        with mock.patch.object(r._Directory, "sync", side_effect=OSError(errno.EIO, "fixture")):
            self.refuse("persistence_uncertain", self.store.acquire, revision, idle())
        status = self.store.status()
        self.assertEqual(status.phase, r.Phase.SELECTING)
        self.refuse("phase_mismatch", self.store.acquire, status.revision, idle())

    def test_compare_before_replace_detects_out_of_lock_edit(self):
        ticket = self.acquire()
        original_verify = r._Directory.verify
        changed = []

        def edit_during_temp_write(directory, name, fd, before=None):
            result = original_verify(directory, name, fd, before)
            if name.startswith(".reservation-write-") and not changed:
                obj = json.loads((self.path / r.STATE_FILE).read_bytes())
                obj["revision"] += 1
                (self.path / r.STATE_FILE).write_text(json.dumps(obj))
                changed.append(True)
            return result

        with mock.patch.object(r._Directory, "verify", edit_during_temp_write):
            self.refuse("reservation_corrupt", self.store.begin_prepare, ticket, idle())
        self.assertEqual(self.store.status().phase, r.Phase.SELECTING)
        self.assertEqual(self.store.status().revision, ticket.status.revision + 1)
        self.assertEqual(set(p.name for p in self.path.iterdir()), {r.STATE_FILE, r.LOCK_FILE})

    def test_revision_cannot_wrap_or_reset(self):
        self.onboard()
        path = self.path / r.STATE_FILE
        obj = json.loads(path.read_bytes())
        obj["revision"] = r.MAX_REVISION
        path.write_text(json.dumps(obj))
        self.refuse("reservation_corrupt", self.store.acquire, r.MAX_REVISION, idle())
        obj["revision"] = 0
        path.write_text(json.dumps(obj))
        self.refuse("reservation_corrupt", self.store.status)


class MultiprocessTests(StoreCase):
    def setUp(self):
        super().setUp()
        self.context = mp.get_context("spawn")
        self.processes = []
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        for process in self.processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)

    def start(self, function, *args):
        process = self.context.Process(target=function, args=args)
        process.start()
        self.processes.append(process)
        return process

    def receive(self, pipe):
        self.assertTrue(pipe.poll(15), "fixture process did not complete handshake")
        return pipe.recv()

    def test_initial_publication_pre_return_crash_keeps_hardlink_refusal(self):
        self.store.status()
        self.assertFalse((self.path / r.STATE_FILE).exists())
        process = self.start(crash_after_initial_link, str(self.path))
        process.join(timeout=15)
        self.assertEqual(process.exitcode, 23)
        self.assertEqual((self.path / r.STATE_FILE).stat().st_nlink, 2)
        self.assertEqual(len(list(self.path.glob(".reservation-write-*"))), 1)
        self.refuse("reservation_unsafe", self.store.status)
        self.refuse("reservation_unsafe", self.store.reconcile, 0,
                    r.ExclusiveOnboarding(idle(), CEREMONY))
        self.refuse("reservation_unsafe", self.store.acquire, 0, idle())

    def test_real_competing_processes_exactly_one_capability_owner(self):
        status = self.onboard()
        barrier = self.context.Barrier(6)
        output = self.context.Queue()
        for _ in range(6):
            self.start(contend, str(self.path), status.revision, barrier, output)
        results = [output.get(timeout=20) for _ in range(6)]
        for process in self.processes:
            process.join(timeout=5)
            self.assertEqual(process.exitcode, 0)
        winners = [value for kind, value in results if kind == "owned"]
        self.assertEqual(len(winners), 1)
        codes = [value for kind, value in results if kind == "refused"]
        self.assertTrue(set(codes) <= {"reservation_contended", "stale_revision"})
        self.assertEqual(self.store.status(), winners[0].status)
        self.refuse("phase_mismatch", self.store.acquire, self.store.status().revision, idle())
        self.assertEqual(self.store.abort(winners[0], r.PrelaunchAbort(BINDING, 0)).phase, r.Phase.AVAILABLE)
        output.close()
        output.join_thread()

    def test_real_kernel_lock_contention_and_owner_independent_unlock(self):
        status = self.onboard()
        ready_parent, ready_child = self.context.Pipe()
        stop_parent, stop_child = self.context.Pipe()
        process = self.start(hold_kernel_lock, str(self.path), ready_child, stop_child)
        self.assertEqual(self.receive(ready_parent), "locked")
        self.refuse("reservation_contended", self.store.status)
        process.terminate()
        process.join(timeout=5)
        # OS lock death only permits inspection; here there was no durable owner.
        self.assertEqual(self.store.status(), status)
        for pipe in (ready_parent, ready_child, stop_parent, stop_child):
            pipe.close()

    def test_actual_owner_death_preserves_attempt_and_blocks_second_process(self):
        status = self.onboard()
        ready_parent, ready_child = self.context.Pipe()
        stop_parent, stop_child = self.context.Pipe()
        process = self.start(owner_then_wait, str(self.path), status.revision, ready_child, stop_child)
        ticket = self.receive(ready_parent)
        persisted = (self.path / r.STATE_FILE).read_bytes()
        process.terminate()
        process.join(timeout=5)
        self.assertFalse(process.is_alive())
        reopened = r.ReservationStore(self.path, HOST)
        self.assertEqual(reopened.status(), ticket.status)
        self.assertEqual((self.path / r.STATE_FILE).read_bytes(), persisted)
        self.assertEqual(reopened.status().phase, r.Phase.CONNECTING)
        self.refuse("phase_mismatch", reopened.acquire, ticket.status.revision, idle())
        barrier, output = self.context.Barrier(1), self.context.Queue()
        second = self.start(contend, str(self.path), ticket.status.revision, barrier, output)
        self.assertEqual(output.get(timeout=15), ("refused", "phase_mismatch"))
        second.join(timeout=5)
        self.assertEqual(second.exitcode, 0)
        uncertain = reopened.require_reconciliation(ticket.status.revision, r.Uncertainty.OWNER_LOST)
        self.assertTrue(uncertain.launch_attempted)
        self.assertEqual(uncertain.phase, r.Phase.RECONCILIATION_REQUIRED)
        self.refuse("generation_mismatch", reopened.reconcile, uncertain.revision,
                    r.ServiceTermination(replace(GENERATION, pid=process.pid), idle()))
        replacement = replace(BINDING, generation=replace(GENERATION, start_id="birth-after-death"))
        available = reopened.reconcile(uncertain.revision, r.ServiceTermination(GENERATION, idle(replacement)))
        self.assertEqual(available.phase, r.Phase.AVAILABLE)
        self.refuse("not_owner", reopened.observe_active, ticket, active(ticket))
        output.close()
        output.join_thread()
        for pipe in (ready_parent, ready_child, stop_parent, stop_child):
            pipe.close()


class WindowsByteLockFixtureTests(unittest.TestCase):
    """API-schedule fixtures; do not assert Linux shim == native Windows behavior."""
    def test_initialized_byte_and_seek_before_nonblocking_lock_and_unlock(self):
        with tempfile.TemporaryFile() as stream:
            fd = stream.fileno()
            stream.write(b"\0")
            stream.flush()
            calls = []

            def locking(actual_fd, mode, count):
                calls.append((mode, count, os.lseek(actual_fd, 0, os.SEEK_CUR), os.fstat(actual_fd).st_size))

            api = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=locking)
            os.lseek(fd, 17, os.SEEK_SET)
            with r._byte_lock(fd, api):
                os.lseek(fd, 9, os.SEEK_SET)
            self.assertEqual(calls, [(2, 1, 0, 1), (0, 1, 0, 1)])

    def test_contention_never_unlocks_unacquired_lock_or_retries(self):
        with tempfile.TemporaryFile() as stream:
            locking = mock.Mock(side_effect=OSError(errno.EACCES, "fixture locked"))
            api = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=locking)
            with self.assertRaises(Refusal) as caught, r._byte_lock(stream.fileno(), api):
                self.fail("contended lock entered")
            self.assertEqual(caught.exception.code, "reservation_contended")
            locking.assert_called_once_with(stream.fileno(), 2, 1)

    def test_body_failure_still_unlocks_and_unknown_lock_error_surfaces(self):
        with tempfile.TemporaryFile() as stream:
            locking = mock.Mock()
            api = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=locking)
            with self.assertRaisesRegex(RuntimeError, "fixture"), r._byte_lock(stream.fileno(), api):
                raise RuntimeError("fixture")
            self.assertEqual(locking.call_args_list,
                             [mock.call(stream.fileno(), 2, 1), mock.call(stream.fileno(), 0, 1)])
            locking.reset_mock(side_effect=True)
            locking.side_effect = OSError(errno.EIO, "fixture")
            with self.assertRaises(OSError), r._byte_lock(stream.fileno(), api):
                self.fail("failed lock entered")
            self.assertEqual(locking.call_count, 1)


class WindowsSecurityFixtureTests(unittest.TestCase):
    """Mock native API records on Linux; no actual NT DACL acceptance claimed."""
    def libraries(self, *, owner=1000, null_acl=False, aces=((0, 1000), (0, 2000), (0, 3000))):
        allocations, buffers, sid_values = [], [], {}

        def pointer(value):
            return value.value if hasattr(value, "value") else value

        def put(ptr, kind, value):
            C.cast(ptr, C.POINTER(kind))[0] = value

        def function(callback):
            return mock.Mock(side_effect=callback)

        def token_information(token, cls, output, length, needed):
            put(needed, W.DWORD, 32)
            if output is None:
                return False
            put(output, C.c_void_p, 1000)
            return True

        def security(handle, kind, flags, out_owner, group, out_acl, sacl, out_descriptor):
            self.assertEqual((handle, kind, flags), (987, 1, 5))
            put(out_owner, C.c_void_p, owner)
            put(out_acl, C.c_void_p, None if null_acl else 5000)
            put(out_descriptor, C.c_void_p, 6000)
            return 0

        def get_ace(acl, index, out):
            ace_type, sid = aces[index]
            buffer = C.create_string_buffer(bytes([ace_type, 0, 20, 0]) + b"\0" * 16)
            buffers.append(buffer)
            address = C.addressof(buffer)
            sid_values[address + 8] = sid
            put(out, C.c_void_p, address)
            return True

        def sid_from_string(text, out):
            put(out, C.c_void_p, {"S-1-5-18": 2000, "S-1-5-32-544": 3000}[text])
            return True

        def equal(a, b):
            left, right = pointer(a), pointer(b)
            return sid_values.get(left, left) == sid_values.get(right, right)

        adv = SimpleNamespace(
            OpenProcessToken=function(lambda process, rights, out: put(out, W.HANDLE, 101) or True),
            GetTokenInformation=function(token_information), GetSecurityInfo=function(security),
            EqualSid=function(equal),
            GetAclInformation=function(lambda acl, out, size, cls: put(out, W.DWORD, len(aces)) or True),
            GetAce=function(get_ace), IsValidSid=function(lambda ptr: pointer(ptr) in sid_values),
            GetLengthSid=function(lambda _: 12), ConvertStringSidToSidW=function(sid_from_string))
        kernel = SimpleNamespace(GetCurrentProcess=function(lambda: 100),
                                 CloseHandle=function(lambda handle: allocations.append(("close", pointer(handle))) or True),
                                 LocalFree=function(lambda ptr: allocations.append(("free", pointer(ptr)))))
        return adv, kernel, allocations

    def check_fixture(self, **kwargs):
        adv, kernel, allocations = self.libraries(**kwargs)
        with mock.patch.object(C, "WinDLL", create=True,
                               side_effect=lambda name, **_: adv if name == "advapi32" else kernel):
            r._windows_private_handle(987)
        self.assertIn(("close", 101), allocations)
        self.assertIn(("free", 6000), allocations)

    def test_only_current_owner_and_user_system_admin_allow_aces_are_accepted_fixture(self):
        self.check_fixture()
        self.check_fixture(aces=((1, 9999), (0, 1000)))  # DENY adds no access.

    def test_foreign_owner_null_dacl_foreign_allow_and_unknown_ace_refuse_fixture(self):
        for kwargs in ({"owner": 9999}, {"null_acl": True}, {"aces": ((0, 9999),)},
                       {"aces": ((5, 1000),)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(Refusal) as caught:
                self.check_fixture(**kwargs)
            self.assertEqual(caught.exception.code, "reservation_unsafe")


if __name__ == "__main__":
    unittest.main(verbosity=2)
