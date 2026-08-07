# ClaudeProjects Status Dashboard

A cross-project status overview built on the markdown you already keep. One
Python file, one HTML template, three well-known dependencies. No database,
no build step, no cloud, no account. Purely local. Binds to `127.0.0.1` only.

---

## Run it

**macOS — easiest:** double-click **`run.command`** in Finder. On first run it
builds a virtual environment in `_dashboard/.venv`, installs the dependencies
into it, and starts the dashboard. Every later run reuses it and starts
immediately. (If Finder refuses to open it, run `chmod +x run.command` once.)

**macOS — terminal:**

```
cd _dashboard
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python dashboard.py
```

**Windows:**

```
cd _dashboard
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python dashboard.py
```

### Two install gotchas

> **`python: command not found` on macOS.** Expected — macOS 12.3 and later ship
> **`python3`** only; bare `python` was Python 2 and has been removed. Use
> `python3`. Windows is the reverse: the python.org installer provides `python`.

> **`ModuleNotFoundError: No module named 'yaml'`, or pip says
> `externally-managed-environment`.** Homebrew (and Debian/Ubuntu) Python is
> marked externally managed under PEP 668, so pip refuses to install into its
> site-packages — that space belongs to the package manager. **Use a virtual
> environment**, as above; it is isolated, needs no admin rights, and is
> deleteable with `rm -rf .venv`. Avoid `pip install --break-system-packages`,
> which does what its name says.

The venv lives inside `_dashboard/`, which the scanner skips, so it never shows
up as a project.

Opens <http://127.0.0.1:8765> in your browser. Ctrl-C to stop.
Python 3.9+ required. `waitress` is optional but gives a cleaner server;
without it Flask's built-in server is used.

| Flag | What it does |
|---|---|
| *(none)* | Live, editable dashboard + file watcher |
| `--once` | Rewrite `dashboard.html` and exit |
| `--no-watch` | Server without the file watcher |
| `--no-browser` | Don't auto-open a browser |
| `--port 9000` | Use a different port (the snapshot's "open live" link follows it) |

## Two ways to view it

**Live** (`python dashboard.py`) — full editing. Change a status, set a due
date, tick sub-tasks, add or delete notes. Every change writes straight back
into the project's tracker markdown.

**Snapshot** (`dashboard.html` in the ClaudeProjects folder) — read-only, fully
self-contained, zero dependencies. Double-click it on any machine, including
Windows with nothing installed. It is rewritten on every change while the
server runs, so it is always current as of the last time the server saw the
files. Copy the whole folder anywhere and this still opens and renders.

---

## How the data works

The **YAML frontmatter of each `00-*tracker*.md` is the single source of
truth.** The "At a glance" table, the "Sub-tasks" section and the "Notes"
section in those files are *generated* from that frontmatter into marked
regions:

```
<!-- STATUS:START   ... -->   ...generated table...      <!-- STATUS:END -->
<!-- SUBTASKS:START ... -->   ...generated checklists...  <!-- SUBTASKS:END -->
<!-- NOTES:START    ... -->   ...generated notes...       <!-- NOTES:END -->
```

Writes only ever touch the frontmatter and the inside of those three regions.
**All of your prose — the detail sections, document indexes, research notes —
is never modified.** This was verified byte-for-byte against the originals.

A consequence worth knowing: **body prose is never *read*.** A checklist you
hand-write in the markdown body will not appear on the dashboard — it has to
live in the frontmatter to be seen. If a region's markers are absent, the
`SUBTASKS` and `NOTES` sections are appended automatically the first time an
item has data for them.

### Item schema

```yaml
- id: 3
  title: "**Renew the office lease**"               # markdown allowed
  priority: 2                                       # 1-5, 1 = highest; blank = unset
  status: blocked                                   # see vocabulary below
  next: "Send the signed rider back to the agent"
  blocked_on: "Countersignature from the landlord"
  due: 2026-08-15                                   # optional, YYYY-MM-DD
  doc: "02-lease-terms.md"
  subtasks:                                         # optional; see below
    - text: "Compare the renewal quote against market rates"
      done: true
    - text: "Have the rider reviewed before signing"
      done: false
  notes:
    - date: 2026-07-24
      text: "Agent says countersignature takes ~3 weeks."
```

### Sub-tasks

Per-item checklists — the small steps that must all happen before an item is
done. Each entry is `{text, done}`; a bare string is also accepted and read as
not-done, so `subtasks: ["Order death certificate"]` is a valid stub.

They surface in three places:

| Where | Form |
|---|---|
| Dashboard row | An `x/y` pill beside the title, and a **sub-tasks** expander with live checkboxes |
| `At a glance` table | ` · x/y` appended to the item title |
| `SUBTASKS` region | A `<details>` block per item with a nested `- [x]` / `- [ ]` checklist |

Ticking a box in the live dashboard writes `done:` straight back to the
frontmatter and regenerates both the table and the checklist. You can also add
a sub-task from the expander (type, press Enter) or delete one with `×`. In the
read-only snapshot the boxes are disabled.

**Sub-tasks never change an item's `status`.** Completing every box does not
flip an item to `done`, by design: a workstream can have all its paperwork
gathered and still be `waiting` on a third party. Status stays a human
judgement — see the vocabulary below.

Items with no sub-tasks show no pill and are skipped in the generated region.

### Status vocabulary

The key distinction is **whose move is it**.

| Status | Meaning | Whose move |
|---|---|---|
| 🔵 `todo` | Yours, not started — "ready to send", "book now" | Yours |
| 🟡 `active` | Yours, under way | Yours |
| 🟣 `waiting` | You have acted; a third party owes you a response | Theirs |
| ⚫ `blocked` | A dependency is unfulfilled, or an external decision / authority is required | Theirs |
| 🟢 `done` | Complete | — |

`blocked` is **not** for work that is merely unstarted. If you could act on it
today, it is `todo`. This matters because the blocked count is meant to be the
list of things you cannot fix by working harder.

**Red is never a status colour** — it is reserved for overdue dates, so the
only red on the dashboard is a genuine time problem.

### Priority and the attention strip

`priority` is 1-5, 1 highest, blank by default. It is editable from the
dashboard and is written straight back to the frontmatter and the `P` column of
the generated table.

The **Needs attention** strip at the top is the one cross-project view: it lists
priority 1-2 items that are not done, across every project, sorted by priority,
then status, then due date. Items at priority 3-5, and items with no priority,
never appear there. With no priorities set the strip shows an empty state — it
fills up as you triage.

`due` is optional and starts empty — no deadlines were invented for you.

### File convention

```
Projects/
  Office Lease/
    00-project-tracker.md            <- primary tracker (exact name)
    00-project-tracker.sub.01.md     <- optional sub-tracker
    00-project-tracker.sub.legal.md  <- the token can be anything
  Car Insurance/
    notes.md                         <- no tracker: shows "No tracker file found."
  _dashboard/
```

**Discovery rules** — projects are found, never hardcoded:

| Rule | Detail |
|---|---|
| Root | The parent of `_dashboard/`, derived from the script's own location |
| Project | Each immediate subfolder, one level deep only |
| Primary | A file named exactly `00-project-tracker.md` |
| Sub-tracker | `00-project-tracker.sub.<token>.md` |
| Section label | The sub-tracker's frontmatter `section:`, falling back to `<token>` |
| Project name | The primary tracker's `project:`, falling back to the folder name |
| Ignored | `_dashboard`, `.git`, `node_modules`, `__pycache__`; any folder starting with `_` or `.`; any folder containing `.no-dashboard` |

Requiring the exact primary filename is deliberate. An earlier glob-based
version matched `00-*tracker*.md` and took the first hit alphabetically — which
meant `00-project-tracker copy.md`, an iCloud `… 2.md`, a Dropbox conflicted
copy, or an archived `00-old-tracker.md` would all silently *outrank* the real
file and start receiving your edits. An exact name removes that class of bug.

### Folders with no tracker

They still appear, with no status and the message **"No tracker file found."**
Sub-trackers in such a folder are ignored (and counted in the message), since a
sub-tracker without a primary has no project to belong to.

This is deliberate — a folder you *meant* to set up shouldn't vanish silently.
If you want a folder gone from the dashboard entirely, opt it out.

### Keeping a folder off the dashboard

Not everything under `ClaudeProjects/` is a project. Two opt-outs, either of
which removes the folder completely — no card, no "No tracker file found":

| Method | How | Use when |
|---|---|---|
| **Name prefix** | Rename the folder to start with `_` (or `.`) — e.g. `_Scratch` | You don't mind the name change; it is visible and self-documenting, and matches `_dashboard` |
| **Marker file** | Put an empty file called **`.no-dashboard`** inside it | You want to keep the folder's normal name |

```
touch "ClaudeProjects/Some Folder/.no-dashboard"
```

Both are **reversible and non-destructive**: rename back, or delete the marker,
and the project reappears at the next sync with its tracker untouched. Opting
out only stops the folder being *scanned* — no tracker file is modified, and
nothing is deleted. There is nothing to clean up afterwards.

To remove a project that is currently tracked, opt it out by either method; the
snapshot is rewritten on the next sync and the card disappears. Leave the
tracker file in place — it will be picked up again unchanged if you opt back in.

### Sub-trackers

Each sub-tracker is a normal tracker file with its own `items:` and its own
generated `STATUS` region. Items are grouped under their section heading inside
the parent project's table, with a roll-up pill: a section shows **Blocked** if
any item in it is blocked, **Done** only when every item is done. Editing an
item writes back to the file it came from — verified by hashing every tracker
before and after an edit.

Item `id`s only need to be unique *within their own file*.

On startup (and on `--once`), every tracker's marker regions are regenerated
from its frontmatter. This is idempotent — files already in sync are not
rewritten, so mtimes and the `updated:` date are left alone. It exists so a
hand-added project, or one whose frontmatter you edited directly, gets its
table filled in without you having to make an edit in the browser first.

---

## Design notes

- **Snapshot vs live.** A `file://` page cannot read sibling files (browser
  security), so the snapshot embeds its data rather than fetching it. That is
  what makes it survive being copied to another machine.
- **Race safety.** Every write re-reads the file from disk first, holds a lock,
  and writes atomically via a temp file + `os.replace`. Editing in a text editor
  while the dashboard is open is safe; last write wins.
- **Watcher.** Changes to any `.md` under the root refresh the snapshot and bump
  a revision counter; the open page polls that counter every 4s and repaints.
  So edits made by hand — or by Claude — show up without a manual refresh.
- **Frontmatter over prose.** Anything that should appear on the dashboard —
  statuses, sub-tasks, notes — is structured data in the frontmatter, not text
  in the body. The body is for prose the generator should leave alone. This is
  why sub-tasks are a `subtasks:` list rather than a hand-written checklist.
- **Privacy.** Nothing leaves the machine. The server is loopback-only. Only
  tracker frontmatter reaches the dashboard, so the PII files in your project
  folders are never read or rendered.

## Files

| File | Role |
|---|---|
| `dashboard.py` | Scanner, markdown reader/writer, API, snapshot generator, watcher |
| `template.html` | The single-page UI (CSS + JS inline); used for both modes |
| `../dashboard.html` | Generated read-only snapshot |
