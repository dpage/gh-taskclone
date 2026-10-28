# gh-taskclone

This is a simple tool for copying tasks (Github issue) from one Github repository to another. This was born from 
the intention to use Github issues for tracking tasks related to PostgreSQL Conferencce Europe, where there are a 
subset of tasks that occur for each annual event. 

The tool will copy issue titles, the body (description), milestones if enabled, the label used to the issues (as example "annual"), and any other
labels listed in `--attach-labels` (or all labels of the issue, with `--attach-labels all`). Optionally all labels of the source repository
can be cloned to the target repository first (`--clone-labels`).

## Dependency

```bash
pip install github3.py
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

Note: `SOURCE_OWNER` and `TARGET_OWNER` is the owner of the repository (the part after `github.com`), not necessarily your own username.

## Labels

* By default (without `--attach-labels`) only the selected label (`--label`, default `annual`) is copied to the new issues. Other labels are not copied.
* `--attach-labels venue,party` attaches these labels (if the source issue carries them), in addition to the selection label. Listing the selection label is accepted.
* `--attach-labels all` attaches all labels of the source issue. `all` (in any case) cannot be combined with other labels.
* Label names are matched case-insensitively.
* `--clone-labels` clones all labels of the source repository, independent of `--label` and `--attach-labels`. Missing labels are created (name, color, description), for labels existing in both repositories color and description are overwritten from the source. Labels which exist only in the target are left alone, labels are never deleted. Any failure while cloning labels stops the script; labels handled before the failure are kept.
* Before anything is written, all labels listed in `--attach-labels` are checked. A label must exist in the source or the target repository, otherwise (for example a typo) the script exits without changes. A listed label which exists only in the source is created in the target when an issue carrying it is copied, with or without `--clone-labels`.
* Comma limitation: Github allows commas in label names, but `--attach-labels` is a comma separated list and can not express them. If a listed name is missing and is part of such a label, the script exits with an error. Use `--attach-labels all` in this case.
* Arguments are checked right after parsing, before any network access: an empty value (`""`, `" "`, `,`), and `all` combined with other labels fail with a usage message (exit code 2).

## Examples

Only the selection label is attached:

```bash
$ python3 gh-taskclone.py --source-repo gh-tc-source --source-owner dpage --target-repo gh-tc-target --target-owner dpage
...
-- Label Cloning Disabled --

-- Milestone Cloning Disabled --

Fetching issues from 'dpage/gh-tc-source' with label 'annual'...
Found 3 issues.

Starting creation of 3 issues in 'dpage/gh-tc-target'...

Processing source issue #3: 'Find a party venue'
  Creating label 'annual'
  - Using labels: 'annual'
  -> Successfully created issue #1.
...

Finished. Copied 3 tasks.
```

Attach additional labels, and clone all labels first:

```bash
$ python3 gh-taskclone.py --source-repo gh-tc-source --source-owner dpage --target-repo gh-tc-target --target-owner dpage --clone-labels --attach-labels "venue,party"
...
-- Label Cloning Enabled --

Attempting to clone labels...
Found 9 labels in 'dpage/gh-tc-source'.
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
$ python3 gh-taskclone.py --source-repo gh-tc-source --source-owner dpage --target-repo gh-tc-target --target-owner dpage --attach-labels all
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
$ python3 gh-taskclone.py --source-repo gh-tc-source --source-owner dpage --target-repo gh-tc-target --target-owner dpage --attach-labels "venu,party"
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

## Add tasks to Project

Go into the repository, click on `Issues`, select all newly created Issues. Then click on *Projects* and assign the Issues to your Project. The Issues will show up in the first column of the Project dashboard.

## Authentication

The script needs to authenticate with Github, for which a Personal Access Token is required. This can either be stored
as a single line in ```~/.github-token```, or exported in the ```GITHUB_TOKEN``` environment variable.
