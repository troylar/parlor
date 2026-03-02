# Connect to OpenRouter

Use Anteroom with [OpenRouter](https://openrouter.ai) to access 200+ models from OpenAI, Anthropic, Google, Meta, and others through a single API key.

## Prerequisites

1. An [OpenRouter account](https://openrouter.ai)
2. An API key from [openrouter.ai/keys](https://openrouter.ai/keys)

## Install the Providers Extra

OpenRouter support uses [LiteLLM](https://docs.litellm.ai/) under the hood. Install it as an optional dependency:

```bash
$ pip install anteroom[providers]
```

## Configure Anteroom

### Option 1: Setup Wizard

```bash
$ aroom init
```

Select **OpenRouter (via LiteLLM)** from the provider list (option 3), then enter your API key and choose a model.

### Option 2: Manual Config

```yaml title="~/.anteroom/config.yaml"
ai:
  provider: "litellm"
  api_key: "sk-or-v1-your-key-here"
  model: "openrouter/openai/gpt-4o"
```

!!! info "No base_url needed"
    LiteLLM routes requests automatically based on the model name prefix. You don't need to set `base_url` for OpenRouter --- it's handled internally.

### Option 3: Environment Variables

```bash
$ export AI_CHAT_PROVIDER="litellm"
$ export AI_CHAT_API_KEY="sk-or-v1-your-key-here"
$ export AI_CHAT_MODEL="openrouter/openai/gpt-4o"
$ aroom chat
```

## Model Names

OpenRouter models use the prefix `openrouter/provider/model`:

| Model | Model Name |
|---|---|
| GPT-4o | `openrouter/openai/gpt-4o` |
| Claude Sonnet 4 | `openrouter/anthropic/claude-sonnet-4` |
| Gemini 2.5 Pro | `openrouter/google/gemini-2.5-pro` |
| Llama 3.1 405B | `openrouter/meta-llama/llama-3.1-405b-instruct` |
| Mixtral 8x22B | `openrouter/mistralai/mixtral-8x22b-instruct` |

See the full model list at [openrouter.ai/models](https://openrouter.ai/models).

## Dynamic API Key with a Command

If you manage your API key through a secrets manager:

```yaml title="~/.anteroom/config.yaml"
ai:
  provider: "litellm"
  api_key_command: "vault read -field=key secret/openrouter"
  model: "openrouter/openai/gpt-4o"
```

The command runs on startup and re-runs automatically if a 401 error is received (transparent token refresh).

## Verify

```bash
$ aroom --test
```

## Launch

```bash
$ aroom           # Web UI
$ aroom chat      # CLI
```

## Using Other LiteLLM Providers

The `litellm` provider isn't limited to OpenRouter. LiteLLM supports 100+ providers via model name prefixes. Some examples:

```yaml title="~/.anteroom/config.yaml"
ai:
  provider: "litellm"
  api_key: "your-provider-key"
  model: "together_ai/meta-llama/Llama-3-70b-chat-hf"    # Together AI
```

```yaml
ai:
  provider: "litellm"
  api_key: "your-provider-key"
  model: "replicate/meta/llama-2-70b-chat"                # Replicate
```

```yaml
ai:
  provider: "litellm"
  api_key: "your-provider-key"
  model: "cohere/command-r-plus"                           # Cohere
```

See the [LiteLLM provider list](https://docs.litellm.ai/docs/providers) for the full list of supported providers and their model name formats.

## AWS Bedrock

Bedrock uses AWS IAM credentials instead of API keys. LiteLLM picks these up automatically from your environment --- no `api_key` needed.

### Option 1: Environment Variables

```bash
$ export AWS_ACCESS_KEY_ID="your-access-key"
$ export AWS_SECRET_ACCESS_KEY="your-secret-key"
$ export AWS_REGION_NAME="us-east-1"
```

```yaml title="~/.anteroom/config.yaml"
ai:
  provider: "litellm"
  model: "bedrock/anthropic.claude-3-sonnet-20240229-v1:0"
```

### Option 2: AWS Profile / SSO

```bash
$ export AWS_PROFILE="my-sso-profile"
```

```yaml title="~/.anteroom/config.yaml"
ai:
  provider: "litellm"
  model: "bedrock/anthropic.claude-3-sonnet-20240229-v1:0"
```

### Option 3: IAM Role (EC2/ECS/Lambda)

If Anteroom runs on an AWS compute resource with an attached IAM role, no credentials are needed at all:

```yaml title="~/.anteroom/config.yaml"
ai:
  provider: "litellm"
  model: "bedrock/anthropic.claude-3-sonnet-20240229-v1:0"
```

### Option 4: Dynamic Credentials via `api_key_command`

Use `api_key_command` to generate temporary credentials:

```yaml title="~/.anteroom/config.yaml"
ai:
  provider: "litellm"
  api_key_command: "aws sts get-caller-identity --query Account --output text && aws configure get aws_access_key_id"
  model: "bedrock/anthropic.claude-3-sonnet-20240229-v1:0"
```

!!! info "Bedrock model names"
    Bedrock models use the `bedrock/` prefix. Common models:

    - `bedrock/anthropic.claude-3-sonnet-20240229-v1:0`
    - `bedrock/anthropic.claude-3-haiku-20240307-v1:0`
    - `bedrock/meta.llama3-1-405b-instruct-v1:0`
    - `bedrock/amazon.titan-text-express-v1`

    See the [LiteLLM Bedrock docs](https://docs.litellm.ai/docs/providers/bedrock) for the full list.

!!! warning "Don't set `api_key` for Bedrock"
    When using Bedrock with AWS credentials (env vars, profiles, IAM roles), leave `api_key` empty. Setting it causes LiteLLM to skip the AWS credential chain.

## Tips

- **Model switching**: Use `/model` in the CLI or the command palette in the web UI to switch models mid-session
- **Cost control**: OpenRouter shows per-model pricing at [openrouter.ai/models](https://openrouter.ai/models)
- **Egress control**: If you need to restrict outbound API traffic, use `allowed_domains` in your config (see [Config Reference](../configuration/config-file.md))
- **Team config**: Teams can set `provider: litellm` in their team config to standardize on OpenRouter across the organization (see [Team Config](../configuration/team-config.md))
