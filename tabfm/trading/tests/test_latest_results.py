import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "latest_results.sh"
RECS = REPO / "data" / "RECOMMENDATIONS.md"


def _run():
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )


def test_latest_results_smoke():
    result = _run()
    assert result.returncode == 0, f"nonzero exit: {result.stderr}"
    for header in ("== LAST RUN ==", "== LATEST RECOMMENDATION ==", "== CURRENT BOOK ==", "== ACCURACY =="):
        assert header in result.stdout, f"missing {header!r}\n---stdout---\n{result.stdout}"


def test_latest_recommendation_shows_newest_card():
    """New cards are prepended, so the helper must surface the FIRST '## '
    header in RECOMMENDATIONS.md (the newest), not the last."""
    if not RECS.exists():
        return  # nothing to assert against
    headers = re.findall(r"^## (.+)$", RECS.read_text(), flags=re.MULTILINE)
    if not headers:
        return
    newest, oldest = headers[0], headers[-1]
    out = _run().stdout
    rec_section = out.split("== LATEST RECOMMENDATION ==", 1)[1].split("== CURRENT BOOK ==", 1)[0]
    assert newest in rec_section, f"newest card {newest!r} not shown:\n{rec_section}"
    if oldest != newest:
        assert oldest not in rec_section, f"stale card {oldest!r} leaked into newest-only section"
