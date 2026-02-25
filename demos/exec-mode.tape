# Anteroom Exec Mode Demo
#
# Shows: non-interactive mode for scripting and CI pipelines.
# Produces: demos/exec-mode.gif
#
# Run: vhs demos/exec-mode.tape

Output demos/exec-mode.gif

Require aroom

Set Shell "zsh"
Set FontSize 16
Set Width 1200
Set Height 700
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms

# Title
Type "# Anteroom — exec mode for scripting & CI"
Enter
Sleep 1s

# Basic exec with text output
Type "aroom exec 'Explain what a Makefile is in 2 sentences.'"
Enter
Sleep 10s

# JSON output for programmatic use
Type "aroom exec --json --quiet 'What are the 3 most common HTTP status codes?' | python3 -c \"import sys,json; d=json.load(sys.stdin); print(f'Model: {d[\"model\"]}'); print(f'Tools used: {len(d[\"tool_calls\"])}'); print(f'Exit: {d[\"exit_code\"]}')\""
Enter
Sleep 10s

# Piping content through stdin
Type "echo 'def add(a, b): return a + b' | aroom exec 'Review this Python function. Is it correct?'"
Enter
Sleep 10s

# Timeout and no-conversation flags for CI
Type "aroom exec --timeout 30 --no-conversation --quiet 'What is 7 * 8?'"
Enter
Sleep 6s

Sleep 2s
