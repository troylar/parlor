# Tutorial: Share a Pack via Git

Publish a pack to a git repository, configure it as a pack source, and have team members consume it automatically.

## Prerequisites

- A pack directory (see [Create a Pack from Scratch](create-pack-from-scratch.md))
- A git remote you can push to (GitHub, GitLab, etc.)

## Step 1: Create a Git Repository

```bash
$ mkdir acme-packs && cd acme-packs
$ git init
```

## Step 2: Add Your Pack

Copy or move your pack into the repository:

```bash
$ cp -r /path/to/python-conventions .
```

Your repo structure:

```
acme-packs/
└── python-conventions/
    ├── pack.yaml
    ├── skills/
    ├── rules/
    ├── instructions/
    └── config_overlays/
```

You can add multiple packs to the same repo:

```
acme-packs/
├── python-conventions/
│   └── pack.yaml
├── security-review/
│   └── pack.yaml
└── frontend-standards/
    └── pack.yaml
```

## Step 3: Push to Remote

```bash
$ git add .
$ git commit -m "feat: add python-conventions pack"
$ git remote add origin https://github.com/acme/anteroom-packs.git
$ git push -u origin main
```

## Step 4: Configure as a Pack Source

On each team member's machine, add the source to config:

```yaml title="~/.anteroom/config.yaml"
pack_sources:
  - url: https://github.com/acme/anteroom-packs.git
    branch: main
    refresh_interval: 30
```

Or distribute via team config to ensure everyone gets it automatically.

## Step 5: Initial Install

The first time Anteroom starts (or when you manually refresh), it clones the repo and installs all packs:

```bash
$ aroom pack refresh
```

```
Refreshing https://github.com/acme/anteroom-packs.git... 1 pack installed
```

## Step 6: Team Members Consume

When a teammate starts Anteroom with the same pack source configured, they get the same packs:

```bash
$ aroom pack list
acme/python-conventions  v1.0.0  6 artifacts
```

No manual installation needed — the pack source handles cloning and installing.

## Step 7: Verify Auto-Pull

The background worker checks for updates every 30 minutes (or whatever `refresh_interval` you set). To verify it's working:

```bash
$ aroom pack sources
https://github.com/acme/anteroom-packs.git  main  30min  cached (abc1234)
```

## Publishing Updates

When you update the pack:

1. Edit the pack files in the git repo
2. Bump the version in `pack.yaml`
3. Commit and push

```bash
$ cd acme-packs
# Edit python-conventions/pack.yaml, bump version to 1.1.0
# Edit skill files, add new rules, etc.
$ git add .
$ git commit -m "feat: update python-conventions to v1.1.0"
$ git push
```

Within the next refresh interval, all team members get the update automatically.

## Using SSH for Private Repos

```yaml
pack_sources:
  - url: git@github.com:acme/private-packs.git
    branch: main
    refresh_interval: 30
```

Ensure SSH keys are configured: `ssh-add ~/.ssh/id_ed25519`

## Next Steps

- [Team Standardization](team-standardization.md) — enforce packs across a team
- [Automatic Updates](automatic-updates.md) — configure refresh behavior
- [Pack Sources](../pack-sources.md) — detailed source lifecycle
