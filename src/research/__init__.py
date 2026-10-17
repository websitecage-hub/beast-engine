"""src/research — the evidence layer (Section 12 of the master architecture).

normalize.py  : pull every local research artefact into one schema.
analyze.py    : coverage, duplication, missing-field and outlier analysis.
report.py     : write docs/research/RESEARCH_AUDIT.md.

These modules are pure data transforms with NO network and NO publishing side
effects, so they are safe to run in CI and by hand. They exist so that a claim
about "the research" can be traced to a record and a source URL instead of a
summary paragraph.
"""
