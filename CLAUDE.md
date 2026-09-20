# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.
- When asking questions, present them in the simplest of terms. Do not mask questions in complexities.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Operator-Facing Communication

**Every message meant for the human** (question, status update, progress report, summary, gate prompt, recommendation, handoff) must be **concise, clear, and de-jargonified**.

This is project-wide — every skill, every session, every stage. Pipeline skills also load the full standard from `research-pipeline/_pipeline-config.md` § *Operator-facing communication*.

Rules:
- Lead with what happened and what it means. Not process labels, skill names, or internal acronyms.
- Keep it short: status ≈ ≤8 short lines; summary ≈ ≤15 unless more was asked.
- Translate jargon: plain phrase first; technical label in parentheses only if once useful.
- Decisions need options with one-line consequences and a marked recommendation.
- One plain question at a time (no compound nested asks).
- Keep numbers that matter; say what they mean in words first.
- On-disk technical artifacts may stay precise. Chat that reports them must translate.
- Self-check: would a smart non-specialist owner understand this in ~20 seconds? If not, rewrite.

Bad: "estimand gate blocking_pass false; awaiting operator before XENA final gate."
Good: "The integrity check failed, so we cannot treat the results as clean. Stop, or fix and re-run? I recommend fix and re-run."

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, clarifying questions come before implementation rather than after mistakes, and operator-facing messages stay plain and short.