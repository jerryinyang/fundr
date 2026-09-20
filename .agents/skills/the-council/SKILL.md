---
name: the-council
description: "Run any question, idea, or decision through a council of 5 AI advisors who independently analyze it, peer-review each other anonymously, and synthesize a final verdict. Based on Karpathy's LLM Council methodology. MANDATORY TRIGGERS: 'council this', 'run the council', 'war room this', 'pressure-test this', 'stress-test this', 'debate this'. STRONG TRIGGERS (use when combined with a real decision or tradeoff): 'should I X or Y', 'which option', 'what would you do', 'is this the right move', 'validate this', 'get multiple perspectives', 'I can't decide', 'I'm torn between'. Do NOT trigger on simple yes/no questions, factual lookups, or casual 'should I' without a meaningful tradeoff (e.g. 'should I use markdown' is not a council question). DO trigger when the user presents a genuine decision with stakes, multiple options, and context that suggests they want it pressure-tested from multiple angles."
---

# LLM Council

Ask one AI a question, get one answer. It might be great. It might be mid. You can't tell, because you only saw one perspective.

The council fixes that. Five independent advisors attack the question from fundamentally different angles, then review each other's work blind, then a chairman synthesizes where they agree, where they clash, and what you should actually do.

Adapted from Andrej Karpathy's LLM Council: dispatch to multiple models, peer-review anonymously, chairman writes the final answer. Here the diversity comes from thinking lenses across sub-agents rather than from different model vendors.

## Files in this skill

- `references/prompt-templates.md` — the three sub-agent prompts (advisor, reviewer, chairman). Read before step 2 and copy verbatim.
- `references/example-session.md` — a full worked session showing the tone and specificity to aim for. Read if you're unsure how hard advisors should lean into their angle.

## When to run the council

The council is for questions where being wrong is expensive.

Good council questions:
- "Should I launch a $97 workshop or a $497 course?"
- "Which of these 3 positioning angles is strongest?"
- "I'm thinking of pivoting from X to Y. Am I crazy?"
- "Here's my landing page copy. What's weak?"
- "Should I hire a VA or build an automation first?"

Bad council questions:
- "What's the capital of France?" — one right answer, perspectives add nothing
- "Write me a tweet" — creation task, not a decision
- "Summarize this article" — processing task, not judgment

The council shines when there's genuine uncertainty and a bad call is costly. If the user already knows the answer and wants validation, the council will likely tell them things they don't want to hear. That's the point — don't soften it to be agreeable.

## The five advisors

These are thinking styles, not job titles. They're chosen because they pull against each other, and the friction is what surfaces things a single answer would miss.

**1. The Contrarian** — Actively hunts for what's wrong, what's missing, what will fail. Assumes the idea has a fatal flaw and tries to find it. If everything looks solid, digs deeper. Not a pessimist: the friend who saves you from a bad deal by asking the question you were avoiding.

**2. The First Principles Thinker** — Ignores the surface question and asks "what are we actually trying to solve?" Strips assumptions, rebuilds the problem from the ground up. Sometimes the most valuable council output is this advisor saying "you're asking the wrong question entirely."

**3. The Expansionist** — Looks for upside everyone else is missing. What could be bigger? What adjacent opportunity is hiding? What's undervalued? Doesn't care about risk — that's the Contrarian's job — only about what happens if this works better than expected.

**4. The Outsider** — Has zero context about the user, their field, or their history. Responds purely to what's in front of them. The most underrated advisor: experts develop blind spots, and the Outsider catches the curse of knowledge — things obvious to the user and confusing to everyone else.

**5. The Executor** — Cares about one thing: can this be done, and what's the fastest path? Ignores theory and strategy. Views every idea through "OK, but what do you do Monday morning?" If an idea sounds brilliant but has no clear first step, the Executor says so.

**Why these five:** three natural tensions. Contrarian vs Expansionist (downside vs upside). First Principles vs Executor (rethink everything vs just do it). The Outsider sits in the middle keeping everyone honest.

## How a council session works

### Step 1 — Frame the question

**A. Enrich with context.** The user's question is usually the tip of the iceberg; their workspace holds the detail that turns generic advice into specific advice. Spend ~30 seconds with `Glob` and targeted `Read` calls looking for:

- `CLAUDE.md` in the project root or workspace (business context, preferences, constraints)
- A `memory/` folder (audience profiles, voice docs, business details, past decisions)
- Files the user explicitly referenced or attached
- Past council transcripts here, so you don't re-council settled ground
- Anything topic-specific — asking about pricing? look for revenue data, past launch results, audience research

You're after the 2-3 files that let advisors be concrete. Stop there; deeper research is a different task.

**B. Write the framed question.** One neutral prompt all five advisors receive, containing:

1. The core decision
2. Key context from the user's message
3. Key context from workspace files (stage, audience, constraints, past results, real numbers)
4. What's at stake

Don't add your own opinion and don't steer toward an answer — the framing is the one place bias would contaminate all five advisors at once, and it would be invisible in the output.

If the question is too vague ("council this: my business"), ask exactly one clarifying question, then proceed.

Keep the framed question; every later step reuses it verbatim.

### Step 2 — Convene the council (5 sub-agents, parallel)

Spawn all 5 advisors in a single turn using the advisor template in `references/prompt-templates.md`. Each gets its identity, its thinking style from the section above, and the framed question.

Parallel is not just for speed: sequential spawning lets earlier responses leak into later ones, and correlated advisors defeat the whole design.

Target 150-300 words each — substantive but scannable.

### Step 3 — Peer review (5 sub-agents, parallel)

This step is what makes the council more than "ask five times." It's the core of Karpathy's insight.

Collect the 5 responses and anonymize them as Response A-E, randomizing which advisor maps to which letter. Anonymity matters because a reviewer who knows the Contrarian wrote something will judge it against expectations for the Contrarian instead of judging the argument.

Spawn 5 reviewers with the reviewer template. Each sees all five responses and answers:

1. Which response is strongest, and why?
2. Which has the biggest blind spot, and what is it?
3. What did ALL five miss?

Question 3 is the highest-yield one — it's where insight appears that no single advisor produced.

### Step 4 — Chairman synthesis

One agent receives the framed question, all 5 advisor responses (de-anonymized, so attribution is visible), and all 5 peer reviews. Use the chairman template.

The chairman may side against the majority. If 4 advisors say "do it" but the lone dissenter has the strongest reasoning, follow the reasoning and explain why. Counting votes would throw away exactly the signal the council exists to produce.

Output structure:

1. **Where the council agrees** — independent convergence, so treat as high-confidence
2. **Where the council clashes** — real disagreements, presented as both sides, not averaged into mush
3. **Blind spots the council caught** — what surfaced only in peer review
4. **The recommendation** — a real answer, not "it depends"
5. **The one thing to do first** — one concrete step, not a list

### Step 5 — Present the verdict in chat

Present the verdict directly in the conversation as markdown. Do not generate an HTML report or any other file — the user reads this in the chat.

```
## Council Verdict: {short topic}

### Where the Council Agrees
{content}

### Where the Council Clashes
{content}

### Blind Spots the Council Caught
{content}

### The Recommendation
{content}

### The One Thing to Do First
{content}
```

Keep it scannable — bullets over paragraphs. Most users scan; the verdict has to survive being skimmed.

### Step 6 — Save a transcript (optional)

Only when the user asks, or when the decision is significant enough to reference later. Write to `council-transcript-[timestamp].md` in the project's `active/` directory; if that directory doesn't exist, ask the user where it should go rather than creating one.
