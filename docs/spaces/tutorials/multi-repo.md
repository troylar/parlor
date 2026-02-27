# Tutorial: Multi-Repo Project

Manage a project spread across multiple git repositories using a single space.

## Scenario

You're working on a microservices architecture with separate repos:

- `user-service` — user authentication and profiles
- `order-service` — order processing
- `shared-proto` — shared protobuf definitions
- `infra` — Terraform and deployment configs

## Step 1: Define the Space

```yaml title="~/.anteroom/spaces/acme-platform.yaml"
name: acme-platform
version: "1"

repos:
  - https://github.com/acme/user-service.git
  - https://github.com/acme/order-service.git
  - https://github.com/acme/shared-proto.git
  - https://github.com/acme/infra.git

instructions: |
  You are working on the ACME platform, a microservices architecture.

  Services:
  - user-service (Go): Authentication, profiles, sessions
  - order-service (Python): Order processing, payments
  - shared-proto: Protobuf definitions shared across services
  - infra: Terraform configs for AWS deployment

  Cross-cutting concerns:
  - All services communicate via gRPC using shared-proto definitions
  - Breaking proto changes require updating all consuming services
  - Infrastructure changes need corresponding service config updates

config:
  ai:
    model: gpt-4o
```

## Step 2: Clone All Repos

```bash
$ aroom space create ~/.anteroom/spaces/acme-platform.yaml
$ aroom space clone acme-platform
Repos root directory
  [~/.anteroom/spaces/acme-platform/repos]: ~/projects/acme
  OK   https://github.com/acme/user-service.git → ~/projects/acme/user-service
  OK   https://github.com/acme/order-service.git → ~/projects/acme/order-service
  OK   https://github.com/acme/shared-proto.git → ~/projects/acme/shared-proto
  OK   https://github.com/acme/infra.git → ~/projects/acme/infra
```

## Step 3: Work Across Repos

Navigate to any repo and the space is auto-detected:

```bash
$ cd ~/projects/acme/user-service
$ aroom chat
Space: acme-platform
> How does the user service authenticate requests?
```

```bash
$ cd ~/projects/acme/order-service/src
$ aroom chat
Space: acme-platform    # walks up to order-service, finds the match
> What proto messages does the order service consume?
```

The AI knows about all repos in the space from the instructions, even when you're working in just one.

## Step 4: Add a New Repo Later

When a new service is created:

1. Edit the space YAML:

   ```yaml
   repos:
     - https://github.com/acme/user-service.git
     - https://github.com/acme/order-service.git
     - https://github.com/acme/shared-proto.git
     - https://github.com/acme/infra.git
     - https://github.com/acme/notification-service.git  # new
   ```

2. Clone the new repo:

   ```bash
   $ aroom space clone acme-platform
     OK   https://github.com/acme/user-service.git → ~/projects/acme/user-service    # skipped (exists)
     OK   https://github.com/acme/order-service.git → ~/projects/acme/order-service  # skipped (exists)
     OK   https://github.com/acme/shared-proto.git → ~/projects/acme/shared-proto    # skipped (exists)
     OK   https://github.com/acme/infra.git → ~/projects/acme/infra                  # skipped (exists)
     OK   https://github.com/acme/notification-service.git → ~/projects/acme/notification-service  # NEW
   ```

   Existing repos are skipped. Only the new one is cloned.

3. Update instructions in the YAML and run `/space refresh`.

## Step 5: Map Pre-Existing Repos

If you already have repos cloned elsewhere:

```bash
$ aroom space map acme-platform /home/dev/legacy-repos/payment-gateway
Mapped: /home/dev/legacy-repos/payment-gateway → acme-platform
```

Now `cd /home/dev/legacy-repos/payment-gateway && aroom chat` auto-detects the space.

## Directory Layout

After setup, your filesystem looks like:

```
~/projects/acme/
├── user-service/          → auto-detects acme-platform
│   ├── cmd/
│   ├── internal/
│   └── ...
├── order-service/         → auto-detects acme-platform
│   ├── src/
│   └── ...
├── shared-proto/          → auto-detects acme-platform
│   └── proto/
├── infra/                 → auto-detects acme-platform
│   ├── terraform/
│   └── ...
└── notification-service/  → auto-detects acme-platform
    └── ...
```

Working in any subdirectory of any repo auto-detects the space.

## Next Steps

- [Team Space](team-space.md) — share this setup with your team
- [Custom Config](custom-config.md) — per-space AI and safety settings
- [Repo Management](../repo-management.md) — detailed repo operations
