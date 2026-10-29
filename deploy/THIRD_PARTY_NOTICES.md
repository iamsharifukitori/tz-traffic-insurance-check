`chromium-seccomp.json` is the Playwright Docker seccomp profile from
https://github.com/microsoft/playwright/blob/main/utils/docker/seccomp_profile.json,
downloaded on 29 October 2026. It permits Chromium's user-namespace sandbox while retaining
the profile's default-deny syscall policy. The upstream license is included as
`PLAYWRIGHT-LICENSE` in this directory. Original file attribution is retained in the JSON.

Vendored profile SHA-256: `cc3e61cabda6bbc1e53e54d27ba4d55a9d3be829b6dd1a596f4a7b31b1cc7849`.
