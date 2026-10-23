"""Carry out an approved change against a live system, safely.

Nothing from the proposal is trusted except the approved selection of stable
keys. At execution time the executor:

1. checks the approval matches this exact proposal (an edit needs a fresh one),
2. re-reads live state (minutes or days may have passed since the preview),
3. re-resolves every key and re-applies every guard against that fresh state,
4. acts one item at a time with spacing, recording each outcome,
5. re-reads afterwards to verify, and returns a per-item audit.

Dry run is the default. A refusal from the remote system is recorded and the
run continues; a transport failure stops it, with the audit intact, so a
person decides what happens next. Nothing retries automatically: a retry
after partial changes would act on state nobody has looked at.
"""

import hashlib
import html
import json
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol


class NotApproved(Exception):
    pass


class Refused(Exception):
    """The remote system declined this one item."""


class TransportError(Exception):
    """The remote system could not be reached; the run must stop."""


class LiveSystem(Protocol):
    def read(self) -> list[dict]: ...      # rows: {'key', 'ref', 'name', 'activity'}
    def delete(self, ref: str) -> None: ...


@dataclass(frozen=True)
class Proposal:
    client_id: int
    action: str
    keys: tuple[str, ...]
    evidence: str

    def digest(self) -> str:
        body = json.dumps({'client_id': self.client_id, 'action': self.action,
                           'keys': list(self.keys), 'evidence': self.evidence}, sort_keys=True)
        return hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True)
class Approval:
    proposal_digest: str
    approved_by: str


@dataclass
class Entry:
    position: int
    key: str
    outcome: str                  # planned, would_delete, deleted, skipped, refused, unknown, not_attempted, verify_failed
    skip_reason: str | None = None
    detail: str | None = None
    name: str | None = None


def normalise(key: str) -> str:
    """Keys arrive HTML-encoded from some sources and plain from others.
    Decode both sides before comparing, or matches are silently missed."""
    return html.unescape(key or '').strip()


@dataclass
class Executor:
    live: LiveSystem
    protected: set[str] = field(default_factory=set)
    stage_limit: int | None = None
    dry_run: bool = True
    spacing_seconds: float = 0.25
    sleep: Callable[[float], None] = time.sleep

    def run(self, proposal: Proposal, approval: Approval) -> list[Entry]:
        if approval.proposal_digest != proposal.digest():
            raise NotApproved('The approval is for a different version of this proposal.')

        entries = self._plan(proposal, self.live.read())
        if self.dry_run:
            for entry in entries:
                if entry.outcome == 'planned':
                    entry.outcome = 'would_delete'
            return entries

        aborted = self._execute(entries)
        if not aborted:
            self._verify(entries)
        return entries

    def _plan(self, proposal: Proposal, rows: list[dict]) -> list[Entry]:
        index: dict[str, list[dict]] = {}
        for row in rows:
            index.setdefault(normalise(row['key']), []).append(row)
        protected = {normalise(k) for k in self.protected}

        entries, planned = [], 0
        for position, raw in enumerate(proposal.keys, start=1):
            key = normalise(raw)
            matches = index.get(key, [])
            entry = Entry(position, key, 'skipped')
            if not matches:
                entry.skip_reason = 'not_found_at_execution'
            elif len(matches) > 1:
                entry.skip_reason = 'ambiguous_match'
            else:
                row = matches[0]
                entry.name = row.get('name')
                if row.get('activity'):
                    entry.skip_reason = 'has_activity_at_execution'
                elif key in protected:
                    entry.skip_reason = 'protected'
                elif self.stage_limit is not None and planned >= self.stage_limit:
                    entry.skip_reason = 'stage_limit'
                else:
                    planned += 1
                    entry.outcome = 'planned'
                    entry.detail = row['ref']   # resolved now, from the fresh read
            entries.append(entry)
        return entries

    def _execute(self, entries: list[Entry]) -> bool:
        first = True
        for i, entry in enumerate(entries):
            if entry.outcome != 'planned':
                continue
            if not first:
                self.sleep(self.spacing_seconds)
            first = False
            try:
                self.live.delete(entry.detail)
                entry.outcome = 'deleted'
            except Refused as e:
                entry.outcome, entry.detail = 'refused', str(e)
            except TransportError as e:
                # Unknown whether it landed: say so, never guess.
                entry.outcome, entry.detail = 'unknown', f'transport failure: {e}'
                for rest in entries[i + 1:]:
                    if rest.outcome == 'planned':
                        rest.outcome = 'not_attempted'
                return True
        return False

    def _verify(self, entries: list[Entry]) -> None:
        still_there = {normalise(row['key']) for row in self.live.read()}
        for entry in entries:
            if entry.outcome == 'deleted' and entry.key in still_there:
                entry.outcome = 'verify_failed'
