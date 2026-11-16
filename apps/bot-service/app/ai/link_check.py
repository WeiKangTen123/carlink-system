"""The linked check (contract 10, plans/2026-11-17-linked-parts/
contract10.md section 3): after the linked parts are stored, per damaged
end of the car -- at most settings.intake_link_check_max_calls calls a
case -- one light call on settings.gemini_link_chain (never the strong
model, never a tool) with that end's photos (at most MAX_PHOTOS, medium
resolution), that area's skill file (ai/skills/<area>.md) and the CLOSED
list of its linked parts. For each linked part it answers a cue first, then
a verdict (VERDICTS), a photo, a box and a reason; code then re-buckets or
demotes the part. It never adds a part that isn't on the list and never
sets a side. Its answer goes on each item (SecondLookItem.link_check) and
on the step (SecondLook.link_run, a CheckRun).

Step 0 of the build (S0): the switch only. The links engine agent (R)
writes the call.
"""
import logging

from app.ai.keys import AiActor
from app.config import settings

logger = logging.getLogger(__name__)

# The answer on one linked part (schema.LinkCheck): the cue, then the verdict.
CUES = ("crush", "gap_misfit", "displaced", "cracked", "none_seen", "cannot_see")
VERDICTS = ("seen", "likely", "strip_down", "no", "cannot_tell")
# The photos of one end sent in one call.
MAX_PHOTOS = 8


def run_for_intake(report_id: str, actor: AiActor) -> None:
    """The linked check for a draft whose suggestions have just been stored.
    Never raises: a failed check leaves the linked parts as they are.
    Nothing at all while settings.intake_link_check is "off" (the default):
    no call, no write."""
    mode = getattr(settings, "intake_link_check", "off")
    if mode == "off":
        return
    # Not built yet (R): "lite" does nothing either.
    logger.info("Linked check for %s: mode %s is not built yet; the linked parts stay as they are", report_id, mode)
