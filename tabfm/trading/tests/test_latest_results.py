import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "latest_results.sh"


def test_latest_results_smoke():
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert result.returncode == 0, f"nonzero exit: {result.stderr}"
    for header in ("== LAST RUN ==", "== LATEST RECOMMENDATION ==", "== CURRENT BOOK =="):
        assert header in result.stdout, f"missing {header!r}\n---stdout---\n{result.stdout}"
