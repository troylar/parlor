# Tool Safety

Two layers of protection prevent accidental damage from AI tool use.

## Destructive Command Confirmation

The following patterns in bash commands trigger an interactive `Proceed? [y/N]` prompt before execution:

- `rm`, `rmdir`
- `git push --force`, `git push -f`
- `git reset --hard`
- `git clean`
- `git checkout .`
- `drop table`, `drop database`
- `truncate`
- `> /dev/`
- `chmod 777`
- `kill -9`

## Path and Command Blocking

Hardcoded blocks that cannot be bypassed:

### Blocked Paths

- `/etc/shadow`
- `/etc/passwd`
- `/etc/sudoers`
- Anything under `/proc/`, `/sys/`, `/dev/` (follows symlinks)

### Blocked Commands

- `rm -rf /`
- `mkfs`
- `dd if=/dev/zero`
- Fork bombs

### Additional Protections

- **Null byte injection**: Rejected in all paths, commands, and glob patterns
- **Path traversal**: Blocked in all file operations
- **Symlink resolution**: `os.path.realpath` is used to resolve symlinks before path checks

## MCP Tool Safety

MCP tool arguments are also protected:

- **SSRF protection**: DNS resolution validates that target URLs don't point to private IP addresses
- **Shell metacharacter rejection**: Tool arguments are sanitized to prevent command injection
