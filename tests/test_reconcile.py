from reliability_sample.reconcile import FailureReason, money_cents, reconcile

SPEC = {'line_sum_equals_subtotal': True, 'subtotal_plus_gst_equals_total': True}


def document(lines, subtotal, gst, total):
    return {'line_items': [{'extended_price': p} for p in lines],
            'totals': {'subtotal': subtotal, 'gst': gst, 'total': total}}


def test_money_parses_printed_forms():
    assert money_cents('$ 1,100.81') == 110081
    assert money_cents('639.48-') == -63948
    assert money_cents('-5') == -500
    assert money_cents('0.29') == 29          # float(0.29) * 100 is 28.999...
    assert money_cents('') is None
    assert money_cents('N/A') is None


def test_balanced_document_passes_with_evidence():
    result = reconcile(document(['100.00', '22.28'], '122.28', '12.23', '134.51'), SPEC)
    assert result['status'] == 'passed'
    assert [c['name'] for c in result['checks']] == ['line_sum_equals_subtotal', 'subtotal_plus_gst_equals_total']
    assert all(c['difference_cents'] == 0 for c in result['checks'])


def test_per_line_rounding_is_tolerated():
    # Printed subtotal 1c under the sum of its two lines: normal supplier rounding.
    assert reconcile(document(['100.01', '22.28'], '122.28', '12.23', '134.51'), SPEC)['status'] == 'passed'


def test_a_misread_line_fails_and_says_by_how_much():
    result = reconcile(document(['100.00', '2.28'], '122.28', '12.23', '134.51'), SPEC)
    assert result['status'] == 'failed'
    assert result['failure_reason'] == FailureReason.LINE_RECONCILIATION_FAILED
    line_check = result['checks'][0]
    assert (line_check['expected_cents'], line_check['actual_cents'], line_check['passed']) == (12228, 10228, False)


def test_a_missing_total_fails_as_missing_not_as_zero():
    result = reconcile(document(['100.00'], '100.00', '10.00', None), SPEC)
    assert result['failure_reason'] == FailureReason.REQUIRED_FIELD_MISSING
    assert 'total' in result['detail']


def test_an_unreadable_line_is_never_counted_as_zero():
    result = reconcile(document(['100.00', 'illegible'], '100.00', '10.00', '110.00'), SPEC)
    assert result['failure_reason'] == FailureReason.REQUIRED_FIELD_MISSING
    assert 'line 2' in result['detail']
