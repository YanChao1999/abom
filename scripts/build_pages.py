#!/usr/bin/env python3
"""Build the GitHub Pages site from the recipe catalog."""

from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src" / "abom" / "recipes.json"
SITE = ROOT / "site"
HOME = "https://yanchao1999.github.io/abom/"
REPO = "https://github.com/YanChao1999/abom"


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    recipes = sorted(catalog["recipes"], key=lambda recipe: recipe["name"])
    rows = "\n".join(_row(recipe) for recipe in recipes)
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "index.html").write_text(_page(rows), encoding="utf-8")


def _row(recipe: dict) -> str:
    name = html.escape(recipe["name"])
    kind = html.escape(recipe["kind"])
    license_id = html.escape(recipe["license"])
    description = html.escape(recipe["description"])
    homepage = html.escape(recipe["homepage"], quote=True)
    return (
        "<tr>"
        f'<td><a href="{homepage}"><code>{name}</code></a></td>'
        f"<td>{kind}</td>"
        f"<td>{license_id}</td>"
        f"<td>{description}</td>"
        "</tr>"
    )


def _page(rows: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>abom</title>
  <meta name="description" content="Agent bill of materials manager for skills, prompts, MCP servers, and packages.">
  <link rel="canonical" href="{HOME}">
  <style>
    :root {{
      color-scheme: light;
      --ink: #1c1915;
      --muted: #5c564c;
      --line: #d9d1c3;
      --paper: #f6f1e8;
      --card: #fffdf8;
      --mark: #8f3d1b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--paper);
      color: var(--ink);
      font: 18px/1.5 Georgia, "Iowan Old Style", "Palatino Linotype", Palatino, serif;
    }}
    main {{
      max-width: 920px;
      margin: 0 auto;
      padding: 48px 20px 72px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 56px;
      line-height: 1;
      letter-spacing: -0.03em;
    }}
    h1 span {{ color: var(--mark); }}
    .lede {{
      max-width: 38rem;
      margin: 0 0 28px;
      color: var(--muted);
    }}
    .install {{
      background: var(--card);
      border: 1px solid var(--line);
      padding: 16px 18px;
      margin-bottom: 36px;
    }}
    code, pre {{
      font-family: "IBM Plex Mono", "ui-monospace", "SFMono-Regular", Menlo, Consolas, monospace;
    }}
    pre {{
      margin: 8px 0 0;
      white-space: pre-wrap;
    }}
    h2 {{
      font-size: 22px;
      margin: 0 0 12px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--card);
    }}
    th, td {{
      text-align: left;
      vertical-align: top;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      font-size: 16px;
    }}
    th {{
      font-size: 13px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    a {{ color: inherit; }}
    footer {{
      margin-top: 28px;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <main>
    <h1>ab<span>om</span></h1>
    <p class="lede">Agent bill of materials manager for skills, prompts, MCP servers, and packages. Install a recipe by name. The git URL stays in the catalog.</p>
    <section class="install">
      <div>Install</div>
      <pre><code>pip install abom
abom recipes</code></pre>
    </section>
    <h2>Recipes</h2>
    <table>
      <thead>
        <tr><th>Name</th><th>Kind</th><th>License</th><th>What it is</th></tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
    <footer>
      <a href="{REPO}">YanChao1999/abom</a>
      · MIT
    </footer>
  </main>
</body>
</html>
"""


if __name__ == "__main__":
    main()
