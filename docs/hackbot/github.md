# GitHub MCP server for local development

The official GitHub MCP server provides read-only repository, pull request, and
issue tools. The Compose setup runs it locally for development, independently of
any agent. Production deployment and agent integration are separate work.

## Start the server

Install Docker with Compose and start Docker, then run from the repository root:

```bash
docker compose -f docs/hackbot/github-mcp/compose.yml up -d
```

The server listens at `http://127.0.0.1:8082/`. Clients running on the same
Compose network can use `http://github-mcp:8082/`. Each client must supply a
GitHub token in its `Authorization` header; the server does not hold the App's
private key.

## Check a pull request

The following uses the active GitHub CLI account to authenticate. Its token may
have more permissions than needed. Alternatively, set `GITHUB_MCP_TOKEN` to a
read-only installation token from a test GitHub App with access to the repository.

```bash
export GITHUB_MCP_TOKEN="$(gh auth token)"
uv run --no-project --with 'mcp>=1,<2' python docs/hackbot/github-mcp/check.py
unset GITHUB_MCP_TOKEN
```

The check reads the details and files of `Foxbolts/hackbot-github-client-test#1`
and checks that all exposed tools have read-only annotations. Inspect the output
to verify it matches the PR. To use another PR, pass `--repo owner/name --pr NUMBER`.

The container's read-only flag controls its exposed tools. GitHub token
permissions also restrict what the token can do. The local server is not a
replacement for production access control.

When finished:

```bash
docker compose -f docs/hackbot/github-mcp/compose.yml down
```
