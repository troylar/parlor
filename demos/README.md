# Demo Recordings

Reproducible terminal demo recordings using [VHS](https://github.com/charmbracelet/vhs).

## Prerequisites

Install VHS:

```bash
# macOS
brew install charmbracelet/tap/vhs

# Go
go install github.com/charmbracelet/vhs@latest
```

VHS also requires `ffmpeg` and `ttyd`:
```bash
brew install ffmpeg ttyd
```

## Available Demos

| Demo | Description | Script |
|------|-------------|--------|
| Quickstart | Install, first run, basic chat | `quickstart.tape` |
| Tool Usage | Agent using read_file, glob, grep | `tools.tape` |
| Exec Mode | Non-interactive mode for scripting/CI | `exec-mode.tape` |

## Building Demos

```bash
# Build all demos
cd demos && make demos

# Build a single demo
cd demos && make quickstart

# Or run directly
vhs demos/quickstart.tape
```

## Regenerating

Demos use `--temperature 0 --seed 42` for reproducibility. To regenerate after changes:

```bash
cd demos && make clean && make demos
```

Output GIFs land in the `demos/` directory. They are not checked into git (see `.gitignore`).

## Writing New Demos

1. Create a new `.tape` file in `demos/`
2. Use `aroom exec` for non-interactive demos (avoids timing issues)
3. Use `--temperature 0 --seed 42` for deterministic output
4. Use `--approval-mode auto` if tools need to run without prompts
5. Set reasonable `Sleep` timers for readability
6. Run `vhs demos/your-demo.tape` to test

Tape file reference: https://github.com/charmbracelet/vhs#vhs-command-reference
