# Publishing intryc-delivery

Source and releases live at [Intryc/intryc-delivery-cli](https://github.com/Intryc/intryc-delivery-cli).
The package and executable are both named `intryc-delivery`. The code is MIT licensed.
The repository contains the standalone CLI only, with a fresh history.

## One-time PyPI setup

Create/sign in to an Intryc-controlled account on both [PyPI](https://pypi.org/account/register/)
and [TestPyPI](https://test.pypi.org/account/register/). Verify the email, enable two-factor
authentication, and retain the recovery codes securely. These are separate services.
No PyPI API token or GitHub repository secret is required.

In each account's **Publishing** page, add a **pending publisher** for GitHub:

| Field | PyPI | TestPyPI |
| --- | --- | --- |
| PyPI project name | `intryc-delivery` | `intryc-delivery` |
| Owner | `Intryc` | `Intryc` |
| Repository name | `intryc-delivery-cli` | `intryc-delivery-cli` |
| Workflow filename | `release.yml` | `release.yml` |
| Environment name | `pypi` | `testpypi` |

Use the filename only, not `.github/workflows/release.yml`.
[PyPI publisher setup](https://pypi.org/manage/account/publishing/) and
[TestPyPI publisher setup](https://test.pypi.org/manage/account/publishing/).
If Intryc already owns the project, add the publisher under that project's
**Manage → Publishing** instead. A pending publisher creates the project on first
successful upload; it does not reserve the name. Add a second Intryc maintainer
as a project owner after that first upload and configure organization ownership
if applicable.

GitHub environments `pypi` and `testpypi` restrict deployments to version tags
matching `v*` and require maintainer approval. Reviewers approve through GitHub's
**Review deployments** control. Update the reviewers in repository settings when
maintainership changes. Only the publish job receives `id-token: write`; builds
and tests have read-only repository permissions. Trusted Publishing generates
short-lived credentials and provenance attestations for each upload.

## First release

1. Deploy the matching importer and readback policies, refresh existing bucket
   grants, and have customers update their writer IAM policy. Run both ownership
   modes, including customer-owned cross-account/SSE-KMS, through actual ingestion.
   Local tests do not establish deployed AWS or downstream access.
2. Confirm both pending publishers are configured. Merge the reviewed code to
   `main`, set `project.version` in `pyproject.toml` (the command reads its installed
   package metadata), update `uv.lock`, and ensure CI passes. The initial version
   is `0.1.0`; use a distinct pre-release version such as `0.1.0rc1` for an early
   public preview if needed.
3. Create the corresponding immutable tag on that reviewed main commit:

   ```sh
   git tag -a v0.1.0 -m 'Release 0.1.0'
   git push origin v0.1.0
   gh workflow run release.yml --repo Intryc/intryc-delivery-cli --ref v0.1.0 -f registry=testpypi
   ```

   Approve the `testpypi` deployment. The workflow rejects branch refs, mismatched
   versions and commits outside main. It builds once, tests that wheel on Linux
   and macOS with Python 3.11–3.14, checks package metadata, then uploads those
   tested artifacts. Download the resulting wheel from TestPyPI and install it
   locally; resolve its dependencies from normal PyPI instead of using an extra
   index that mixes package names across services.
4. Once the TestPyPI upload and pilot are satisfactory, publish a GitHub Release
   for the same tag (GitHub's **Releases → Draft a new release → Publish release**).
   This triggers production publishing. Approve the `pypi` environment deployment.
   Successful publication attaches the distributions and `SHA256SUMS` to the
   GitHub Release. A published GitHub pre-release also triggers PyPI, so choose
   the package version deliberately.
5. Verify package ownership, metadata, provenance and installation from PyPI:

   ```sh
   uv tool install intryc-delivery==0.1.0
   intryc-delivery --version
   ```

You can also dispatch `release.yml` on an existing version tag with
`registry=pypi`. That path publishes the package but does not create or attach
assets to a GitHub Release. Never replace tags or published artifacts. A failed
upload may have published some files; inspect the registry before retrying.
Existing files are not silently skipped or overwritten. Publishing remains
unexercised until the PyPI-side configuration and first release are completed.

## Development and contract maintenance

`uv sync --locked`, `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
and `uv build --no-sources` are the local checks. `uv run twine check --strict dist/*`
checks distribution metadata. CI runs tests against an installed wheel built from
the source archive, not an editable source installation. Full commit pins protect
action versions; Dependabot proposes updates for Actions and uv dependencies.

`contract-provenance.json` records the importer revision and exact snapshot hashes.
When updating the contract, run
`INTRYC_BIFROST_CONTRACT_ROOT=/path/to/AutoQA uv run pytest tests/test_contract_parity.py`
against the matching private checkout. Public CI validates the bundled snapshot
and fixtures without requiring access to private source. Preserve shared schema
versions and coordinate changes with the importer. Never copy private repository
history, credentials, customer fixtures or backend dependencies into this repo.

## References

- [Create a project through Trusted Publishing](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
- [PyPA publishing action](https://github.com/pypa/gh-action-pypi-publish)
