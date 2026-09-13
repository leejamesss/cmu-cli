"""Shared models and configuration for cmu-cli."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

CONFIG_PATH = Path.home() / ".config/cmu_cli/config.json"


class InvalidTypeError(TypeError, ValueError):
    """Invalid input type, compatible with existing ValueError callers."""


@dataclass(frozen=True)
class Course:
    code: str
    canvas_id: int
    name: str
    directory: str
    public_site_url: str | None = None
    assessment_year: int | None = None
    assessment_hour: int = 0
    piazza_url: str | None = None
    gradescope_url: str | None = None
    ed_course_id: str | int | None = None

    def __post_init__(self):
        if self.ed_course_id is not None:
            validate_ed_id(self.ed_course_id)


def validate_ed_id(value):
    if type(value) not in (int, str) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise ValueError("Ed IDs must be canonical positive decimal integers")
    return value


@dataclass(frozen=True)
class ProviderConfig:
    ed_api_base_url: str = "https://us.edstem.org/api"
    browser_auth: dict | None = None


def load_provider_config(path: Path | None = None) -> ProviderConfig:
    """Optional provider-only config; never require Canvas or discover cookies."""
    selected = path or os.environ.get("CMU_CLI_CONFIG")
    candidate = Path(selected).expanduser() if selected else CONFIG_PATH
    if not selected and not candidate.exists():
        return ProviderConfig()
    raw = json.loads(candidate.read_text(encoding="utf-8"))
    return provider_config(raw)


def provider_config(raw) -> ProviderConfig:
    from .ed_client import ED_API_BASES

    base = raw.get("ed_api_base_url", "https://us.edstem.org/api")
    if not isinstance(base, str) or base not in ED_API_BASES:
        raise ValueError("Unsupported exact Ed API base")
    return ProviderConfig(base, raw.get("browser_auth"))


@dataclass(frozen=True)
class Config:
    canvas_base_url: str
    storage_root: Path
    courses: tuple[Course, ...]

    term: str = "current"
    timezone: str = "America/New_York"
    browser_auth: dict | None = None
    ed_api_base_url: str = "https://us.edstem.org/api"

    def find_course(self, query: str) -> Course:
        normalized = re.sub(r"[^a-z0-9]", "", query.lower())
        matches = [
            course
            for course in self.courses
            if normalized in re.sub(r"[^a-z0-9]", "", course.code.lower())
            or normalized in re.sub(r"[^a-z0-9]", "", course.name.lower())
        ]
        if not matches:
            raise ValueError(f"No matching course: {query}")
        if len(matches) > 1:
            choices = ", ".join(course.code for course in matches)
            raise ValueError(f"Ambiguous course: {choices}")
        return matches[0]


def load_config(path: Path | None = None) -> Config:
    path = Path(path or os.environ.get("CMU_CLI_CONFIG", CONFIG_PATH)).expanduser()
    raw = json.loads(path.read_text(encoding="utf-8"))
    base = raw["canvas_base_url"].rstrip("/")
    parsed = urlparse(base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path
    ):
        raise ValueError("canvas_base_url must be a plain HTTPS origin")
    if not isinstance(raw["courses"], list):
        raise InvalidTypeError("courses must be a list")
    courses = tuple(Course(**course) for course in raw["courses"])
    timezone = raw.get("timezone", "America/New_York")
    if not isinstance(timezone, str):
        raise InvalidTypeError("timezone must be an IANA timezone string")
    ZoneInfo(timezone)
    for key in ("code", "canvas_id", "directory"):
        values = [str(getattr(c, key)).casefold() for c in courses]
        if len(values) != len(set(values)):
            raise ValueError("Course codes, IDs and directories must be unique")
    term = raw.get("term", "current")
    for value in [term, *(c.directory for c in courses)]:
        if (
            not isinstance(value, str)
            or not value
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
            or value.endswith((".", " "))
            or value.split(".")[0].upper()
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10)),
            }
            or Path(value).is_absolute()
            or len(Path(value).parts) != 1
            or value in {".", ".."}
        ):
            raise ValueError(
                "Course directory and term must be single safe path components"
            )
    for c in courses:
        if type(c.canvas_id) is not int or c.canvas_id < 1:
            raise ValueError("canvas_id must be a positive integer")
        if not all(isinstance(v, str) and v.strip() for v in (c.code, c.name)):
            raise ValueError("Course code and name must be nonempty strings")
        if c.assessment_year is not None and (
            type(c.assessment_year) is not int or not 1900 <= c.assessment_year <= 9999
        ):
            raise ValueError("assessment_year must be an integer between 1900 and 9999")
        if type(c.assessment_hour) is not int or not 0 <= c.assessment_hour <= 23:
            raise ValueError("assessment_hour must be between 0 and 23")
        for url in [c.public_site_url, c.piazza_url, c.gradescope_url]:
            if url and (
                not isinstance(url, str)
                or not urlparse(url).hostname
                or urlparse(url).scheme != "https"
                or urlparse(url).username
                or urlparse(url).password
            ):
                raise ValueError("Platform URLs must use HTTPS without credentials")
    storage = Path(raw["storage_root"]).expanduser()
    if not storage.is_absolute():
        storage = path.resolve().parent / storage
    return Config(
        base,
        storage,
        courses,
        term,
        raw.get("timezone", "America/New_York"),
        raw.get("browser_auth"),
        provider_config(raw).ed_api_base_url,
    )
