# Anteroom Tool Usage Demo
#
# Shows: agent using built-in tools (read_file, glob_files, grep).
# Produces: demos/tools.gif
#
# Run: vhs demos/tools.tape

Output demos/tools.gif

Require aroom

Set Shell "zsh"
Set FontSize 16
Set Width 1200
Set Height 700
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms

# Title
Type "# Anteroom — tool usage demo"
Enter
Sleep 1s

# Agent reads a file
Type "aroom exec --temperature 0 --seed 42 --approval-mode auto 'Read pyproject.toml and tell me the project name and version.'"
Enter
Sleep 12s

# Agent searches the codebase
Type "aroom exec --temperature 0 --seed 42 --approval-mode auto 'Find all Python files in src/anteroom/services/ and list them.'"
Enter
Sleep 12s

# Agent uses grep for content search
Type "aroom exec --temperature 0 --seed 42 --approval-mode auto 'Which file defines the AIService class? Just the file path.'"
Enter
Sleep 12s

Sleep 2s
