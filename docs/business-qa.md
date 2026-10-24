# Business Q&A: the first flagship application

**The idea in one line:** a business owner asks any question about
their business in Egyptian Arabic, and the answer comes from their
own data, with its evidence attached.

## Position on the platform

This is not a new platform. It is the first real application on the
general Personal Assistant platform: the same LangGraph core (router,
planner, executor, reflector), tool registry, skill manifests, and
multi layer memory. What this slice adds is one tool family (read
only data tools) and one skill (`business_qa`). Everything else is
reuse.

## The pain, and what is sold

A business owner asks dozens of questions a day: this month's sales,
the top customer, stock that is about to run out, how a branch is
performing. Each answer used to take hours of waiting on someone to
pull it from the data. This product sells time: how long a question
took before, versus how long it takes now. That value is measurable
from the first day of operation.

## How a question becomes an answer

1. **The question, in Egyptian dialect.** "مبيعات الشهر ده كام؟" or
   "مين أكتر عميل اشترى؟" Dialect understanding is a requirement,
   not a nice to have.
2. **Understanding and planning.** The planner decides which table,
   which measure, and which filter (branch, period, category) the
   question needs.
3. **A read only query.** The executor runs a read query through
   `query_business_data`. Writes are impossible at the tool level:
   validation rejects write statements, and the connection itself
   is query only after seeding.
4. **Verification before answering.** The query must match the
   question, rows must exist, and the numbers must make sense. If
   anything is not confident, the answer is "مش عارف" instead of
   an invented number.
5. **The answer, with its evidence.** Every answer has exactly
   three parts: the short answer, the query that was executed (in
   readable form), and the source rows it was computed from, or a
   summary when there are many.
6. **Learning from corrections.** If the owner corrects an answer,
   the correction is recorded as long term memory, so the same
   mistake does not repeat.

## Permissions model

This is what makes companies trust or reject the product, so it is
built from the start:

- **The owner** sees everything.
- **An employee** sees only their scope (their branch, team, or
  customers). A question outside their scope gets a clear refusal,
  not a partial answer.
- **Read first.** All tools in this phase are read only. Any later
  write (creating an order, changing stock) is a high risk action
  that needs explicit approval before it runs.
- **Audit trail.** Every question, answer, and query is recorded,
  so any number can be traced back to its source later.

## Pilot plan

- **One data source first:** sales or inventory, not everything
  at once.
- **Read only**, with no writes of any kind.
- **A fixed set of test questions** agreed with the owner before
  go live, so accuracy is measured, not felt.
- After accuracy holds, expand in order: more sources, then writes
  with approvals, then proactive alerts (for example: stock that
  will run out within a week).

## What does not change

Decisions D1 (identity provider) and D2 (hosted model provider)
are platform decisions, not application decisions. This application
runs on any choice from either shortlist. This slice also adds no
real identity or model adapters, and uses demo data only.

## What is needed to connect real data

1. **Access to the data:** what kind (database, sheets, an existing
   system's API) and how to connect to it securely.
2. **The first business and first data source:** sales or
   inventory, and which branch or scope.
3. **Who the users are and their roles:** the owner alone first,
   or employees with different scopes from the start.

Once those arrive, connection is: wire the read tool, describe the
data schema as a skill, agree the test question set, then run the
pilot.

## Running the demo

```bash
python examples/business_qa_demo.py
```

It answers four Egyptian Arabic questions over seeded demo data
and prints each answer with its query and source rows. Tests live
in `tests/test_business_qa.py`.
