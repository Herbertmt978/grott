# Licensing and Distribution Status

This fork preserves the upstream Grott Git history and does not add a repository-level license to inherited upstream code.

Upstream Grott currently does not include a license file that GitHub detects. On 2026-07-14, the fork owner confirmed that upstream redistribution permission has been obtained for this fork and its container images. Preserve the permission record outside this repository unless upstream publishes an explicit repository licence. This permission record does not authorize commercial use or reuse unless Johan Meijer has separately agreed and any financial reward or appreciation is directed to him. Do not relicense inherited files in this fork without that permission.

Local/private testing from a reviewed checkout may continue. Public release still requires the repository controls, hosted CI, protected tag, release workflow, UAT, and rollback gates described in `RELEASING.md`. Keep upstream history and attribution intact.

The practical approach for this fork is:

- Keep upstream copyright, history, and attribution intact.
- Keep the candidate clearly marked as experimental.
- Do not publish a new Home Assistant add-on, Docker image, Git tag, or GitHub release unless the `RELEASING.md` gates pass.
- Ask upstream to add an explicit licence when possible, or retain the written redistribution permission record before any public release.

Relevant upstream issue: https://github.com/johanmeijer/grott/issues/512

Rechecked on 2026-07-14 before owner confirmation: the issue was still open, the maintainer's recorded position remained that the default no-licence/all-rights-retained rules apply, and the fork owner's 2026-04-25 request for redistribution permission had no maintainer response in the public issue.
