from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from reliability_sample.report_envelope import Client, Login, Refusal, daily_sales

NOW = datetime(2026, 10, 23, 1, 0, tzinfo=ZoneInfo('UTC'))      # 23 Oct, 11am in Brisbane
CLIENT = Client(1, 'Sample Plumbing Co', 'Australia/Brisbane')
OTHER = Client(2, 'Another Client', 'Australia/Brisbane')
CLIENTS = {1: CLIENT, 2: OTHER}
LOGIN = Login(user_id=7, client_ids=frozenset({1}))


def days(start, cents_list):
    return {start + timedelta(days=i): c for i, c in enumerate(cents_list) if c is not None}


def test_a_client_outside_the_login_is_refused_before_anything_is_read():
    with pytest.raises(Refusal, match='not one this login can see'):
        daily_sales(LOGIN, CLIENTS, {2: {date(2026, 10, 1): 100}}, client_id=2, now=NOW)


def test_missing_days_are_reported_not_summed_as_zero():
    data = {1: days(date(2026, 10, 1), [10000, 0, None, 5000])}
    result = daily_sales(LOGIN, CLIENTS, data, client_id=1, asked_from='2026-10-01', asked_to='2026-10-04', now=NOW)

    assert [r['status'] for r in result['rows']] == ['traded', 'zero', 'no_data', 'traded']
    assert result['rows'][2]['sales'] is None
    assert result['totals'] == {'sales': 150.0, 'complete': False, 'missing_dates': ['2026-10-03']}
    assert any('not zero days' in note for note in result['window']['notes'])


def test_the_window_is_clamped_and_every_adjustment_is_said():
    data = {1: days(date(2026, 10, 10), [100] * 13)}           # 10 to 22 Oct
    result = daily_sales(LOGIN, CLIENTS, data, client_id=1, asked_from='2026-10-01', asked_to='2026-10-30', now=NOW)

    window = result['window']
    assert (window['from'], window['to']) == ('2026-10-10', '2026-10-22')
    assert window['notes'] == ["Ends 2026-10-22, the client's latest complete day.",
                               'Starts 2026-10-10, the first day this client has data for.']


def test_the_envelope_carries_what_a_reviewer_needs_to_check_it():
    data = {1: days(date(2026, 10, 20), [100, 200, 300])}
    result = daily_sales(LOGIN, CLIENTS, data, client_id=1, now=NOW)

    assert result['as_of'] == '2026-10-23T11:00:00+10:00'
    assert result['basis'] == 'ex tax, AUD'
    assert result['source_url'].endswith('/sales?client_id=1&from=2026-10-20&to=2026-10-22')
    assert set(result['columns']) == {'date', 'sales', 'status'}
    assert result['totals']['sales'] == 6.0


def test_a_badly_written_date_is_refused_in_one_sentence():
    with pytest.raises(Refusal, match='YYYY-MM-DD'):
        daily_sales(LOGIN, CLIENTS, {1: {date(2026, 10, 1): 1}}, client_id=1, asked_from='1/10/2026', now=NOW)
