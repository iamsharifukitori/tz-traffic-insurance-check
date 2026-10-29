# Security

Do not include credentials or private fleet records in public issues or pull requests.

Once hosted on GitHub, use the repository Security tab's private vulnerability reporting feature if enabled. If it is unavailable, ask the maintainer for a private reporting channel without posting exploit details or affected fleet data. No private contact address is configured in this template.

Before a public release, the repository owner should enable private vulnerability reporting. If secrets are accidentally committed, revoke/rotate them immediately; deleting a file does not remove it from Git history.

Production deployments require HTTPS, a strong private API key, protected SMTP credentials, persistent database storage and separate backups. Never expose PostgreSQL directly to the internet. Keep Chromium sandboxing enabled.
