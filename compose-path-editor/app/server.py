from __future__ import annotations

import difflib
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

HOST_ROOT = Path(os.getenv("HOST_UMBREL_ROOT", "/host/umbrel")).resolve()
APP_ID = os.getenv("APP_ID", "martin-tools-compose-path-editor")
MAX_COMPOSE_SIZE_BYTES = 2 * 1024 * 1024

ALLOWED_REPLACEMENT_PATHS = [
    "/home/umbrel/umbrel/home/Downloads/qbittorrent/complete",
    "/home/umbrel/umbrel/home/Downloads/qbittorrent/incomplete",
    "/home/umbrel/umbrel/home/Downloads/sabnzbd/complete",
    "/home/umbrel/umbrel/home/Downloads/Films",
    "/home/umbrel/umbrel/home/Downloads/Films2",
    "/home/umbrel/umbrel/home/Downloads/TVSerie",
    "/home/umbrel/umbrel/home/Downloads/TVSeriesOLD",
]

# Herkent de host-zijde van een Docker volume-regel, bijvoorbeeld:
#   - /home/umbrel/umbrel/home/Downloads/Films:/media/movies
#   - "${UMBREL_ROOT}/data/storage/downloads:/downloads"
VOLUME_LINE_RE = re.compile(
    r"^(?P<prefix>\s*-\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<host>(?:/[^:\"'#\s]+|\$\{UMBREL_ROOT\}/[^:\"'#\s]+))"
    r"(?P<suffix>:[^\"'#\n]*)"
    r"(?P=quote)"
    r"(?P<trailing>\s*(?:#.*)?)$"
)

ABSOLUTE_PATH_RE = re.compile(r"(?P<path>/(?:home|mnt|media|srv|data|var|opt)/[^:\"'#\s]+)")


def error(message: str, status: int = 400):
    response = jsonify({"error": message})
    response.status_code = status
    return response


def to_host_display(path: Path) -> str:
    try:
        return "/home/umbrel/umbrel/" + str(path.resolve().relative_to(HOST_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def from_client_path(raw_path: str | None) -> Path:
    if not raw_path:
        raise ValueError("Geen docker-compose.yml bestand opgegeven.")

    path = Path(raw_path)
    if str(raw_path).startswith("/home/umbrel/umbrel/"):
        path = HOST_ROOT / str(raw_path).removeprefix("/home/umbrel/umbrel/")

    resolved = path.resolve()
    root = HOST_ROOT.resolve()

    if resolved.name != "docker-compose.yml":
        raise ValueError("Alleen bestanden met de naam docker-compose.yml zijn toegestaan.")

    if resolved != root and root not in resolved.parents:
        raise ValueError("Bestand valt buiten de toegestane Umbrel root.")

    if not resolved.is_file():
        raise ValueError("Het gekozen docker-compose.yml bestand bestaat niet.")

    if resolved.stat().st_size > MAX_COMPOSE_SIZE_BYTES:
        raise ValueError("Het compose-bestand is groter dan de ingestelde veiligheidslimiet van 2 MB.")

    return resolved


def scan_compose_files() -> list[dict[str, Any]]:
    candidates: list[Path] = []
    search_roots = [HOST_ROOT / "app-data", HOST_ROOT / "app-stores"]

    for root in search_roots:
        if not root.exists():
            continue
        for compose in root.rglob("docker-compose.yml"):
            try:
                resolved = compose.resolve()
            except OSError:
                continue
            if APP_ID in resolved.parts:
                continue
            if resolved.is_file() and resolved.stat().st_size <= MAX_COMPOSE_SIZE_BYTES:
                candidates.append(resolved)

    unique = sorted(set(candidates), key=lambda p: ("/app-data/" not in str(p), str(p)))
    result: list[dict[str, Any]] = []

    for path in unique:
        display_path = to_host_display(path)
        installed = "/app-data/" in display_path
        app_name = path.parent.name
        if installed:
            label = app_name
        else:
            store_name = path.parent.parent.name if path.parent.parent else "app-store"
            label = f"{app_name} ({store_name})"

        result.append(
            {
                "label": label,
                "file": str(path),
                "displayPath": display_path,
                "installed": installed,
            }
        )

    return result


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def classify_line(line: str) -> dict[str, Any]:
    stripped = line.strip()
    is_comment = stripped.startswith("#")
    is_empty = stripped == ""
    is_volume_like = bool(VOLUME_LINE_RE.match(line))
    contains_known_path = any(path in line for path in ALLOWED_REPLACEMENT_PATHS)
    contains_absolute_path = bool(ABSOLUTE_PATH_RE.search(line))

    return {
        "selectable": not is_empty and not is_comment,
        "recommended": is_volume_like or contains_known_path or contains_absolute_path,
        "volumeLike": is_volume_like,
        "containsKnownPath": contains_known_path,
        "containsAbsolutePath": contains_absolute_path,
    }


def build_replacement_line(original_line: str, new_path: str) -> tuple[str, str]:
    if new_path not in ALLOWED_REPLACEMENT_PATHS:
        raise ValueError("Gekozen pad staat niet in de vaste toegestane lijst.")

    volume_match = VOLUME_LINE_RE.match(original_line)
    if volume_match:
        groups = volume_match.groupdict()
        replacement = (
            f"{groups['prefix']}{groups['quote']}{new_path}"
            f"{groups['suffix']}{groups['quote']}{groups['trailing']}"
        )
        return replacement, "host-pad in volume-regel vervangen"

    for old_path in ALLOWED_REPLACEMENT_PATHS:
        if old_path in original_line:
            return original_line.replace(old_path, new_path, 1), "bekend pad in regel vervangen"

    absolute_match = ABSOLUTE_PATH_RE.search(original_line)
    if absolute_match:
        start, end = absolute_match.span("path")
        return original_line[:start] + new_path + original_line[end:], "eerste absolute pad in regel vervangen"

    indentation = re.match(r"^\s*", original_line).group(0)
    return f"{indentation}{new_path}", "hele regel vervangen"


def make_new_file_lines(path: Path, line_number: int, new_path: str) -> tuple[list[str], str, str, str]:
    lines = read_lines(path)
    if line_number < 1 or line_number > len(lines):
        raise ValueError("Ongeldig regelnummer.")

    old_line = lines[line_number - 1]
    new_line, mode = build_replacement_line(old_line, new_path)
    if old_line == new_line:
        raise ValueError("De gekozen wijziging verandert de regel niet.")

    new_lines = list(lines)
    new_lines[line_number - 1] = new_line
    return new_lines, old_line, new_line, mode


def unified_diff(old_lines: list[str], new_lines: list[str], path: Path) -> str:
    return "\n".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"{to_host_display(path)} (huidig)",
            tofile=f"{to_host_display(path)} (nieuw)",
            lineterm="",
        )
    )


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/paths")
def api_paths():
    return jsonify({"paths": ALLOWED_REPLACEMENT_PATHS})


@app.get("/api/apps")
def api_apps():
    return jsonify({"apps": scan_compose_files(), "hostRoot": str(HOST_ROOT)})


@app.get("/api/file")
def api_file():
    try:
        path = from_client_path(request.args.get("file"))
        lines = read_lines(path)
    except ValueError as exc:
        return error(str(exc))
    except OSError as exc:
        return error(f"Kan bestand niet lezen: {exc}", 500)

    return jsonify(
        {
            "file": str(path),
            "displayPath": to_host_display(path),
            "lines": [
                {
                    "number": index + 1,
                    "text": line,
                    **classify_line(line),
                }
                for index, line in enumerate(lines)
            ],
        }
    )


@app.post("/api/preview")
def api_preview():
    payload = request.get_json(silent=True) or {}
    try:
        path = from_client_path(payload.get("file"))
        line_number = int(payload.get("lineNumber", 0))
        new_path = payload.get("newPath")
        old_lines = read_lines(path)
        new_lines, old_line, new_line, mode = make_new_file_lines(path, line_number, new_path)
    except (ValueError, TypeError) as exc:
        return error(str(exc))
    except OSError as exc:
        return error(f"Kan voorbeeld niet maken: {exc}", 500)

    return jsonify(
        {
            "mode": mode,
            "oldLine": old_line,
            "newLine": new_line,
            "diff": unified_diff(old_lines, new_lines, path),
        }
    )


@app.post("/api/apply")
def api_apply():
    payload = request.get_json(silent=True) or {}
    try:
        path = from_client_path(payload.get("file"))
        line_number = int(payload.get("lineNumber", 0))
        new_path = payload.get("newPath")
        old_lines = read_lines(path)
        new_lines, old_line, new_line, mode = make_new_file_lines(path, line_number, new_path)
    except (ValueError, TypeError) as exc:
        return error(str(exc))
    except OSError as exc:
        return error(f"Kan wijziging niet voorbereiden: {exc}", 500)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = path.with_name(f"{path.name}.bak-{timestamp}")

    try:
        shutil.copy2(path, backup_path)
        new_content = "\n".join(new_lines) + "\n"
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(new_content)
            temp_name = temp_file.name
        os.replace(temp_name, path)
    except OSError as exc:
        return error(f"Kan wijziging niet opslaan: {exc}", 500)

    return jsonify(
        {
            "ok": True,
            "mode": mode,
            "oldLine": old_line,
            "newLine": new_line,
            "backupPath": to_host_display(backup_path),
            "message": "docker-compose.yml is aangepast. Herstart daarna de gekozen Umbrel app zodat Docker Compose de wijziging gebruikt.",
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8099, debug=False)
