#!/usr/bin/env python3
from pathlib import Path
import shutil

import pdoc

here = Path(__file__).parent
out = here / "src" / "api"
if out.exists():
    shutil.rmtree(out)

# Render to docs/src/api
pdoc.render.configure(template_directory=here / "pdoc-template")
packages = sorted([p.parent.name for p in (here / ".." / "src").glob("*/__init__.py")])
pdoc.pdoc(*packages, output_directory=out)

# delete unused files that pdoc generates
Path(out / "index.html").unlink(missing_ok=True)
Path(out / "search.js").unlink(missing_ok=True)

# rename the .html files to .md
files = []
for filepath in out.glob("**/*.html"):
    newpath = filepath.with_suffix(".md")
    filepath.rename(newpath)
    files.append(newpath.relative_to(out))

# append entries to SUMMARY.md
api_lines = []
for package_name in packages:
    api_lines.append(f"- [{package_name}](./api/{package_name}.md)\n")
    for filepath in sorted(files):
        path = str(filepath)
        module_name = path.replace(".md", "").replace("/", ".")
        if package_name in path and "/" in path:
            api_lines.append(f"  - [{module_name}](./api/{filepath})\n")

with open(here / "src" / "SUMMARY.md", "r") as fh:
    all_lines = fh.readlines()
    rewrite_lines = []
    for line in all_lines:
        rewrite_lines.append(line)
        if line == "# API Reference\n":
            break

with open(here / "src" / "SUMMARY.md", "w") as fh:
    fh.writelines(rewrite_lines)
    fh.writelines(api_lines)
