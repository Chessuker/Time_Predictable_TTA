"""Phase 3 formal properties (formal/tta_core.sby): k-induction proof and covers.

Runs SymbiYosys from oss-cad-suite in the Ubuntu-24.04 WSL distro; skipped
like the lockstep tests when that is not available. The prove task takes
about 3 minutes.
"""
import shlex

import pytest

from host.lockstep.runner import ROOT, wsl, wsl_path

from .test_lockstep import pytestmark  # noqa: F401  (same skip rule)


@pytest.mark.parametrize("task", ["prove", "cover"])
def test_formal(task):
    cmd = f"cd {shlex.quote(wsl_path(ROOT / 'formal'))} && sby -f tta_core.sby {task}"
    code, out = wsl(cmd, timeout=1800)
    assert code == 0 and f"[tta_core_{task}] DONE (PASS" in out, out[-4000:]
