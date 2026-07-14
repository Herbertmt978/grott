# Releasing Grott HA Docker

This runbook prepares and verifies the fork's beta artifacts. It does not grant permission to publish them, change repository controls, create a tag, deploy to a VM, or update Home Assistant. Those actions require explicit owner approval at the time they are performed.

## Release identity

| Version domain | Current value | Owner |
| --- | --- | --- |
| Fork/add-on release | `0.1.10-beta` / `v0.1.10-beta` | Canonical current version: `addons/grott/config.yaml`; the protected Git tag adds the `v` prefix. |
| Bundled Grott core (upstream startup version) | `2.8.3` | `grott.py` (`verrel`) |
| Bundled Home Assistant extension | `0.0.7-rc2` | `examples/Home Assistent/grott_ha.py` (`__version__`) |

The release images are `ghcr.io/herbertmt978/grott` and `ghcr.io/herbertmt978/grott-ha-docker`. Both must represent the same reviewed source SHA and the supported platforms `linux/amd64`, `linux/arm64`, `linux/arm/v7`, and `linux/386`.

## Hard gates

**No public redistribution release is permitted until the upstream maintainer supplies an explicit licence or written redistribution permission.** Upstream currently has no repository licence and the request in [johanmeijer/grott#512](https://github.com/johanmeijer/grott/issues/512) remains the authority to re-check. Local/private testing may continue without publishing images or tags.

Every item below is fail-closed. A missing, ambiguous, or failing item stops the release:

- Explicit upstream licence or written redistribution permission covers the inherited code and built images.
- The default branch is a protected default branch and the release commit has passed required review and hosted CI.
- A protected `release` environment exists, is restricted to the protected default branch, and requires an independent reviewer before package or release writes.
- A protected `v*` tag ruleset restricts tag creation, update, and deletion to authorized release maintainers.
- The hosted CI run succeeds at the exact proposed source SHA, including tests, release/add-on validators, locked dependency audit, native image smoke tests, four-platform Buildx builds, artifact checks, and vulnerability/secret scans.
- Controlled Home Assistant UAT passes with a backup and tested rollback path.

The workflow's read-only control gate uses the hash-pinned `tools/verify_release_controls.py` verifier to check the exact default-branch SHA, a successful `ci.yml` push run at that SHA, active branch rules, the reviewed `release` environment, and an active `v*` tag ruleset before package-write permission is granted. GitHub's read-only API does not expose every bypass setting. Immediately before approving the `release` environment, an independent reviewer must also inspect the GitHub settings UI and record that:

- branch and tag ruleset bypass actors are limited to the explicitly authorized release maintainers;
- administrators are not allowed to bypass the `release` environment protection rules; and
- the required `test` status context is supplied by the intended GitHub Actions integration.

Do not approve the environment if controls changed after the automated gate ran. The latest read-only GitHub audit on 2026-07-14 found no rulesets, no environments, and an unprotected default branch. Those controls must be created and independently re-checked before any release; this document does not authorize changing them.

### Protocol-capture privacy boundary

The protocol-06 example in the current candidate tree is identifier-pseudonymized. Existing Git ancestry was not remediated and may retain the prior encrypted capture. Any history rewrite and coordinated remote remediation is a separate decision requiring explicit owner authorization; it is not part of this release-hardening work.

## Preflight and metadata

Run from a clean Git worktree of the protected default branch. The release validator's `--check-worktree` gate rejects modified and untracked paths, so establish cleanliness before running any release checks. Use the release virtual environment or install the hash-locked development requirements first.

```sh
export RELEASE_REPO="Herbertmt978/grott"
export RELEASE_REMOTE="origin"
export VERSION="$(python -c 'import yaml; print(yaml.safe_load(open("addons/grott/config.yaml", encoding="utf-8"))["version"])')"
export TAG=v${VERSION}
export SOURCE_SHA="$(git rev-parse HEAD)"
export DEFAULT_BRANCH="$(gh repo view "${RELEASE_REPO}" --json defaultBranchRef --jq '.defaultBranchRef.name')"

git rev-parse --is-inside-work-tree
test "$(gh repo view "${RELEASE_REPO}" --json nameWithOwner --jq '.nameWithOwner')" = "${RELEASE_REPO}"
case "$(git remote get-url "${RELEASE_REMOTE}")" in
  "https://github.com/${RELEASE_REPO}"|"https://github.com/${RELEASE_REPO}.git"|"git@github.com:${RELEASE_REPO}.git") ;;
  *) echo "ERROR: ${RELEASE_REMOTE} is not ${RELEASE_REPO}" >&2; exit 1 ;;
esac
test -z "$(git status --porcelain --untracked-files=all)"
python tools/validate_release.py --check-worktree --tag "${TAG}"
python tools/validate_ha_addon_repo.py
python -m pytest -q
python -m pip check
python -m pip_audit --require-hashes -r requirements.lock
python -m compileall -q grott.py grottconf.py grottdata.py grottlayout.py grottprotocol.py grottproxy.py grottserver.py grottsniffer.py grottext tools
```

Confirm the metadata owners directly:

```sh
grep -F "version: ${VERSION}" addons/grott/config.yaml
grep -F "ghcr.io/herbertmt978/grott:${VERSION}" docker/docker-compose.yml README.md
grep -F "## ${VERSION} - prepared 2026-07-14" addons/grott/CHANGELOG.md
grep -F 'verrel = "2.8.3"' grott.py
grep -F '__version__ = "0.0.7-rc2"' 'examples/Home Assistent/grott_ha.py'
```

`## Unreleased` must be empty. The candidate entry records its preparation date without claiming that publication has happened; the GitHub Releases page remains the availability authority. Confirm the add-on is still `stage: experimental`.

## Hosted validation and local image UAT

Do not treat local checks as a substitute for hosted CI. Before any tag, the exact `SOURCE_SHA` must have a green hosted CI run on the protected default branch.

For private/local UAT before redistribution permission exists, build without pushing:

```sh
docker build --file docker/dockerfile \
  --build-arg BUILD_VERSION="${VERSION}" \
  --build-arg VCS_REF="${SOURCE_SHA}" \
  --tag grott:${VERSION}-local .

docker build --file addons/grott/Dockerfile \
  --build-arg BUILD_VERSION="${VERSION}" \
  --build-arg VCS_REF="${SOURCE_SHA}" \
  --tag grott-ha-docker:${VERSION}-local .
```

Validate each local image as non-root with a read-only filesystem, `/tmp` tmpfs, no network, all capabilities dropped, and `no-new-privileges`. Run `/usr/local/bin/validate_container_artifact.py`, the passive health check, and a real proxy listener smoke test. Record the image IDs and scan results in the release evidence.

## Controlled Home Assistant UAT

Use a private test VM or explicitly authorized Home Assistant test window. Never replace the known-good service without the operator present and the rollback prepared.

1. Create a full Home Assistant backup named **Grott pre-update rollback**, wait for completion, verify it is visible and contains the Grott add-on/configuration, and record its backup ID plus the current options. Without that verified backup, UAT must not begin.
2. Record the current Docker/add-on image, configuration, datalogger target, MQTT broker target, and the process that owns TCP port `5279`.
3. Stop the existing listener before starting the candidate; exactly one Grott service or forwarder may own port `5279`.
4. Start the locally built candidate and confirm it becomes healthy without stderr errors.
5. Wait for multiple live packets. Confirm `grott_last_push` advances and representative power, energy, battery, and temperature values remain plausible.
6. Compare the same values with ShinePhone and confirm ShinePhone continues receiving fresh data through the proxy path.
7. Restart the candidate once and confirm it recovers, republishes MQTT discovery without duplicate entities, and forwards packets normally.
8. Stop immediately and follow **Rollback** if parsing, forwarding, MQTT output, health, or ShinePhone behavior regresses.

Record the exact candidate image ID, source SHA, architecture, Home Assistant version, add-on options (with secrets redacted), test timestamps, and rollback result.

## Protected tag and manual dispatch

Only after every hard gate passes and the owner explicitly authorizes publication:

1. Reconfirm the release commit is the exact green hosted-CI SHA on the protected default branch.
2. Create the annotated `v0.1.10-beta` tag under the protected `v*` ruleset and push only that tag.
3. Verify the annotated tag structure and remote refs. The direct remote ref is the annotated tag-object SHA; the peeled remote ref is the release commit SHA, and they must differ. Exactly one of each ref must exist, and only the peeled ref may equal `SOURCE_SHA`.
4. Reconfirm the protected default branch still resolves to `SOURCE_SHA`. Start `Publish GHCR images` manually with `workflow_dispatch`, selecting that protected default branch and passing `v0.1.10-beta` as the `tag` input. The workflow binds the tag to `${{ github.sha }}`, the exact protected workflow-dispatch commit, and fails if the branch moved to any other commit.

Example commands, to be run only with that authorization:

```sh
git tag -a "${TAG}" "${SOURCE_SHA}" -m "Grott HA Docker ${VERSION}"
test "$(git cat-file -t "refs/tags/${TAG}")" = tag
DIRECT_TAG_SHA="$(git rev-parse "refs/tags/${TAG}")"
PEELED_TAG_SHA="$(git rev-parse "refs/tags/${TAG}^{commit}")"
test "${DIRECT_TAG_SHA}" != "${PEELED_TAG_SHA}"
test "${PEELED_TAG_SHA}" = "${SOURCE_SHA}"
git push "${RELEASE_REMOTE}" "refs/tags/${TAG}"
git ls-remote "${RELEASE_REMOTE}" "refs/tags/${TAG}" "refs/tags/${TAG}^{}"
gh workflow run publish-ghcr.yml --repo "${RELEASE_REPO}" --ref "${DEFAULT_BRANCH}" -f tag="${TAG}"
```

The workflow preserves the protected dispatch SHA before tag checkout, requires an annotated tag whose peeled commit equals that SHA exactly, verifies the live repository controls with a hash-pinned read-only gate, and requires the protected `release` environment. Before package login, final promotion, and prerelease creation, it queries GitHub's live repository identity and current default branch, requires the branch name to remain the one whose controls and CI were reviewed, requires that exact branch tip to match the source SHA, verifies one distinct direct tag-object ref with one matching peeled ref, and confirms the live repository state did not change during those checks. It stages source-SHA candidate manifests, validates and scans each platform by immutable digest, then promotes those digests to both `v0.1.10-beta` and `0.1.10-beta`. It never builds directly to a final release tag.

## Human-written release notes

The canonical public notes for this release are committed at `docs/releases/v0.1.10-beta.md`. They must be reviewed with the release commit and use plain language to explain the problem, the fix, why the problem mattered, and the benefit to users. The file must remain UTF-8, LF-only, non-empty, and must not contain the CI-owned `## Immutable release images` heading.

At prerelease creation, the workflow fetches this file through the GitHub Contents API at the exact source SHA already proven by the protected annotated tag. It never reads notes from a mutable branch or generates replacement prose. CI constructs one deterministic body containing the verified runtime/add-on digests and four-platform line, followed by the exact human-written file, and passes that body through `--notes-file`.

An existing prerelease is accepted only when its tag, prerelease state, and exact full body match. A missing, blank, malformed, unreachable, manually edited, prefix-only, or appended body is a hard failure; the workflow does not overwrite or repair it automatically.

## Digest and post-promotion verification

From the completed publish job, record `SOURCE_SHA`, the runtime manifest digest, and the add-on manifest digest. The GitHub prerelease notes must begin with both immutable image references and the four supported platforms before the committed human-written notes.

Inspect all four final references:

```sh
docker buildx imagetools inspect "ghcr.io/herbertmt978/grott:${TAG}"
docker buildx imagetools inspect "ghcr.io/herbertmt978/grott:${VERSION}"
docker buildx imagetools inspect "ghcr.io/herbertmt978/grott-ha-docker:${TAG}"
docker buildx imagetools inspect "ghcr.io/herbertmt978/grott-ha-docker:${VERSION}"
```

For each image, both tags must resolve to the digest recorded by the publish job. Each index must contain `linux/amd64`, `linux/arm64`, `linux/arm/v7`, and `linux/386`; `unknown/unknown` attestation manifests do not replace a required runtime platform. Pull and execute the artifact validator by immutable platform digest for every platform, and confirm the GitHub release is a prerelease targeting `SOURCE_SHA`.

Copy the immutable runtime and add-on references from the prerelease notes into the release evidence. Do not infer a digest from a local tag or a single-platform image.

## Idempotent retry and Recovery

The workflow is designed for an **Idempotent retry** of the same protected tag and source SHA:

- If validation or a candidate scan fails, no final release tags are promoted. Fix the cause in a new reviewed commit and use a new release tag/version; never move the failed protected tag.
- If one final reference was promoted before a later transient failure, rerun the same workflow only when its candidate digests are unchanged. The conflict guard accepts an existing final reference only when it already equals the candidate digest.
- If every final reference is correct but prerelease creation failed, rerun the same workflow. Digest promotion and release creation are safe only when the existing values match exactly.
- If any final tag points to a different digest, stop. Do not overwrite or delete it automatically. Preserve logs, compare the remote tag/source SHA, and obtain explicit owner approval for a documented recovery decision.

Do not retag a different commit as `v0.1.10-beta`, force-push, delete a public package version, or edit release evidence to make a mismatch appear valid.

## Rollback

The independently verified previous beta is `0.1.9-beta`. On 2026-07-13, both its version and `v`-prefixed tags resolved to these four-platform manifests:

- Runtime: `ghcr.io/herbertmt978/grott@sha256:e9314693651e0cce82c603b53f88c66ae4757d93e09b97a24c56070c845d2351`
- Home Assistant add-on: `ghcr.io/herbertmt978/grott-ha-docker@sha256:7c55c161195eccd5d93bd22576bee2ea2958f68a380087eb6694d103bcfafbb1`

For Docker, restore the backed-up `grott.ini`, pin the runtime digest above, and recreate the container. For Home Assistant, the only supported rollback is to stop the candidate and restore the verified full backup named **Grott pre-update rollback** by its recorded backup ID; confirm the restored options before starting anything. In both cases, start exactly one listener on TCP `5279`, wait for fresh packets, confirm `grott_last_push`, compare values with ShinePhone, and confirm ShinePhone still updates.

The previous add-on digest above is evidence that identifies the prior artifact. It does not make historical-version reinstall from the current add-on repository a supported rollback path. Without the named verified backup, Home Assistant UAT must not begin.

Rollback mitigates an operator deployment; it does not erase a published artifact or repair a compromised tag. Any public package or GitHub release correction is a separate, explicitly authorized recovery action.
