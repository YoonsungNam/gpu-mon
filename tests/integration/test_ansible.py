"""
Integration tests — Ansible playbooks and roles

Validates that all Ansible playbooks pass syntax-check and (when
ansible-lint is available) lint cleanly.  No real hosts are contacted:
syntax-check and lint are static analysis only.

Requires:
    ansible-playbook on PATH  (auto-skipped otherwise)
    ansible-lint on PATH      (lint tests auto-skipped if missing)

Mark: @pytest.mark.ansible
"""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ansible

REPO_ROOT    = Path(__file__).resolve().parents[2]
ANSIBLE_DIR  = REPO_ROOT / "ansible"
PLAYBOOK_DIR = ANSIBLE_DIR / "playbooks"
INVENTORY    = ANSIBLE_DIR / "inventory" / "hosts.example"

# Discover all playbooks automatically
PLAYBOOKS = sorted(PLAYBOOK_DIR.glob("*.yaml")) + sorted(PLAYBOOK_DIR.glob("*.yml"))


# ─── Skip guards ─────────────────────────────────────────────────────────────

def _require_ansible():
    if not shutil.which("ansible-playbook"):
        pytest.skip("ansible-playbook not on PATH")


def _require_ansible_lint():
    if not shutil.which("ansible-lint"):
        pytest.skip("ansible-lint not on PATH")


# ─── Syntax check ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("playbook", PLAYBOOKS, ids=[p.name for p in PLAYBOOKS])
def test_playbook_syntax_check(playbook: Path):
    """Every playbook must pass ansible-playbook --syntax-check."""
    _require_ansible()

    # Use example inventory if present; otherwise fall back to localhost stub
    inv_args = ["-i", str(INVENTORY)] if INVENTORY.exists() else ["-i", "localhost,"]

    result = subprocess.run(
        ["ansible-playbook", "--syntax-check"] + inv_args + [str(playbook)],
        capture_output=True,
        text=True,
        cwd=str(ANSIBLE_DIR),
    )
    assert result.returncode == 0, (
        f"Syntax check failed for {playbook.name}:\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


# ─── Lint ─────────────────────────────────────────────────────────────────────

def test_ansible_lint_passes():
    """ansible-lint should report no errors on the entire ansible/ directory."""
    _require_ansible()
    _require_ansible_lint()

    result = subprocess.run(
        ["ansible-lint", "--profile", "min", str(ANSIBLE_DIR)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"ansible-lint found issues:\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
