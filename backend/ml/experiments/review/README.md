# Withheld model candidates

These artifacts are offline research outputs, not production models.

`report.json` records the comparison of three source-balanced tree classifiers
and a 64/32-unit neural network. The neural optimizer reached its iteration limit.
`cascade.json` bundles the former production cascade with an 80-tree correction
stage (accept at 0.9; reject nuisance-context decisions below 0.1).

The correction stage improved development recall, but full-page regression
holdouts gained six unwanted URLs without any additional expected URLs. It was
withheld. See [the review](../../reports/model-review.md) for dataset limitations,
reproduction, production fixes and measured results.
