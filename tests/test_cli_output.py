"""What `stenny` prints into a pipe is UTF-8 whatever the system's code page (cli.utf8_output): the connector and
install.ps1 read it as UTF-8, and a Windows user folder may hold any letter."""
import json
import os
import subprocess
import sys


def test_where_json_is_utf8_in_a_pipe_with_a_legacy_code_page(tmp_path):
    home = tmp_path / "Łukasz Böttcher"   # ł isn't in cp1252: printing it there would stop the command
    env = {**os.environ, "STENNY_HOME": str(home), "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    env.pop("STENNY_CONFIG", None)
    done = subprocess.run([sys.executable, "-m", "stenny", "where", "--json"], capture_output=True, env=env, timeout=60)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    places = json.loads(done.stdout.decode("utf-8"))
    assert "Łukasz Böttcher" in json.dumps(places, ensure_ascii=False)
