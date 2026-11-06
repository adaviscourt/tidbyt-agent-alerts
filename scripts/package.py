"""Produce a portable source archive from an explicit allowlist."""

import hashlib
from pathlib import Path
import tarfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tidbyt_agent_alerts import __version__  # noqa: E402


def package(destination):
    destination.mkdir(parents=True, exist_ok=True)
    name = f"tidbyt-agent-alerts-{__version__}"
    output = destination / (name + ".tar.gz")
    files = [ROOT / path for path in ("README.md", "LICENSE", "NOTICE", "CHANGELOG.md",
                                      "config.env.example", "install.sh", "install.py", "run.py")]
    files.extend(sorted(path for path in (ROOT / "tidbyt_agent_alerts").rglob("*")
                        if path.is_file() and path.suffix in (".py", ".star")))
    with tarfile.open(output, "w:gz") as archive:
        for path in files:
            archive.add(path, arcname=str(Path(name) / path.relative_to(ROOT)), recursive=False)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    (destination / "SHA256SUMS").write_text(f"{digest}  {output.name}\n")
    return output


if __name__ == "__main__":
    print(package(ROOT / "dist"))
