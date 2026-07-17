"""Worked few-shot example for the read prompt (plan §4.5).

One fully verified tune shown as input-summary → output-ABC, to teach the
house dialect (adjacency beaming, octave case, triplets, ties, pickup,
reduced key signature with inline accidentals). CLOSE YOUR EYES is chosen
because it exercises all of them.

The benchmark must EXCLUDE whatever tune is used here — reading it back would
be teaching to the test. `EXAMPLE_STEM` names it so the scorer can drop it.
"""

EXAMPLE_STEM = "149_01_CLOSE_YOUR_EYES"

# The verified ABC, shown as the target output for its input summary.
EXAMPLE_ABC = """\
X:1
T:CLOSE YOUR EYES
C:Bernice PETKERE (1933)
O:Chords: Anthologie des grilles de jazz, p. 77 (data/chords/05_annotated/77_01_CLOSE_YOUR_EYES.json). Melody: AGJ melody ms (data/melody/01_crops/149_01_CLOSE_YOUR_EYES.png).
R:standard, medium
M:4/4
L:1/8
K:F
c3 G || "^A" B8- | B4 c3 G | (3B2c2B2 (3G2B2G2 | E2 C2 _A3 E |
G8- | G4 _A3 E | (3G2_A2F2 G4- | G4 c3 G ||
"^A1" B8- | B4 c3 G | (3B2c2B2 (3G2B2G2 | E2 C2 _A3 E |
G8- | G4 _A3 E | (3G2_A2E2 F4- | F4 f3 c ||
"^B" _e8- | e4 f3 c | (3_e2f2e2 (3c2e2c2 | A2 F2 d3 A |
c8- | c8 | _d8- | c4 c3 G |
"^A2" B8- | B4 c3 G | (3B2c2B2 (3G2B2G2 | E2 C2 _A3 E |
G8- | G4 _A3 E | (3G2_A2E2 F4- | F4 c3 G ||"""

EXAMPLE_INPUT_SUMMARY = """\
Tune: CLOSE YOUR EYES (Bernice Petkere, 1933), F minor, printed with ONE flat
(so K:F with inline accidentals). 4/4, L:1/8. Sections and bar counts:
  "^A" 8 bars, "^A1" 8 bars, "^B" 8 bars, "^A2" 8 bars.
There is a two-note pickup (c3 G) before "^A".
Chord anchors (per bar of A): Gm7b5 | C7 | Gm7b5 | C7 | Fm | Gm7b5 / C(b9) | Fm | Fm."""
