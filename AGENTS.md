# Working on Obelisk Companion

Use Danish in chat and English in the application, logs and project documents.

## Keep each change small

- Read the active step in `PLAN.md`, then the relevant function and its closest test. Expand to another file when a caller, shared input guard or failing test makes it relevant.
- Work on one observable behavior at a time. Reuse the current runner, recognition code and Windows input guards. Keep Obelisk fights independent of future bomb automation.
- Use one relevant capture or a short log excerpt around the transition. Refer to files and test names in handoffs. Leave dependency folders, unrelated screenshots and full session logs out of routine context.

## Changes to game input

- Identify the starting screen, evidence permitting the action, expected next observation and bounded failure path. Check focus and stop conditions before sending input.
- Confirm progress with a fresh screen observation or increasing damage. A click, a cursor position or an elapsed delay alone cannot confirm success. Keep unknown readings distinct from zero.
- Preserve F8, session limits and mouse release on stop, error or loss of focus. Keep retries bounded and confirm the current screen before retrying.

## Verification and handoff

- Exercise the affected behavior with recorded images or fake input first. For transition changes, check both the expected path and the ambiguous or delayed observation that could trigger a wrong action.
- From the project root, run `python -m unittest discover -s tests -v` after a behavior change. Use `python tools/check_vision.py` when changing recognition. Distinguish offline evidence from observations in the live game.
- Report the changed behavior, test result and remaining uncertainty briefly. Update only the affected step in `PLAN.md` when a decision or verification result changes. Mark missing evidence as open.
