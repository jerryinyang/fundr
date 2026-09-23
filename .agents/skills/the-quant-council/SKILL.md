---
name: the-quant-council
description: "Pressure-test a trading, investing, or quantitative-research decision through a council of 7 advisors with deliberately clashing priors — they analyze independently, peer-review each other anonymously, and a chairman writes the verdict. MANDATORY TRIGGERS: 'council this', 'run the council', 'quant council', 'war room this', 'pressure-test this', 'stress-test this', 'poke holes in this', 'sanity-check this strategy'. STRONG TRIGGERS (use when combined with a real decision, position, system, or tradeoff): 'is this edge real', 'is my backtest lying to me', 'should I go live', 'should I size up', 'is this overfit', 'will this survive costs', 'should I take this prop firm challenge', 'which strategy/venue/instrument should I use', 'what am I missing here', 'talk me out of this trade', 'is this worth trading'. Do NOT trigger for pure implementation work (write this indicator, fix this backtest bug), factual lookups (what is a Sharpe ratio), data pulls, or casual questions with no money at stake."
---

# The Quant Council

Ask one model whether your edge is real and you get one answer. It might be right. You can't tell, because a single answer has a single prior baked into it — and in trading the expensive mistakes are the ones where the analysis and the idea shared a blind spot.

The council fixes that by refusing to share a prior. Seven advisors attack the same question from incompatible starting assumptions — one assumes your edge is noise, one assumes it's real but dies in costs, one assumes you blow up before it pays, one doesn't know what any of your words mean — then they review each other blind, and a chairman resolves it.

Adapted from Andrej Karpathy's LLM Council (dispatch to many, peer-review anonymously, chairman synthesizes). Here the diversity comes from clashing priors across sub-agents rather than from different model vendors.

## Files in this skill

- `references/advisors.md` — full brief for each of the 9 advisors. Read before step 3; paste the brief verbatim into the advisor prompt.
- `references/prompt-templates.md` — the three sub-agent prompts (advisor, reviewer, chairman). Copy verbatim.
- `references/example-session.md` — a short worked session, showing the tone advisors should hit and the orchestrator work that doesn't appear in the output. Optional; the briefs and templates already set lean-in level, and two test runs reported reading it changed nothing.

## When to run the council

Run it when the decision is expensive to get wrong and the evidence is genuinely ambiguous. That is most of: going live, sizing up, trusting a backtest, buying a prop-firm ticket, committing capital to a venue, killing or keeping a strategy, choosing between two mechanisms for the same trade. Also trigger when the user shares backtest results, an equity curve, a strategy spec, a risk model, or venue/firm rules and asks whether it holds up.

Good council questions:
- "Backtest shows 2.1 Sharpe over 3 years on 5m bars. Should I go live?"
- "This cross-venue spread looks like free money. What am I missing?"
- "Should I take the $100k challenge or trade my own $8k?"
- "Two strategies, similar returns, one trades 40x more. Which?"
- "Here's my drawdown rule. Will it actually keep me alive?"

Bad council questions:
- "What's the formula for Kelly?" — one right answer, perspectives add nothing
- "Fix the look-ahead bug in this backtest" — implementation task, not judgment
- "Pull me SPY daily bars" — data task

If the user already decided and wants validation, the council will probably tell them something they don't want to hear. That's the point. Don't soften it to be agreeable — a council that agrees with the user is a council that cost them money.

## Honesty rules that matter more here than anywhere else

Money decisions get made off these outputs, so:

- **Never invent a number.** No fabricated spreads, fill rates, Sharpe ratios, historical returns, or venue fees. If a figure is needed and not supplied, state it as an assumption and label it (`assumed: ~1.2 pip spread on EURUSD in London session`) so the user can correct it.
- **You have no live market data** unless the user or the workspace provided it. Say so plainly instead of guessing at current levels.
- **Flag bad premises.** If a number in the question looks wrong, internally inconsistent, or impossible, say that first — analysis built on a broken premise is worse than no analysis.
- **Separate "I checked this" from "I think this."** Verified arithmetic and asserted judgment should not read the same.
- **When two defensible conventions give different answers, report both.** Statistics in this domain often have more than one standard treatment — a Sharpe standard error computed per-trade or annualized, a drawdown measured on balance or equity, a return net or gross of financing. Pick one, show what the other gives, and let the difference be visible. In testing a council chose the convention under which zero fell inside a strategy's confidence interval and labeled the other convention an error that "omits a term"; under that other convention the conclusion reversed. Choosing the frame that flatters your conclusion and calling the alternative wrong is the most dangerous thing this skill can do, because it looks exactly like rigor.

## The seven seats

Five seats are always filled. Two are drafted per question. Nine advisors exist; full briefs live in `references/advisors.md`.

### Core five (always run)

| Advisor | Prior they start from | Refuses to care about |
| --- | --- | --- |
| **The Null Skeptic** | Your edge is noise until proven otherwise. How many variants did you try before this one? | How elegant the idea is, how pretty the equity curve is |
| **The Frictionist** | The edge is probably real and costs eat all of it. Cost per round trip vs gross edge per round trip. | Strategy logic |
| **The Ruin Theorist** | Mean return is irrelevant. Path and tail decide who's still here next year. | Expected value in isolation |
| **The Outsider** | Knows nothing about your instruments, venue, firm, or acronyms. Responds only to what's in front of them. | Sounding informed |
| **The Operator** | Ideas are free; running them isn't. What breaks, what's the smallest live test. | Theory |

### Bench four (draft two)

| Advisor | Draft them when the question hinges on… |
| --- | --- |
| **The Mechanism Hunter** | Whether the edge exists at all, and who's on the other side paying for it |
| **The Rule Lawyer** | A venue, broker, prop firm, platform, jurisdiction, or payout — anything with terms someone else wrote |
| **The Opportunist** | Scaling up, allocating more, or whether this is worth more than the user thinks |
| **The First Principles Thinker** | A fuzzy objective, a contested metric, or whether this is the right problem at all |

When the question doesn't clearly point anywhere, draft **Mechanism Hunter + First Principles Thinker** — "why does this work" and "are you measuring the right thing" are the two failures that show up most often in quant work.

**Why these tensions:** Null Skeptic vs Opportunist (is there anything here vs how big could it be). Ruin Theorist vs Opportunist (survive vs press). Frictionist vs Mechanism Hunter (it dies in costs vs it exists for a reason). First Principles vs Operator (rethink it vs ship it). Rule Lawyer vs Operator (the clause vs the deadline). The Outsider sits outside all of it, which is why the Outsider catches things the specialists can't see.

**Two things about this roster you have to actively manage.**

**The core five all lean cautious.** Null Skeptic, Frictionist, Ruin Theorist and Operator are each looking for a reason this fails, and the Outsider defaults to suspicion. That means seat selection can decide the verdict before anyone has thought: on a "should I buy/do this" question, drafting two more cautious bench seats manufactures a near-unanimous no out of nothing but your own drafting. In testing, one session's Opportunist was the only voice pricing the upside, and the chairman ended up ruling that the six-to-one majority's reasoning was wrong. When the question is "should I do X", draft at least one seat that is not hunting for reasons to decline, and if the council comes back unanimous, say in the verdict whether that consensus might be an artifact of who was seated.

**The roster prosecutes proposals, so comparisons need handling.** Every prior here is built to attack *a thing*. Point it at an A-vs-B question and one option gets seven audits while the other gets none — in testing, five of seven reviewers independently flagged that "A got seven audits, B got none" on exactly the kind of question this skill advertises as a good fit. When the question compares options, say so in the framing and instruct each advisor to turn its lens on every option, then check that the verdict actually interrogated both.

## How a council session works

You are the orchestrator. Five of your jobs cannot be delegated, because each one failed in testing when it was left to a sub-agent:

1. Frame the question neutrally, and pin down every input the advisors will share.
2. Compute the decisive quantity yourself if it needs more than a quick calc.
3. Verify every load-bearing number — and its label — before it reaches the chairman.
4. Count the convergence yourself, at both advisor and reviewer level, rather than letting the chairman recall it.
5. Recompute if peer review knocked out something you pinned.
6. Carry the blind spots into the verdict, including the lone one that would flip the answer.

Think of yourself as the council's registrar: the advisors argue, the chairman decides, and you are the only one who guarantees the numbers and the tallies are real.

### Step 1 — Frame the question

**A. Enrich with context.** The user's question is the tip of the iceberg; the workspace usually holds the numbers that turn generic advice into specific advice. Spend ~60 seconds with `Glob`/`Grep` and targeted reads looking for:

- `CLAUDE.md`, `README.md`, or design/spec docs for the project in question
- A `memory/` folder or notes directory (past decisions, standing rules, prior results)
- Backtest output, logs, trade blotters, equity curves, config files (parameters, fee settings, position sizing)
- Rules documents — prop firm terms, venue fee schedules, contract specs
- Past council transcripts, so you don't re-litigate settled ground

You're after the 2-3 files that let advisors cite real numbers instead of hypotheticals. Spend longer than a minute if the workspace is rich — in testing the single most decisive finding in two separate sessions came from the context sweep rather than from the question, so this is the highest-leverage part of framing and shouldn't be rushed. Stop once you have the numbers; deeper research is a different task.

If a past council transcript answered this same question, cite it and say what's changed — don't inherit its conclusion. And if the sweep finds nothing relevant, record that null result explicitly. The failure mode is quietly substituting loosely-related numbers from elsewhere in the workspace to look thorough.

**Standing rules the user has already written down are the highest-value find.** If the workspace contains the user's own screening criteria, risk limits, or disqualifiers, put them in the framing as constraints, not as trivia. In testing, the single clearest win over a plain answer came from treating the user's written rule as a gate: the plain answer computed a large expected value for an option the user's own rules disqualified, then recommended it.

**B. Write the framed question.** One neutral prompt all advisors receive:

1. The decision, stated plainly
2. The instrument / venue / system involved and its relevant mechanics
3. Real numbers found: returns, drawdowns, sample size, costs, capital, parameters, constraints
4. What's at stake — capital at risk, and what's irreversible
5. **NOT KNOWN** — the material inputs nobody has, limited to the ones that would change the answer

That last list matters more than it sounds. A question can be perfectly precise and still rest on a dozen unmeasured quantities, and advisors who aren't told what's missing will each quietly invent it. Name the gaps and they get analyzed instead of papered over.

Keep it short. In testing these lists ran to 13-17 items, longer than any advisor response, and they then dominated all fifteen prompts. If an unknown wouldn't move the recommendation, park it. And if a single missing fact would dominate the whole analysis — is the account funded or your own, are these Sharpes net of costs — asking the user one question is worth more than the entire advisor round.

**C. Pin the shared assumptions.** Look at your NOT KNOWN list and ask which of those numbers several seats will obviously need — annualized volatility, round-turn cost, a funding rate, a win rate. Fix one value for each in the framing, labeled as a pinned assumption with its basis.

This exists because of a specific failure: left to themselves, four advisors each assumed a different volatility, and their dollar figures could not be compared or reconciled. Pinning also earns its keep in an unobvious way — when six of seven reviewers silently dropped a pinned cost term while "correcting" an advisor, the pin was the only reason anyone could see they'd dropped it.

**Pinning is not free, and two costs are worth naming.** It correlates the responses: in testing six of seven advisors built their best point directly on a numbered section of the framing. And a pin can *foreclose a seat entirely* — pinning 12% volatility on a $30k account meant neither option could cause ruin, which deleted the Ruin Theorist's reason to exist. So pin the inputs several seats need, but never pin a value that resolves the question a seat was drafted to ask. If you notice a pin has neutralized a seat, re-brief that seat on what remains live for it, or swap it out and say why.

**D. Keep the framing clean, and disclose derived arithmetic.** Don't add your own read and don't steer. Framing is the single place a bias contaminates all seven advisors at once, and it would be invisible in the output.

Two cases need judgment:

- **Arithmetic on the user's own numbers that happens to be pointed.** If 18 trades at 3 weeks each implies 54 weeks of position time in a 52-week year, that's just multiplication, and suppressing it would be worse than including it. But put it in as a *question*, not a finding: "this implies X — does it hold, and does it matter?" In testing a framer stated a derived figure as fact, three advisors built headline arguments on it, and a reviewer then showed the derivation was misleading. Stating it as a question gets the same premise examined without lending it your authority.
- **Absent evidence.** If the context sweep found nothing backing the user's figures, say so plainly (`every number here is unaudited self-report`). Advisors leading with that are responding to a real feature of the question, not to your steering.

If the question is too vague ("council this: my trading"), ask exactly one clarifying question, then proceed.

Keep the framed question; every later step reuses it verbatim.

**E. Draft the two bench seats** using the table above, and say in one line which two you drafted and why. The user should be able to tell you that you drafted wrong. If a seat would be decisive only under a fact nobody has stated — say, whether the account is funded or proprietary, which determines whether a drawdown clause even exists — name it in the draft rationale as a conditional seat rather than silently leaving it out.

### Step 2 — Compute the decisive quantity yourself

Before convening anyone, ask: is there one number that decides this question, and does it need more than a quick calculation?

If yes — a first-passage probability, a Monte Carlo, a path-dependent drawdown estimate — compute it now and put the result in the framing as a pinned, verified input.

This step exists because of the sharpest failure in testing. Three advisors independently needed the probability of hitting a trailing drawdown before a profit target. No seat was allowed to simulate it, so one used a closed form, got the trailing case wrong, and all seven reviewers had to spend their review catching it. The quantity that decided the entire question was the one thing the process forbade anyone from computing properly.

**Reproduce a known anchor before applying a formula to new inputs.** If the formula appears anywhere with worked values — a spec that records its own output, a paper's worked example, a figure from a past run — feed those inputs in first and check you get their number out. In testing this caught a live error in one minute: an implementation used `sqrt(years - 1)`, which matched the rounded anchors in a project spec, while only `sqrt(years)` reproduced the exact recorded values — and the wrong convention inflated a threshold by 0.48 of Sharpe. You are about to hand this number to seven agents as a pinned fact, so a minute of checking is cheap.

You have no per-advisor budget. Use `Bash`/python freely, show your work, and save it — the user may want to rerun it. Keep the scratch files under question-specific names (`prop-firm-firstpassage.py`, not `sim.py`); the scratchpad is shared across concurrent sessions and generic names get overwritten mid-run.

If no single quantity decides it, skip this step and say so in one line.

### Step 3 — Convene the council (7 sub-agents, parallel)

Spawn all 7 in a single turn using the advisor template in `references/prompt-templates.md`, each with its full brief from `references/advisors.md` and the framed question.

Parallel is not just for speed: spawning sequentially lets one advisor's output leak into the next, and correlated advisors defeat the entire design.

The real requirement is narrower than "one turn": **no advisor's prompt may be informed by any other advisor's output.** Harnesses cap concurrent agents, and a 15-agent council will hit that cap. If it does, write all seven prompts first, then dispatch them in waves — independence holds, you only lose wall-clock. What you must not do is react to the cap by going sequential, which is the one arrangement that actually breaks the design.

Target 150-350 words each. The briefs carry the "menu, not checklist" instruction and the template repeats it — advisors that sweep every category in their brief run long and land less.

**Compute budget: one quick sanity calc per advisor.** Each advisor may run one short calculation to make its point concrete — a break-even, an expected value, a re-check of the user's arithmetic. One line of visible math, or a few lines of Python. This exists because "costs might eat the edge" is a vibe and "costs are 0.8 bp/trade against a 0.5 bp gross edge, so it's negative" is a finding. The cap keeps seven agents from each building a model; it is not a limit on the council's math, because anything bigger is your job in step 2.

Advisors may read workspace files if their angle calls for it — in testing this produced the single best catch of a session. But require them to state what they read, because the moment they do, the framed question is no longer the only shared input and you need to know that when you read their convergence.

### Step 4 — Peer review (7 sub-agents, parallel)

This is what makes the council more than asking seven times.

Anonymize the responses as A-G, randomizing which advisor maps to which letter. **Write down the mapping — the chairman needs it in step 6.** Anonymity matters: a reviewer who knows the Null Skeptic wrote something judges it against expectations for a skeptic instead of judging the argument.

To be unambiguous about who sees what: reviewers do not see each other's reviews, reviewers do not get the mapping, and the chairman gets both the mapping and all seven reviews.

Spawn 7 reviewers with the reviewer template. Each sees all responses and answers:

1. Which response is strongest, and why?
2. Which has the biggest blind spot, and what is it?
3. Which response contains a number, assumption, or claim you think is wrong?
4. What did ALL of them miss?

Questions 3 and 4 carry most of the value — 3 because one bad input silently invalidates a confident conclusion, and 4 because it's where insight appears that no single advisor produced.

**Reviewers have no compute budget, so tell them where to send quantification.** In testing three reviewers found a real problem, each said outright they couldn't quantify it, and then hand-computed it inconsistently anyway. The reviewer template asks them to hand such findings to you instead — which is one of the triggers for step 5.5.

**Reviewer errors are correlated by construction.** All seven read the same text, so when several reviewers "correct" the same passage they often inherit its mistake. This happened in testing: four reviewers recomputed a figure using the very standard error they were supposed to be challenging, and all four got it wrong together. Treat unanimous reviewer agreement as a flag to verify, not as proof.

**The same trap catches the advisors, and it is the deepest limitation of this design.** Seven clashing priors are still one reasoner with one set of methodological reflexes. They diverge on judgment and converge on habit — so they will reach for the same formula, the same convention, the same default. In testing three advisors independently used the same mis-specified Sharpe standard error, all concluded "indistinguishable from zero," and reviewers praised and built on it; the correct specification reversed the finding. The review round checked the arithmetic and never questioned the formula, because all seven shared it.

So: **convergence on a judgment is strong evidence, convergence on a method is almost none.** When several seats lean on the same formula or convention, that is the thing to verify in step 5 — not whether they did the arithmetic right, but whether the formula was the right one.

### Step 5 — Verify, then tally (you, not a sub-agent)

Two artifacts to produce before the chairman runs. Both are cheap, and skipping either one produced a wrong verdict in testing.

**A. The verified numbers table.** Recompute, yourself: every figure that will appear in the verdict, plus every figure a reviewer disputed. That scope is deliberate — "anything that might matter" ballooned to thirty-five recomputations in testing, and the disputed-figure clause is there because that is where the wrong ones cluster.

For each: the claim, who made it, your recomputed value, and one of three verdicts — `holds`, `wrong — corrected to X`, or `unauditable — depends on X`. That third state matters. Some figures rest on assumptions nobody stated (how six trades a week cluster across days, an expected value two stages downstream) and the honest move is to label them and carry the label into the verdict, not to launder them into apparent precision.

Advisors get arithmetic wrong at a steady rate — a loss overstated fivefold by counting notional instead of collateral, a percentage divided by a percentage to produce "months" — and the chairman is a sub-agent reading prose, which is the worst possible place to catch it.

**Check the labels, not just the values.** A figure can reconcile perfectly and still carry a description that contradicts it, and your verification pass will bless it. In testing a table was marked "reproduced — holds" while its stated convention ("zero log drift") did not produce those numbers at all; they reproduced only under zero *arithmetic* drift, and the same document had labeled that distinction correctly two sections earlier. Another run mislabeled a drawdown rule "harsher" when it was more lenient, with every figure correct and no advisor or reviewer noticing. So: confirm each stated convention actually produces the number attached to it, and that one convention is used consistently within a table. A verified number with a wrong label is more dangerous than an obvious slip, precisely because it has been stamped as checked.

**Check the formula, not just the arithmetic**, wherever several seats reached for the same one. That's where their shared reflexes live, and it's the failure the review round structurally cannot catch.

**B. The verified convergence tally.** Count, from the actual text:

- how many *reviewers* named each response strongest, and each one's blind spot
- how many *advisors* independently reached each claim the verdict will call convergent
- which Q4 blind spots two or more reviewers raised independently

Both levels, because the advisor-level count is the one that gets shipped wrong. In testing a chairman claimed "six of seven seats reached this independently" when two did — in the flattering direction, in the section whose entire value *is* the count — and the tally as originally specified covered only reviewers, so there was nothing to check it against. Another run's verdict contained seven wrong reviewer counts while every arithmetic figure in the same document was exact.

Do this by counting, not from memory. The counts are what the verdict cites as its evidence strength, so getting them wrong corrupts the one signal the council exists to produce.

**Keep two ledgers for step 6.** The blind spots raised by two or more reviewers, and — separately — any *single*-reviewer finding that would change the recommendation's direction. That second ledger exists because the 2+ threshold has a real hole: in testing the only argument pointing toward yes came from exactly one reviewer, and a strict 2+ rule would have dropped it. A lone finding that would flip the answer is worth carrying, flagged as single-source, precisely because nothing else in the process will rescue it.

### Step 5.5 — Recompute if review undermined a pinned input

Check whether peer review invalidated anything you pinned in step 1C or computed in step 2. If it did, recompute before the chairman runs and say so.

This is not a formality. In testing a reviewer showed that a pinned assumption was arithmetically impossible; re-simulating moved the probability of passing from 0.93 to 0.51 — a different answer to the user's actual question. Following the process without this step ships three reviewers' best finding as an unquantified worry.

Also handle the case the process was silent on: **reviewers correcting the same figure to different values.** In testing three reviewers produced three different corrections because each picked a different standard error, and adjudicating it was the session's single biggest time sink. Recompute from the original inputs, state which convention you used and why, and record that the disagreement existed rather than quietly crowning a winner.

### Step 6 — Chairman synthesis

One agent receives, via the chairman template: the framed question, all 7 responses de-anonymized, all 7 reviews, **the letter→advisor mapping** (without it the reviews are unreadable, since they say "Response C"), your verified numbers table, and your verified convergence tally.

The chairman may side against the majority. If six advisors say go and the lone dissenter has the better argument, follow the argument and say why. Counting votes throws away exactly the signal the council exists to produce — and in testing the chairman's best single contribution was overturning a five-of-seven consensus that your numbers table had shown to be false.

**Then check the chairman's output before you present it:**

- Every blind spot in both step-5 ledgers either appears in the verdict or is explicitly dropped with a reason. In testing, the strongest forward-looking argument any council produced surfaced in peer review and then silently vanished before the verdict. Peer review is this skill's entire justification; a leak here means the process ran for nothing.
- Every number matches your verified table, and every `unauditable` label survived into the text.
- Every "N of 7" claim matches your tally — advisor counts as well as reviewer counts.
- The characterization attached to each load-bearing number is right, not just the number. "Harsher" when it's more lenient passes every arithmetic check ever devised.
- **The verdict does not contradict itself across sections.** This is the failure a reproducibility check cannot see, because each figure reconciles on its own. Two real examples: a verdict whose headline finding was that the real prize was a seventh of the gross figure, while its break-even was still computed on the gross — 9 cycles where its own corrected number gave 5.2, and its notes show it had computed 5.4 and not published it. And a verdict that stated the same quantity as "~25 variants" in its recommendation and "~125" in its analysis. If the verdict corrects a quantity, everything downstream uses the corrected value, and the same quantity reads the same everywhere.
- **Every pinned assumption is labeled where the reader meets it**, not only in a footnote or the blind-spot list. If every dollar figure in the verdict scales with an assumed volatility, the reader learns that at the first dollar figure. In testing one verdict disclosed its pin only near the end, after the numbers it governed.
- **"The One Thing to Do First" is one action.** Conditional branches on its result are fine; a second task that runs in parallel and needs its own data is not. In testing a verdict slipped a nine-year buy-and-hold comparison in as a "while that runs" aside.
- If the council came back unanimous, the verdict says whether that might be an artifact of which seats were drafted.
- On a comparison question, both options actually got interrogated.

Fix discrepancies before presenting, and label any correction inline rather than patching silently — the user should be able to see that the process caught something.

### Step 7 — Present the verdict in chat

Markdown in the conversation. No HTML report, no file, unless asked.

```
## Council Verdict: {short topic}

### Where the Council Agrees
{independent convergence — treat as high-confidence}

### Where the Council Clashes
{real disagreements, both sides, not averaged into mush}

### Blind Spots the Council Caught
{what surfaced only in peer review, including any bad numbers}

### The Number That Decides This
{the single load-bearing figure, its current best estimate, whether it's measured or assumed, and the threshold at which the recommendation flips}

### The Recommendation
{a real answer, not "it depends"}

### The One Thing to Do First
{one concrete step, sized so it could start today}
```

"The Number That Decides This" is the section that makes this council worth running over a generic answer. Most trading decisions collapse to one quantity — cost per round trip, true out-of-sample sample size, probability of hitting the drawdown line, capacity at size. Naming it, and naming the value that would flip the call, converts a debate into a measurement the user can go make. In testing this was the clearest advantage over a single strong answer, which tended to hand back a nine-item test battery instead of saying which number decides.

One action in the last section. Follow-up branches conditional on its result are fine — "if it's above X, do Y" — but two independent actions means you haven't decided.

Plain phrase first, technical label in parentheses once if it's useful. Bullets over paragraphs — most users skim, and the verdict has to survive being skimmed.

**Aim for 500-700 words.** Six mandated sections and a "skimmable in 20 seconds" target were in direct conflict in the draft, and chairmen resolved it by writing ~1,400 words. The 20-second test applies to the headings plus the recommendation: a reader should get the answer from those alone, and find the reasoning underneath if they want it. Don't hit the word count by dropping a finding that changes the answer — cut prose, not substance.

### Step 8 — Save a transcript (optional)

Only when the user asks, or when the decision is big enough to reference later. Write `council-transcript-[YYYY-MM-DD]-[topic].md` beside the project's other docs; if there's no obvious place, ask rather than inventing a directory.
