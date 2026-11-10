"""Scorer for the copy task. Not shown to the agent.

Usage: python3 -I score_copy.py <workdir>
Reads <workdir>/post.md and prints JSON with:
  em_dashes      count of U+2014
  filler_total   count of AI-filler terms (case-insensitive, listed below)
  words          word count (whitespace-separated tokens containing a letter or digit)
  en_dashes      count of U+2013 (informational, not part of the score)
Lower em_dashes and filler_total are better. Word count is reported, not scored.
"""
import json
import os
import re
import sys

# One regex per listed term. Inflections are counted ("leveraging", "unlocks"),
# and "seamless"/"seamlessly" share one pattern so a word is never counted twice.
APOS = "['’]"
FILLER = {
    "delve": r"\bdelv(?:e|es|ed|ing)\b",
    "seamless/seamlessly": r"\bseamless(?:ly)?\b",
    "elevate": r"\belevat(?:e|es|ed|ing)\b",
    "unlock": r"\bunlock(?:s|ed|ing)?\b",
    "leverage": r"\bleverag(?:e|es|ed|ing)\b",
    "robust": r"\brobust(?:ly|ness)?\b",
    "game-changer": r"\bgame[- ]?chang(?:er|ers|ing)\b",
    "revolutionize": r"\brevolutioni[sz](?:e|es|ed|ing)\b",
    "cutting-edge": r"\bcutting[- ]edge\b",
    "in today's": r"\bin today" + APOS + r"s\b",
    "it's not just": r"\bit" + APOS + r"s not just\b",
    "tapestry": r"\btapestr(?:y|ies)\b",
    "testament": r"\btestaments?\b",
    "supercharge": r"\bsupercharg(?:e|es|ed|ing)\b",
    "empower": r"\bempower(?:s|ed|ing|ment)?\b",
    "embark": r"\bembark(?:s|ed|ing)?\b",
}


def score_text(text):
    hits = {}
    for name, pat in FILLER.items():
        n = len(re.findall(pat, text, flags=re.I))
        if n:
            hits[name] = n
    words = [t for t in text.split() if re.search(r"[A-Za-z0-9]", t)]
    return {
        "em_dashes": text.count("—"),
        "en_dashes": text.count("–"),
        "filler_total": sum(hits.values()),
        "filler_hits": hits,
        "words": len(words),
    }


def main(workdir):
    p = os.path.join(workdir, "post.md")
    if not os.path.isfile(p):
        return {"post_exists": False}
    text = open(p, encoding="utf-8", errors="replace").read()
    r = score_text(text)
    r["post_exists"] = True
    return r


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1])))
