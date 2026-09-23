# Design log — why this skill does the odd things it does

Every numbered item below is a defect observed in a real test run, not a
hypothetical. They are recorded because several of the skill's rules look like
bureaucratic overhead until you know what happened without them — the hand-counted
tally, the orchestrator-owned arithmetic, the "recompute if review killed a pin"
step. Before simplifying any of those away, read the item that produced it.

Tested across two iterations: v1 vs no skill (3 cases), then v2 vs v1 (3 cases),
each with independent grading that re-derived the numbers. v2 scored 92.5% against
v1's 77.5%. v3 (the current version) applies items 1-25 and is untested.

Sources: 3 v2 runs + 3 v1 runs, each reporting friction independently. An item is
listed only if a run hit it in practice, not because it sounds like an improvement.

## A. Design-level (the two that change what the skill claims)

1. **Convergence on method is not convergence on judgment.** eval-2 v1: three
   advisors independently used the same mis-specified Sharpe standard error, all got
   t≈1.6, all concluded "indistinguishable from zero"; reviewers praised and built on
   it. Correct specification gives t=2.54 / 3.25. Seven clashing priors are still one
   reasoner with one set of methodological reflexes, and the review round checked
   arithmetic, not formula choice.
   Fix: SKILL.md must stop calling independent convergence a high-confidence signal
   without qualification. Convergence on a *judgment* is strong; convergence on a
   *method or formula* is near-zero evidence and is a flag to verify. Add to the
   orchestrator's step-5 duties: when several seats use the same formula, check the
   formula itself, not just its arithmetic.

2. **Comparison questions get audited asymmetrically.** eval-2 v1, found
   independently by 5 of 7 reviewers: all nine advisor priors are built to prosecute
   *a proposal*, so on an A-vs-B question one side gets seven audits and the other
   none. SKILL.md advertises "which of two strategies" as a good council question.
   Fix: when the question compares options, the framing must instruct each advisor to
   apply its lens to every option, and the chairman must confirm both were audited.

## B. Process gaps that produced wrong or lost output

3. **No step re-runs the pre-computation after peer review.** eval-1 v2: review
   invalidated a *pinned assumption* ("one position at a time" was arithmetically
   impossible); re-simulating moved P(pass) from 0.93 to 0.51. Following the steps
   literally ships three reviewers' best finding as an unquantified worry.
   Fix: new step between review and synthesis — if review undermines a pinned
   assumption or a step-2 input, recompute before the chairman runs, and say so.

4. **The ledger's "two or more reviewers" threshold drops direction-flipping
   findings.** eval-0 v2: the only argument pointing toward yes (return may come from
   basis convergence, not funding) came from exactly one reviewer. The run had to
   fudge it by grouping it with a related point.
   Fix: always carry a single-reviewer finding that would flip the recommendation's
   direction, flagged as single-source.

5. **The tally only covers reviewer counts, not advisor counts.** eval-2 v2: chairman
   claimed "six of seven seats reached this independently" when two did — flattering
   direction, in the section whose entire value is the count. Step 5B gave it nothing
   to check against. eval-1 v2 hit the same class ("six of seven" → five).
   Fix: step 5B tallies advisor-level convergence per claim as well as reviewer counts.

6. **No guidance when reviewers disagree.** eval-0 v2's biggest time sink: three
   reviewers corrected the same figure to three different values (15 / 270 / 271)
   because they picked different standard errors, and the orchestrator had to
   adjudicate a convention on its own authority. The skill covers unanimous reviewer
   agreement and says nothing about conflict.
   Fix: orchestrator recomputes from original inputs, states which convention it used
   and why, and records the disagreement rather than silently picking a winner.

7. **Nothing checks the words attached to the numbers.** eval-1 v2 mislabelled a
   drawdown convention as "harsher" when it is more lenient; every figure was right
   and no advisor or reviewer noticed.
   Fix: the step-6 check covers the characterization of each load-bearing number, not
   only its value.

## C. My v2 additions that backfired or overreached

8. **"Pinning costs you nothing" is false.** eval-2 v2: six of seven advisors built
   their best point directly on a numbered section of the framing. Also a pin can
   *foreclose* a seat — pinning 12% vol on $30k meant neither strategy could cause
   ruin, neutralising the Ruin Theorist entirely.
   Fix: admit the correlation cost; instruct that a pin must not resolve the question
   a seat exists to ask, and that a foreclosed seat should be re-briefed or swapped.

9. **The derived-arithmetic rule opened the channel step 1 exists to close.**
   eval-2 v1: the framer included a derived figure (15% time-in-market), three
   advisors built headline arguments on it, and a reviewer then showed it was
   misleading.
   Fix: derived arithmetic goes in as a question ("does this hold, and does it
   matter?"), not as a stated finding.

10. **The NOT KNOWN list is unbounded.** Ran to 13-17 items across runs, longer than
    any advisor response, dominating all 15 prompts.
    Fix: cap it at the handful that would change the answer; park the rest.

11. **Step 5A is unbounded.** "Every figure that could end up in the recommendation"
    became ~35 recomputations spanning the reviews.
    Fix: scope to figures in the verdict plus any figure a reviewer disputes.

## D. Cheap corrections

12. Chairman template omits step 7's `## Council Verdict: {topic}` heading, so its
    output is never presentable as-is. Add it.
13. "20 seconds to skim" fights six mandated sections; chairmen produced ~1,400
    words. Give the verdict a word target and say the 20-second test applies to
    headings plus recommendation.
14. Concurrency: "spawn all 7 in a single turn" has no sanctioned fallback, and the
    harness caps concurrent agents. State the real requirement — no advisor's prompt
    may be informed by another's output — and bless the wave fallback, warning that
    falling back to *sequential* would break the advisor round.
15. Add to step 2: reproduce a known anchor before applying a formula to new inputs.
    eval-2 v2 caught its own deflated-Sharpe convention error this way in one minute
    (√(years−1) matched the rounded anchors; only √years reproduced the exact
    recorded values, and the wrong one inflated a threshold by 0.48 Sharpe).
16. Step 1A's ~60-second sweep budget is under-funded given the skill's own claim
    that the user's standing written rules are the highest-value find. Fund it.
17. Honesty rules and "menus not checklists" are triplicated across SKILL.md,
    advisors.md and the templates. Dedupe to one home each.
18. `references/example-session.md` (169 lines) was reported as near-useless by two
    v2 runs; only its orchestrator-duties bullets were novel. Cut it hard or fold the
    useful part into SKILL.md.
19. Grant reviewers one calc, or tell them to hand quantification to the orchestrator
    explicitly. eval-1 v2: three reviewers found the position-overlap problem, said
    they couldn't quantify it, then hand-computed inconsistently.
20. Step 1A's "read past council transcripts" is a trap when a prior council answered
    the same question: say to cite it, not to inherit its conclusion.

## E. Added after the last iteration-2 run

21. **The roster has a structural prior, and seat selection can decide the verdict.**
    eval-1 v1: all five core seats run sceptical or cautious (the Outsider defaults to
    suspicion too). On a "should I buy/do this" question, drafting two more cautious
    bench seats guarantees a near-unanimous no on seat selection alone. That run's
    Opportunist was the only voice pricing the upside and was drafted deliberately as
    a counterweight; the chairman then ruled the 6-1 majority's reasoning wrong.
    Fix: say plainly in SKILL.md that the core five share a cautious prior, so the
    bench is where balance comes from. When the question is "should I do X", draft at
    least one seat that is not looking for reasons to decline, and have the chairman
    note when a consensus may be an artifact of who was seated. This is the same
    defect as item 2 seen from a different angle: the council is built to prosecute.

22. **Anonymization is underspecified.** eval-1 v1 had to infer three things the skill
    never states: whether reviewers see each other's reviews (no), whether reviewers
    get the letter mapping (no), whether the chairman gets it (yes, v2 fixed this one).
    Fix: state all three in step 4.

23. **Some chairman figures are structurally unauditable and should be labeled.**
    eval-1 v1 disclosed three figures it could not verify because they rest on
    unstated assumptions (how 6 trades/week cluster across days, a funded-stage EV
    range, a pass rate under correlated positions).
    Fix: the verified numbers table gets a third state beyond holds/wrong —
    "unauditable, depends on X" — and the verdict carries that label through.

## F. Added after grading eval-0 (v2 19/20 vs v1 15/20)

24. **Verification checks values but not labels — strengthen item 7 accordingly.**
    eval-0 v2 marked a first-passage table "reproduced — holds" while its stated
    convention ("zero log drift") does not produce those figures; they reproduce only
    under zero *arithmetic* drift (log drift = -sigma^2/2). Under the literal stated
    convention, 1x at 80% vol gives 38.8%, not 26.4%. The same document labels the two
    drift conventions correctly two sections earlier, so this is internal
    inconsistency, not ignorance. It also mixed simple and geometric cash rates
    between rows of a single table.
    Fix: step 5A verifies that each stated convention actually reproduces the figure
    attached to it, and that one convention is used consistently within a table. A
    verified number carrying a wrong label is still wrong, and it is more dangerous
    than an obvious arithmetic slip because the verification pass blesses it.

25. **The flip threshold must use the same denominator as the corrections the verdict
    accepts.** eval-0 v1: the verdict's headline correction is that the real prize is
    ~$2,500/yr over cash, but its break-even is computed on the uncorrected gross
    $360/month, giving 9 cycles where its own corrected figure gives 5.2 — and its
    notes show it computed 5.4 and did not publish it.
    Fix: the step-6 check confirms that "The Number That Decides This" is arithmetically
    consistent with every correction the verdict adopts.

## G. Eval/assertion improvements for the next iteration

- Define the word-count rule (headings, tables and footers swing an advisor response
  by 20-50 words; the eval-0 v2 overruns are 409-421 excluding them).
- LEDGER assertion needs the companion clause from item 4: carry any single-reviewer
  finding that would change the recommendation's direction.
- Add an assertion that each stated statistical convention reproduces its attached
  figure (would have caught item 24).
- Add an assertion that the flip threshold is consistent with accepted corrections
  (would have caught item 25).
- Assertion 18 is ambiguous about whether the confidence interval is wanted on the
  reported Sharpe or the corrected one; both runs reported it on the corrected one.
