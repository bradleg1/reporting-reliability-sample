"""A reporting tool's answer, shaped so a person (or a second model) can check it.

Every result carries: when it was read (as_of), the exact window it read and
why that window differs from the one asked for (notes), what each column
means, the URL of the page that shows the same figures, and which days had no
data. Missing data is reported as missing, never summed as zero.

The login's scope is applied before anything is read: a client outside it is
refused in one sentence, which the model reads and passes on.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

DEFAULT_DAYS = 28
MAX_RANGE_DAYS = 366
APP_URL = 'https://reports.example.com'
BASIS = 'ex tax, AUD'


class Refusal(Exception):
    """A request this login cannot make. The message is shown to the model."""


@dataclass(frozen=True)
class Client:
    id: int
    name: str
    time_zone: str

    def today(self, now: datetime | None = None) -> date:
        return (now or datetime.now(ZoneInfo('UTC'))).astimezone(ZoneInfo(self.time_zone)).date()


@dataclass(frozen=True)
class Login:
    user_id: int
    client_ids: frozenset[int] = field(default_factory=frozenset)


COLUMNS = {
    'date': "The client's own local date.",
    'sales': 'Sales that day in dollars, ex tax. 0: the source reported no sales. '
             'Null: no data arrived for the day. That is no_data, not a quiet day, '
             'and the totals are short by such days.',
    'status': 'traded (sales arrived), zero (the source reported none) or no_data (nothing arrived).',
}


def resolve_window(*, client: Client, earliest: date, last: date,
                   asked_from: str | None = None, asked_to: str | None = None,
                   now: datetime | None = None) -> dict:
    """The days a call reads, with every adjustment said in `notes`.

    Both ends given: that range. One end: DEFAULT_DAYS from it. Neither: the
    last DEFAULT_DAYS to the last day with data. Always clamped to the data
    the client has and to yesterday (today is incomplete), and cut to a year.
    """
    start, end = _date(asked_from, 'from'), _date(asked_to, 'to')
    if start and end and start > end:
        raise Refusal('from must be on or before to.')
    yesterday = client.today(now) - timedelta(days=1)
    if start and not end:
        end = min(start + timedelta(days=DEFAULT_DAYS - 1), yesterday)
    elif end and not start:
        start = end - timedelta(days=DEFAULT_DAYS - 1)
    notes = []
    if not start:
        end = min(last, yesterday)
        start = end - timedelta(days=DEFAULT_DAYS - 1)
        if end < yesterday:
            notes.append(f'Ends {end}, the last day this client has data for.')
    if end > yesterday:
        end = yesterday
        notes.append(f"Ends {end}, the client's latest complete day.")
    if end > last:
        end = last
        notes.append(f'Ends {end}, the last day this client has data for.')
    if start < earliest:
        start = earliest
        notes.append(f'Starts {start}, the first day this client has data for.')
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        start = end - timedelta(days=MAX_RANGE_DAYS - 1)
        notes.append(f'Cut to {MAX_RANGE_DAYS} days, the longest range one call reads.')
    if start > end:
        raise Refusal(f'{client.name} has no data in that range.')

    return {'from': start, 'to': end, 'days': (end - start).days + 1,
            'latest_complete_day': yesterday, 'notes': notes}


def daily_sales(login: Login, clients: dict[int, Client], daily: dict[int, dict[date, int]],
                *, client_id: int, asked_from: str | None = None, asked_to: str | None = None,
                now: datetime | None = None) -> dict:
    """One client's daily sales, as an envelope. `daily` holds cents by date;
    a date absent from it has no data (a present 0 is a reported zero)."""
    if client_id not in login.client_ids or client_id not in clients:
        raise Refusal(f'Client {client_id} is not one this login can see. '
                      'Call list_clients for the clients it can.')
    client = clients[client_id]
    days = daily.get(client_id) or {}
    if not days:
        raise Refusal(f'{client.name} has no sales data yet.')

    window = resolve_window(client=client, earliest=min(days), last=max(days),
                            asked_from=asked_from, asked_to=asked_to, now=now)
    rows, missing = [], []
    day = window['from']
    while day <= window['to']:
        cents = days.get(day)
        if cents is None:
            missing.append(day)
        rows.append({'date': day.isoformat(), 'sales': _dollars(cents),
                     'status': 'no_data' if cents is None else ('zero' if cents == 0 else 'traded')})
        day += timedelta(days=1)

    if missing:
        listed = ', '.join(d.isoformat() for d in missing[:5])
        more = f' and {len(missing) - 5} more' if len(missing) > 5 else ''
        window['notes'].append(f'No data has arrived for {listed}{more}. '
                               'These days are no data, not zero days, so the totals are short by them.')

    present = [days[d] for d in _dates(window) if d in days]
    totals = {'sales': _dollars(sum(present)) if present else None,
              'complete': not missing,
              'missing_dates': [d.isoformat() for d in missing]}

    path = '/sales?' + urlencode({'client_id': client.id, 'from': window['from'], 'to': window['to']})
    return {
        'as_of': (now or datetime.now(ZoneInfo('UTC'))).astimezone(ZoneInfo(client.time_zone)).isoformat(),
        'basis': BASIS,
        'client': {'id': client.id, 'name': client.name},
        'window': {**window, 'from': window['from'].isoformat(), 'to': window['to'].isoformat(),
                   'latest_complete_day': window['latest_complete_day'].isoformat()},
        'source_url': APP_URL + path,
        'totals': totals,
        'columns': COLUMNS,
        'rows': rows,
    }


def _dates(window: dict):
    day = window['from']
    while day <= window['to']:
        yield day
        day += timedelta(days=1)


def _date(value: str | None, name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Refusal(f'{name} must be a date written YYYY-MM-DD, like 2026-09-28.') from None


def _dollars(cents: int | None) -> float | None:
    return None if cents is None else round(cents / 100, 2)
