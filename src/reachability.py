"""LLM reachability probe for the test suite (see tests/test_v2.py).

Kept in src/ rather than the test file so the check is the same one the pipeline
uses, not a second implementation that can drift.

WHY: the model-backed tests must SKIP when the writer/critic are unreachable
rather than fail. A network blip in CI is not a content regression, and a suite
that goes red for it teaches people to ignore the suite.
"""
from __future__ import annotations


def writer_up() -> bool:
    """True if the writer model answers."""
    from . import llm
    try:
        out = llm.chat([{"role": "user", "content": "Reply with the single word OK."}],
                       expect_json=False)
        return bool(out)
    except Exception:  # noqa: BLE001
        return False


def critic_up() -> bool:
    """True if the critic model is reachable."""
    from . import critic_model
    try:
        return bool(critic_model.available())
    except Exception:  # noqa: BLE001
        return False


def models_up() -> bool:
    return writer_up() and critic_up()