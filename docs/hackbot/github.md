# GitHub MCP server for local development

The official GitHub MCP server provides read-only repository, pull request, and
issue tools. The Compose setup runs it locally for development, independently of
any agent. Production deployment and agent integration are separate work.

## Start the server

Install Docker with Compose and start Docker, then run from the repository root:

```bash
docker compose -f services/github-mcp/compose.yml up -d
```

The server listens at `http://127.0.0.1:8082/`. Clients running on the same
Compose network can use `http://github-mcp:8082/`. Each client must supply a
GitHub token in its `Authorization` header; the server does not hold the App's
private key.

The container's read-only flag controls its exposed tools. GitHub token
permissions also restrict what the token can do. The local server is not a
replacement for production access control.

When finished:

```bash
docker compose -f services/github-mcp/compose.yml down
```
