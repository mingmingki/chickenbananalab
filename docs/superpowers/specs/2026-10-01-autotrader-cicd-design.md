# Autotrader CI/CD transition

Complete the existing infra/autotrader-cicd transition without changing live trading,
exchange protections, runtime configuration, or active service state. The user
explicitly authorized continuous execution, branch commit/push and Actions checks.

Use the existing local branch as the base. Import selected source and test files,
never runtime data or keys. Run each historical test suite in its own process to
avoid duplicate module imports. Use a temporary project directory, remove inherited
credentials and deny network access during tests. Fail CI on any failing suite.

Build a source-only, SHA256-labelled artifact only after successful verification.
After infrastructure safety checks pass, a separate connection job checks the existing GCP VM through SSH and prints only
service state and a fixed success marker. It never imports the application or
runs deployment scripts. Production activation is outside this non-disruptive
transition: no automatic deploy, restart, stop, configuration changes or order API.

GitHub secrets contain the dedicated SSH key and trusted host-key entry; repository
variables select the existing host and user. The connection command is fixed and
read-only. A dedicated restricted SSH key can enforce the same command server-side.
No cloud service account key is required for this SSH-only workflow.

The transport-only probe may run while application regressions fail; it cannot
activate a release. Artifact publication still requires every regression suite.
