# Releasing pi-tmux-images

## Release a new stable version

The repository-owned release command is a dry run by default. It prints a plan without running commands or changing release state:

```sh
mise run release -- 0.1.1
```

Use this flow for each stable release:

1. Land a standalone `## 0.1.1` entry in `CHANGELOG.md`.
2. Run the dry run and review its plan.
3. Confirm that the checkout is clean and that `main` matches `origin/main`.
4. Start the external-effect path:

   ```sh
   mise run release -- 0.1.1 --execute
   ```

Before it changes anything, the command checks that:

- The target is a greater stable version.
- Local tags, remote tags, and npm don't contain the target.
- `package.json` and `package-lock.json` agree.
- The target has a matching changelog entry.
- Prior CI passed, GitHub authentication works, and `mise run verify` passes.
- `publish.yml` still uses OpenID Connect (OIDC) provenance publishing without token or one-time password (OTP) credentials.

After those checks pass, the command:

1. Updates the package and lock versions.
2. Runs the full local verification again.
3. Creates and pushes the release commit and annotated tag.
4. Creates the GitHub Release.
5. Waits for the publish workflow and verifies its result.
6. Checks the npm version, `latest` tag, and provenance attestations.
7. Installs the published package into a temporary Pi project.

### Recover from a partial release

Do not blindly rerun `--execute` after an external effect fails. The command rejects an existing tag or target version by design.

First identify the last completed phase:

```sh
git log
git ls-remote --tags origin
gh release view v<version>
gh run list --workflow publish.yml
```

Then complete only the missing phase. For example:

- Push a tag that already exists locally.
- Create the GitHub Release if it's missing.
- Watch and verify a publish run that already exists.

Keep the release commit and tag paired. Never recreate or retarget them. Record the recovery outcome in the changelog or PR notes before the next release.

The command never accepts, stores, or uses npm tokens or OTPs. It also can't bootstrap a new package. The following sections preserve the one-time bootstrap and first-release record for this package. Do not repeat them for later releases.

## Initial publication prerequisites

Before the first release, the maintainer:

1. Ran `mise run verify` and inspected the real `assets/demo.png` described in `assets/README.md`.
2. Confirmed that `package.json` shipped the screenshot, `pi.image` used its final public URL, and the README rendered it.
3. Created `safurrier/pi-tmux-images`, pushed `main`, and confirmed that the thin CI workflow passed.

## npm trusted-publishing bootstrap record

GitHub Actions had to publish the first stable release with OIDC provenance. The maintainer first established npm package ownership with a lower bootstrap version.

1. Created a disposable checkout of the verified `main` commit.
2. Confirmed that it was clean with `git diff --exit-code`.
3. Set only that checkout to `0.0.0-bootstrap.0`:

   ```sh
   npm version 0.0.0-bootstrap.0 --no-git-tag-version
   ```

   The temporary version was never committed or tagged.
4. Published the bootstrap package with an npm credential and required OTP:

   ```sh
   npm publish --access public --tag bootstrap --otp=<code>
   npm view pi-tmux-images@bootstrap version
   npm dist-tag ls pi-tmux-images
   ```

   The observed `npm dist-tag ls` output placed `0.0.0-bootstrap.0` under both `bootstrap` and `latest`, despite `--tag bootstrap`. Publishing `v0.1.0` later moved `latest` to the stable version.
5. Added an npm trusted publisher for GitHub Actions. It named owner `safurrier`, repository `pi-tmux-images`, workflow `publish.yml`, and no environment.
6. Ran `npm logout`, revoked the temporary npm token in npm, removed the local credential, and deleted the disposable checkout.

The repository retained no `NPM_TOKEN` secret or token-based publish workflow.

## v0.1.0 publication record

For the first stable release, the maintainer:

1. Returned to the intended `main` commit and confirmed a clean checkout. `npm ci` and `mise run verify` passed, and `package.json` still reported `0.1.0`.
2. Created and pushed an annotated `v0.1.0` tag. The pushed tag resolved to the verified commit.
3. Created a non-prerelease GitHub Release. `publish.yml` checked the tag and package version, then published through npm trusted publishing with provenance.
4. Verified the npm version and distribution tags, then made npm the primary README installation path.
