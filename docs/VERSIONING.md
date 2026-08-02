# Version status and release policy

The latest published release is `v0.2.0`. The default branch contains later
work and declares `1.0.0` in `pyproject.toml`, but no `v1.0.0` tag or GitHub
release exists. Treat the default branch as an **unreleased 1.0 development
line**, not as evidence of a stable v1 product.

The public static PWA is runnable. The FastAPI service is tested source with
deployment configuration; this repository does not claim that a public API is
currently deployed. The project also remains an engineering prototype whose
guidance has not been clinically or legally validated for real-world reliance.

## Next release gate

The next release version must be chosen deliberately before a candidate is
prepared. A stable `v1.0.0` is eligible only if all of these are true:

1. the manifest, README status, candidate tag, and release notes agree;
2. supported Python tests, Ruff, CI, and the public-release guard pass on the
   intended release commit;
3. offline/PWA behavior and any API deployment statement are reverified in the
   environment actually claimed;
4. privacy boundaries and the clinical/legal nonvalidation language remain
   explicit; and
5. release notes distinguish local or mock-provider measurements from live
   service behavior and cover user-visible changes, known limitations, data
   migration, cache/offline recovery, and rollback expectations.

If those stability conditions are not met, work remains on the unreleased
development line; a tag is not created merely to match the manifest.

## Operational evidence boundary

For local source evaluation, repository rollback and resettable local cache or
audit state are proportionate recovery evidence. A genuinely deployed API
would additionally require an identified environment owner, health and error
signals, a tested rollback path, and state-recovery guidance. Those production
operations claims are not made by the current repository.
