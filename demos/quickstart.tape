# Anteroom Quickstart Demo
#
# Shows: install, first run, basic chat via exec mode.
# Produces: demos/quickstart.gif
#
# Run: vhs demos/quickstart.tape

Output demos/quickstart.gif

Require aroom

Set Shell "zsh"
Set FontSize 16
Set Width 1200
Set Height 600
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms

# Title
Type "# Anteroom — quickstart demo"
Enter
Sleep 1s

# Show version
Type "aroom --version"
Enter
Sleep 2s

# Simple question via exec mode
Type "aroom exec 'What is Anteroom? Answer in one sentence.'"
Enter
Sleep 8s

# Follow-up with deterministic settings
Type "aroom exec --temperature 0 --seed 42 'List 3 benefits of self-hosted AI tools. Be brief.'"
Enter
Sleep 10s

# Show JSON output mode for scripting
Type "aroom exec --json --quiet 'What is 2+2?' | python3 -m json.tool"
Enter
Sleep 6s

Sleep 2s
