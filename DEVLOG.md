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