"""Turn a validated draft into files: an ATS-friendly LaTeX resume, a cover letter and notes.

Resume format: 2-line summary, exactly 10 skills, pdfLaTeX-compatible
(mathptmx, fontenc, inputenc), no emojis or decorative symbols. It compiles in Overleaf.
"""

import re
from pathlib import Path

from job_agent.models import Job
from job_agent.resume import MasterResume
from job_agent.text_utils import short_company

_LATEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_ASCII = {
    "\u2013": "--",
    "\u2014": "--",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": "``",
    "\u201d": "''",
    "\u2026": "...",
    "\u00a0": " ",
    "\u2022": "-",
}


def tex(text: str) -> str:
    """Escape text for LaTeX and keep it plain ASCII so pdfLaTeX never trips."""
    out = "".join(_LATEX_SPECIAL.get(ch, ch) for ch in text)
    for k, v in _ASCII.items():
        out = out.replace(k, v)
    return out.encode("ascii", "ignore").decode()


def _month(ym: str | None) -> str:
    if not ym:
        return "Present"
    m = re.match(r"(\d{4})-(\d{2})", ym)
    if not m:
        return ym
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{months[int(m.group(2)) - 1]} {m.group(1)}"


def render_resume_tex(resume: MasterResume, plan: dict) -> str:
    c = resume.contact
    contact = [x for x in (c.email, c.linkedin, c.github) if x]
    lines = [
        r"\documentclass[11pt]{article}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage{mathptmx}",
        r"\usepackage[a4paper,top=0.45in,bottom=0.45in,left=0.55in,right=0.55in]{geometry}",
        r"\usepackage{enumitem}",
        r"\usepackage{titlesec}",
        r"\usepackage[hidelinks]{hyperref}",
        r"\setlist[itemize]{leftmargin=1.2em,itemsep=0pt,topsep=1pt,parsep=0pt}",
        r"\titleformat{\section}{\large\bfseries}{}{0pt}{}[\titlerule]",
        r"\titlespacing*{\section}{0pt}{6pt}{3pt}",
        r"\pagestyle{empty}",
        r"\setlength{\parindent}{0pt}",
        r"\begin{document}",
        r"\begin{center}",
        rf"{{\LARGE\textbf{{{tex(resume.name)}}}}}\\[2pt]",
        tex(" | ".join(contact)),
        r"\end{center}",
        "",
        r"\section{Summary}",
        tex(plan["summary"]),
        "",
        r"\section{Skills}",
        tex(", ".join(plan["skills"])),
        "",
        r"\section{Experience}",
    ]
    for idx, e in enumerate(resume.experience, 1):
        lines += [
            rf"\textbf{{{tex(e.company)}}} \hfill {tex(e.location or '')}\\",
            rf"\textit{{{tex(e.title)}}} \hfill {_month(e.start)} -- {_month(e.end)}",
            r"\begin{itemize}",
        ]
        for bid in plan["experience_order"][f"E{idx}"]:
            j = int(bid.split(".")[1]) - 1
            lines.append(rf"  \item {tex(e.bullets[j])}")
        lines += [r"\end{itemize}", ""]
    lines.append(r"\section{Projects}")
    for pid in plan["projects"]:
        p = resume.projects[int(pid[1:]) - 1]
        lines += [
            rf"\textbf{{{tex(p.name)}}} \textit{{| {tex(', '.join(p.tech))}}}",
            r"\begin{itemize}",
            *[rf"  \item {tex(b)}" for b in p.bullets],
            r"\end{itemize}",
            "",
        ]
    lines.append(r"\section{Education}")
    for ed in resume.education:
        lines.append(
            rf"\textbf{{{tex(ed.degree)}}}, {tex(ed.institution)} "
            rf"\hfill {tex(ed.start or '')} -- {tex(ed.end or '')}\\"
        )
        if ed.details:
            lines.append(rf"{{\small {tex(' | '.join(ed.details))}}}\\[2pt]")
    lines += ["", r"\end{document}", ""]
    return "\n".join(lines)


def render_cover_letter(resume: MasterResume, job: Job, body: str) -> str:
    body = re.sub(r"^\s*(dear|hi|hello)\b[^\n]*\n+", "", body.strip(), flags=re.I)
    body = re.sub(
        r"\n+\s*(kind regards|best regards|regards|sincerely|yours)[\s\S]*$", "", body, flags=re.I
    ).strip()
    greeting = f"Dear {short_company(job.company)} Hiring Team,"
    return f"{greeting}\n\n{body}\n\nKind regards,\n{resume.name}\n"


def render_notes(job: Job, plan: dict, report: dict, model: str, also_in: list[str]) -> str:
    fit = plan.get("fit", {})
    lines = [
        f"# {job.title} at {short_company(job.company)}",
        "",
        f"- Company: {job.company}",
        f"- Location: {job.city or ('Remote' if job.is_remote else 'not stated')}"
        + (f" (also posted in: {', '.join(also_in)})" if also_in else ""),
        f"- Match score: {job.match_score}",
        f"- Apply: {job.apply_url}",
        f"- Drafted by: {model}",
        "",
        "## Fit",
        fit.get("summary", ""),
        "",
        "Strengths:",
        *[f"- {s}" for s in fit.get("strengths", [])],
        "",
        "Gaps:",
        *[f"- {g}" for g in fit.get("gaps", [])],
        "",
        "## Truthfulness check",
        "Passed" if report["passed"] else "FAILED",
        *[f"- Problem: {p}" for p in report["problems"]],
        *[f"- Style note: {w}" for w in report["warnings"]],
        "",
        "Before sending: read both documents, check every claim, "
        "and compile resume.tex in Overleaf.",
    ]
    return "\n".join(lines) + "\n"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def write_files(folder: Path, resume_tex: str, letter: str, notes: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "resume.tex").write_text(resume_tex, encoding="utf-8")
    (folder / "cover_letter.txt").write_text(letter, encoding="utf-8")
    (folder / "notes.md").write_text(notes, encoding="utf-8")
