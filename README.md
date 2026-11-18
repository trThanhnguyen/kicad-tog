# kicad-tog: KiCad Trace-over-Gap Checker

A small, headless Python checker that flags signal traces routed over a gap in their
reference plane. Stable KiCad's DRC (10.0.x) does not catch this defect.

## Why

**Fast edges make every board a high-frequency board.** What matters is the rise time,
not the clock rate. A 100 ps edge carries real energy up to about 0.35/RT ≈ 3.5 GHz,
even on a slow clock.

**At high frequency, the return current follows the trace.** Every signal current returns
through a reference plane: usually GND, sometimes a power plane. At high frequency the
return takes the path of lowest inductance, which is the strip of plane directly under
the trace. About 50% of it flows within ±h of the trace and about 80% within ±3h, where
h is the dielectric height.

**A gap forces a detour.** Sometimes the plane under the trace is cut: a split between
two pours, a slot, or a void left by routing on the plane layer. The return current then
has to go around the cut. The bigger loop adds series inductance right at the crossing.
The gap is short compared with the edge, so it acts as a lumped inductor, and its
impedance (jωL) grows with frequency: the faster the edge, the bigger the bump. The
result:
- **Reflections** at the impedance discontinuity.
- **Crosstalk**: nearby traces that cross the same gap share the detour.
- **EMI**: the enlarged loop and the slot radiate.

**Why KiCad.** KiCad is the leading open-source PCB tool, used from hobby boards to
open-hardware reference designs and, more and more, in industry. Its board files are
plain-text S-expressions, and `kicad-cli` runs headless, so a check can run in CI on
every commit. Commercial flows already check reference-plane continuity: Altium's
*Return Path* rule, Cadence Allegro's *Return Path* constraint, and Ansys SIwave's
*Net Crossing Split*. Stable KiCad does not. Its DRC checks clearance, width,
connectivity and similar rules, but none of them relates a trace to the plane beneath
it. A GND plane with a slot in it is still one connected net, so the board passes DRC.
Upstream KiCad is adding a similar rule for its next major release. Until then,
kicad-tog fills the gap for KiCad 10 users, and afterwards it can serve as a reference
to compare against the native rule.

## What

kicad-tog runs a **reference-plane continuity check**. For each critical net, it checks
that the trace, widened by a margin on each side, lies entirely over the filled copper
of its reference plane.

It is a *geometric* check, not a field solver. It finds **where** the return path
breaks, not **how much** that hurts: it gives no impedance, reflection or EMI numbers.

## How

1. **Parse** the `.kicad_pcb` file with its own S-expression parser (no pcbnew
   dependency). Read each net's tracks, the filled zone polygons on each layer, and the
   stackup (dielectric heights).
2. **Configure**: the critical nets (by name or net class), the reference layer for each
   signal layer, and the margin factor `k`.
3. **Compute**, for each trace segment:
   `uncovered = buffer(segment, w/2 + k·h) − union(filled reference copper)`.
   The margin is k·h because the return current spreads in proportion to h.
    `k = 3` by default (`--margin-k`): the plane must extend at least 3h beyond each trace edge, which keeps about 80% of the high-frequency return current (J(x) ∝ 1/(1+(x/h)²)) over copper. Smaller k misses traces that hug plane edges; larger k flags most traces near pour boundaries.
4. **Filter** out slivers shorter than a minimum span (numerical noise).
5. **Classify** each uncovered span:
   - **split crossing** (error): the plane exists on both ends of the span, so the trace
     leaves the plane and comes back.
   - **edge overhang** (warning): the trace runs off the plane's edge, for example into a
     connector at the board edge.
6. **Report** the net, layer, location, uncovered length and class, as text and JSON.
   The exit code is non-zero on any error, so CI fails the build.

## Validation

- Fixture boards with known answers, in `fixtures/`. Native KiCad DRC passes all of
  them (0 errors, 0 warnings); kicad-tog must flag exactly the bad ones.

  All fixtures are the same 30 × 20 mm KiCad 10 board: two 2-pin headers (J1, J2) joined
  by one 0.2 mm trace, net `SIG`, on F.Cu. The stackup is KiCad's default 4 layers
  (F.Cu / In1.Cu / In2.Cu / B.Cu, 1.6 mm), and the reference plane is the GND zone on
  In1.Cu. So h = 0.1 mm (dielectric 1 only; copper thickness never counts) and
  k·h = 0.3 mm.

  | Fixture | GND plane under `SIG` | Expected result |
  |---|---|---|
  | `continuous` | Solid under the whole trace | 0 violations, exit 0 |
  | `split_crossed` | Slotted: an 8 mm × 1 mm slot crosses under the trace; the plane stays one piece | 1 split crossing (error), exit 1 |
  | `edge_overhang` | Cut short: the plane ends about 13 mm before J2 (J2's ground reaches J1 through a B.Cu trace) | 1 edge overhang (warning), exit 0 |

  Planned next: `near_split` (the plane edge runs alongside the trace, closer than k·h)
  and `pair_one_member` (only one trace of a differential pair crosses a gap).
- The tests run in pytest, on every push, in GitHub Actions and GitLab CI.
- A real board: the Raspberry Pi RP2040 minimal design (USB D+/D− over GND).

## Limits (v1)

- Geometry only; nothing about electrical severity.
- No layer changes: vias, stitching vias, and capacitors bridging a split are ignored.
- One reference plane per signal layer (a stripline's second plane is ignored).
- Zones must be filled and saved before the check runs.