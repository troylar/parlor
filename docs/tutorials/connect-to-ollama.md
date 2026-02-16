# Connect to Ollama

Run Anteroom with a local LLM via [Ollama](https://ollama.ai).

## Prerequisites

1. Install Ollama: `brew install ollama` (macOS) or see [ollama.ai](https://ollama.ai)
2. Pull a model:

```bash
$ ollama pull llama3.1
```

3. Start the Ollama server:

```bash
$ ollama serve
```

## Configure Anteroom

```yaml title="~/.anteroom/config.yaml"
ai:
  base_url: "http://localhost:11434/v1"
  api_key: "ollama"
  model: "llama3.1"
```

!!! info
    Ollama's OpenAI-compatible endpoint is at `/v1`. The API key can be any non-empty string --- Ollama doesn't validate it.

## Verify

```bash
$ aroom --test
```

You should see your Ollama models listed and a successful test prompt.

## Launch

```bash
$ aroom           # Web UI
$ aroom chat      # CLI
```

## Tips

- **SSL verification**: Ollama runs on HTTP locally, so `verify_ssl` doesn't apply
- **Model switching**: Use the command palette (`Cmd+K`) or `/model` command to switch between pulled models
- **Performance**: Ollama runs models on your GPU. Larger models need more VRAM
- **Multiple models**: Pull several models and switch between them mid-conversation

## Disable Built-in Tools

If your model doesn't support function calling well, disable tools:

```bash
$ aroom chat --no-tools
```

Or in config:

```yaml
cli:
  builtin_tools: false
```
