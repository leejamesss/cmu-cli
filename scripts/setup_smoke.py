"""Exercise an installed executable in a real PTY with a disposable empty home."""

import errno
import json
import os
import pty
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def smoke(executable):
    with tempfile.TemporaryDirectory(prefix="cmu-setup-smoke-") as temporary:
        home = Path(temporary)
        output = home / "new-config.json"
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("CMU_CLI_", "CMUCW_", "PYTHON"))
        }
        env["HOME"] = str(home)
        master, slave = pty.openpty()
        child = subprocess.Popen(
            [str(executable), "setup", "--output", str(output)],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            cwd=home,
        )
        os.close(slave)
        answers = [
            "https://canvas.example.invalid",
            "DEMO-101",
            "https://canvas.example.invalid/courses/12",
            "no",
            "",
            "Synthetic Course",
            "",
            "",
            "",
            "Fall 2030",
            str(home / "materials"),
            "yes",
        ]
        os.write(master, ("\n".join(answers) + "\n").encode())
        transcript = bytearray()
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                        break
                    if not chunk:
                        break
                    transcript.extend(chunk)
                elif child.poll() is not None:
                    break
            child.wait(timeout=5)
            assert child.returncode == 0, transcript.decode(errors="replace")
            raw = json.loads(output.read_text())
            assert raw["courses"] == [
                {
                    "code": "DEMO-101",
                    "name": "Synthetic Course",
                    "canvas_id": 12,
                    "directory": "DEMO-101",
                }
            ]
            assert raw["term"] == "Fall 2030"
            assert "browser_auth" not in raw
            assert not (home / "materials").exists()
            result = subprocess.run(
                [
                    str(executable),
                    "--config",
                    str(output),
                    "config",
                    "validate",
                    "--json",
                ],
                env=env,
                cwd=home,
                capture_output=True,
                text=True,
                check=True,
            )
            assert json.loads(result.stdout)["data"]["valid"] is True
            print(
                "Installed executable: real PTY setup created and validated a synthetic config; no material writes."
            )
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            os.close(master)


if __name__ == "__main__":
    smoke(Path(sys.argv[1]).resolve())
