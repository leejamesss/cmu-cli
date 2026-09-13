"""Local cache and human-readable exports for cmu-cli."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import uuid
from collections.abc import Callable
from contextlib import contextmanager, suppress
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .models import Config, Course, InvalidTypeError
from .submissions import submission_state


@contextmanager
def directory_fd(path: Path, *, create: bool = False):
    """Pin each directory with O_NOFOLLOW (POSIX only; fail closed elsewhere).

    This prevents symlink substitution, not a same-user attacker moving an
    already-open directory outside the tree. Workspaces must not be concurrently
    modified by untrusted processes; concurrent sync writers are unsupported.
    """
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeError(
            "Secure storage requires POSIX no-follow directory operations"
        )
    absolute = Path(os.path.abspath(path))
    # macOS exposes these system-owned roots as symlinks.
    if (
        absolute.parts[1:2] in [("tmp",), ("var",), ("etc",)]
        and os.uname().sysname == "Darwin"
    ):
        absolute = Path("/" + absolute.parts[1]).resolve().joinpath(*absolute.parts[2:])
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in absolute.parts[1:]:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def safe_read(path: Path) -> bytes:
    with directory_fd(path.parent) as parent:
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("Refusing a non-regular storage file")
            return handle.read()


def atomic_write(path: Path, content: bytes, *, exclusive: bool = False) -> None:
    """Private, fsynced, random staging; publish without following leaf links."""
    with directory_fd(path.parent, create=True) as parent:
        temporary = ".cmu-cli-" + uuid.uuid4().hex + ".tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                shutil.copyfileobj(io.BytesIO(content), handle)
                handle.flush()
                os.fsync(handle.fileno())
            if exclusive:
                os.link(
                    temporary,
                    path.name,
                    src_dir_fd=parent,
                    dst_dir_fd=parent,
                    follow_symlinks=False,
                )
            else:
                try:
                    existing = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    existing = None
                if existing and not stat.S_ISREG(existing.st_mode):
                    raise ValueError(
                        "Refusing a symlink or non-regular storage destination"
                    )
                os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=parent)


def safe_unlink(path: Path) -> None:
    with directory_fd(path.parent) as parent:
        os.unlink(path.name, dir_fd=parent)


LOCAL_TZ = ZoneInfo("America/New_York")
DOWNLOADABLE_EXTENSIONS = {
    ".pdf",
    ".ppt",
    ".pptx",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".zip",
    ".ipynb",
    ".py",
    ".r",
    ".txt",
    ".csv",
}


def clean_html(value: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f\x7f]", "_", name).strip(" .")
    if len(cleaned.encode("utf-8")) > 120:
        suffix = Path(cleaned).suffix
        suffix = suffix if len(suffix.encode("utf-8")) < 20 else ""
        cleaned = (
            cleaned.encode("utf-8")[:100].decode("utf-8", errors="ignore") + suffix
        )
    return cleaned or "untitled"


def format_time(value: str | None) -> str:
    if not value:
        return "未设置"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return "Unknown"
    if parsed.tzinfo is None:
        return "Unknown"
    return parsed.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %a %H:%M %Z")


def course_root(config: Config, course: Course) -> Path:
    return config.storage_root / course.directory / config.term


def ensure_layout(config: Config, course: Course) -> Path:
    root = course_root(config, course)
    checked_destination(config.storage_root, root)
    for relative in ["00_课程信息", "01_讲义", "02_作业", "03_Recitations", ".cmucw"]:
        checked_destination(root, root / relative)
        with directory_fd(root / relative, create=True):
            pass
    return root


HOMEWORK_MARKER = re.compile(
    r"(?<![a-z])(?:hw|homework|assignments?|problem[_ -]+sets?|psets?)(?![a-z])",
    re.IGNORECASE,
)


def canvas_homework_number(filename: str) -> int | None:
    """Accept one explicit 0–99 homework number, not a year or a bundle.

    Course codes before the marker and a year suffix are harmless. Other
    trailing numbers (ranges, lists, parts) are deliberately left unclassified.
    """
    stem = Path(filename).stem
    markers = list(HOMEWORK_MARKER.finditer(stem))
    if len(markers) != 1:
        return None
    match = re.match(
        r"[_ #:-]*(\d{1,2})(?![\da-z])", stem[markers[0].end() :], re.IGNORECASE
    )
    if not match:
        return None
    tail = stem[markers[0].end() + match.end() :]
    if any(not re.fullmatch(r"(?:19|20)\d{2}", n) for n in re.findall(r"\d+", tail)):
        return None
    return int(match.group(1))


def numbered_homework_folder(root: Path, number: int) -> Path:
    """Reuse a unique folder first, then the course's established family."""
    parent = root / "02_作业"
    generic = parent / "Canvas资料"
    folders = []
    if parent.is_dir():
        for path in parent.iterdir():
            match = re.fullmatch(
                r"(hw|assignment)[_ -]?(\d{1,2})", path.name, re.IGNORECASE
            )
            if match and path.is_dir():
                folders.append((path, match.group(1).lower(), int(match.group(2))))
    existing = [path for path, _, n in folders if n == number]
    if len(existing) == 1:
        return existing[0]
    if len(existing) > 1:
        return generic  # Never choose arbitrarily between HW2 and Assignment_02.
    families = {family for _, family, _ in folders}
    if len(families) > 1:
        return generic
    return parent / (
        f"HW{number}" if families == {"hw"} else f"Assignment_{number:02d}"
    )


def canvas_material_folder(root: Path, filename: str) -> Path:
    """Route a Canvas file into its human-facing course category."""
    stem = Path(filename).stem.lower()
    if stem.startswith("old"):
        return root / "legacy" / "课程资料"
    if any(
        token in stem for token in ("calendar", "syllabus", "schedule", "dataset list")
    ):
        return root / "00_课程信息"
    if any(token in stem for token in ("recitation", "recap", "tutorial")):
        return root / "03_Recitations"
    if HOMEWORK_MARKER.search(stem):
        number = canvas_homework_number(filename)
        if number is not None:
            return numbered_homework_folder(root, number)
        return root / "02_作业" / "Canvas资料"
    return root / "01_讲义"


def normalized_canvas_filename(filename: str) -> str:
    """Use stable, readable names for common Canvas material filenames."""
    safe = safe_filename(filename)
    match = re.fullmatch(
        r"lec(?:ture)?[_ -]?(\d+)(?:[_ -]?20\d{2})?(\.[^.]+)", safe, re.IGNORECASE
    )
    if match:
        return f"Lecture_{int(match.group(1)):02d}{match.group(2).lower()}"
    if safe.lower() == "calendar.pdf":
        return "Course_Calendar.pdf"
    return safe


def file_sha256(path: Path) -> str:
    return hashlib.sha256(safe_read(path)).hexdigest()


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(safe_read(path).decode("utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise InvalidTypeError("Storage manifest must be a JSON object")
    return value


def write_json(path: Path, data: Any) -> None:
    atomic_write(
        path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def assignments_markdown(course: Course, assignments: list[dict[str, Any]]) -> str:
    lines = [
        f"# {course.code} Canvas 作业索引",
        "",
        f"由 `cmu-cli sync` 生成。截止时间时区：{LOCAL_TZ}。",
        "",
    ]
    if not assignments:
        lines.append("当前 Canvas 没有发布作业。")
    for item in assignments:
        submission = item.get("submission") or {}
        state = submission_state(
            submission.get("workflow_state"), submission.get("submitted_at")
        )
        status = {True: "已提交", False: "未提交", None: "未知"}[state]
        lines.extend(
            [
                f"## {item.get('name', '未命名作业')}",
                f"- 截止：{format_time(item.get('due_at'))}",
                f"- 状态：{status}",
                f"- 分值：{item.get('points_possible') if item.get('points_possible') is not None else '未设置'}",
                f"- 链接：{item.get('html_url', '')}",
                f"- 说明：{clean_html(item.get('description')) or '无'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def announcements_markdown(course: Course, announcements: list[dict[str, Any]]) -> str:
    lines = [f"# {course.code} Canvas 通知索引", "", "由 `cmu-cli sync` 生成。", ""]
    if not announcements:
        lines.append("当前 Canvas 没有课程通知。")
    for item in announcements:
        lines.extend(
            [
                f"## {item.get('title', '未命名通知')}",
                f"- 发布时间：{format_time(item.get('posted_at'))}",
                f"- 链接：{item.get('html_url', '')}",
                "",
                clean_html(item.get("message")) or "无正文",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def download_file(
    item: dict[str, Any],
    destination: Path,
    fetch_bytes: Callable[[str], bytes],
) -> dict[str, Any]:
    url = item.get("url")
    if not url:
        return {"status": "skipped", "reason": "no_url"}
    expected_size = item.get("size")

    content = fetch_bytes(url)
    size = len(content)
    if expected_size is not None and size != expected_size:
        raise RuntimeError(
            f"下载大小不符：{destination.name}，预期 {expected_size}，实际 {size}"
        )
    digest = hashlib.sha256(content).hexdigest()
    if destination.exists() or destination.is_symlink():
        if file_sha256(destination) == digest:
            return {"status": "unchanged", "size": size, "sha256": digest}
        raise FileExistsError("Refusing to overwrite an unmanaged download")
    atomic_write(destination, content, exclusive=True)
    return {"status": "downloaded", "size": size, "sha256": digest}


def canvas_conflict_paths(destination: Path, file_id: str):
    """Yield deterministic alternatives without ever recycling an occupied name."""
    yield destination
    base = f"{destination.stem}__canvas_{safe_filename(file_id)[:32]}"
    yield destination.with_name(f"{base}{destination.suffix}")
    index = 2
    while True:
        yield destination.with_name(f"{base}_{index}{destination.suffix}")
        index += 1


def migrate_canvas_homework(
    root: Path,
    source_name: str,
    file_id: str,
    entry: dict[str, Any],
) -> Path:
    """Only relocate manifest-managed files directly inside the old generic folder.

    Copy exclusively before unlinking: a collision (including a dangling symlink)
    can never clobber a user file. Identical regular files may be reused.
    """
    relative = Path(entry["relative_path"])
    checked_destination(root, root / relative)
    old = root / relative
    legacy = Path("02_作业/Canvas资料")
    if relative.parent != legacy or old.is_symlink():
        return old
    folder = canvas_material_folder(root, source_name)
    if folder.parent != root / "02_作业" or folder == root / legacy:
        return old
    # Do not move user aliases or follow a directory symlink outside the course.
    if (
        old.parent.resolve() != root.resolve() / legacy
        or folder.resolve().parent != (root / "02_作业").resolve()
    ):
        return old
    if old.exists() and not old.is_file():
        return old
    digest = file_sha256(old) if old.is_file() else None
    with directory_fd(folder, create=True):
        pass
    destination = initial_destination = folder / old.name
    for destination in canvas_conflict_paths(initial_destination, file_id):
        if destination.exists() or destination.is_symlink():
            if (
                digest
                and not destination.is_symlink()
                and destination.is_file()
                and file_sha256(destination) == digest
            ):
                safe_unlink(old)
                break
            continue
        if digest is not None:
            try:
                atomic_write(destination, safe_read(old), exclusive=True)
            except FileExistsError:
                continue
            safe_unlink(old)
        break
    entry["relative_path"] = str(destination.relative_to(root))
    if digest is not None:
        # Do not bless user edits as a verified remote revision.
        entry.setdefault("sha256", digest)
    return destination


def checked_destination(root: Path, destination: Path) -> None:
    if ".." in destination.parts:
        raise ValueError("Refusing parent traversal in storage paths")
    try:
        destination.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError(
            "Refusing a destination outside the course directory"
        ) from None
    current = destination
    while current != current.parent:
        system_alias = (
            os.name == "posix"
            and os.uname().sysname == "Darwin"
            and str(current) in ("/tmp", "/var", "/etc")
        )
        if current.is_symlink() and not system_alias:
            raise ValueError("Refusing a symlink download destination")
        current = current.parent


def sync_canvas_file(
    item: dict[str, Any],
    root: Path,
    manifest: dict[str, Any],
    fetch_bytes: Callable[[str], bytes],
) -> dict[str, Any]:
    """Download one Canvas file without duplicate user-facing copies."""
    source_name = safe_filename(
        item.get("display_name") or item.get("filename") or "file"
    )
    if item.get("id") is None:
        raise ValueError("Canvas downloads require a stable file ID")
    file_id = str(item["id"])
    expected_size = item.get("size")
    source_updated_at = item.get("updated_at")
    entry = manifest.get(file_id) if isinstance(manifest.get(file_id), dict) else None

    if entry and entry.get("relative_path"):
        relative = Path(entry["relative_path"])
        if relative.is_absolute():
            raise ValueError("Manifest paths must be relative")
        destination = root / relative
        checked_destination(root, destination)
        if relative.parts[0] == ".cmucw" or relative.name in (
            "Canvas作业索引.md",
            "Canvas通知索引.md",
        ):
            raise ValueError("Manifest download cannot own an internal export path")
        shared = any(
            key != file_id
            and isinstance(other, dict)
            and other.get("relative_path")
            and (root / other["relative_path"]).resolve() == destination.resolve()
            for key, other in manifest.items()
        )
        # Keep aliases and edited legacy files in place; never bless local edits.
        if not shared and (
            not destination.exists() or file_sha256(destination) == entry.get("sha256")
        ):
            destination = migrate_canvas_homework(root, source_name, file_id, entry)
        if (
            destination.is_file()
            and entry.get("sha256") == file_sha256(destination)
            and source_updated_at is not None
            and entry.get("source_updated_at") == source_updated_at
            and (expected_size is None or destination.stat().st_size == expected_size)
        ):
            return {
                "status": "unchanged",
                "size": destination.stat().st_size,
                "sha256": file_sha256(destination),
                "relative_path": str(destination.relative_to(root)),
            }
    else:
        folder = canvas_material_folder(root, source_name)
        destination = folder / normalized_canvas_filename(source_name)

    checked_destination(root, destination)
    url = item.get("url")
    if not url:
        return {"status": "skipped", "reason": "no_url"}
    content = fetch_bytes(url)
    size = len(content)
    if expected_size is not None and size != expected_size:
        raise RuntimeError(
            f"下载大小不符：{source_name}，预期 {expected_size}，实际 {size}"
        )
    digest = hashlib.sha256(content).hexdigest()

    # On first discovery, reuse a canonical file with identical bytes even if
    # its readable filename differs from Canvas's source filename.
    if not entry:
        folder = canvas_material_folder(root, source_name)
        if folder.exists():
            for candidate in sorted(folder.iterdir()):
                if (
                    candidate.is_file()
                    and not candidate.is_symlink()
                    and not candidate.name.startswith(".")
                    and candidate.stat().st_size == size
                    and file_sha256(candidate) == digest
                ):
                    destination = candidate
                    break

    checked_destination(root, destination)
    previous_digest = file_sha256(destination) if destination.is_file() else None
    owners = [
        key
        for key, other in manifest.items()
        if key != file_id
        and isinstance(other, dict)
        and other.get("relative_path")
        and (root / other["relative_path"]).resolve() == destination.resolve()
    ]
    # New IDs, shared canonical paths and local edits cannot replace bytes.
    protected = not entry or owners or previous_digest != entry.get("sha256")
    if (
        previous_digest != digest
        and (destination.exists() or destination.is_symlink() or owners)
        and protected
    ):
        for candidate in canvas_conflict_paths(destination, file_id):
            if candidate.exists() or candidate.is_symlink():
                continue
            if any(
                isinstance(other, dict)
                and other.get("relative_path") == str(candidate.relative_to(root))
                for other in manifest.values()
            ):
                continue
            destination = candidate
            previous_digest = None
            break
    if previous_digest == digest:
        status = "deduplicated" if not entry else "unchanged"
    else:
        if destination.exists():
            version_dir = root / ".cmucw" / "versions" / safe_filename(file_id)
            checked_destination(root, version_dir)
            stamp = re.sub(
                r"[^0-9A-Za-z]+", "-", str(source_updated_at or "previous")
            ).strip("-")
            archive = (
                version_dir / f"{stamp[:60]}_{uuid.uuid4().hex}_{destination.name}"
            )
            atomic_write(archive, safe_read(destination), exclusive=True)
            status = "updated"
        else:
            status = "downloaded"
        atomic_write(destination, content, exclusive=previous_digest is None)

    relative_path = str(destination.relative_to(root))
    manifest[file_id] = {
        "source_name": source_name,
        "relative_path": relative_path,
        "source_updated_at": source_updated_at,
        "size": size,
        "sha256": digest,
    }
    return {
        "status": status,
        "size": size,
        "sha256": digest,
        "relative_path": relative_path,
    }


def sync_course(
    config: Config,
    course: Course,
    assignments: list[dict[str, Any]],
    files: list[dict[str, Any]],
    announcements: list[dict[str, Any]],
    modules: list[dict[str, Any]],
    download: bool,
    fetch_bytes: Callable[[str], bytes],
) -> dict[str, Any]:
    root = ensure_layout(config, course)
    cache = root / ".cmucw"
    write_json(cache / "assignments.json", assignments)
    write_json(cache / "files.json", files)
    write_json(cache / "announcements.json", announcements)
    write_json(cache / "modules.json", modules)
    atomic_write(
        root / "02_作业/Canvas作业索引.md",
        assignments_markdown(course, assignments).encode("utf-8"),
    )
    atomic_write(
        root / "00_课程信息/Canvas通知索引.md",
        announcements_markdown(course, announcements).encode("utf-8"),
    )

    downloads: list[dict[str, Any]] = []
    if download:
        manifest_path = cache / "download_manifest.json"
        download_manifest = load_json_object(manifest_path)
        for item in files:
            filename = safe_filename(
                item.get("display_name") or item.get("filename") or "file"
            )
            if Path(filename).suffix.lower() not in DOWNLOADABLE_EXTENSIONS:
                continue
            result = sync_canvas_file(item, root, download_manifest, fetch_bytes)
            downloads.append({"name": filename, **result})
        write_json(manifest_path, download_manifest)
    summary = {
        "course": course.code,
        "root": str(root),
        "assignments": len(assignments),
        "files": len(files),
        "announcements": len(announcements),
        "modules": len(modules),
        "downloads": downloads,
        "synced_at": datetime.now(LOCAL_TZ).isoformat(),
    }
    write_json(cache / "last_sync.json", summary)
    return summary
