# Write-up

*At most two pages.*

## What I focused on, and why

I designed around `check.py`'s asymmetries. A wrong "done" costs 1 and a wrong "open" 0, so done needs
P(done) > 2·P(open), the exact break-even. Help is scored the same way, so we act only when two rules agree. Organisation Jaccard is divided by max(#ours, #theirs), so we emit few organisations. Loops carry 38 of the 74
checkable points, so most work went there. No name, number or chat title from the data appears in code or prompts
(`scripts/leak_check.py`); a name-swapped dev run scores 19.2 vs 20.5. **A result up front: run-to-run noise is about
±3 points.** Identical prompts scored 26.5–29.5 quality points over 4 fresh runs (each call can go to a different
provider), so changes needed replicates, a two-level bootstrap, and for the model choice a rule fixed in advance.

## My approach

`./run.sh` writes a valid skeleton first; each stage overwrites it only if `check.validate()` passes; a 25-minute
watchdog keeps the last valid file. Python 3.9 standard library only.

1. **Organisations and chats:** rules (seed names, group-name and member-overlap clusters, one tie per chat).
2. **Circles:** rules, then one batched LLM pass over a short card per 1:1 contact.
3. **Goals:** ~30 of the user's own messages by keyword, one call, each goal backed by message ids.
4. **Open loops:** rule candidates (Latin, Devanagari and Bengali cue words) in the last 16 days, windows with
   context, ~7,000-character calls. The LLM gives source, type, due date, status and help probabilities. Code moves a
   context line to the explicit ask and merges duplicates. Help acts only when two rules give the same action:
   "whose move is it" (someone else's, including every followup → leave unless overdue; the user's → remind, or assist
   if every safety gate passes) and P(action) > 2·P(leave). Otherwise it leaves, saying why.

| Dev (frozen config) | Points | Range, 4 runs | 95% CI (items) |
|---|---|---|---|
| Circles /12 | 5.6 | 5.3–6.2 | 3.8–7.3 |
| Organisations /8 | 1.8 | 1.7–1.8 | 0.5–3.5 |
| Chats /8 | 7.1 | 7.1 | 6.5–7.6 |
| Loops found /14 | 3.4 | 3.4–3.7 | 2.4–4.3 |
| Loops closed right /14 | 2.7 | 2.5–3.6 | 1.5–4.0 |
| Help /10 (25 labels) | 3.2 | 2.4–3.2 | 1.2–5.2 |
| **check.py total /56** (baseline 17.5) | **20.5** | 20.5–21.4 | |

Not adopted (all ≤ 0 or no effect): burst splitting, delegations, runtime name, an organisations call, confidence
cutoffs, stricter merging, consensus, other status thresholds, gpt-oss-20b. Reasoning *medium* gained
+0.91 on loops+help, under the pre-registered +1.0, with 3.5% truncated calls, so we stay on *low*.

**Adaptive behaviour already in place:** cut replies are split and retried; JSON mode falls back to plain text;
retries with backoff and adaptive concurrency; probability- and ownership-gated decisions; OpenRouter provider fallback.
**Future work:** escalate only uncertain loops to medium reasoning (our medium frontier point suggests it could pay
off cheaply) and learn help preferences from the user's corrections.

## What it costs

`openai/gpt-oss-120b` via OpenRouter, reasoning low, listed $0.15 / $0.60 per million input / output tokens
(11 Nov 2026). Final dev run (OpenRouter's own cost data): 97 calls, 275.5K input + 58.4K output tokens, 185 s.
**$0.0170 per 1,000 messages at the listed price** ($0.0061 as routed; baseline $0.038). Loops 95%, circles 4%,
goals 1%.

Dev is the worst case: all of it falls in the 16-day loop window. On the ~17K-message hidden set only those days
are sent (projected; circles and goals 2× dev):

| Messages in the last 16 days | 2,000 | 4,500 | 6,500 | 8,000 | 10,000 | all 17,000 |
|---|---|---|---|---|---|---|
| $ per 1K (listed price) | 0.0023 | 0.0047 | 0.0066 | 0.0080 | 0.0099 | 0.0166 |

Full cost points (≤ $0.0095) hold up to ~9,500 window messages. Wall time: ~4–5 minutes.

## Where it's wrong

Measured on dev with the final run (74 key loops; the key is generated and noisy):

- **Loops: ~38% recall** (28/74). Requests to the user in 1:1 chats 42%; **questions put to the user 9% (1/11)**;
  the user's own followups 66%; group deadlines 20%.
- **Precision ~17% on dev** (132 of 160 not in the key). Many are real asks the key lacks; expect some context lines
  read as tasks.
- **Done loops called open: 14 of 15.** Never a false "done". Often the closure isn't visible in the window.
- **Help** is capped by recall (11 of 25 labelled loops not found). The rule was chosen on all 35 labels including
  10 AI-suggested hold-out labels (2.0/10 there), so no help number is independent; expect some over-acting.
- **Circles ~46%** (19/41): friend 0/5, relative 3/8; friends and relatives with work contact read as colleagues.
- **Organisations 1.8/8:** organisations without a group chat and single-chat companies are missed.
- **Chats:** a group that belongs to a person (a child's class-parents group) is always tied to an organisation;
  dev has none of 40 such groups, so expect a few percent of hidden groups wrong.
- **Goals** 2.9/8 on 3 labels (one run scored 5.3): noise-level.
- **Overfitting:** each adopted change is ≥ 0 in both halves of dev; 3 of 23 variants adopted. Dev help, goals,
  orgs and chats are optimistic; one scoring run can land ±1.5 points away for reasons outside the code.
