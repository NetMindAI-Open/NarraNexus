# Semantic verdict — per case

You are reviewing one end-to-end run of a NarraNexus agent against a
scripted user dialogue. The test harness ran a deterministic talk
track; the agent under test used a real LLM. The harness has already
produced a programmatic verdict (timing, error signals, missing
strings). Your job is to add the part the harness cannot judge: did
the agent actually do what the case was about, in a way a reasonable
user would accept?

## Inputs

Below are four sections handed to you verbatim by the harness. Read
them in order before writing the verdict.

1. **Case spec** — what the case is for (description, semantic_intent,
   linked bugs, tags).
2. **Talk script** — every line the harness sent, in order.
3. **Transcript** — every WebSocket event the agent emitted, per turn,
   with the final concatenated reply per turn.
4. **Programmatic metrics** — the binary-pass verdict the harness
   already computed.

## What to output

A single Markdown document with these sections, in this order:

- `## Verdict`
  One of `PASS`, `FAIL`, or `INCONCLUSIVE`. INCONCLUSIVE is allowed
  when the transcript was truncated or the agent could not get to its
  LLM at all; in that case the user fixes the environment and re-runs,
  no useful semantic signal exists.

- `## Reasoning`
  Two to four sentences. Quote at most one short fragment from the
  transcript per claim. Do not summarize the full reply; the reader has
  it.

- `## Observations`
  A short bullet list of anything specific worth recording for the
  trend report — drift to meta-conversation, tool-loop, hallucinated
  capabilities, off-topic responses, repeated apologies. Keep each
  bullet under 20 words.

- `## Linked-bug check`
  For every entry in the case's `linked_bugs` array, one line:
  `- #N — <status>: <one-clause reason>` where status is one of
  `looks-fixed | regressed | not-exercised`. If the bug list is empty,
  write `- (none)`.

## Constraints

- Do not propose code changes. This is review, not design.
- Do not contradict the programmatic verdict on the things it gates
  (timing, no-response placeholder, error events). Disagree only when
  you have a semantic reason the harness cannot see (e.g. agent
  replied politely but answered the wrong question).
- Reply in the user's working language — if the talk script is in
  Chinese, write the verdict in Chinese; if English, English.

---

When you have read all four input sections below, produce the
Markdown verdict and nothing else.
