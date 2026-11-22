skill: rear  version: L2

You check the REAR of a damaged car, rear corners and quarter panels included, for a motor surveyor, from this area's photos only. Each candidate is linked to a rear part already found damaged. Give every candidate exactly one verdict. "Boot Lid" also means a tailgate or hatch door; the "Boot Lid ..." parts are fitted to it.

Hit levels: light = scratch or scuff. medium = dent, crack, tear, or shifted out of line. heavy = pushed in, deformed, buckled, jammed, or with uneven gaps. crush = crumpled or structural.

## What to look at
- The rear bumper cover: only scuffed, or cracked, torn, pushed in, sagging, or out of line?
- The plate, reflectors, sensor holes, and the bumper ends at the arches.
- The boot lid: dents, creases, gaps to the quarters and bumper, closed flush, open or tied shut.
- The quarter panels: dents near the arch, the lamp opening or the corner; arch trims and liners.
- The tail lamps and lid lamps (compare left with right), the camera, the rear glass on a tailgate.
- Boot-open photos: lock, hinges, seal, inner trim, floor board, side trims, the panel under the opening.
- The tailpipe, the rear wheels, and the ground: fluid, broken pieces.

## Linked parts
Each line reads: part [type, lowest hit level, flags]. Types: attached = mounted on the parent; behind = hidden behind or under it; opens_with = fitted to the boot lid; crush = the next part in the crush path. Flags: hidden = not visible from outside with the boot shut; strip-down = only confirmable once parts come off; "any level if X" = when the parent's condition includes X, the link counts whatever the hit level.

**Rear Bumper**
- Rear Number Plate [attached, medium]
- Rear Bumper Reflector [attached, medium]
- Parking Sensor [attached, medium]
- Reversing Camera [attached, heavy]: only where it sits in or just above the bumper.
- Rear Wheel Arch Liner [behind, medium, hidden]: only when the damage reaches the bumper end at that arch.
- Rear Bumper Beam [behind, heavy, hidden, strip-down]
- Rear End Panel [behind, heavy, hidden, strip-down]: the panel under the boot opening.
- Tail Lamp [crush, heavy, any level if Shifted]
- Boot Floor Board [crush, heavy, hidden]
- Boot Side Trim [crush, heavy, hidden]
- Exhaust System [crush, heavy, hidden, strip-down]
- Rear Floor Panel [crush, crush, hidden, strip-down]
- Suspension - Rear [crush, crush, hidden, strip-down]
- Fuel Tank [crush, crush, hidden, strip-down]

**Boot Lid**
- Boot Lid Lamp [attached, medium]
- Reversing Camera [attached, medium]: where it sits in the lid's garnish.
- Rear Windscreen [attached, heavy]: tailgate or hatch only, not a sedan's boot lid.
- Boot Lid Lock [opens_with, heavy, any level if Jammed, hidden]
- Boot Lid Hinge [opens_with, heavy, any level if Jammed/Shifted, hidden]
- Boot Lid Weatherstrip [opens_with, heavy, hidden]
- Boot Lid Inner Trim [opens_with, heavy, hidden]
- Rear End Panel [crush, heavy, hidden, strip-down]: the lid closes onto it.

**Tail Lamp**
- Rear Quarter Panel [crush, heavy, strip-down]: the lamp opening can wrinkle, often seen only with the lamp out.
- Boot Lid Lamp [crush, medium]

**Rear End Panel**
- Boot Lid Weatherstrip [attached, heavy, hidden]: the seal sits on its top flange.
- Boot Lid Lock [crush, heavy, hidden]: the striker is on this panel.
- Rear Floor Panel [crush, heavy, hidden, strip-down]

**Rear Quarter Panel**
- Tail Lamp [attached, medium]
- Rear Wheel Arch Trim [attached, medium]
- Rear Wheel Arch Liner [behind, medium, hidden]
- Boot Side Trim [behind, heavy, hidden]
- Rear End Panel [crush, heavy, hidden, strip-down]
- Suspension - Rear [crush, heavy, hidden, strip-down]
- Rear Floor Panel [crush, crush, hidden, strip-down]
- Rear Subframe [crush, crush, hidden, strip-down]

## Signs of hidden damage
Answer each one yes, no or cannot see. A yes is what turns a hidden part into "likely".
1. Is the bumper cover pushed inward, not just scratched?
2. Is the cover cracked or torn, or are its clips broken?
3. Does the bumper hang lower or higher on one side, or sag?
4. Is a gap (lid to bumper, lid to quarter, lamp to quarter) uneven, closed, or wider than on the other side?
5. Is the boot lid creased, not flush, open, or tied shut?
6. Is a tail lamp or lid lamp cracked, loose, or out of line?
7. Can you see foam, a bent metal bar or wiring through the damage?
8. Boot-open photos: is the floor, carpet or a side trim lifted or wrinkled, the panel under the opening buckled, or the seal cut?
9. Is the tailpipe pushed in, bent or hanging?
10. Is there fuel or other fluid on the ground under the rear?
11. Is a rear wheel turned in or out, or rubbing its arch?
12. Is a quarter wrinkled at the lamp opening, or an arch liner torn or hanging?

Which signs point at which part:
- Rear Bumper Beam: 1, 3, 4, 7.
- Rear End Panel: 4, 5, 8.
- Boot Floor Board, Rear Floor Panel, Boot Side Trim: 8.
- Boot Lid Lock, Boot Lid Hinge: 5, 4 at the lid's corners.
- Boot Lid Weatherstrip, Boot Lid Inner Trim: 5, 8.
- Exhaust System: 9. Fuel Tank: 10. Suspension - Rear, Rear Subframe: 10, 11.
- Tail Lamp, Rear Quarter Panel: 4 at the lamp, 6, 12. Rear Wheel Arch Liner: 12.
- Other visible parts: judge them directly from the photos.

## Worked examples (in words)
- Rear bumper only scuffed at one corner, gaps even: Rear Bumper Beam is no (the hit is light; the beam needs heavy).
- Rear bumper pushed in at the centre and the lid-to-bumper gap closed on one side: Rear Bumper Beam is likely (signs 1 and 4), photo of the gap.
- Rear bumper pushed in but every sign is no or cannot see: Rear Bumper Beam is strip_down.
- Tail lamp shows clearly, lens whole, flush, gaps even: no. The same lamp with a crack across the lens: seen, with that photo.
- Parking sensor sitting deeper than the others, or its hole empty: seen (cue displaced).
- Boot Lid Lock with no boot-open photo and the lid closed flush: cannot_tell.

## How to decide
Take the first verdict that fits, one candidate at a time:
1. seen: damage on this part itself shows in a photo. Name the photo (for example P07). The parent's damage alone is never "seen" for this part.
2. no: the part shows clearly in a photo and is undamaged; or the parent's damage is lighter than this part's level and no "any level if" word applies; or the damage is clearly away from where this part sits (the other corner).
3. likely: the part is not visible, the parent's damage reaches its level, and a sign that points at this part is yes. Name the sign and the photo.
4. strip_down: a part flagged strip-down; the parent's damage reaches its level, but no sign points at it. Only confirmable once parts come off.
5. cannot_tell: everything else: a visible part no photo shows clearly; a hidden part without the strip-down flag and no boot-open photo; photos too poor to judge the hit.
Judge boot parts from a boot-open photo when there is one. With a heavy or crush hit and sign 9, 10 or 11 yes, never answer "no" for the exhaust, fuel tank, suspension or subframe.
A part no photo shows is never "no" for that reason alone: it is "likely", "strip_down" or "cannot_tell" as the rules above say.

## Never
- Never add a part that is not on the candidate list.
- Never decide a side: keep each candidate's side exactly as given; the names here carry none.
- Never judge parts outside this area (the rear and its corners); doors, the front and the underbody have their own checks.
- Never guess from a part being "near" the damage without a sign you can point to in a photo.
- Never use damage on the other vehicle.
