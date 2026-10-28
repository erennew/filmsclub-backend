"""Optional fast-forward updater.

Runtime deployments must not rewrite the repository. This script only performs
``git pull --ff-only`` and only when explicitly enabled:

    ENABLE_SELF_UPDATE=true python update.py

It never deletes .git, never runs ``git add``, and never resets local changes.
"""

import os
import subprocess
import sys


def main() -> int:
    if os.getenv("ENABLE_SELF_UPDATE", "").lower() != "true":
        print("Self-update is disabled. Set ENABLE_SELF_UPDATE=true to enable.")
        return 0

    if os.getenv("UPSTREAM_REPO", "").strip():
        print("UPSTREAM_REPO is ignored by this safe updater; configure the git remote instead.")

    result = subprocess.run(["git", "pull", "--ff-only"], check=False)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
