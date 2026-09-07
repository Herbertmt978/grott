# Licensing and Distribution Status

The [Grott Personal Use License](../LICENSE.md) is the governing license for material in this fork that its copyright holders have authorised to be distributed under it. It allows personal, non-commercial use, modification and free sharing on its stated terms. Commercial use requires Johan Meijer's separate written agreement; a donation alone does not grant permission. This page explains the history and publication requirements; it does not grant additional rights.

## Authorisation and scope

On 2026-09-07, the fork owner confirmed that Johan Meijer had authorised publication of a personal-use license for his code. This is the authority for adding `LICENSE.md`, and is separate from the redistribution permission recorded on 2026-07-14. Preserve the permission record outside this repository.

The license replaces this fork's earlier statements that it provided no repository-level license for the material now covered. It does not relicense another contributor's work without that contributor's authorisation. Third-party material supplied under separate licenses keeps those terms, notices and attribution. Upstream Git history and copyright remain intact. Publishing the license here does not change the upstream repository or rewrite the terms of older immutable releases and images.

## Distribution and release checks

Local/private testing may continue within the license or other applicable permission. Public release still requires the repository controls, hosted CI, protected tag, release workflow, UAT and rollback gates in [RELEASING.md](../RELEASING.md).

- Keep upstream copyright, history and attribution intact.
- Keep the add-on's experimental status until it is separately qualified for promotion.
- Include the complete `LICENSE.md` at `/app/LICENSE.md` in both new container images. The artifact validator checks it before publication.
- Confirm that every included contribution and third-party dependency is covered by the license or its own permission and retain the required notices.
- Obtain Johan's separate written agreement before any commercial use. An acknowledgement, donation or unanswered request is not permission.

## Earlier permission record

On 2026-07-14, the fork owner confirmed redistribution permission for this fork and its container images. That permission alone did not authorise relicensing. The subsequent personal-use authorisation above is what supports the new license.

The earlier discussion in [johanmeijer/grott#512](https://github.com/johanmeijer/grott/issues/512) and the published release notes are historical records. They are not the current license for the material covered by `LICENSE.md`.
