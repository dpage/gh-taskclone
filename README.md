# gh-taskclone

This is a simple tool for copying tasks (Github issue) from one Github repository to another. This was born from 
the intention to use Github issues for tracking tasks related to PostgreSQL Conferencce Europe, where there are a 
subset of tasks that occur for each annual event. 

The tool will copy issue titles, the body (first comment), the label used to mark an issue as "annual", and any other
whitelisted labels (or all labels, if the whitelist is omitted).

## Usage

```bash
$ python3 gh-taskclone.py -h
usage: gh-taskclone.py [-h] --source-repo SOURCE_REPO --source-owner SOURCE_OWNER --target-repo TARGET_REPO --target-owner TARGET_OWNER [--label LABEL] [--whitelist WHITELIST]

Copy tasks (issue titles) from one Github project to another.

options:
  -h, --help            show this help message and exit
  --source-repo SOURCE_REPO
                        the source repo name
  --source-owner SOURCE_OWNER
                        the source repo owner name (owner may be an org or user)
  --target-repo TARGET_REPO
                        the target project name
  --target-owner TARGET_OWNER
                        the target repo owner name (owner may be an org or user)
  --label LABEL         a label to limit copying to (default: annual)
  --whitelist WHITELIST
                        a comma delimited list of labels to copy (in addition to the selection label. If omitted (or empty), all labels will be copied.
```

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

## Authentication

The script needs to authenticate with Github, for which a Personal Access Token is required. This can either be stored
as a single line in ```~/.github-token```, or exported in the ```GITHUB_TOKEN``` environment variable.