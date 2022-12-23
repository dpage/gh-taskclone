# gh-taskclone

This is a simple tool for copying tasks (Github issue titles) from one Github repository to another. This was born from 
the intention to use Github issues for tracking tasks related to PostgreSQL Conferencce Europe, where there are a 
subset of tasks that occur for each annual event. It does not copy the comments or other metadata on issues, with one
exception: the label that is used to indicate that a task occurs every year.

## Usage

```bash
$ python3 gh-taskclone.py -h
usage: gh-taskclone.py [-h] --source-repo SOURCE_REPO --source-owner SOURCE_OWNER --target-repo TARGET_REPO --target-owner TARGET_OWNER [--label LABEL]

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

```

## Example

```bash
$ python3 gh-taskclone.py --source-repo source-repo --source-owner dpage --target-repo target-repo --target-owner dpage 
Creating: Find a venue
Creating: Book a party location
Copied 2 tasks.
```

## Authentication

The script needs to authenticate with Github, for which a Personal Access Token is required. This can either be stored
as a single line in ```~/.github-token```, or exported in the ```GITHUB_TOKEN``` environment variable.