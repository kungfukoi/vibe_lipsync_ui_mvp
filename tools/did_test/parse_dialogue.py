import json
from dataclasses import dataclass
from typing import List


@dataclass
class Line:
    speaker: str  # "A" or "B"
    text: str


def parse_dialogue(script: str) -> List[Line]:
    lines: List[Line] = []

    for i, raw in enumerate(script.splitlines(), start=1):
        s = raw.strip()

        if not s:
            continue

        if ":" not in s:
            raise ValueError(f"Line {i}: missing ':' -> {raw!r}")

        left, right = s.split(":", 1)
        speaker = left.strip().upper()
        text = right.strip()

        if speaker not in ("A", "B"):
            raise ValueError(f"Line {i}: speaker must be A or B -> {raw!r}")

        if not text:
            raise ValueError(f"Line {i}: missing dialogue after ':' -> {raw!r}")

        lines.append(Line(speaker=speaker, text=text))

    if not lines:
        raise ValueError("No dialogue lines found.")

    return lines


def main():
    with open("script.txt", "r", encoding="utf-8") as f:
        script = f.read()

    parsed = parse_dialogue(script)
    out = [{"speaker": ln.speaker, "text": ln.text} for ln in parsed]

    with open("lines.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print(f"Parsed {len(out)} lines -> lines.json")
    for idx, ln in enumerate(out, start=1):
        print(f"{idx}. {ln['speaker']}: {ln['text']}")


if __name__ == "__main__":
    main()