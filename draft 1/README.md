# Go Lab Geospatial — portfolio

A small static site generator. It reads the YAML in `data/` plus the templates in
`templates/` and writes plain HTML into `docs/`, which GitHub Pages can serve
directly. There is no framework, no client-side JavaScript, and no build step that
needs a bundler.

The site's purpose is verification. A stranger should be able to read the methods,
the data sources and the validation numbers, and conclude the work is
trustworthy. So every claim on the site is either taken from a data file or
fetched from a public metadata API. Nothing is typed in by hand at render time
and no placeholder is ever shown in place of content that has not been supplied.

## Requirements

```
pip install pyyaml jinja2
```

Python 3.9 or newer. Those two packages are the entire dependency list.

## Build

```
python3 build.py
```

Output lands in `docs/`. Useful flags:

| Flag | Effect |
| --- | --- |
| `--out DIR` | build somewhere other than `docs/` |
| `--no-network` | never contact any API; use the cache and the YAML fallbacks only |

## Preview locally

`docs/` is a directory of files, so any static server works. From this folder:

```
python3 -m http.server 8000 --directory docs
```

Then open <http://localhost:8000>. The site is fully static, so you can also open
`docs/index.html` straight off disk — though the browser will resolve some links
more reliably over a server than via `file://`.

## Publish on GitHub Pages

The repo publishes from the `docs/` folder on a branch.

**First time, via the settings UI:** open **Settings → Pages**, set **Source** to
**Deploy from a branch**, choose the branch you want to build from and set the
folder to `/docs`, then save. GitHub will serve whatever is committed in `docs/`.

**After that, keep it simple.** Commit the built site:

```
python3 build.py
git add docs
git commit -m "Rebuild site"
git push
```

If you would rather have the build run on every push instead of committing the
output, delete `docs/` from version control, add a `.github/workflows/pages.yml`
that runs `pip install pyyaml jinja2 && python3 build.py` and uploads `docs/`,
and set **Settings → Pages → Source** to **GitHub Actions**. The committed-output
route is simpler and works with no secrets, so start there.

### Adding a custom domain later

1. Create the domain, and add a **CNAME** file at the root of `docs/` containing
   just the hostname, one line, no protocol, no trailing slash:
   ```
   golabgeospatial.com
   ```
2. Rebuild and commit `docs/CNAME`.
3. In the domain registrar, point the apex or `www` record at GitHub Pages:
   - apex: `A` records to `185.199.108.153`, `185.199.109.153`,
     `185.199.110.153`, `185.199.111.153`
   - `www`: a `CNAME` to `<user>.github.io`
4. In **Settings → Pages**, set the custom domain and wait for the TLS
   certificate to issue. Enable **Enforce HTTPS** once it does.

To keep the CNAME across a clean rebuild, have `build.py` write it if a
`domain.txt` file exists at the root of this folder. That is deliberately not
implemented yet, so nothing guesses a hostname for you.

## How publication metadata is fetched

`data/publications.yaml` lists one entry per paper, keyed by DOI. At build time
`build.py` asks **Crossref** and **OpenAlex** for the authoritative record —
title, authors, year, venue, abstract, open-access status and the OA PDF URL.
Neither API needs a key.

Responses are cached under `data/cache/`, so once a machine has built the site
it can rebuild with no network at all, and the cached copy is what makes an
offline build produce the same page.

If an API is unreachable, the builder falls back to the `fallback_*` fields in the
YAML. Those were read back from Crossref rather than typed in, so the offline
result is accurate rather than approximate. If even that is missing, the field is
simply omitted — it is never invented.

### The access rules

The open-access status drives what gets linked, and the rules are enforced in
`templates/publications.html`:

- **Open access** — full citation, abstract, a **Read full paper, open access**
  link to the OA PDF, and the DOI.
- **Closed access** — citation and abstract, plus the DOI labelled
  **Full text via publisher**.
- **Unknown** — the plain DOI link and an honest note that access status was not
  confirmed at build time. The builder never guesses in either direction, and it
  never links a pirated copy.

Figures reproduced from a paper are only embedded if that paper's licence allows
it, for example CC-BY. Otherwise, use your own figures. Record the licence per
figure in the project YAML.

## Layout

```
draft 1/
  build.py              the generator
  README.md             this file
  data/
    resume.yaml         bio, experience, education, skills, contact
    publications.yaml   one entry per paper, keyed by DOI
    cache/              API responses, for offline builds
    projects/
      <slug>.yaml       one file per project
  templates/
    base.html           shared layout, nav, footer
    home.html
    projects_index.html
    project.html
    publications.html
    standards.html      "How I deliver"
    about.html
  assets/
    css/style.css
    projects/<slug>/    figures, named 01-overview.png and so on
  docs/                 generated output — never edit by hand
```

## Unfinished content

Fields still marked `# TODO` in the YAML are suppressed by the build, not
rendered. Right now that is the work email, the one-sentence positioning line,
and the two manuscripts with no DOI yet. Search for `TODO` to find them all.

Add alt text to every figure. The generator will not invent any, so an image
without an `alt` in the project YAML ships with an empty one — fix that before
publishing.

---

## How to add a new project or paper

**To add a paper:** drop a new entry into `data/publications.yaml` under
`papers:`. The only field the fetcher needs is `doi`:

```yaml
  - id: short-unique-slug
    doi: "10.xxxx/your-doi-here"
    kind: journal-article
    fallback_title: "Title, as a fallback if Crossref is unreachable"
    fallback_venue: "Journal Name"
    fallback_year: 2026
```

The title, authors, venue and abstract come from Crossref, so `fallback_*` is
only used when the API is down. Then run `python3 build.py` and commit `docs/`.

**To add a project:** create `data/projects/<slug>.yaml`, where `<slug>` is the
URL — `reef-bathymetry` becomes `projects/reef-bathymetry.html`. The builder
looks for figures in `assets/projects/<slug>/` automatically. Add `featured: true`
to have it appear on the home page. The full section list, with comments, is in
`data/projects/TEMPLATE.yaml`.
