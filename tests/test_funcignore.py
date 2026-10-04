"""Guard for .funcignore.

The deploy action reads it with gitignore rules, so a bare name such as
`tools` matches a folder of that name at ANY depth, including inside the
installed packages. `tools` once deleted pandas/core/tools and stopped the
whole function from loading. Entries that name a project file or folder must
start with `/` so they only match at the project root.
"""

from pathlib import Path

FUNCIGNORE = Path(__file__).resolve().parent.parent / ".funcignore"

# Safe to match at any depth: wildcard patterns, and pure cache folders.
ANYWHERE = {"__pycache__"}


def test_project_entries_are_anchored_to_the_root():
    entries = [line.strip() for line in FUNCIGNORE.read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.startswith("#")]
    unanchored = [e for e in entries
                  if not e.startswith("/") and "*" not in e and e not in ANYWHERE]
    assert not unanchored, (
        f"{unanchored} would also match inside .python_packages. Anchor with a leading '/'.")
