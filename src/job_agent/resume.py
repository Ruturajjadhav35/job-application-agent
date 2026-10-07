"""The master resume: the single source of truth about you.

Matching reads it now; in step 5 the truthfulness check will compare every
drafted claim against it. Keep it accurate.
"""

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

# Words in brackets that describe a level, not a separate skill.
QUALIFIERS = {"advanced", "awareness", "basic", "intermediate", "expert", "merit"}


class Contact(BaseModel):
    email: str | None = None
    linkedin: str | None = None
    github: str | None = None


class Education(BaseModel):
    degree: str
    institution: str
    start: str | None = None
    end: str | None = None
    details: list[str] = []


class Experience(BaseModel):
    title: str
    company: str
    location: str | None = None
    start: str
    end: str | None = None  # None means current
    bullets: list[str] = Field(min_length=1)


class Project(BaseModel):
    name: str
    tech: list[str] = []
    bullets: list[str] = Field(min_length=1)


class MasterResume(BaseModel):
    name: str
    contact: Contact = Contact()
    summary: str
    skills: dict[str, list[str]]
    education: list[Education] = []
    experience: list[Experience] = []
    projects: list[Project] = []

    def skill_names(self) -> list[str]:
        """Flat list of individual skills.

        "Python (Pandas, NumPy, OOP)" -> Python, Pandas, NumPy, OOP
        "SQL (Advanced)" -> SQL
        """
        names: list[str] = []
        for group in self.skills.values():
            for item in group:
                m = re.match(r"^(.*?)\s*\((.*)\)\s*$", item)
                if m:
                    names.append(m.group(1))
                    inner = [x.strip() for x in m.group(2).split(",")]
                    if not all(x.lower() in QUALIFIERS for x in inner):
                        names += inner
                else:
                    names.append(item)
        for p in self.projects:
            names += p.tech
        # de-duplicate, keep order, drop one-letter names like "R" (too many false matches)
        seen, out = set(), []
        for n in (x.strip() for x in names):
            if len(n) > 1 and n.lower() not in seen:
                seen.add(n.lower())
                out.append(n)
        return out

    def as_text(self) -> str:
        """Plain text used for matching. Contact details are left out on purpose."""
        parts = [self.summary]
        for group, items in self.skills.items():
            parts.append(f"{group}: {', '.join(items)}")
        for e in self.experience:
            parts.append(f"{e.title} at {e.company}. " + " ".join(e.bullets))
        for p in self.projects:
            parts.append(f"{p.name} ({', '.join(p.tech)}). " + " ".join(p.bullets))
        for ed in self.education:
            parts.append(f"{ed.degree}, {ed.institution}. " + " ".join(ed.details))
        return "\n".join(parts)


def load_resume(path: Path) -> MasterResume:
    if not path.exists():
        raise FileNotFoundError(
            f"Master resume not found at {path}. "
            "Put your master_resume.json in the private/ folder."
        )
    return MasterResume.model_validate(json.loads(path.read_text(encoding="utf-8")))
