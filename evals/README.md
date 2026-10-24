# Evaluation suite

Run it:

```bash
python -m evals        # prints PASS or FAIL per task, exit code reflects the result
pytest tests/test_evals.py
```

The suite is a release gate (Blueprint section 16): CI runs it on
every change, and any regression fails the build. It currently
holds 16 tasks across Arabic, English, mixed, document lookup,
ambiguous requests, tool failures, injection in documents,
cross-tenant attempts, approval-required actions, invalid tool
arguments, limits, reliability, and chat. Tasks run in process
against the stub and deterministic fixtures; the same tasks run
against a real provider on demand. Grow the set toward 50 to 100
tasks, keeping every expectation explicit.
