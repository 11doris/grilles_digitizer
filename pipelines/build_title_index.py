"""Match chord sheets (data/chords/01_crops/) to melody sheets (data/melody/01_crops/) by title.

Emits data/title_index.csv in wide format (one row per tune) flagging which sheets
have a counterpart in the other dataset. Page numbers differ between datasets,
so matching is on normalized title, not page. Re-run any time the crop folders
change:  python pipelines/build_title_index.py
"""
import os, re, csv
from difflib import SequenceMatcher

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
CHORDS = os.path.join(ROOT, "data", "chords", "01_crops")
MELODY = os.path.join(ROOT, "data", "melody", "01_crops")
OUT = os.path.join(ROOT, "data", "title_index.csv")


def parse(dirpath):
    rows = []
    for fn in os.listdir(dirpath):
        if not fn.lower().endswith(".png"):
            continue
        m = re.match(r"^(\d+)_(\d+)_(.+)\.png$", fn, re.IGNORECASE)
        if not m:
            print("SKIP unparsed:", fn)
            continue
        page, idx, title = m.group(1), m.group(2), m.group(3)
        rows.append({"file": fn, "page": int(page), "idx": int(idx), "title": title})
    return rows


def norm(title):
    # uppercase, strip non-alphanumerics, drop leading/trailing THE, fold 0->O
    t = re.sub(r"[^A-Z0-9]", "", title.upper())
    if t.startswith("THE"):
        t = t[3:]
    if t.endswith("THE"):
        t = t[:-3]
    return t.replace("0", "O")


def score(a, b):
    full = SequenceMatcher(None, a, b).ratio()
    # truncation-aware: melody titles are cut ~30 chars; compare shorter vs prefix
    s, l = (a, b) if len(a) <= len(b) else (b, a)
    pref = SequenceMatcher(None, s, l[:len(s)]).ratio() if len(s) >= 6 else 0.0
    return max(full, pref)


THRESH = 0.86

# Manual confirmed pairs (OCR variants below fuzzy threshold),
# keyed by (chords_file, melody_file). A 1-tuple (chords_file,) confirms a
# chord has NO melody counterpart, pinning it chords_only so no auto pass
# mismatches it to a same-title sheet meant for another chord.
MANUAL = [
    ("260_01_MARMADONE.png", "520_02_MARMADUKE.png"),
    ("337_02_RED_HOT_MAMMA.png", "670_02_RED_HOW_MAMA.png"),
    ("85_02_CRAZY.png", "164_01_CRAZY_HE_CALLS_ME.png"),
    ("136_01_GHOST_OF_A_CHANCE_WITH_YOU_I_DON_T_STAND_A.png", "267_02_I_DONT_STAND_A_GHOST_OF_A_CHANCE.png"),
    ("159_02_HOME.png", "309_01_HOME_WHEN_SHADOWS_FALL.png"),
    ("121_01_FALLING_IN_LOVE_WITH_LOVE.png", "239_01_FALLING_IN_LOVE_WITH_LOVE.png"),
    ("121_01_FALLING_IN_LOVE_WITH_LOVE.png", "239_02_FALLING_IN_LOVE_WITH_LOVE.png"),
    ("266_02_MILESTONES.png",),  # milestones part 1 is missing in the melody sheets
    ("266_03_MILESTONES.png", "534_01_MILESTONES.png"),
    ("466_01_WILD_CAT_BLUES_PART2.png", "929_01_WILD_CAT_BLUES.png"),
    ("185_01_I_LOVE_YOU.png", "351_01_I_LOVE_YOU.png"),
]


def main():
    chords = parse(CHORDS)
    melody = parse(MELODY)
    for r in chords:
        r["n"] = norm(r["title"])
    for r in melody:
        r["n"] = norm(r["title"])

    def dup_report(rows, label):
        seen = {}
        for r in rows:
            seen.setdefault(r["n"], []).append(r["file"])
        dups = {k: v for k, v in seen.items() if len(v) > 1}
        if dups:
            print(f"\n== Duplicate normalized titles in {label} ==")
            for k, v in sorted(dups.items()):
                print(" ", k, "->", v)

    dup_report(chords, "chords")
    dup_report(melody, "melody")

    matched_pairs = []
    used_chords, used_melody = set(), set()
    # candidate counterparts seen per file (exact or fuzzy>=THRESH); >1 = ambiguous
    cand_c, cand_m = {}, {}

    # Pass 1: manual confirmed pairs (take precedence over auto-matching)
    manual_no_melody = []  # chords confirmed to have no melody counterpart
    for entry in MANUAL:
        cf, mf = (entry[0], entry[1]) if len(entry) > 1 else (entry[0], None)
        c = next((x for x in chords if x["file"] == cf), None)
        if not c:
            print("WARN manual pair: chords file not found:", cf)
            continue
        if mf is None:
            used_chords.add(id(c))  # pinned chords_only (see manual_no_melody)
            manual_no_melody.append(c)
            continue
        m = next((x for x in melody if x["file"] == mf), None)
        if not m:
            print("WARN manual pair: melody file not found:", mf)
            continue
        matched_pairs.append((c, m, "manual"))
        used_chords.add(id(c))
        used_melody.add(id(m))

    # Pass 2: exact normalized match (greedy by page order)
    melody_by_n = {}
    for r in melody:
        melody_by_n.setdefault(r["n"], []).append(r)
    for cr in sorted(chords, key=lambda x: (x["page"], x["idx"])):
        for mr in melody_by_n.get(cr["n"], []):
            cand_c.setdefault(cr["file"], []).append(mr["file"])
            cand_m.setdefault(mr["file"], []).append(cr["file"])
        if id(cr) in used_chords:
            continue
        for mr in melody_by_n.get(cr["n"], []):
            if id(mr) not in used_melody:
                matched_pairs.append((cr, mr, "exact"))
                used_chords.add(id(cr))
                used_melody.add(id(mr))
                break

    # Pass 3: fuzzy match remaining (best score first)
    rem_chords = [c for c in chords if id(c) not in used_chords]
    rem_melody = [m for m in melody if id(m) not in used_melody]
    fuzzy = []
    for c in rem_chords:
        for m in rem_melody:
            r = score(c["n"], m["n"])
            if r >= THRESH:
                fuzzy.append((r, c, m))
                cand_c.setdefault(c["file"], []).append(m["file"])
                cand_m.setdefault(m["file"], []).append(c["file"])
    fuzzy.sort(key=lambda x: -x[0])
    for r, c, m in fuzzy:
        if id(c) in used_chords or id(m) in used_melody:
            continue
        matched_pairs.append((c, m, f"fuzzy:{r:.2f}"))
        used_chords.add(id(c))
        used_melody.add(id(m))

    only_chords = [c for c in chords if id(c) not in used_chords] + manual_no_melody
    only_melody = [m for m in melody if id(m) not in used_melody]

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["match_status", "match_type", "chords_page", "chords_title", "chords_file",
                    "melody_page", "melody_title", "melody_file"])
        for c, m, mt in sorted(matched_pairs, key=lambda x: (x[0]["page"], x[0]["idx"])):
            w.writerow(["both", mt, c["page"], c["title"], c["file"], m["page"], m["title"], m["file"]])
        for c in sorted(only_chords, key=lambda x: (x["page"], x["idx"])):
            w.writerow(["chords_only", "", c["page"], c["title"], c["file"], "", "", ""])
        for m in sorted(only_melody, key=lambda x: (x["page"], x["idx"])):
            w.writerow(["melody_only", "", "", "", "", m["page"], m["title"], m["file"]])

    def multi_report(cand, label):
        multi = {f: v for f, v in cand.items() if len(v) > 1}
        if multi:
            print(f"\n== {label} with multiple match candidates ==")
            for f, v in sorted(multi.items()):
                print(" ", f, "->", v)

    multi_report(cand_c, "chords")
    multi_report(cand_m, "melodies")

    print("\n===== SUMMARY =====")
    print("chords sheets :", len(chords))
    print("melody sheets :", len(melody))
    print("matched (both):", len(matched_pairs),
          f"(exact={sum(1 for _,_,t in matched_pairs if t=='exact')}, "
          f"fuzzy={sum(1 for _,_,t in matched_pairs if t.startswith('fuzzy'))}, "
          f"manual={sum(1 for _,_,t in matched_pairs if t=='manual')})")
    print("chords_only (no melody counterpart):", len(only_chords))
    print("melody_only (no chords counterpart):", len(only_melody))
    print("CSV written:", OUT)


if __name__ == "__main__":
    main()
