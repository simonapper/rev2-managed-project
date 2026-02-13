from __future__ import annotations


def normalise_sections(text: str) -> str:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("# "):
            if out and out[-1] != "":
                out.append("")
            out.append(line)
            out.append("")
            i += 1
            while i < len(lines) and lines[i].strip() == "":
                i += 1
            continue

        if line == "":
            if out and out[-1] == "":
                i += 1
                continue
            out.append("")
        else:
            out.append(line)
        i += 1

    while len(out) >= 2 and out[-1] == "" and out[-2] == "":
        out.pop()
    if out and out[-1] != "":
        out.append("")

    return "\n".join(out).rstrip("\n") + "\n"
