# File References

Reference files and directories directly in your prompt with `@`.

## Syntax

```
you> explain @src/main.py
you> what tests cover @src/auth/
you> compare @old.py and @new.py
you> review @"path with spaces/file.py"
```

## Reference Types

| Syntax | What it does |
|---|---|
| `@file.py` | Inlines the full file contents wrapped in `<file path="...">` tags (truncated at 100KB) |
| `@directory/` | Inlines a listing of directory contents wrapped in `<directory>` tags (up to 200 entries, with `/` suffix for subdirectories) |
| `@"quoted path"` | Handles paths containing spaces (single or double quotes) |

## Path Resolution

Paths are resolved relative to the working directory. Absolute paths are also supported. If a path doesn't exist, the `@reference` is left as-is in the prompt.

## Tab Completion

Type `@` then press `Tab` to browse files and directories from the working directory:

- Subdirectories show with a `/` suffix
- Hidden files (starting with `.`) are excluded
- Supports nested path completion (`@src/` then `Tab`)
