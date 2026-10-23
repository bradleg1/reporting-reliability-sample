# Reporting reliability sample

Three small patterns for AI-assisted reporting that people can check, taken from
production code I wrote and maintain. Standard-library Python, no network calls,
fully tested.

| Module | The question it answers |
|---|---|
| [`reconcile.py`](reliability_sample/reconcile.py) | Do these extracted numbers agree with themselves? |
| [`report_envelope.py`](reliability_sample/report_envelope.py) | Can a person check what a reporting tool told Claude? |
| [`guarded_executor.py`](reliability_sample/guarded_executor.py) | Can an approved change be carried out without acting on stale state? |

## Where it comes from

These are sanitised extracts from **Klariti** (klariti.io), a business-intelligence
product I build for hospitality groups: a Rails app, a Python service that reads
supplier invoices with the Claude API, and a Node.js integration service.

- `reconcile.py` is adapted from the extractor's reconciliation gate (Python).
  Every invoice Claude reads must reconcile to its printed totals before it is used.
- `report_envelope.py` is a Python port of the result shape every tool on Klariti's
  MCP server returns (Ruby in production).
- `guarded_executor.py` is a Python port of the executor behind a tool that removes
  unused codes from a third-party system (Ruby in production). The approval digest
  is added here to show the proposal → approval → execution step end to end.

Supplier, client and venue details, credentials and data have been removed, and the
code is simplified so it runs on its own. It is a sample, not the production code.

## The ideas

**Missing is not zero.** A day with no data is `no_data`, a printed total that cannot
be read is `required_field_missing`. Neither is ever summed as 0, and totals say when
they are short.

**Every answer carries its evidence.** A tool result returns when it was read, the
exact window it read, why that window differs from the one asked for, what each
column means, and the URL of the page that shows the same figures. A reviewer, or a
second model, can check it against the source.

**Scope comes from the login, not the model.** A client outside the login's reach is
refused before anything is read.

**Approve a specific version, then re-check before acting.** An approval is tied to a
digest of the exact proposal, so an edited proposal needs a fresh approval. At
execution time the live state is re-read and every guard re-applied: not found,
ambiguous, activity since the preview, protected, stage limit. Dry run is the default.
Changes go one at a time, are verified afterwards, and each item gets an audit entry.
A transport failure stops the run and marks the item it interrupted as `unknown`
rather than guessing. Nothing retries automatically.

## Run it

```bash
uv run pytest          # or: pip install pytest && python -m pytest
```

## Author

Brad Legassick, AppMate Consulting (Gold Coast, Australia)
