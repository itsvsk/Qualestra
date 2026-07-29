# Releasing Qualestra

Qualestra has two distribution surfaces:

1. a Python wheel and source archive containing the onboarding CLI, SDK, worker, control API, and
   Atlas reference application;
2. versioned worker and dashboard OCI images for the standalone Compose deployment.

The GitHub tag workflow builds both surfaces, installs and smoke-tests the wheel, runs the complete
validation suite, uploads the Python archives, publishes the two images to GHCR, and creates build
provenance attestations. It does not publish to PyPI.

## Owner decisions before the first public tag

The source license is Apache-2.0 and is declared in `LICENSE`, `NOTICE`, `pyproject.toml`, and both
container-image labels. Preserve those declarations and the personal copyright notice in every
release. Decide whether the Python distribution should remain a GitHub release artifact or use
PyPI trusted publishing.

The GHCR packages created by the first workflow run may initially be private. Change their
visibility only after reviewing the release contents and repository ownership.

## Prepare a release

1. Update `sentinelqa.__version__`, `apps/dashboard/package.json`, and `CHANGELOG.md`.
2. Run `uv lock` and commit the updated lock file if required.
3. Run:

   ```bash
   make release-check
   uv run python scripts/release_check.py --public-release
   make check
   pnpm --filter @sentinelqa/dashboard lint
   pnpm --filter @sentinelqa/dashboard test
   pnpm --filter @sentinelqa/dashboard build
   docker compose config --quiet
   docker compose --env-file .env.standalone -f compose.standalone.yml config --quiet
   ```

4. Build and smoke-test the distributions with `uv build`, an isolated virtual environment, and
   `qualestra --version`, `qualestra init`, and `qualestra doctor`.
5. Review the diff, merge it, and create an annotated `vX.Y.Z` tag whose value exactly matches the
   package version.
6. Inspect the tag workflow, downloaded archives, image labels, digests, attestations, and package
   visibility before announcing the release.

Never put provider tokens, test credentials, `.env.standalone`, or generated evidence in a release
archive.
