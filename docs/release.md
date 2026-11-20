# Creating a release

The web updater installs only release tags that are signed with a key listed in
[`security/allowed_signers`](../security/allowed_signers). A push to `main` alone
installs nothing on the Pis.

## One-time setup

Configure Git to sign with the SSH key whose public half is in `security/allowed_signers`:

```sh
git config gpg.format ssh
git config user.signingkey ~/.ssh/id_ed25519.pub
```

## Publishing a release

1. Set the new version in `pyproject.toml`, for example `0.3.0` or `0.3.0b1` for a beta.
2. Commit and push the change to `main`.
3. Create a signed tag whose name is `v` plus exactly that version, then push it:

```sh
git tag -s v0.3.0 -m "KIBM 0.3.0"
git verify-tag v0.3.0
git push origin v0.3.0
```

The dashboard offers the update within a few minutes. Before installing, the Pi checks
the signature and that the tag matches the version in `pyproject.toml`.

A beta sorts before its final release (`0.3.0b2` < `0.3.0`). The dashboard never offers
an older version than the installed one.

## Rotating the key

Add the new public key to `security/allowed_signers` (the old one may stay for a
transition), and publish that change in a release signed with the **old** key. From
then on, Pis that installed it accept the new key.

## Troubleshooting

An unsigned or wrongly signed tag that has the highest version blocks updates: the Pi
refuses to install it. Delete that tag on GitHub (`git push origin :refs/tags/vX.Y.Z`)
and publish a correctly signed one.
