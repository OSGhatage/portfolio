#!/usr/bin/env python3
"""
Static site generator for the Go Lab Geospatial portfolio.

Reads YAML data from data/ and templates from templates/, and writes plain
static HTML into docs/, ready to be served by GitHub Pages.

Dependencies: pyyaml, jinja2. Nothing else.

Network is optional. At build time each publication is looked up on Crossref
and OpenAlex (neither needs an API key). Responses are cached under
data/cache/, so a machine that has built once can rebuild with no network at
all. When an API is unreachable, the builder falls back to the fallback_*
fields in publications.yaml, which were themselves read back from Crossref and
so are accurate rather than guessed.

The output is fully static. No JavaScript is emitted or required.

Usage:
    python3 build.py                 # build into docs/
    python3 build.py --out dist      # build somewhere else
    python3 build.py --no-network    # use cache and YAML only, never dial out
"""

import argparse
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    import yaml
except ImportError:
    sys.exit("pyyaml is required:  pip install pyyaml")

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ImportError:
    sys.exit("jinja2 is required:  pip install jinja2")

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
TEMPLATES = os.path.join(ROOT, "templates")
ASSETS = os.path.join(ROOT, "assets")
CACHE = os.path.join(DATA, "cache")

USER_AGENT = "GoLabGeospatial-Portfolio/1.0 (static site build; mailto:contact@example.com)"
HTTP_TIMEOUT = 12

# Anything still marked TODO in the YAML is suppressed rather than rendered.
TODO_SENTINEL = "TODO"


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def is_missing(value):
    """True when a value should be suppressed from the site entirely.

    An unset field is not the same as an empty one. A field still carrying the
    TODO sentinel means the owner has not supplied it, and the site must not
    pretend otherwise by showing a placeholder.
    """
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped == TODO_SENTINEL:
            return True
        # A bare "# TODO ..." comment leaked into a value.
        if stripped.startswith("#") and TODO_SENTINEL in stripped[:6]:
            return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def clean(value):
    """Return value, or None if it is still a TODO placeholder."""
    return None if is_missing(value) else value


def load_yaml(path):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return slug or "item"


def cache_path(source, doi):
    return os.path.join(CACHE, source, f"{slugify(doi)}.json")


def read_cache(source, doi):
    path = cache_path(source, doi)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return None


def write_cache(source, doi, payload):
    directory = os.path.join(CACHE, source)
    os.makedirs(directory, exist_ok=True)
    with open(cache_path(source, doi), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def http_get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


# --------------------------------------------------------------------------
# publication metadata
# --------------------------------------------------------------------------

def crossref_fetch(doi, allow_network):
    """Return a normalised Crossref record for doi, or None."""
    cached = read_cache("crossref", doi)
    if cached:
        return cached
    if not allow_network:
        return None
    try:
        raw = http_get_json(
            "https://api.crossref.org/works/"
            + urllib.parse.quote(doi, safe="")
        )
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return None
    if raw.get("status") != "ok":
        return None
    record = normalise_crossref(raw.get("message", {}))
    write_cache("crossref", doi, record)
    return record


def normalise_crossref(message):
    """Reduce a Crossref work message to the fields the site actually uses."""
    titles = message.get("title") or []
    containers = message.get("container-title") or []
    issued = message.get("issued", {}).get("date-parts") or [[None]]
    year = issued[0][0] if issued and issued[0] else None

    authors = []
    for person in message.get("author") or []:
        family = person.get("family")
        if not family:
            continue
        authors.append({
            "given": person.get("given") or "",
            "family": family,
            "orcid": (person.get("ORCID") or "").replace("https://orcid.org/", ""),
        })

    return {
        "source": "crossref",
        "doi": message.get("DOI", ""),
        "title": titles[0] if titles else None,
        "venue": containers[0] if containers else None,
        "year": year,
        "type": message.get("type"),
        "authors": authors,
        "abstract": message.get("abstract"),
        "license": message.get("license") or [],
        "is_oa": None,      # set below only when a licence clearly says so
        "oa_url": None,
    }


def openalex_fetch(doi, allow_network):
    """Return a normalised OpenAlex record for doi, or None.

    OpenAlex is the better signal for open-access status and the OA PDF URL,
    because Crossref licences describe reuse rights rather than free-to-read.
    """
    cached = read_cache("openalex", doi)
    if cached:
        return cached
    if not allow_network:
        return None
    try:
        raw = http_get_json(
            "https://api.openalex.org/works/https://doi.org/"
            + urllib.parse.quote(doi, safe="")
        )
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not raw or not raw.get("id"):
        return None
    record = normalise_openalex(raw)
    write_cache("openalex", doi, record)
    return record


def normalise_openalex(work):
    is_oa = work.get("open_access", {}).get("is_oa")
    oa_url = None
    oa_status = work.get("open_access", {}).get("oa_status") or None
    best = work.get("best_oa_location") or {}
    if best.get("pdf_url"):
        oa_url = best["pdf_url"]
    elif best.get("landing_page_url"):
        oa_url = None   # landing page only, do not present as a PDF

    return {
        "source": "openalex",
        "doi": (work.get("doi") or "").replace("https://doi.org/", ""),
        "is_oa": is_oa,
        "oa_status": oa_status,
        "oa_url": oa_url,
    }


def build_paper_record(paper, allow_network, stats):
    """Merge YAML, Crossref and OpenAlex into one record for rendering.

    Precedence is fetched > cached > YAML fallback. Nothing is invented: if no
    source supplies a field, it stays None and the template omits it.
    """
    doi = paper["doi"]
    record = {
        "id": paper.get("id") or slugify(doi),
        "doi": doi,
        "doi_url": f"https://doi.org/{doi}",
        "title": clean(paper.get("fallback_title")),
        "venue": clean(paper.get("fallback_venue")),
        "year": paper.get("fallback_year"),
        "authors": [],
        "abstract": None,
        "is_oa": None,
        "oa_status": None,
        "oa_url": None,
        "metadata_source": "publications.yaml",
    }

    crossref = crossref_fetch(doi, allow_network)
    if crossref:
        stats["crossref_hit"] += 1
        for field in ("title", "venue", "year", "abstract"):
            if crossref.get(field):
                record[field] = crossref[field]
        if crossref.get("authors"):
            record["authors"] = crossref["authors"]
        record["metadata_source"] = "crossref"

    openalex = openalex_fetch(doi, allow_network)
    if openalex:
        stats["openalex_hit"] += 1
        if openalex.get("is_oa") is not None:
            record["is_oa"] = openalex["is_oa"]
        record["oa_status"] = openalex.get("oa_status")
        if openalex.get("oa_url"):
            record["oa_url"] = openalex["oa_url"]

    # Crossref alone cannot establish open access, so only trust an explicit
    # OpenAlex answer. Unknown stays unknown and is never presented as closed.
    record["is_owner"] = any(
        a["family"].lower().startswith(("ghatage", "gatage"))
        for a in record["authors"]
    )
    return record


def citation_text(record):
    """Plain-text citation, in the style the prompt asks for."""
    names = [a["family"] for a in record["authors"]]
    if names:
        authors = ", ".join(names[:-1]) + (", & " + names[-1] if len(names) > 1 else names[0])
        if len(names) > 3:
            authors = ", ".join(names[:3]) + " et al."
    else:
        authors = None
    bits = [b for b in (authors, str(record["year"]) if record["year"] else None,
                        record["title"], record["venue"]) if b]
    return ". ".join(bits) + "."


# --------------------------------------------------------------------------
# site assembly
# --------------------------------------------------------------------------

def load_projects():
    directory = os.path.join(DATA, "projects")
    if not os.path.isdir(directory):
        return []
    projects = []
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith((".yaml", ".yml")):
            continue
        # TEMPLATE.yaml and anything like it is documentation, not a project.
        # It must never be rendered, or its example content would appear on the
        # live site as though it were real work.
        if filename.upper().startswith(("TEMPLATE", "_")):
            continue
        project = load_yaml(os.path.join(directory, filename))
        project.setdefault("slug", os.path.splitext(filename)[0])
        project["figures"] = find_figures(project["slug"], project)
        projects.append(project)

    def sort_key(item):
        return (str(item.get("date") or ""), item.get("title") or "")

    projects.sort(key=sort_key, reverse=True)
    return projects


def find_figures(slug, project):
    """List real figure files for a project. Never fabricates imagery.

    Anything the owner has not supplied is simply absent, and the page says so
    in plain words rather than showing a broken or invented image.
    """
    declared = project.get("figures") or []
    if declared:
        found = []
        for figure in declared:
            entry = dict(figure)
            entry["src"] = f"assets/projects/{slug}/{entry['src']}"
            entry["alt"] = clean(entry.get("alt")) or ""
            found.append(entry)
        return found

    directory = os.path.join(ASSETS, "projects", slug)
    if not os.path.isdir(directory):
        return []
    found = []
    for filename in sorted(os.listdir(directory)):
        if not filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".svg")):
            continue
        found.append({
            "src": f"assets/projects/{slug}/{filename}",
            "caption": os.path.splitext(filename)[0].replace("-", " "),
            "alt": "",   # TODO: owner must supply alt text; left blank on purpose
        })
    return found


def related_publications(project, papers_by_id, all_papers):
    related = []
    for ref in project.get("related_publications") or []:
        found = papers_by_id.get(ref)
        if found:
            related.append(found)
    return related


def copy_assets(out_dir):
    target = os.path.join(out_dir, "assets")
    if os.path.isdir(ASSETS):
        shutil.copytree(ASSETS, target, dirs_exist_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Build the portfolio into docs/")
    parser.add_argument("--out", default=os.path.join(ROOT, "docs"),
                        help="output directory (default: docs/)")
    parser.add_argument("--no-network", action="store_true",
                        help="never contact Crossref or OpenAlex; use cache and YAML only")
    args = parser.parse_args()

    allow_network = not args.no_network
    out_dir = os.path.abspath(args.out)

    resume = load_yaml(os.path.join(DATA, "resume.yaml"))
    publications = load_yaml(os.path.join(DATA, "publications.yaml"))
    projects = load_projects()

    stats = {"crossref_hit": 0, "openalex_hit": 0}
    papers = [build_paper_record(p, allow_network, stats)
              for p in publications.get("papers") or []]
    papers.sort(key=lambda p: (p["year"] or 0, p["title"] or ""), reverse=True)

    by_year = {}
    for paper in papers:
        by_year.setdefault(paper["year"], []).append(paper)

    papers_by_id = {p["id"]: p for p in papers}
    for project in projects:
        project["related"] = related_publications(project, papers_by_id, papers)

    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["yearsort"] = lambda v: (v is not None, v)
    env.globals["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Templates call these directly, so an unset TODO field disappears from the
    # page instead of reaching it as a placeholder.
    env.globals["clean"] = clean
    env.globals["cite"] = citation_text

    nav = [
        ("home.html", "Home", "index.html"),
        ("projects_index.html", "Projects", "projects/index.html"),
        ("publications.html", "Publications", "publications.html"),
        ("standards.html", "Standards", "standards.html"),
        ("about.html", "About", "about.html"),
    ]

    common = {
        "resume": resume,
        "projects": projects,
        "nav": nav,
        "depth": 0,
    }

    os.makedirs(out_dir, exist_ok=True)

    def render(template, destination, **context):
        page = env.get_template(template).render(**{**common, **context})
        path = os.path.join(out_dir, destination)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(page)

    # Simple pages sit at the site root, so their asset paths are flat.
    render("home.html", "index.html", active="Home", depth=0,
           papers=by_year, highlighted=[p for p in projects if p.get("featured")][:3])
    render("publications.html", "publications.html", active="Publications", depth=0,
           by_year=by_year, papers=papers,
           other=publications.get("other_contributions") or [],
           in_press=publications.get("in_press") or [],
           under_review=publications.get("under_review") or [])
    render("standards.html", "standards.html", active="Standards", depth=0)
    render("about.html", "about.html", active="About", depth=0)

    # These two live one directory down, so every asset path needs one level.
    render("projects_index.html", os.path.join("projects", "index.html"),
           active="Projects", depth=1)
    for project in projects:
        render("project.html", os.path.join("projects", f"{project['slug']}.html"),
               active="Projects", depth=1, project=project)

    copy_assets(out_dir)

    # docs/ is generated. Say so inside it, so nobody hand-edits the output.
    with open(os.path.join(out_dir, "README.md"), "w", encoding="utf-8") as handle:
        handle.write(
            "# Generated output\n\n"
            "Everything in `docs/` is produced by `../build.py` from the YAML in\n"
            "`../data/` and the templates in `../templates/`. Do not edit these files\n"
            "by hand - your changes will be overwritten on the next build.\n\n"
            "To change the site, edit the data or templates and run:\n\n"
            "```\npython3 ../build.py\n```\n"
        )

    print(f"Built {out_dir}")
    print(f"  publications : {len(papers)} "
          f"(crossref {stats['crossref_hit']}, openalex {stats['openalex_hit']})")
    print(f"  projects     : {len(projects)}")
    unpublished = len(publications.get("in_press") or []) + \
        len(publications.get("under_review") or [])
    print(f"  in press / under review : {unpublished}")
    if not stats["crossref_hit"] and not stats["openalex_hit"]:
        print("  note: no live metadata was available; used cache and YAML fallbacks.")


if __name__ == "__main__":
    main()
