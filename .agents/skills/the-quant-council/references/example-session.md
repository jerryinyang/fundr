# Worked example: counciling a go-live decision

Optional reading. It shows two things the briefs and templates don't: how hard an
advisor should lean into its prior before it starts sounding balanced, and the
orchestrator work that never appears in the output. Numbers are illustrative — note
that each one is either taken from the user's input or explicitly labeled as an
assumption.

**User:** "Council this: 5-minute mean reversion on MNQ. Backtest is 3 years,
620 trades, 2.3 Sharpe, max drawdown 11%, average winner 14 ticks, win rate 61%.
I want to go live with $25k. Am I good?"

**Bench drafted:** Mechanism Hunter (is there a reason intraday MNQ mean-reverts)
and First Principles Thinker (is Sharpe the right target for a single-strategy
account). Rule Lawyer not drafted — own capital, retail futures account, no
third-party terms in play.

Three of the seven responses, to show the range:

**The Null Skeptic:** "620 trades over 3 years is under one a day — that's a small
sample dressed up as a big one, and 2.3 Sharpe on 620 observations has a standard
error wide enough to contain 1.0. The question I need answered before anything else:
how many parameter sets did you test to find this one? Mean reversion on 5m bars has
maybe six knobs — lookback, entry threshold, exit, stop, session filter, size. A
modest grid is thousands of combinations, and the best of thousands of noise draws
looks exactly like a 2.3 Sharpe. Show me the result on data you never looked at
while iterating, and show me the Sharpe of the *median* parameter set, not the best.
If the surface is a spike rather than a plateau, you found a fluke. Also: 2021-2024
on the Nasdaq is one mostly-trending regime with two volatility spikes. Does the
edge exist in each year separately, or does 2022 carry the whole thing?"

**The Frictionist:** "Average winner 14 ticks on MNQ = 14 × $0.50 = $7.00 gross.
Round-turn commission is roughly $1.00-$1.30 retail (assumption — check your
broker). MNQ is typically 1 tick wide but you're a taker on entries, so budget half
a tick to a tick of slippage per side: 1-2 ticks round trip = $0.50-$1.00. That's
about $2.00 of friction against a $7.00 average winner, but the average winner isn't
your edge — your *expectancy* is. At 61% win rate, if losers average 18 ticks
($9.00), expectancy is 0.61 × 7.00 − 0.39 × 9.00 = $0.76 gross per trade. Take out
$2.00 of costs and you're at −$1.24 per trade. You didn't give me the average loser,
so that number is my guess, and it is the entire decision. If your backtest didn't
subtract commission and slippage per trade, it is not a backtest of a strategy, it's
a backtest of a market."

**The Outsider:** "I don't know what MNQ is, what a tick is worth, or what '5-minute
mean reversion' does. Here's what I notice anyway: you described how well it did and
nothing about why it should work. You also asked 'am I good?' rather than 'what
would prove this wrong,' which reads like someone who has already decided. One more
thing — you told us the win rate and the average winner but not the average loser.
In everything else I've ever looked at, the number people leave out is the
unflattering one. Why isn't it here?"

---

## Chairman's verdict

**Where the council agrees:** Nobody argued for going live at $25k — the Skeptic
on statistical grounds, the Frictionist on cost arithmetic, the Ruin Theorist on
leverage, the Operator and First Principles on experiment design all converged
independently on "start smaller." Independent convergence from that many different
priors is the strongest signal this process produces.

**Where the council clashes:** whether there's an edge here at all. The Skeptic says
the sample can't distinguish 2.3 Sharpe from 1.0. The Mechanism Hunter says the
underlying phenomenon is real and well-documented. Both are right, and the variable
underneath the disagreement is regime: a real mechanism that only pays in choppy
markets, backtested over a period containing one, produces exactly this result.

**Blind spots the council caught:** the missing average loser. Four reviewers
independently flagged that expectancy cannot be computed without it, which means
every cost conclusion in this session — including the Frictionist's −$1.24 — rests
on a guess. Second: adverse selection on resting liquidity is invisible in a
bar-based backtest and systematically favorable in it.

**The number that decides this:** net expectancy per trade, in dollars, after real
commission and measured slippage. Currently unknown, because the average loser
wasn't supplied. Using the Frictionist's assumed 18-tick loser it is about −$1.24
per trade; if the real average loser is 12 ticks instead, it flips to about +$1.30.
That single input decides whether this is a business or a donation, and it is sitting
in the user's backtest log right now.

**The recommendation:** don't deploy $25k. Compute net expectancy from the existing
backtest first — it's a five-minute query and it may end the discussion. If it's
positive after real costs, run one contract for 30 days with fill logging. Treat the
2.3 Sharpe as unverified until it survives data you never tuned on, and add a hard
contract cap plus an economic-release flat rule before any live order.

**The one thing to do first:** pull the average loser in ticks from the backtest and
compute `0.61 × avg_winner − 0.39 × avg_loser − round_turn_cost`. If that is
negative, nothing else in this session matters.

---

## What the orchestrator did that isn't visible above

The advisors and the chairman get the credit, but three things behind the scenes
made the session trustworthy, and all three are the orchestrator's job:

- **The missing average loser was named in the framing** under NOT KNOWN, which is
  why four separate seats attacked it instead of each quietly inventing a value.
- **The round-turn cost was pinned** at $2.00 in the framing, so the Frictionist's
  and the Operator's dollar figures were comparable.
- **Every number in the verdict was recomputed** before it shipped. The Frictionist's
  expectancy arithmetic was right; had it been wrong, the chairman reading prose
  would not have caught it.

## What makes this a good session

- No advisor hedged. The Frictionist never said "but the logic looks sound." The
  Null Skeptic never softened its sample-size attack by granting that the mechanism
  is real. That tension is the product, and the chairman is the only one who
  reconciles it.
- Every number is traceable — either from the user's input or labeled as an
  assumption. The Frictionist explicitly flagged that its own conclusion rested on a
  guess, which is what let the chairman make the missing input the headline.
- The most useful catch here came from the advisor with the least context. The
  Outsider couldn't compute anything and still found the load-bearing omission by
  noticing which number was absent. Don't read that as the Outsider always winning
  — in testing this seat was sometimes rated the weakest of the seven, because an
  advisor reasoning without domain knowledge will occasionally call something
  impossible that a mechanism it doesn't know about explains. That trade is worth
  making. A seat that hedges to avoid ever being wrong stops being able to see the
  question the way a stranger with money at risk would, and the reviewers exist to
  catch the misses.
- The chairman didn't average "there's an edge" and "there's no edge" into "maybe."
  It named regime dependence as the variable underneath.
- The final step is one action, it takes five minutes, and it can kill the whole idea
  before any money moves.
