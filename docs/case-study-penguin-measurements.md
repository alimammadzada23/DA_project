# Case study: environmental field measurements (authentic public data)

Objective: understand biometrics while distinguishing species composition,
within-species associations, missing measurements and inferential assumptions.

Source: Palmer Station LTER, distributed by Horst, Hill & Gorman (2020),
palmerpenguins. Original research: Gorman, Williams & Fraser (2014), PLoS ONE
9(3):e90081. The publisher explicitly states CC0 and requests attribution and
respect for LTER data-use/collaboration expectations. This portfolio analysis is
not represented as new ecological research or an official scientific finding.

## Method and executed results

- 344 source records from 2007–2009. Original CSV retained and SHA-256
  checked against a pinned publisher revision.
- Literal `NA` is explicitly converted to NULL in a reversible version; no
  records are discarded. 342 recorded masses and
  2 unknown masses.
- Species mean recorded mass (g, rounded): {"Gentoo": 5076, "Chinstrap": 3733, "Adelie": 3701}.
- One planned within-Adelie comparison: male minus female mean mass is
  approximately 675 g (73 male,
  73 female observations), model-based 95% Welch interval
  [573, 776] g;
  two-sided p=6.4e-26, Hedges g=2.16.
- Within-Adelie flipper-length/body-mass Pearson r=0.468.
  This within-species calculation avoids interpreting a between-species mixture
  as evidence for the same relationship within every species.
- Independent pandas calculations reconcile row/missing counts, mean mass,
  the sex mean difference and complete-pair correlation.

## Investigation and interpretation

The standalone report shows ranked species means and a labeled biometrics
scatterplot, along with the SQL and selected Adélie evidence. The difference in
means is descriptive; the model-based interval depends on independence and
sampling assumptions. Statistical evidence does not establish mechanism or
population-wide representativeness. The pooled-species scatter is labeled as an
association and is not used for the within-species inferential result.

Practical next investigation: inspect measurement coverage and stratify by
sampling year/island before generalizing. No ecological intervention, business
impact or conservation outcome is claimed.

## Limitations and reproduction

A small observational field dataset, not a random sample of all penguins.
Species/island/year composition, sex-label missingness, repeated-measurement
uncertainty and selection can affect inference. No population incidence,
causality, prediction deployment or commercial outcome is established.

Run `uv run --frozen python scripts/reproduce_public_cases.py --download` and
open its penguins workspace with `INSIGHTPILOT_UNIVERSAL_WORKSPACE`. Complete
calculations, assumptions and source metadata are saved with the generated report.
