"""src/formats — declarative format contracts and the shared renderer interface.

registry.py  : the FORMATS table (purpose, input schema, duration bounds,
               pacing, gates, eligibility, status) plus eligibility/duration
               checks. Declarative: adding a format is data, not a new agent.
renderers.py : the four-method renderer interface and the adapters that put the
               existing v2 (paced.py) and v3 (build_short.py) code behind it.

Adding a third (cinematic) renderer is intentionally NOT done here; it is
registered as `not_eligible` until first-party evidence justifies the build
(Section 16, decision 3).
"""
