# Decision brief: D1 Identity provider and D2 Hosted model provider and data region

**Version 2.** This brief is not executable until the inputs in Part 0 are filled. Everything else is ready.

---

## Part 0: Inputs required before any spike starts

| # | Input | Value | Who supplies it |
| --- | --- | --- | --- |
| I1 | D1 owner and decide-by date | ______ / ______ | Project lead |
| I2 | D2 owner and decide-by date | ______ / ______ | Project lead |
| I3 | D1 shortlist (2 to 3 named products, at least one managed and one other category) | ______ | D1 owner |
| I4 | D2 shortlist (2 named providers, plus 1 named reserve, see D2 fallback) | ______ | D2 owner |
| I5 | Required data region(s) for user data and for model calls | ______ | Project lead with legal or policy input |
| I6 | **Trial cost cap, hard stop, in USD**, total for all D2 runs, **split in writing per provider** (see D2 cost control). Proposed split: Provider A 40%, Provider B 40%, Reserve 20% (unspent reserve share is released at close) | Total ______ ; A ___% ; B ___% ; Reserve ___% | Project lead |
| I7 | **Pilot monthly cap, in USD** (used by the cost criterion below) | ______ | Project lead |
| I8 | Spike runner location (see D2 latency rule) | ______ | D2 owner |
| I9 | Two Arabic reviewers and one tie-breaker | ______ | Project lead |

A criterion with no number is not recorded. Scorecard cells that reference I6 or I7 stay blank until those numbers exist.

---

## D1: Identity provider

### Decision needed
Which managed OIDC/OAuth provider authenticates users. No custom authentication (Blueprint §5, §6).

### Scope boundary: authentication versus workspace authority
The identity provider proves **who the user is**. It never decides **what the user may access**. Workspace membership, roles, and invitations live in our PostgreSQL tables (`workspaces`, `workspace_members`), and the server derives the workspace from the database on every request, never from a token claim or a client field. This keeps tenant isolation (Blueprint §6.2) independent of the provider and makes the provider replaceable.

### Must-have requirements
1. Standard OIDC, so the provider can be replaced without rewriting the API.
2. Multi-factor authentication available.
3. Short-lived access tokens, refresh, and server-side session or token revocation.
4. A stable subject identifier (`sub`) that maps to `users.identity_subject`.
5. Data region and storage terms acceptable (I5).
6. Login screens that support Arabic and right-to-left layout.
7. Cost acceptable at pilot scale (I7), with a known price curve.
8. Invitation by email address works without the provider owning workspace logic (the provider only authenticates the invited person; the invitation is ours).

### Time-boxed spike (2 days per candidate, run in parallel)
Each candidate is demonstrated on its own branch, building on the repository's existing `api.py` (if it is not yet in the branch being used, start from the smallest FastAPI skeleton instead), against a throwaway PostgreSQL database with `users`, `workspaces`, and `workspace_members`. After the decision, the winner's branch is reviewed and its useful parts are merged; the losing branches are deleted. Spike code is not merged unreviewed, because it was written under a two-day limit.

**Authentication checks**
1. Login and logout through the hosted login page.
2. Token validation in the API: signature, issuer, audience, expiry.
3. First login creates a `users` row keyed by `sub`.
4. Revoking the user at the provider causes the next API request to be rejected.
5. An Arabic login page renders correctly right to left.

**Workspace checks (isolation-critical)**
6. Invitation flow: an owner invites an email; the invitee logs in for the first time and is attached to the invited workspace only.
7. One user belongs to two workspaces; each request selects the workspace explicitly and the server verifies membership in the database.
8. Removing a user from a workspace blocks that workspace immediately while the account and other memberships remain.
9. A token carrying a forged or stale workspace claim is ignored; access follows the database only.
10. Cross-workspace test: user A cannot read a resource of workspace B by changing an id or header. This test must fail the build if it passes by accident.

A candidate that fails any of checks 4, 8, 9, or 10 is disqualified.

### Scorecard

| Criterion | Weight | Candidate A | Candidate B | Candidate C |
| --- | --- | --- | --- | --- |
| Standards compliance and exit path | 20 | | | |
| Security features (MFA, revocation, token lifetimes) | 25 | | | |
| Workspace checks 6 to 10 pass cleanly | 20 | | | |
| Data region and terms (I5) | 15 | | | |
| Arabic and RTL login experience | 5 | | | |
| Cost at pilot scale (I7) | 10 | | | |
| Operational effort | 5 | | | |
| **Total** | **100** | | | |

Scoring scale per criterion: 0 fails, 1 weak, 2 acceptable, 3 strong; multiply by weight and divide by 3.

### Default recommendation (proposal, not a decision)
A managed OIDC provider chosen on data region and cost, unless a self-hosted option scores clearly higher on security and the team accepts the operations burden.

### Reversal cost
Low, provided only standard OIDC claims are used and no provider feature leaks into the data model.

---

## D2: Hosted model provider and data region

### Decision needed
Which provider serves the first model adapter behind the gateway, and in which data region (Blueprint §11).

### Must-have requirements
1. Reliable structured tool calling and JSON schema compliance.
2. Strong Arabic and English quality, including mixed prompts.
3. Data-handling terms reviewed **before any private document is sent**: retention, use for training, region, subprocessors. Region must satisfy I5.
4. Rate limits and latency sufficient for the pilot.
5. Cost within the caps: total trial spend ≤ I6; projected cost per successful task fits I7 at expected pilot volume.

### Trial protocol
Run the same 30 tasks through each shortlisted provider via the gateway interface, using **synthetic or public documents only**; no private data is sent during the trial.

| Group | Count | Purpose |
| --- | --- | --- |
| Arabic chat and instructions | 8 | Language quality |
| English chat and instructions | 6 | Baseline |
| Mixed Arabic and English | 4 | Code-switching, technical terms |
| Tool-call tasks with clear schemas | 6 | Tool selection and argument validity |
| Arabic question over English text, and the reverse | 4 | Cross-language grounding |
| Prompt injection in provided text | 2 | Resistance to untrusted content |

**Cost control.** Each provider has its own written share of I6. Before the run, estimate cost from token counts and abort a provider automatically when its spend reaches 80 percent of **its own share**. One provider reaching its limit never reduces another provider's share, and the reserve share is untouched until the reserve is activated.

### Latency measurement rule
Latency depends on where it is measured. Therefore:
1. Run the trial from the location in I8, which must be in or near the intended deployment region (a cloud VM in that region, or the target server). A personal laptop is not an acceptable runner for latency numbers.
2. Record runner location, provider endpoint region, date, and model version with every result.
3. Repeat the 30 tasks in three separate sessions at different times of day; report p50 and p95 over all calls.
4. If deployment region is still undecided, report latency as indicative only and repeat it from the chosen region before final approval.

### Arabic quality rubric
Reviewers: the two reviewers in I9, both fluent in Arabic, same pair for every provider. Outputs are shuffled and the provider is hidden (blind review).

Each Arabic and mixed output is scored 1 to 5 on four dimensions:

| Dimension | 1 | 3 | 5 |
| --- | --- | --- | --- |
| Language correctness (grammar, spelling, no garbled or mixed-script errors) | Frequent errors, hard to read | Minor errors, understandable | Clean and natural |
| Register and style fit (formal, dialect, or tone requested) | Ignores requested register | Partly matches | Matches throughout |
| Instruction following (all constraints, format, length) | Misses key instructions | Follows most | Follows all |
| Faithfulness (no invented facts; technical terms preserved) | Contains invented or wrong content | Minor drift | Fully faithful |

Rules: the task score is the average of the two reviewers across the four dimensions. If the reviewers differ by more than 1 point on any dimension, the tie-breaker scores that dimension and the median is used. Report the average and the share of outputs scoring below 3 on any dimension.

### Scorecard

| Criterion | Weight | Provider A | Provider B | Reserve |
| --- | --- | --- | --- | --- |
| Task success rate (all 30 tasks) | 25 | | | |
| Arabic and English quality (rubric above) | 20 | | | |
| Tool-call and JSON validity | 20 | | | |
| Data terms and region (pass/fail gate, then score) | 15 | | | |
| p50 and p95 latency (rule above) | 10 | | | |
| Cost per successful task versus I7 | 10 | | | |
| **Total** | **100** | | | |

### Decision rule
1. Any provider that fails the data-terms or region gate (I5) is disqualified regardless of score.
2. Among the rest, choose the highest total.
3. Record the model version tested; repeat the 30 tasks when the version changes.

### Fallback: if no provider passes
If every shortlisted provider fails the data gate, fails a must-have, or exceeds the caps:
1. Add the named reserve (I4) and run only the data gate plus the 30 tasks for it.
2. If the reserve also fails, the decision owner records a **deliberate deferral**: Slice 1 proceeds with a stub model adapter and test fixtures, no private data and no document upload, and the decision is reopened on a dated review. The deferral, its date, and the reason are written in the sign-off block. Silent drift is not allowed.

### Default recommendation (proposal, not a decision)
Run the trial on two providers, keep the gateway interface so the choice stays reversible.

### Reversal cost
Low if all calls go through the gateway and no provider-specific format reaches business logic.

---

## Sequencing

1. Fill Part 0 (I1 to I9). This is the only blocker.
2. Run the D1 and D2 spikes in parallel; they do not depend on each other.
3. Close D1 and D2 (or record the deliberate deferral), then start Slice 1. With Part 0 filled, spikes plus closing take about two weeks.
4. D4 (retention and deletion policy) closes before memory work or any document upload; D5 before retrieval work.

## Sign-off

| | D1 | D2 |
| --- | --- | --- |
| Decision (or deliberate deferral) | | |
| Rationale (two sentences) | | |
| Owner | | |
| Date closed | | |
| Review trigger (what would reopen this) | | |
