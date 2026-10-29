# Publish on GitHub

This directory is the public-source package. Do not publish the original workspace, private validation outputs, Desktop truck list, `.env`, database dumps or email previews.

1. Review the MIT license and repository name (suggestion: `fleet-deadline-monitor`). Retain the Playwright third-party license and notices.
2. Create an empty GitHub repository. Choose public only after reviewing this package.
3. From this directory, run the following with your actual repository URL:

```sh
git init -b main
git add .
git diff --cached --stat
git commit -m "Initial open-source release"
git remote add origin https://github.com/YOUR-ACCOUNT/fleet-deadline-monitor.git
git push -u origin main
```

4. Enable private vulnerability reporting in GitHub Security settings and protect the main branch with the CI checks.
5. Confirm GitHub Actions passes before tagging a release. Local validation does not mean the workflow has already run on GitHub.

No GitHub repository has been created or published by this preparation step.
