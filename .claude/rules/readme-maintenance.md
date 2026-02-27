# README.md Maintenance

The README is the project's front door. Keep it accurate when user-facing changes are made.

## When to Update

| Change Type | README Section to Update |
|---|---|
| New user-facing feature | "The full picture" table |
| New security feature | "Enterprise-grade security" bullet list |
| New built-in tool | Tool count in CLI example, tools list in "Agentic, not just chat" |
| New approval mode or tier | Approval mode count in security section and feature table |
| Test count crosses a hundreds boundary | Development section (`2900+ tests` etc.) |
| New MCP config capability | MCP YAML example |
| ASVS level change | Security section header and SECURITY.md link text |
| Version bump | CLI example version number |

## Accuracy Checks

Before committing README changes, verify:

1. **Test count**: Run `pytest tests/ --collect-only -q | tail -1` and round down to nearest hundred
2. **ASVS level**: Must match `SECURITY.md` (currently Level 2, ASVS v5.0)
3. **Tool count**: Count entries in `DEFAULT_TOOL_TIERS` in `tools/tiers.py` (currently 12 user-facing built-in tools + 3 optional office tools: read_file, write_file, edit_file, bash, glob_files, grep, create_canvas, update_canvas, patch_canvas, run_agent, ask_user, introspect; optional: docx, xlsx, pptx)
4. **Approval mode count**: Count entries in `APPROVAL_MODE_NAMES` in `tools/tiers.py` (currently 4: auto, ask_for_dangerous, ask_for_writes, ask)
5. **Hard-block pattern count**: Count entries in `_HARD_BLOCK_PATTERNS` in `tools/security.py` (currently 16)
6. **YAML examples**: Verify field names match actual config dataclass fields in `config.py`

## What Not to Change

- The project tagline and positioning ("Your private AI gateway")
- The badge links (PyPI, coverage, license)
- The "What is Anteroom?" section (unless the product identity changes)
- Screenshot references (update only when new screenshots are taken)
