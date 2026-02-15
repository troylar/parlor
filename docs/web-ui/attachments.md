# File Attachments

Drag-and-drop or click to attach files to your messages. **35+ file types** supported with up to **10 files per message** and **10 MB each**.

## Supported File Types

| Category | Extensions |
|---|---|
| **Code** | `.py` `.js` `.ts` `.java` `.c` `.cpp` `.h` `.hpp` `.rs` `.go` `.rb` `.php` `.sh` `.bat` `.ps1` `.sql` `.css` |
| **Data** | `.json` `.yaml` `.yml` `.csv` `.xml` `.toml` `.ini` `.cfg` `.log` |
| **Documents** | `.txt` `.md` `.pdf` |
| **Images** | `.png` `.jpg` `.jpeg` `.gif` `.webp` |

## Security

Every file is verified with **magic-byte detection** --- a renamed `.exe` won't sneak through as a `.png`. Files must pass both MIME type allowlist and magic-byte verification.

- Image attachments show inline thumbnails with file size
- Non-image files force-download (never rendered in-browser)
- Filenames are sanitized: path components stripped, special characters replaced
- Path traversal attempts are blocked
