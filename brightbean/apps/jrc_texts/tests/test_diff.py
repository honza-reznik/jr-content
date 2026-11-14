"""Run the browser diff algorithm's Node tests in the same built environment as the app."""

import subprocess


def test_browser_diff_node_suite():
    result = subprocess.run(
        ["timeout", "30s", "node", "--max-old-space-size=2048", "--test", "/opt/jrc-texts/diff.test.mjs"],
        capture_output=True,
        text=True,
        timeout=35,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
