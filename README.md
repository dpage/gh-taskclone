# gh-taskclone

This is a simple tool for copying tasks (Github issue) from one Github repository to another. This was born from 
the intention to use Github issues for tracking tasks related to PostgreSQL Conferencce Europe, where there are a 
subset of tasks that occur for each annual event. 

The tool will copy issue titles, the body (first comment), milestones if enabled, the label used to mark an issue as "annual", and any other
whitelisted labels (or all labels, if the whitelist is omitted).

## Dependency

```bash
pip install github3.py
```

## Usage

```bash
$ python3 gh-taskclone.py -h
usage: gh-taskclone.py [-h] --source-repo SOURCE_REPO --source-owner SOURCE_OWNER --target-repo TARGET_REPO --target-owner TARGET_OWNER [--label LABEL] [--clone-milestones]
                       [--whitelist WHITELIST]

Copy tasks (issue titles) from one Github project to another.

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
  --whitelist WHITELIST
                        a comma delimited list of labels to copy (in addition to the selection label). If omitted, all labels will be copied.
```

Note: `SOURCE_OWNER` and `TARGET_OWNER` is the owner of the repository (the part after `github.com`), not necessarily your own username.

## Example

```bash
$ python3 gh-taskclone.py --source-repo gh-tc-source --source-owner dpage --target-repo gh-tc-target --target-owner dpage --whitelist venue,party 
Creating label: annual
Creating label: party
Creating issue: Find a party venue
Creating issue: Review AV requirements
Creating label: venue
Creating issue: Find a venue.
Copied 3 tasks.
```

```bash
$ python3 gh-taskclone.py --source-repo pgconfde2025 --source-owner pgeu --target-repo gh-issue-copy-test --target-owner ImTheKai --clone-milestones
Logging into GitHub...
Login successful.
Accessing source repo: pgeu/pgconfde2025
Accessing target repo: ImTheKai/gh-issue-copy-test

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
Fetching existing labels from target repo...
Found 9 labels in target.
  Creating label: annual
  - Using labels: annual
  -> Successfully created issue #11.

Processing source issue #72: 'Create A6 cards for Lightning Talks'
  - Using labels: annual
  -> Successfully created issue #12.
```

## Add tasks to Project

Go into the repository, click on `Issues`, select all newly created Issues. Then click on *Projects* and assign the Issues to your Project. The Issues will show up in the first column of the Project dashboard.

## Authentication

The script needs to authenticate with Github, for which a Personal Access Token is required. This can either be stored
as a single line in ```~/.github-token```, or exported in the ```GITHUB_TOKEN``` environment variable.
