"""The re-check of a draft's suggestions (contract 9 section 4.7): after the
second look has stored them (second_look._store), one question per
suggestion -- is this part of the insured car damaged in these photos? --
on the Decisions API (gemini-decision-1, its own free bucket, ~1 s, no
output tokens), with one batched lite call as the safety net
(settings.intake_check_mode). It labels and sorts the suggestions; it never
adds or removes one and never touches the damage list. Its answer goes on
each suggestion (SecondLookItem.check) and on the step
(SecondLook.check, a CheckRun).

Step 1 of the build: the place in the pipeline and the switch only. The
Gemini plumbing agent (G) writes the calls here and in app/ai/decisions.py.
"""
import logging

from app.ai.keys import AiActor
from app.config import settings

logger = logging.getLogger(__name__)


def run_for_intake(report_id: str, actor: AiActor) -> None:
    """The re-check for a draft the reading and the second look have just
    saved (intake/pipeline._read, right after second_look.run_for_intake).
    Never raises: a failed re-check leaves the suggestions as they are.
    Nothing at all while settings.intake_check_mode is "off" (the
    default): no call, no write."""
    if getattr(settings, "intake_check_mode", "off") == "off":
        return
    # Not built yet (G): a mode other than "off" does nothing either.
    logger.info("Re-check for %s: mode %s is not built yet; the suggestions stay as they are",
                report_id, settings.intake_check_mode)
