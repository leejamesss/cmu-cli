"""Ed attachment CLI: fresh inventory selection, anonymous immutable downloads."""

from .discussion_materials import download_attachment, ed_materials
from .models import UsageError
from .web_session import SessionError


def read_materials(args, client):
    result = ed_materials(
        client, args.course_id, page_size=args.page_size, max_pages=args.max_pages
    )
    if args.action == "download":
        matches = [row for row in result["items"] if row["id"] == args.id]
        if not matches:
            result.update(complete=False, download=None)
            result["issues"].append(
                {"reason": "attachment_id_not_in_returned_inventory"}
            )
        else:
            try:
                result["download"] = download_attachment(matches[0], args.output)
            except FileExistsError:
                raise UsageError(
                    "Destination differs from remote bytes; local edits were preserved. Choose another --output root."
                ) from None
            except (SessionError, ValueError, OSError):
                result.update(complete=False, download=None)
                result["issues"].append({"reason": "download_unavailable_or_unsafe"})
    result["reason"] = (
        "; ".join(dict.fromkeys(i["reason"] for i in result["issues"]))
        or "Recognized references in returned thread/reply bodies enumerated"
    )
    return result


def material_lines(action, result):
    if action == "download":
        download = result.get("download")
        return [
            f"{download['status']}: {download['relative_path']}"
            if download
            else "No attachment downloaded; inspect issues with --json."
        ]
    rows = result["items"]
    lines = ["Ed thread/reply references (not Resources/Lessons):"]
    for row in rows:
        lines.extend(
            [
                f"◆ {row['filename']} | {row['kind']} | {row['availability']}",
                f"  id: {row['id']}",
                f"  {row['url']}",
            ]
        )
    return (
        lines
        if rows
        else lines + ["No recognized references returned; inspect source status."]
    )
