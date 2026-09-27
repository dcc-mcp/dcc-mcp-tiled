# Re-publishing an existing release

The release workflow owns two independent entry points.

| Entry point | Trigger | Purpose |
| --- | --- | --- |
| `release-please` → `build` → `publish` → `attach-release-assets` | `push` to `main` | Normal path. Release Please cuts the tag, then the chain builds, publishes to PyPI, and attaches the wheel and sdist to the GitHub Release. |
| `recovery-target` → `recovery-build` → `recovery-publish` → `recovery-attach-release-assets` | `workflow_dispatch` | Recovery path. Re-publishes a tag that already exists on GitHub but never reached PyPI. |

## When to use the recovery path

Use it when a GitHub Release exists but the PyPI upload never happened, for
example when the trusted publisher was still pending and the `publish` job
failed with `invalid-publisher`.

A single-job or whole-workflow **rerun does not work**. `publish` recaptures the
exact artifact identity that `build` produced (`bundle_artifact_id` and
`bundle_artifact_digest`), and a rerun makes `build` upload a *new* artifact
while `publish` still holds the *old* output values. The re-capture fails before
the upload step. Rerunning the whole workflow also fails, because Release Please
does not re-create a release that already exists, so `publish` is skipped by the
`release_created == 'true'` gate.

The recovery path avoids both problems: it freezes the existing GitHub Release
freshly in `recovery-target`, then builds and re-captures identity within a
single run.

## Running it

```bash
gh workflow run release.yml --repo dcc-mcp/dcc-mcp-tiled -f tag=v0.4.2
```

| Input | Required | Meaning |
| --- | --- | --- |
| `tag` | yes | Existing canonical release tag, for example `v0.4.2`. |
| `source_sha` | no | Commit the tag must resolve to. Leave empty to use the tag's current commit. |

Both inputs are passed through to the release guards, so the channel is generic:
no version, tag, release id, or run id is baked into the workflow.

## Guarantees

The recovery jobs reuse the same reviewed tool scripts as the normal path, and
the same structural contract in `tools/verify_release_workflow.py` covers them:

- `recovery-target` checks out `inputs.tag`, requires a canonical
  `vMAJOR.MINOR.PATCH` tag, proves the checked-out commit equals the tag commit,
  cross-checks the version against every release version source, and then freezes
  the live GitHub Release entity and its asset baseline.
- `recovery-build` rebuilds the wheel and sdist from the immutable tag and hashes
  the bundle manifest.
- `recovery-publish` recaptures the artifact identity and the remote release
  identity immediately before the upload, then publishes with OIDC trusted
  publishing only.
- `recovery-attach-release-assets` runs after a successful publish and attaches
  the exact verified files through `tools/upload_release_assets.py`.

## Constraints

- The target GitHub Release must be **published** (not a draft, prerelease, or
  immutable release) and must still have **no attached assets**. That is exactly
  the state a release is left in when `publish` fails, because the attach job is
  gated on publish success. After a successful recovery run the release is
  complete, and re-running the recovery for the same tag stops at
  `recovery-target` rather than silently duplicating assets.
- `recovery-publish` passes `skip-existing`, so a re-run for a version that is
  already on PyPI does not fail the run.
- Publishing still requires the repository `pypi` environment and the PyPI
  trusted publisher registered for `release.yml`. A pending publisher expires 30
  days after registration, so registration and the first publish must happen in
  the same window.
