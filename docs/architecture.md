# Architecture

One canonical layer model lives here and in the README. It is the
model from the adopted blueprint (docs/blueprint-v2.md, section 4).
Earlier drafts of this repository used a different 8-layer view;
that view is retired and must not be used in new material.

## Layers

| Layer | Responsibility | Status in this repo |
|---|---|---|
| 1. Interface Layer | Web UI, API clients, future messaging channels | Not started |
| 2. API Layer | Authentication context, validation, task endpoints, streaming | Started: task status endpoint (issue 11), platform mode with token auth and database membership checks |
| 3. Orchestrator Layer | Explicit state machine, retries, replans, checkpoints, cancellation, budgets | Built (Step 1 + Slice 2) |
| 4. Model Gateway Layer | Provider routing, structured output, failover, token and cost accounting | Started: gateway with per provider budget shares and a stub adapter; D2 trial harness with the brief's 30 tasks |
| 5. Tools and Skills Layer | Typed tools, permission levels, skills | Started: registry, strict schemas, builtin tools |
| 6. Knowledge Layer | Ingestion, chunking, embeddings, pgvector, citations | Started: search_documents over a corpus interface (issue 12) |
| 7. Memory Layer | Working, episodic, semantic memory | Not started |
| 8. Policy and Safety Layer | Tenant isolation, approvals, redaction, prompt injection defences | Built for the orchestrator core (issues 3, 7, 10) |
| 9. Observability Layer | Traces, audit events, metrics, cost visibility | Started: trace + audit sink (issue 10) |
| 10. Storage and Infrastructure Layer | PostgreSQL, object storage, queues, deployment | Started: checkpoint stores incl. Postgres adapter (issue 6); tenancy tables as a store interface with an in memory reference implementation |

## The execution cycle

```text
Request
  -> Understand Intent
  -> Create Plan
  -> Choose Tool
  -> Permission and approval check (bound to exact arguments)
  -> Execute Tool (per tool timeout, cancellation aware)
  -> Observe Result (usage accounted, output truncated to budget)
  -> Checkpoint
  -> Is Goal Achieved?
       No  -> continue, retry, replan, wait for approval, cancel
       Yes -> Verify and Return (with a summary)
```

Run lifecycle (blueprint section 7.2): queued, running,
waiting_for_approval, completed, failed, cancelled. Stop reasons
are separate: goal_achieved, step_limit, timeout, retries_exhausted,
approval_required, permission_denied, cancelled,
verification_failed, internal_error, budget_exceeded.

## Key invariants

- Every run carries an authenticated TenantContext. Tools receive
  it and re-authorize; identity never comes from model output.
- Approval is bound to a SHA-256 hash of the exact tool arguments.
  Changed arguments require a new approval.
- Checkpoints are written after every observation, redacted, and
  resume preserves counters so limits never reset.
- Retrieved document text is untrusted data, labelled as such.
- Nothing stored or returned exposes raw secrets: the redactor
  runs before storage and before return.
