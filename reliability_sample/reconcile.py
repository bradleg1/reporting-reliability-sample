"""Financial reconciliation gate.

An AI-extracted document may only be used when its numbers agree with
themselves: the line items against the printed totals, and the totals block
against its own arithmetic. A failure returns structured evidence and a
reason, never a quietly "corrected" figure.

Tolerances: suppliers round per line (a subtotal printed 1c under the sum of
its lines is normal), so line-sum checks allow 1 cent per line, minimum 2.
The totals block itself must agree within 2 cents.
"""

import re
from enum import StrEnum

_MONEY_RE = re.compile(r'^-?[\d,]+(?:\.\d{1,2})?-?$')


# The printed fields each check reads. They are required whenever the check
# is switched on, so a missing total fails as missing, never as a crash.
_CHECK_FIELDS = {
    'line_sum_equals_subtotal': ('subtotal',),
    'subtotal_plus_gst_equals_total': ('subtotal', 'gst', 'total'),
    'line_sum_plus_gst_equals_total': ('gst', 'total'),
    'subtotal_plus_charges_equals_total': ('subtotal', 'total'),
}


class FailureReason(StrEnum):
    REQUIRED_FIELD_MISSING = 'required_field_missing'
    LINE_RECONCILIATION_FAILED = 'line_reconciliation_failed'
    TOTALS_RECONCILIATION_FAILED = 'totals_reconciliation_failed'


def money_cents(value) -> int | None:
    """Parse a printed money string ("$ 1,100.81", "639.48-") to cents.

    Returns None for anything unparseable. None means "not printed or not
    readable", which is never the same as 0.
    """
    if value is None:
        return None
    s = str(value).strip().replace('$', '').replace(' ', '')
    if not s:
        return None
    negative = s.endswith('-') or s.startswith('-')
    s = s.strip('-')
    if not _MONEY_RE.fullmatch(s):
        return None
    # Integer arithmetic on the digits: float(s) * 100 can be off by a cent.
    whole, _, frac = s.replace(',', '').partition('.')
    cents = int(whole or '0') * 100 + int(frac.ljust(2, '0') or '0')
    return -cents if negative else cents


def reconcile(document: dict, spec: dict) -> dict:
    """Run a supplier's reconciliation spec against an extracted document.

    Returns evidence: {'status': 'passed'|'failed', 'failure_reason',
    'checks': [{name, expected_cents, actual_cents, difference_cents,
    tolerance_cents, passed}]}. Every check is kept, passed or not, so a
    reviewer sees exactly which figure disagreed and by how much.
    """
    totals = document.get('totals') or {}
    items = document.get('line_items') or []

    required = list(spec.get('require', []))
    for name, fields in _CHECK_FIELDS.items():
        if spec.get(name):
            required += [f for f in fields if f not in required]

    for field in required:
        if money_cents(totals.get(field)) is None:
            return _failed(FailureReason.REQUIRED_FIELD_MISSING,
                           f'printed totals field missing or unreadable: {field}')

    line_cents = []
    for i, item in enumerate(items):
        cents = money_cents(item.get('extended_price'))
        if cents is None:
            return _failed(FailureReason.REQUIRED_FIELD_MISSING,
                           f'line {i + 1} extended_price unreadable: '
                           f'{item.get("extended_price")!r}')
        line_cents.append(cents)
    line_sum = sum(line_cents)
    line_tolerance = max(2, len(items))

    checks = []
    failure_reason = None

    def check(name, expected, actual, tolerance, reason):
        nonlocal failure_reason
        difference = abs(expected - actual)
        passed = difference <= tolerance
        checks.append({'name': name, 'expected_cents': expected,
                       'actual_cents': actual, 'difference_cents': difference,
                       'tolerance_cents': tolerance, 'passed': passed})
        if not passed and failure_reason is None:
            failure_reason = reason

    subtotal = money_cents(totals.get('subtotal'))
    gst = money_cents(totals.get('gst'))
    total = money_cents(totals.get('total'))

    if spec.get('line_sum_equals_subtotal'):
        check('line_sum_equals_subtotal', subtotal, line_sum, line_tolerance,
              FailureReason.LINE_RECONCILIATION_FAILED)

    if spec.get('subtotal_plus_gst_equals_total'):
        check('subtotal_plus_gst_equals_total', total, subtotal + gst, 2,
              FailureReason.TOTALS_RECONCILIATION_FAILED)

    if spec.get('line_sum_plus_gst_equals_total'):
        check('line_sum_plus_gst_equals_total', total, line_sum + gst,
              line_tolerance, FailureReason.TOTALS_RECONCILIATION_FAILED)

    if spec.get('subtotal_plus_charges_equals_total'):
        # Tax-inclusive layouts: discounts and freight print between the
        # subtotal and the total with their own sign.
        charges = sum(money_cents(c.get('amount')) or 0
                      for c in (totals.get('charges') or []))
        check('subtotal_plus_charges_equals_total', total, subtotal + charges, 2,
              FailureReason.TOTALS_RECONCILIATION_FAILED)

    return {'status': 'passed' if failure_reason is None else 'failed',
            'failure_reason': failure_reason,
            'checks': checks}


def _failed(reason: FailureReason, detail: str) -> dict:
    return {'status': 'failed', 'failure_reason': reason, 'checks': [], 'detail': detail}
