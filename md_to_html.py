#!/usr/bin/env python3
"""Convert Markdown files in a folder to HTML.

Behavior:
- Converts every .md file in the target folder to a same-name .html file.
- Looks for an ordering file named content.txt (or contents.txt as fallback).
- If no ordering file exists:
  - Creates contents.txt listing discovered Markdown filenames.
  - Builds a combined Markdown file with subsection headers per file.
- Always produces index.html from the combined Markdown sequence.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from typing import Iterable, List


def _try_import_markdown():
    try:
        import markdown  # type: ignore

        return markdown
    except Exception:
        return None


def to_html(markdown_text: str, title: str = "Document") -> str:
    """Convert Markdown text to an HTML page.

    Uses the `markdown` package when available, otherwise uses a minimal
    plain-text fallback.
    """
    markdown_pkg = _try_import_markdown()

    if markdown_pkg is not None:
        body = markdown_pkg.markdown(markdown_text, extensions=["extra", "tables", "fenced_code"])
    else:
        escaped = (
            markdown_text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        body = f"<pre>{escaped}</pre>"

    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{title}</title>
  <style>
    body {{
      max-width: 980px;
      margin: 2rem auto;
      padding: 0 1rem;
      font-family: Segoe UI, Arial, sans-serif;
      line-height: 1.55;
    }}
    pre {{
      background: #f4f4f4;
      padding: 0.75rem;
      overflow-x: auto;
    }}
    code {{
      background: #f4f4f4;
      padding: 0.1rem 0.25rem;
      border-radius: 3px;
    }}
    table {{ border-collapse: collapse; }}
    th, td {{ border: 1px solid #ddd; padding: 0.4rem 0.6rem; }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""


def find_order_file(base: Path) -> Path | None:
    preferred = base / "content.txt"
    alternate = base / "contents.txt"

    if preferred.exists() and preferred.is_file():
        return preferred
    if alternate.exists() and alternate.is_file():
        return alternate
    return None


def read_ordered_files(order_file: Path, base: Path) -> List[Path]:
    ordered: List[Path] = []

    for raw_line in order_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        name = line if line.lower().endswith(".md") else f"{line}.md"
        path = base / name

        if not path.exists() or not path.is_file():
            print(f"Warning: listed file not found, skipping: {name}")
            continue

        ordered.append(path)

    return ordered


def discover_markdown_files(base: Path) -> List[Path]:
    skip_names = {
        "combined.md",
    }

    files = [
        p
        for p in base.glob("*.md")
        if p.is_file() and p.name not in skip_names
    ]
    files.sort(key=lambda p: p.name.lower())
    return files


def write_contents_file(path: Path, files: Iterable[Path]) -> None:
    lines = [p.name for p in files]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def slugify_heading(text: str) -> str:
    lowered = text.strip().lower()
    cleaned = re.sub(r"[^a-z0-9\s-]", "", lowered)
    slug = re.sub(r"[\s_-]+", "-", cleaned).strip("-")
    return slug or "section"


def build_jump_table(items: List[tuple[str, str]]) -> str:
    links = ", ".join([f"[{label}](#{anchor})" for label, anchor in items])
    return "| Jump to | Sections |\n|---|---|\n| Links | " + links + " |"


def build_combined_markdown(files: Iterable[Path]) -> str:
    chunks: List[str] = []
    sections: List[tuple[Path, str, str]] = []

    for file_path in files:
        heading = file_path.stem.replace("-", " ").replace("_", " ").strip()
        anchor = slugify_heading(heading)
        sections.append((file_path, heading, anchor))

    jump_items = [(heading, anchor) for _, heading, anchor in sections]
    jump_table = build_jump_table(jump_items)

    for file_path, heading, anchor in sections:
        content = file_path.read_text(encoding="utf-8")
        chunks.append(f"## {heading}\n\n<a id=\"{anchor}\"></a>\n\n{jump_table}\n\n{content.strip()}\n")

    return "\n\n".join(chunks).strip() + "\n"


def convert_single_files_to_html(files: Iterable[Path]) -> None:
    for md_path in files:
        md_text = md_path.read_text(encoding="utf-8")
        html = to_html(md_text, title=md_path.stem)
        html_path = md_path.with_suffix(".html")
        html_path.write_text(html, encoding="utf-8")
        print(f"Wrote: {html_path.name}")


def run_command(args: List[str], cwd: Path) -> str:
    result = subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip() or "Unknown command error"
        raise RuntimeError(f"Command failed: {' '.join(args)}\n{stderr}")

    return result.stdout.strip()


def upload_to_github(base: Path, repo_url: str, branch: str, commit_message: str) -> None:
    run_command(["git", "--version"], base)

    if not (base / ".git").exists():
        run_command(["git", "init"], base)

    remotes = run_command(["git", "remote"], base).splitlines()
    if "origin" in remotes:
        run_command(["git", "remote", "set-url", "origin", repo_url], base)
    else:
        run_command(["git", "remote", "add", "origin", repo_url], base)

    run_command(["git", "checkout", "-B", branch], base)
    run_command(["git", "add", "-A"], base)

    status = run_command(["git", "status", "--porcelain"], base)
    if status:
        run_command(["git", "commit", "-m", commit_message], base)
        print("Committed local changes.")
    else:
        print("No changes to commit.")

    run_command(["git", "push", "-u", "origin", branch], base)
    print(f"Uploaded to GitHub: {repo_url} ({branch})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert Markdown files to HTML.")
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Target directory (default: current directory)",
    )
    parser.add_argument(
        "--repo-url",
        default="https://github.com/wolfwadehn/MDreader",
        help="GitHub repository URL for automatic upload.",
    )
    parser.add_argument(
        "--branch",
        default="main",
        help="Git branch to push (default: main).",
    )
    parser.add_argument(
        "--commit-message",
        default="Update generated HTML from Markdown",
        help="Commit message used for automatic upload.",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Disable automatic GitHub upload.",
    )
    args = parser.parse_args()

    base = Path(args.directory).resolve()
    if not base.exists() or not base.is_dir():
        raise SystemExit(f"Not a directory: {base}")

    order_file = find_order_file(base)

    if order_file is not None:
        ordered_files = read_ordered_files(order_file, base)
        if not ordered_files:
            raise SystemExit(f"No valid markdown files listed in {order_file.name}")
        print(f"Using order from {order_file.name}")
    else:
        ordered_files = discover_markdown_files(base)
        if not ordered_files:
            raise SystemExit("No markdown files found.")
        contents_file = base / "contents.txt"
        write_contents_file(contents_file, ordered_files)
        print(f"Created: {contents_file.name}")

    convert_single_files_to_html(ordered_files)

    combined_md_text = build_combined_markdown(ordered_files)
    combined_md_path = base / "combined.md"
    combined_html_path = base / "index.html"

    combined_md_path.write_text(combined_md_text, encoding="utf-8")
    combined_html_path.write_text(to_html(combined_md_text, title="Combined Markdown"), encoding="utf-8")

    print(f"Wrote: {combined_md_path.name}")
    print(f"Wrote: {combined_html_path.name}")

    if not args.no_upload:
        try:
            upload_to_github(base, args.repo_url, args.branch, args.commit_message)
        except RuntimeError as ex:
            raise SystemExit(f"Upload failed: {ex}")


if __name__ == "__main__":
    main()
