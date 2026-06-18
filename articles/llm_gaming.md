# The Government Scores Your Grant Application. AI Can Score Perfectly on All of It.

*Why language models are about to break procurement, and what the detection fingerprint looks like*

---

Every government grant and contract comes with a scoring rubric. Reviewers rate proposals on innovation, feasibility, cost-effectiveness, team qualifications, and a dozen other dimensions. The rubric exists to make evaluation consistent and defensible.

It also creates a target.

If you know exactly how something will be scored, you can optimize for the score rather than the underlying quality. Procurement consultants have done this for decades, writing proposals that hit every checkbox without necessarily proposing great projects. What changes when a language model enters the picture is the ceiling. Human consultants are good at gaming rubrics. A fine-tuned LLM is close to perfect at it.

---

## The Old Version of This Problem

Goodhart's Law states that when a measure becomes a target, it ceases to be a good measure. Applied to government procurement, this means: publish a detailed rubric, and eventually the market produces specialists who optimize for the rubric rather than the work.

This is documented and widespread. NIH grant reviewers privately acknowledge that funded proposals often win on presentation over substance. Defense procurement has an entire consulting ecosystem that exists to translate capabilities into rubric-friendly language. The problem is not new.

But it has historically had a ceiling. Writing a winning proposal takes time, expertise, and judgment. Gaming every scoring dimension simultaneously is hard. A proposal that scores perfectly on cost-effectiveness often scores lower on ambition, and a consultant editing for one criterion tends to weaken another. Human gaming leaves fingerprints.

---

## What Changes With LLMs

A language model fine-tuned on thousands of past winning proposals learns, with high precision, what language correlates with high scores on each dimension. More importantly, it can optimize all dimensions simultaneously, something human writers struggle to do because it requires holding contradictory stylistic goals in mind at once.

The result is a proposal that scores at or near ceiling on every rubric criterion. Innovation: strong. Cost-effectiveness: strong. Team qualifications: strong. Feasibility: strong. The model does not actually know whether the project is good. It knows what a highly-scored proposal looks like.

This is already happening informally. Academics and consultants are using general-purpose LLMs to polish grant applications. The next step, fine-tuning on a corpus of funded proposals within a specific program, requires modest technical resources and is available to any moderately well-funded applicant or consulting firm.

---

## The Detection Fingerprint

LLM-generated proposals are detectable, at least for now.

Real proposals have trade-offs. A team proposing genuinely ambitious work tends to score lower on risk management, because ambitious work is risky. A proposal with a tight, realistic budget tends to score lower on scope. Good reviewers know that a proposal scoring at ceiling on every dimension simultaneously is probably not reflecting reality.

When you measure the variance in scores across rubric dimensions, clean proposals show meaningful spread. LLM-optimized proposals show near-uniform high scores. The standard deviation across dimensions drops below roughly 0.15 on a normalized scale.

In simulation, this fingerprint is detectable with moderate accuracy. Flag proposals in the top quartile of overall scores that also show anomalously low cross-dimension variance, and you catch a meaningful fraction of machine-optimized applications. It is not a perfect filter. A sophisticated adversary can inject artificial variance while maintaining high scores, writing one dimension slightly lower to look more authentic. But it raises the cost of gaming, which is the goal.

---

## Why This Is Different From Normal Cheating

The uncomfortable thing about LLM-assisted proposal gaming is that it is legal. The applicant is not bribing a reviewer or falsifying data. They are writing a very good-looking proposal. The rubric said to score on these criteria. The proposal scores well on these criteria.

This puts the burden of response on the rubric itself rather than on enforcement. You cannot prosecute someone for submitting an optimized proposal. You can only make the rubric harder to optimize against.

The partial solution is a split rubric: publish 60% of the scoring criteria before submission, and draw the remaining 40% from a larger pool, revealing it only after submission closes. Applicants can optimize for the visible portion but not the full score surface. Each procurement round draws different confidential criteria, so the optimization target shifts.

This works, but incompletely. Gaming the visible 60% still provides a substantial advantage. And maintaining a rotating pool of confidential criteria requires institutional discipline that most procurement agencies do not currently have.

---

## The Case Against Treating This as a Crisis

Before accepting that LLMs break procurement, it is worth considering the pushback.

One argument is that better-written proposals are not actually a problem. If an LLM helps a genuinely good research team write a more coherent application, and that team wins a grant they would have lost to a worse-written but equally good competitor, the outcome might be fine. The underlying quality is the same. The presentation improved. Maybe that is acceptable, or even good.

There is also a selection effect worth considering. Fine-tuning a model on past winning proposals requires access to those proposals. In many grant programs, funded applications are public or available on request. In others, they are not. The technique is most available to well-resourced applicants in fields with transparent disclosure practices, which often means large universities and established firms rather than small or new entrants. This might worry you for equity reasons, but it means the most sophisticated gaming may be happening in categories where the applicants were already advantaged. The marginal harm is unclear.

The uniformity detection fingerprint also has a real limitation: it conflates LLM gaming with genuine excellence. A team that is actually strong on every rubric dimension will also show low cross-dimension variance. Flagging them as suspicious punishes quality. Any threshold calibrated to catch most LLM-gamed proposals will also flag some legitimate ones, and the false positive rate matters enormously in a system where wrongful exclusion is both unfair and legally risky.

These are honest objections. The uniformity fingerprint is a useful signal, not a reliable classifier. And the question of whether AI-assisted proposals reduce procurement quality in practice, rather than in theory, is genuinely open. The simulation evidence is suggestive but not definitive.

---

## The Arms Race Problem

The deeper issue is that the generator updates faster than the rubric.

If a procurement agency changes its scoring criteria in response to detected gaming, the information leaks through funded proposals, consultant networks, and public records requests. A fine-tuned model can be updated on new examples within weeks. The rubric changes on a yearly budget cycle, at best.

This asymmetry is not unique to AI. It exists in any detection game where the adversary gets feedback. But AI compresses the feedback loop in a way that makes the arms race harder to win.

No purely technical detection system beats a sufficiently motivated, well-resourced adversary with access to a capable model and a corpus of past successful proposals. The rubric needs to become partially unpredictable, qualitative human review needs to remain in the loop for high-value awards, and post-award performance tracking needs to close the feedback loop. Bad projects that scored perfectly should eventually degrade the signal that produced them.

None of that is a complete solution. It is a collection of mechanisms that together raise the cost of gaming without eliminating it. Which is, realistically, what working on hard problems usually looks like.

---

## The Takeaway

LLMs do not introduce a new category of procurement corruption. They accelerate an existing one and raise its ceiling. The detection fingerprint, uniform high scores across rubric dimensions, is a real signal but an imperfect one, and the argument that AI-assisted writing is harmless when the underlying project is good deserves to be taken seriously. The structural fix, split rubrics with post-submission reveal, addresses the problem at its root rather than chasing the symptom. The arms race continues either way. The goal is to make gaming expensive enough that most actors find it not worth playing, while being honest that the line between gaming and good writing is not always obvious.
