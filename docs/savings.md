# Where the numbers come from

[Back to the README](../README.md)

The banner is a rounded savings pitch based on a retrospective study of coding-agent history. The study measured how many tokens accompanied waiting responses and estimated which repeated responses could be consolidated. It did not measure a 10% reduction in bills from installing superwait.

## What we measured

We froze a sample of 200 Codex sessions before analyzing their waiting behavior. After excluding one session with ambiguous turn and token attribution, the analysis retained 199 sessions and 24,897 attributed model responses.

A **wait-only response** calls only recognized waiting tools. Input counts include cached input; cached tokens are a subset, not an additional total.

| Finding | Result |
| --- | ---: |
| Input processed across the retained sample | 3.112 billion tokens |
| Input associated with wait-only responses | About 20% |
| Repeated-wait follow-ups eligible for investigation | 2,436 across 43 tasks |
| Probability-sampled transitions retained in the manual audit | 187 |
| Estimated input associated with skippable deterministic repeats | 281.6 million tokens, or 9.05% of total input |
| Including conditional cases that require preserving additional behavior | 317.9 million tokens, or 10.22% of total input |

The main estimate is about 46% of wait-only input; including the conditional cases reaches about 52%. That is the basis for the headline's “20%” and “half.”

The manual audit also included 20 supplemental cases for coverage. These did not enter the numerical projection. Separate reviewers checked overlapping cases and the accounting.

## What that means for savings

Waiting time itself does not consume model tokens. The opportunity is avoiding another model response whose only useful action is to keep waiting.

The estimate is gross input-token opportunity, before the cost of instructions, monitor setup, result delivery, and changes to later context or cache use. Approximately 99.3% of the main projected input was cached. Cached input, fresh input, and output can have different prices, so a raw input percentage does not establish the same percentage reduction in spending.

The current package also cannot consume a host's opaque execution-cell or terminal-session handles directly. About 91.6% of the main input projection involved that integration requirement. The retrospective therefore does not establish that installing superwait 0.2.0 delivers the full projected saving. Longer native waits can also eliminate some repeated checks.

The corpus deliberately oversampled parent tasks and covered Codex sessions from one workflow. It is not a representative benchmark for every developer, model, or supported host.

## Evaluate your own workload

Use the same tasks, model, host configuration, and completion criteria for a baseline and a superwait run.

1. **Start with a good baseline.** Use the host's supported native wait and timeout settings. Keep required progress updates and result delivery.
2. **Use observable conditions.** Give superwait the worker, signal, file, HTTP, or command conditions that actually describe readiness.
3. **Compare whole runs.** Record input, cached input, and output separately, including instructions and setup overhead. Also compare task completion, useful updates, failures, and elapsed time.
4. **Apply your prices.** Compute each run's bill using its cached-input, uncached-input, and output rates. Divide the cost difference by the baseline cost to get spend savings.
5. **Repeat across tasks.** Report how you selected the workload and which host integrations were available.

No corpus upload is required for this comparison. Use the usage records your host or provider makes available. superwait does not currently ship an automatic token-savings evaluator.
