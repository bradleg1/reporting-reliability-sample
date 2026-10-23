import pytest

from reliability_sample.guarded_executor import (
    Approval, Executor, NotApproved, Proposal, Refused, TransportError,
)


class FakeLive:
    """An in-memory remote system. `refuse` and `fail_on` script its failures."""

    def __init__(self, rows, refuse=(), fail_on=()):
        self.rows = list(rows)
        self.refuse, self.fail_on = set(refuse), set(fail_on)
        self.deleted = []

    def read(self):
        return [dict(row) for row in self.rows]

    def delete(self, ref):
        if ref in self.fail_on:
            raise TransportError('connection reset')
        if ref in self.refuse:
            raise Refused('in use by an open order')
        self.deleted.append(ref)
        self.rows = [row for row in self.rows if row['ref'] != ref]


def row(key, ref, activity=0):
    return {'key': key, 'ref': ref, 'name': f'Item {key}', 'activity': activity}


def approved(keys):
    proposal = Proposal(client_id=1, action='delete_unused_codes', keys=tuple(keys), evidence='no sales in 90 days')
    return proposal, Approval(proposal.digest(), approved_by='reviewer@example.com')


def test_an_edited_proposal_needs_fresh_approval():
    proposal, approval = approved(['A1'])
    edited = Proposal(proposal.client_id, proposal.action, ('A1', 'B2'), proposal.evidence)
    with pytest.raises(NotApproved):
        Executor(FakeLive([row('A1', 'r1')])).run(edited, approval)


def test_dry_run_is_the_default_and_changes_nothing():
    live = FakeLive([row('A1', 'r1')])
    entries = Executor(live).run(*approved(['A1']))
    assert [e.outcome for e in entries] == ['would_delete']
    assert live.deleted == []


def test_every_guard_is_reapplied_against_fresh_state():
    live = FakeLive([
        row('A1', 'r1'),
        row('B2', 'r2', activity=3),          # sold something since the preview
        row('C3', 'r3'), row('C3', 'r3b'),    # two rows now share the key
        row('P9', 'r9'),
        row('Fish &amp; Chips', 'r5'),        # stored encoded; selected plain
    ])
    executor = Executor(live, protected={'P9'}, dry_run=False, sleep=lambda _: None)
    entries = executor.run(*approved(['A1', 'B2', 'C3', 'P9', 'GONE', 'Fish & Chips']))

    assert [(e.key, e.outcome, e.skip_reason) for e in entries] == [
        ('A1', 'deleted', None),
        ('B2', 'skipped', 'has_activity_at_execution'),
        ('C3', 'skipped', 'ambiguous_match'),
        ('P9', 'skipped', 'protected'),
        ('GONE', 'skipped', 'not_found_at_execution'),
        ('Fish & Chips', 'deleted', None),
    ]
    assert live.deleted == ['r1', 'r5']


def test_staged_runs_stop_at_the_limit():
    live = FakeLive([row(k, f'r{k}') for k in 'ABC'])
    entries = Executor(live, stage_limit=2, dry_run=False, sleep=lambda _: None).run(*approved('ABC'))
    assert [e.skip_reason for e in entries] == [None, None, 'stage_limit']


def test_a_refusal_is_recorded_and_the_run_continues():
    live = FakeLive([row('A', 'ra'), row('B', 'rb')], refuse={'ra'})
    entries = Executor(live, dry_run=False, sleep=lambda _: None).run(*approved('AB'))
    assert [(e.outcome, e.detail) for e in entries] == [('refused', 'in use by an open order'), ('deleted', 'rb')]


def test_a_transport_failure_stops_the_run_with_the_audit_intact():
    live = FakeLive([row(k, f'r{k}') for k in 'ABC'], fail_on={'rB'})
    entries = Executor(live, dry_run=False, sleep=lambda _: None).run(*approved('ABC'))
    assert [e.outcome for e in entries] == ['deleted', 'unknown', 'not_attempted']


def test_deletes_are_spaced_one_at_a_time():
    waits = []
    live = FakeLive([row(k, f'r{k}') for k in 'ABC'])
    Executor(live, dry_run=False, spacing_seconds=0.25, sleep=waits.append).run(*approved('ABC'))
    assert waits == [0.25, 0.25]


def test_a_delete_that_did_not_stick_fails_verification():
    class SilentlyIgnores(FakeLive):
        def delete(self, ref):
            self.deleted.append(ref)       # says yes, changes nothing

    live = SilentlyIgnores([row('A', 'ra')])
    entries = Executor(live, dry_run=False, sleep=lambda _: None).run(*approved('A'))
    assert entries[0].outcome == 'verify_failed'
