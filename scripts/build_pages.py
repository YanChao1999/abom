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
  <meta name="description" content="Install an agent tool with one command, manage it, and link it into another project.">
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
    .panels {{
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 16px;
      margin-bottom: 36px;
    }}
    .panels section {{
      background: var(--card);
      border: 1px solid var(--line);
      padding: 16px 18px;
    }}
    .panels h2 {{
      margin: 0 0 8px;
    }}
    .panels p {{
      margin: 0 0 8px;
      color: var(--muted);
      font-size: 16px;
    }}
    @media (max-width: 860px) {{
      .panels {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 44px; }}
    }}
    code, pre {{
      font-family: "IBM Plex Mono", "ui-monospace", "SFMono-Regular", Menlo, Consolas, monospace;
    }}
    pre {{
      margin: 8px 0 0;
      overflow-x: auto;
      white-space: pre;
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
    <p class="lede">Install an agent tool with one command, manage it, and link it into another project.</p>
    <div class="panels">
      <section>
        <h2>Install</h2>
        <p>One command names the tool. The git URL stays in the catalog.</p>
        <pre><code>pip install abom
abom install abom-skill</code></pre>
      </section>
      <section>
        <h2>Manage</h2>
        <p>List the catalog, check a tool, and remove it when you are done.</p>
        <pre><code>abom recipes
abom check abom-skill
abom remove abom-skill</code></pre>
      </section>
      <section>
        <h2>Outside</h2>
        <p>Link a tool into another project, or install a checkout that lives outside this one.</p>
        <pre><code>abom link abom-skill ~/other-project
abom install extra-tool ~/outside-tool</code></pre>
      </section>
    </div>
    <p class="lede">Skills, prompts, MCP servers, and packages.</p>
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
