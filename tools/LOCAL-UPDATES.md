# Local Windows updates

After installing an update, launch the user's archive only through
`open-installed-archive.ps1 -Executable <installed exe>` in an **unsandboxed**
execution (Codex `sandbox_permissions: require_escalated`). Filesystem write
permission does not switch the process out of the Codex sandbox account.

Never launch the live archive from a sandbox via Start-Process or Shell.Application.
An invisible instance can lock the profile and cannot use the user's DPAPI tokens.
The launcher refuses an account that does not own Explorer in the same session.

If a wrong-account instance exists, send the existing panel-exit-request.json
request and wait for its graceful exit. Do not delete the lock or force-start a
second profile writer. Never stop Chatterino as part of an archive update.

Verify the process owner matches Explorer and the titled window is visible from
that interactive session. A sandbox-local IsWindowVisible result is insufficient.
Keep disposable test profiles and offscreen tests separate from the live archive.
