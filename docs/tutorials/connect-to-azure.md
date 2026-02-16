# Connect to Azure OpenAI

Use Anteroom with Azure OpenAI Service.

## Prerequisites

1. An Azure subscription with Azure OpenAI access
2. A deployed model in Azure OpenAI Studio

## Configure Anteroom

```yaml title="~/.anteroom/config.yaml"
ai:
  base_url: "https://your-resource.openai.azure.com/openai/deployments/your-deployment/v1"
  api_key: "your-azure-api-key"
  model: "your-deployment-name"
```

!!! warning
    Azure OpenAI uses deployment names as model identifiers. The `model` field should match your deployment name, not the underlying model name (e.g., `my-gpt4-deployment`, not `gpt-4`).

## Using Environment Variables

```bash
$ export AI_CHAT_BASE_URL="https://your-resource.openai.azure.com/openai/deployments/your-deployment/v1"
$ export AI_CHAT_API_KEY="your-azure-api-key"
$ export AI_CHAT_MODEL="your-deployment-name"
$ aroom chat
```

## Dynamic API Key with Azure CLI

Use `api_key_command` to get tokens from Azure CLI:

```yaml title="~/.anteroom/config.yaml"
ai:
  base_url: "https://your-resource.openai.azure.com/openai/deployments/your-deployment/v1"
  api_key_command: "az account get-access-token --resource https://cognitiveservices.azure.com --query accessToken -o tsv"
  model: "your-deployment-name"
```

This automatically refreshes the token when it expires (transparent retry on HTTP 401).

## Verify

```bash
$ aroom --test
```

## SSL Verification

Azure OpenAI uses valid TLS certificates, so `verify_ssl` should remain `true` (the default). If you're behind a corporate proxy with custom CA certificates, you may need to configure your system's certificate store.
