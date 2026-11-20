KiCAD version: 10.0.8
pixi version: 0.63.2
Line ending: forced text line ending = LF

## Log

### 2026-11-16
- README problem statement written; margin k = 3 (≈80% of return current over copper).
- Tutorial board was never saved (79-byte file); P1 uses KiCad's bundled demos instead.

### 2026-11-17
- S-expression parser done (tokenize → parse → child/children), 19 tests, TDD.
- Decision: own parser instead of pcbnew/kiutils, so it runs headless in CI with no KiCad install.
- Quotes kept in tokens, stripped in parse(): a quoted "(" can't be mistaken for a paren.
- Parses pic_programmer (KiCad 10, 0.6 MB) in 0.2 s and Jetson Thor (KiCad 9, 85 MB) in 29.5 s.
  Per-character loop; a regex tokenizer is the fix if Jetson is needed in P7.
- KiCad 9 writes (net 1) plus a net table; KiCad 10 writes (net "VCC"). Handle it in board.py.

### 2026-11-18
- Craft 3 KiCad fixture boards for the checker (known answers).
- Create tests for board loader (`tests\test_board.py`), specify what a board loader must provide
- Provide corresponding `src\kicad_tog\board.py` - conversion from S-expressions to board data structures
- Create tests for the checking part and implementation - the main geometry algorithms 
- Fix: reference copper = every net's fill on ref_layer; constants back to MIN_SPAN 0.05, PAD_CLEARANCE_MAX 2.5

### 2026-11-19
- P5 report.py: text, JSON and PNG output. The figure tests read pixels, because a label box was hiding the very span it labelled.
- cli.py: exit codes 0/1/2. A misspelled net is an error (exit 2), not a pass.
- P6 CI: GitHub Actions runs pytest, then the checker on all three fixtures. split_crossed must exit exactly 1, so a crash (exit 2) can’t pass as a detection. The PNG and JSON reports are uploaded on every run, even a failing one.

### 2026-11-20
- P7 real board: Raspberry Pi CM5 carrier.
- The fixtures passed; the real board exposed four things they couldn't:
  - KiCad 9 net numbers + net table: resolved to names (the skipped P3 test is live).
  - Nets change layers through vias: --layer checks one signal layer.
  - Speed: ~0.4 s per segment from eroding the whole board's copper. STRtree + per-trace window (bounds ± 2r) gives identical results (erode(A ∩ W) = erode(A) ∩ erode(W)); 4 nets 83 s → 14 nets 7 s.
  - KiCad writes pour holes as keyhole outlines whose rings touch themselves; Shapely gave false splits on them. make_valid fixed it.
- Flag-free runs: a plane is a copper layer whose fills cover ≥ 50% of the board (Edge.Cuts bounding box). Each trace is checked against the nearest plane above and below: gap in the nearest = error, in the farther = warning, both errors within 10%. No plane at all = no_reference error.
- Nets: --nets (names or wildcards), else non-Default net classes from the .kicad_pro (patterns + assignments; schematic directive labels end up there), else _P/_N pair names, else exit 2.
- CM5 with no flags: 40 nets from net classes, planes In1/In3/In4, one PNG per plane, 10 s.
- Current known limits: arc tracks are skipped; a gap under a bend is misread as two pad_clearance infos; edge_overhang's 44% In1 pour is not a plane in auto mode.