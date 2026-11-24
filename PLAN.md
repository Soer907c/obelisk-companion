# Obelisk Companion — fight reliability

Continue the existing Python companion for Idle Obelisk Miner on Windows through Google Play Games. The goal is to stop reliably at the selected target or session limit, including when the screen is hard to read. Bomb automation is a separate future feature.

## Starting point

The project README records three live fights, including two consecutive attempts with cooldown and return navigation. F8 and focus loss were exercised. The last documented offline run passed 17 tests. Real victory and several hours of continuous operation remain unverified.

## Transition criteria

Use these as observable acceptance criteria when reviewing the existing runner. The names below describe game screens and actions.

| From | Evidence required | Action and expected result |
| --- | --- | --- |
| Mines or another ordinary menu | Game in foreground, Obelisk shortcut recognized | Open it and confirm the fight menu before reading readiness. |
| Fight menu | Same target, limits allow another fight, two ready cooldown readings | Start once. Wait for the fight screen before holding the mouse. |
| Fight | Recognized fight screen and valid target position | Hold and verify increasing total damage. On a stall, allow one release and reapply attempt, then stop if damage still stalls. |
| Fight approaching timeout | Two reliable zero readings on the fight timer | Release and use Exit when recognized. A missing reading alone cannot end a fight. |
| Exit menu | Return To Mines is visible | Select it and confirm arrival before beginning another cycle. |
| Possible victory | Winning screen or target change needs evidence | With stop on victory enabled, release and stop before attacking the next target. Record an ambiguous outcome as unknown. |

Throughout the cycle, F8, loss of focus and the runtime limit take precedence over the next action. Release held input on an unknown screen, wait only within the recognition deadline, then stop with a reason. A delayed screen change must not cause repeated clicks.

## Work in order

1. **Check delayed and missing observations offline.** Start with `tests/test_runner.py` and the matching branch in `obelisk/runner.py`. Check coverage before adding a case. Replay a delayed response after Start and an unreadable timer before Exit. Pass when fake input shows one Start, no premature Exit and a released mouse on stop. If a case fails, fix only that transition and rerun the offline suite.
2. **Verify stop on victory.** Inspect existing target-change handling and recorded images. Simulate a target change with stop on victory enabled. Pass offline when the runner releases input, records why it stopped and never starts the next target. Open decision: which visible cue confirms a real victory on this layout? Keep live validation pending until a supervised victory provides a capture and session log that agree.
3. **Validate longer sessions.** After the offline checks, use a bounded, supervised multi-fight session before extending duration. Record configured limits, actual duration, completed fights and the final stop reason. Pass the endurance step only when a run covers the chosen multi-hour duration and the log shows confirmed transitions, respected limits and released input at the end. An early protective stop is a diagnostic result. The exact duration and attempt cap remain open.

## Current handoff

Next task: step 1. Begin with the existing fake-input tests. Record the case checked, result, and next unresolved observation here when the status changes.
