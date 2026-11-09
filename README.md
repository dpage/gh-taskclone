# gh-taskclone

[![Unit tests](https://github.com/dpage/gh-taskclone/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/dpage/gh-taskclone/actions/workflows/tests.yml)

This is a simple tool for copying tasks (GitHub issues) from one GitHub repository to another. It was born from
the intention to use GitHub issues for tracking tasks related to PostgreSQL Conference Europe, where there is a
subset of tasks that occur for each annual event.

The tool will copy issue titles, the body (description), milestones if enabled, the label used to select the issues
(for example `annual`), and any other labels listed in `--attach-labels` (or all labels of the issue,
with `--attach-labels all`). Optionally all labels of the source repository can be cloned to the target
repository first (`--clone-labels`). Task list items in the issue body are copied unchecked.

The copied issues can be added to a GitHub Project (`--target-project`), and the Status columns and the date fields
of another Project can be cloned into it first (`--clone-project`).

## Dependencies

```bash
pip install -r requirements.txt
```

## Usage

```bash
$ python3 gh-taskclone.py -h
usage: gh-taskclone.py [-h] --source-repo SOURCE_REPO --source-owner SOURCE_OWNER --target-repo TARGET_REPO --target-owner TARGET_OWNER [--label LABEL]
                       [--clone-milestones] [--clone-labels] [--attach-labels ATTACH_LABELS]

Copy tasks (issues) from one Github project to another, optionally including labels and milestones.

options:
  -h, --help            show this help message and exit
  --source-repo SOURCE_REPO
                        the source repo name
  --source-owner SOURCE_OWNER
                        the source repo owner name (org or user)
  --target-repo TARGET_REPO
                        the target repo name
  --target-owner TARGET_OWNER
                        the target repo owner name (org or user)
  --label LABEL         a label to limit copying to (default: annual). Issues MUST have this label to be copied.
  --clone-milestones    Enable cloning of milestones and assignment of issues to them. Default: False
  --clone-labels        Enable cloning of all labels of the source repo (name, color, description) to the target repo. Missing labels are created, existing labels get
                        color and description overwritten from the source. Labels are never deleted. Default: False
  --attach-labels ATTACH_LABELS
                        a comma delimited list of labels to attach to the copied issues (in addition to the selection label), or 'all' to attach all labels of the
                        source issue. If omitted, only the selection label is attached.
```

Note: `SOURCE_OWNER` and `TARGET_OWNER` are the owners of the repositories (the part after `github.com/` in the URL), not necessarily your own username.

## Labels

* By default (without `--attach-labels`) only the selected label (`--label`, default `annual`) is copied to the new issues. Other labels are not copied.
* `--attach-labels venue,party` attaches these labels (if the source issue carries them), in addition to the selection label. Listing the selection label is accepted.
* `--attach-labels all` attaches all labels of the source issue. `all` (in any case) cannot be combined with other labels.
* Label names are matched case-insensitively.
* `--clone-labels` clones all labels of the source repository, independent of `--label` and `--attach-labels`. Missing labels are created (name, color, description); for labels existing in both repositories color and description are overwritten from the source. Labels which exist only in the target are left alone; labels are never deleted. Any failure while cloning labels stops the script; labels handled before the failure are kept.
* Before anything is written, all labels listed in `--attach-labels` are checked. A label must exist in the source or the target repository, otherwise (for example a typo) the script exits without changes. A listed label which exists only in the source is created in the target when an issue carrying it is copied, with or without `--clone-labels`.
* Comma limitation: GitHub allows commas in label names, but `--attach-labels` is a comma separated list and cannot express them. If a listed name is missing and is part of such a label, the script exits with an error. Use `--attach-labels all` in this case.
* Arguments are checked right after parsing, before any network access: an empty value (`""`, `" "`, `,`), and `all` combined with other labels fail with a usage message (exit code 2).

## Examples

Only the selection label is attached:

```bash
$ python3 gh-taskclone.py --source-repo gh-tc-source --source-owner pgeu --target-repo gh-tc-target --target-owner pgeu
...
-- Label Cloning Disabled --

-- Milestone Cloning Disabled --

Fetching issues from 'pgeu/gh-tc-source' with label 'annual'...
Found 3 issues.

Starting creation of 3 issues in 'pgeu/gh-tc-target'...

Processing source issue #3: 'Find a party venue'
  Creating label 'annual'
  - Using labels: 'annual'
  -> Successfully created issue #1.
...

Finished. Copied 3 tasks.
```

Attach additional labels, and clone all labels first:

```bash
$ python3 gh-taskclone.py --source-repo gh-tc-source --source-owner pgeu --target-repo gh-tc-target --target-owner pgeu --clone-labels --attach-labels "venue,party"
...
-- Label Cloning Enabled --

Attempting to clone labels...
Found 9 labels in 'pgeu/gh-tc-source'.
  - Creating label 'party'
  - Creating label 'good first issue'
  - Updating label 'venue' (color 'ededed' -> 'fbca04', description)
Label cloning finished: 2 created, 1 updated, 6 unchanged.
...
Processing source issue #3: 'Find a party venue'
  - Using labels: 'annual', 'party', 'venue'
  -> Successfully created issue #1.
...

Finished. Copied 3 tasks.
```

Attach all labels of the source issues:

```bash
$ python3 gh-taskclone.py --source-repo gh-tc-source --source-owner pgeu --target-repo gh-tc-target --target-owner pgeu --attach-labels all
...
Processing source issue #2: 'Review AV requirements'
  Creating label 'av'
  - Using labels: 'annual', 'av'
  -> Successfully created issue #2.
...

Finished. Copied 3 tasks.
```

A label which exists in neither repository fails before any change is made:

```bash
$ python3 gh-taskclone.py --source-repo gh-tc-source --source-owner pgeu --target-repo gh-tc-target --target-owner pgeu --attach-labels "venu,party"
...
Found 1 labels in target.
Error: these labels given in --attach-labels exist in neither the source nor the target repository: 'venu'
No changes were made.
```

Argument errors are reported before any network access:

```bash
$ python3 gh-taskclone.py --source-repo a --source-owner b --target-repo c --target-owner d --attach-labels "all,venue"
usage: gh-taskclone.py [-h] ...
gh-taskclone.py: error: 'all' in --attach-labels cannot be combined with other labels
```

A run with milestones:

```bash
$ python3 gh-taskclone.py --source-repo pgconfde2025 --source-owner pgeu --target-repo gh-issue-copy-test --target-owner ImTheKai --clone-milestones
Logging into GitHub...
Login successful.
Accessing source repo: pgeu/pgconfde2025
Accessing target repo: ImTheKai/gh-issue-copy-test
Found 9 labels in target.

-- Label Cloning Disabled --

-- Milestone Cloning Enabled --

Attempting to clone milestones...
Found 13 milestones in 'pgeu/pgconfde2025'.
Found 2 milestones in 'ImTheKai/gh-issue-copy-test'.
  - Creating milestone 'CfP opens'...
    -> Created milestone number 6.
  - Creating milestone 'CfS opens'...
...
Milestone cloning process finished.

Fetching issues from 'pgeu/pgconfde2025' with label 'annual'...
Found 66 issues.

Starting creation of 66 issues in 'ImTheKai/gh-issue-copy-test'...

Processing source issue #75: 'Create dynamic tag for distributing t-shirts at the conference'
  Creating label 'annual'
  - Using labels: 'annual'
  -> Successfully created issue #11.
...

Finished. Copied 66 tasks.
```

## Tests

The unit tests mock the GitHub API, so they need no token or network access:

```bash
pip install -r requirements.txt
python3 -m unittest discover -s tests
```

They run on every pull request, and on every push to `main`.

## Projects

The tool works with the current GitHub Projects (the board and table ones at `github.com/orgs/<owner>/projects/<n>`).

* `--target-project` adds every copied issue to this project of `--target-owner`. The value is the project number
  (`3`), or the full project URL (`https://github.com/orgs/<owner>/projects/3`, or `.../users/<owner>/projects/3`; a
  trailing `/views/<m>` is accepted). The owner in the URL must be `--target-owner`.
* `--clone-project` clones fields from this project of `--source-owner` (number or URL, same format) into the target
  project, once, before the issues are copied. It requires `--target-project`, and source and target project must not
  be the same project.
* Issues are only added to the project, nothing is set on them (no Status etc.): they show up with empty fields, the
  default of the project applies.
* What `--clone-project` copies, each field only if it exists in the source project (a missing field is skipped with an
  info line, and left alone in the target):
  * The `Status` field: the options (the board columns) with color, description and order. Options are matched by
    name, case-insensitively. Missing options are created, matching options get color and description from the source
    (the target keeps its spelling of the name). Options which exist only in the target are kept and sorted after all
    options copied from the source. If the target has no `Status` field, it is created.
  * The date fields `Start date` and `Due date`: created in the target if missing. Existing date fields are left alone.
    The names are fixed. The `Target date` field of GitHub's roadmap template is not matched, a project from that
    template gets new `Start date` and `Due date` fields next to it.
* Nothing else is copied: no items, views, workflows or other fields.
* A field of the same name with another type (for example `Due date` as a text field) is an error, and so are two
  options of a `Status` field which differ only in case. Everything is checked before the first change is made.
* The GitHub API replaces the whole option list of a single select field, and has no way to keep the ids of existing
  options. When the Status options of the target project change, all of its options are therefore expected to get new
  ids, and the items which are already in the target project are expected to lose their Status value. For this reason
  the tool refuses to change the Status options of a target project which already contains items, unless
  `--allow-status-replace` is given (it requires `--clone-project`). A target project without items needs no flag, and
  neither does a run which changes nothing, creates the `Status` field, or has no `Status` field in the source.
* The script has no duplicate detection. If adding an issue to the project fails, the script stops (the issue was
  created and is not rolled back) and prints the source issues copied so far. A rerun would create these issues again,
  so they must be excluded or removed by hand first.
* A failure while cloning the project fields stops the script, the changes made before it are kept.
* Arguments are checked right after parsing, before any network access: an invalid project number or URL, a URL of
  another owner, `--clone-project` without `--target-project`, the same project as source and target, and
  `--allow-status-replace` without `--clone-project` fail with a usage message (exit code 2).

Add the issues to project 3:

```bash
$ python3 gh-taskclone.py --source-repo gh-tc-source --source-owner pgeu --target-repo gh-tc-target --target-owner pgeu --target-project 3
...
Accessing project 3 of 'pgeu'...
Found project 'Annual tasks' with 0 items.

-- Label Cloning Disabled --

-- Project Cloning Disabled --

-- Milestone Cloning Disabled --
...
Processing source issue #3: 'Find a party venue'
  - Using labels: 'annual'
  -> Successfully created issue #1.
  -> Added to project 'Annual tasks' (project 3).
...

Finished. Copied 3 tasks.
```

Clone the Status columns and date fields of another project first, with the project given as URL:

```bash
$ python3 gh-taskclone.py --source-repo pgconfde2025 --source-owner pgeu --target-repo gh-issue-copy-test --target-owner pgeu --target-project https://github.com/orgs/pgeu/projects/3 --clone-project 2
...
Accessing project 3 of 'pgeu'...
Found project 'Annual tasks' with 0 items.
Accessing project 2 of 'pgeu'...
Found project 'Conference' with 12 items.
...
-- Project Cloning Enabled --

Attempting to clone project fields...
Field 'Due date' does not exist in the source project, skipping.
  - Replacing the options of field 'Status' (5 options)
  - Creating field 'Start date'
Project cloning finished: Status 5 options (2 created, 1 updated), 1 field created.
...
Processing source issue #75: 'Create dynamic tag for distributing t-shirts at the conference'
  - Using labels: 'annual'
  -> Successfully created issue #1.
  -> Added to project 'Annual tasks' (project 3).
...
```

A project which does not exist, or which the token cannot access, fails before any change is made:

```bash
$ python3 gh-taskclone.py --source-repo gh-tc-source --source-owner pgeu --target-repo gh-tc-target --target-owner pgeu --target-project 9
...
Accessing project 9 of 'pgeu'...
Error: project 9 of 'pgeu' was not found or is not accessible. The project may not exist, or the token may lack access to it: the token needs the 'project' scope (classic token) or the Projects read/write permission (fine-grained token).
No changes were made.
```

If the Status options of a target project which contains items would change, the script refuses:

```bash
$ python3 gh-taskclone.py --source-repo gh-tc-source --source-owner pgeu --target-repo gh-tc-target --target-owner pgeu --target-project 3 --clone-project 2
...
Accessing project 3 of 'pgeu'...
Found project 'Annual tasks' with 14 items.
Accessing project 2 of 'pgeu'...
Found project 'Conference' with 12 items.
Error: the Status options of the target project 3 of 'pgeu' would change, and it contains 14 items. The update may drop the Status value of existing items. Use --allow-status-replace to do it anyway.
No changes were made.
```

The same run with `--allow-status-replace` prints a warning and continues:

```bash
$ python3 gh-taskclone.py --source-repo gh-tc-source --source-owner pgeu --target-repo gh-tc-target --target-owner pgeu --target-project 3 --clone-project 2 --allow-status-replace
...
Found project 'Conference' with 12 items.
Warning: the Status options of the target project 3 of 'pgeu' are replaced, the 14 existing items may lose their Status value (--allow-status-replace).
...
Project cloning finished: Status 5 options (2 created, 1 updated).
...
```

Argument errors:

```bash
$ python3 gh-taskclone.py --source-repo a --source-owner b --target-repo c --target-owner d --clone-project 2
usage: gh-taskclone.py [-h] ...
gh-taskclone.py: error: --clone-project requires --target-project
```

## Authentication

The script needs to authenticate with GitHub, for which a personal access token is required. This can either be stored
as a single line in `~/.github-token`, or exported in the `GITHUB_TOKEN` environment variable.

To use `--target-project` or `--clone-project`, the token also needs access to the projects: the `project` scope for a
classic token, or the Projects read/write permission for a fine-grained token. Fine-grained tokens may not be able to
access projects owned by a user account (only organization projects), in which case a classic token is needed.

## Rate limits

When GitHub's hourly API rate limit is used up, the script waits until it resets (at most an hour) and then retries
the request once, so a long run pauses rather than stopping partway through. It also pauses for a minute after every
10 issues it creates, to stay below GitHub's secondary rate limit.
