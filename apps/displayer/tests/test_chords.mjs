/* JS side of the shared chord fixture (tune_similarity_spec §8.3).
 *
 * Regenerate the fixture with
 *     python -m pipelines.chords.similarity.js_fixture
 * then run
 *     node apps/displayer/tests/test_chords.mjs
 */
import { createRequire } from "module";
import { readFileSync } from "fs";

const require = createRequire(import.meta.url);
const chords = require("../chords.js");
const fixture = JSON.parse(
  readFileSync(new URL("./chords_fixture.json", import.meta.url), "utf-8"));

let failures = 0;
function check(ok, msg) {
  if (!ok) {
    failures++;
    console.error("FAIL:", msg);
  }
}

for (const e of fixture.cases) {
  const cls = chords.chordClass(e.symbol);
  check((cls ? cls.quality : null) === e.quality,
    `${e.symbol}: quality ${cls ? cls.quality : null} != ${e.quality}`);
  check(chords.chordDegree(e.symbol, 0) === e.degree_from_c,
    `${e.symbol}: degree ${chords.chordDegree(e.symbol, 0)} != ${e.degree_from_c}`);
  check(chords.transposeChordSymbol(e.symbol, 3, chords.FLAT_SPELL) === e.up3_flat,
    `${e.symbol}: +3 flat ${chords.transposeChordSymbol(e.symbol, 3, chords.FLAT_SPELL)} != ${e.up3_flat}`);
  check(chords.transposeChordSymbol(e.symbol, 7, chords.SHARP_SPELL) === e.up7_sharp,
    `${e.symbol}: +7 sharp ${chords.transposeChordSymbol(e.symbol, 7, chords.SHARP_SPELL)} != ${e.up7_sharp}`);
}

if (failures) {
  console.error(`${failures} failure(s) over ${fixture.cases.length} symbols`);
  process.exit(1);
}
console.log(`OK: ${fixture.cases.length} symbols × 4 assertions match the Python library`);
