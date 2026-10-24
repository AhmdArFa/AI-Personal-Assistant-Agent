# Skill: business_qa

## Goal

Answer a business owner's questions about their business from their
own data, in Egyptian Arabic, with evidence for every answer.

## Position

This is the first flagship application on the general agent platform,
not a new platform. The platform (router, planner, executor, verifier,
memory, permissions) stays unchanged. This skill adds one tool family
and one workflow on top of it.

## Tools used

- `query_business_data` (permission: READ): runs a read only SELECT
  over the business data. Writes are rejected by validation and by
  the query only connection, not by instruction.

## Workflow

1. Understand the question in Egyptian Arabic. Dialect matters:
   "مبيعات الشهر ده كام؟" and "مين أكتر عميل اشترى؟" are the input
   forms, not formal Arabic.
2. Match the question to one query over the known schema
   (sales: product, customer, branch, quantity, unit_price, sale_date;
   inventory: product, branch, quantity_on_hand, reorder_level).
3. Run the query through `query_business_data`.
4. Verify before answering: does the query match the question, are
   there rows, do the numbers make sense? If anything is not
   confident, stop.
5. Answer in exactly three parts: the short answer, the query that
   was executed, and the source rows (or a summary when many).

## Validation rules

- Never invent a number. An unmatched question, or a query that
  returns nothing, gets the explicit fallback: "مش عارف", with no
  query and no rows claimed.
- Read only, always. Any later write capability (creating orders,
  changing stock) is a separate, high risk tool family that needs
  explicit approval per action. It is out of scope for this skill.
- Answers are scoped: an owner sees everything, an employee sees
  only their scope. The demo data is shared demo data; real tenant
  scoping lands with the real data connector.

## Pilot scope

One data source first (sales or inventory), read only, with a fixed
set of test questions agreed before go live. Expansion order: more
sources, then writes with approvals, then proactive alerts.

## Out of scope

Real business data, real identity providers, real model adapters.
Those belong to the platform decisions (D1/D2), which this skill
does not change.
