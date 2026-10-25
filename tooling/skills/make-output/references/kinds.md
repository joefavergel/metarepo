# Kinds, citations and formats

## Kinds

`<engine> output register --kinds --json` lists every usable kind with its template folder. Built-in kinds ship with
the toolkit; a project kind in `outputs/_templates/<kind>/` replaces the built-in kind of the same name. A kind is
a folder with `template.md` (the skeleton) and `instructions.md` (guidance for the author). Follow both; adapt the
number of sections to the material.

| Kind | Shape | Typical length |
|---|---|---|
| `one-pager` | summary, key points, next steps, sources | one page |
| `brief` | bottom line, context, findings, open points | one to two pages |
| `report` | executive summary, themed sections, limitations | multi-page |
| `faq` | question and short answer pairs | as many as the wiki answers |
| `slides` | one idea per slide, sources slide | 6 to 15 slides |

## Citations (R4)

- Cite wiki pages, not originals. Use a relative Markdown link from the output to the page
  (`../30_wiki/<path>.md`); footnotes (`[^1]`) in documents, a final "Sources" slide in decks.
- One footnote per weight-bearing statement; several statements may share a footnote.
- For a figure taken from an extraction, cite the source page and the locator (page, timestamp, sheet) as
  `consult-wiki` does.
- Pass every page you cite as `--based-on`; the engine records its sha256 so a later change makes the output
  stale.

## Formats (R5)

- **markdown** (default): plain Markdown with frontmatter added by the engine.
- **marp**: Markdown slides separated by `---`; keep `marp: true` in the frontmatter (the engine adds it if
  missing). Keep slide text short; detail goes in HTML comments as speaker notes.
- **html**: one self-contained file: inline CSS and scripts only, no external requests, no remote fonts or
  images (images as `data:` URIs). Provenance is stored in a `.meta.yaml` sidecar next to the file.
- **artifact**: the deliverable lives in a harness-native artifact; the engine writes a stub Markdown file in
  `outputs/` that records the artifact link and the provenance.

## "Not covered" note

Given to the operator in the chat, never written into the file or invented into the content:

```
Not covered by the wiki: <topic or question>.
Closest material: [<page>](<link>) (does not state <what is missing>).
To fill the gap: drop a source in 00_inbox/ and run /intake.
```
