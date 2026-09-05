import os
import socket

from fastapi.testclient import TestClient

from app import paths
from app.launcher import HOST, select_port
from app.main import APP_DIR, app
from app.single_instance import SingleInstance
from app.version import VERSION


client = TestClient(app)


def test_runtime_path_selection_in_dev_mode():
    assert paths.runtime_dir(frozen=False) == paths.source_root() / "data" / "runtime"
    assert paths.sde_dir(frozen=False) == paths.source_root() / "data" / "sde"


def test_frozen_mode_writable_path_selection():
    base = r"C:\Users\TestUser\AppData\Local"
    assert paths.runtime_dir(frozen=True, local_appdata=base).as_posix().endswith("/EveSkillOptimizer/runtime")
    assert paths.logs_dir(frozen=True, local_appdata=base).as_posix().endswith("/EveSkillOptimizer/logs")


def test_template_static_path_resolution():
    assert (APP_DIR / "templates" / "index.html").exists()
    assert (APP_DIR / "static" / "app.js").exists()
    assert (APP_DIR / "static" / "styles.css").exists()


def test_release_server_binds_only_to_localhost_constant():
    assert HOST == "127.0.0.1"


def test_release_version_is_0_9_3():
    assert VERSION == "0.9.10"
    assert app.version == "0.9.10"


def test_launcher_port_selection_does_not_kill_occupied_process():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        sock.listen()
        occupied = sock.getsockname()[1]
        chosen = select_port(occupied)
        assert chosen != occupied


def test_single_instance_lock_file_roundtrip(tmp_path):
    lock = SingleInstance(tmp_path / "optimizer.lock")
    assert lock.acquire(8000) is None
    existing = SingleInstance(tmp_path / "optimizer.lock").acquire(8001)
    assert existing is not None
    assert existing.pid == os.getpid()
    assert existing.port == 8000
    lock.release()


def test_status_does_not_expose_credential_paths_or_tokens():
    response = client.get("/api/status")
    text = response.text.lower()
    assert "refresh_token" not in text
    assert "access_token" not in text
    assert "clientsecret" not in text
    assert "settings.xml" not in text


def test_root_docs_and_status_still_work():
    assert client.get("/").status_code == 200
    assert client.get("/docs").status_code == 200
    status = client.get("/api/status")
    assert status.status_code == 200
    assert status.json()["version"] == "0.9.10"
