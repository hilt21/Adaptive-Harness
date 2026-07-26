from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).parents[2]
INSTALLER = REPOSITORY / "scripts/install.sh"
PACKAGER = REPOSITORY / "scripts/package_release.py"
RELEASE_WORKFLOW = REPOSITORY / ".github/workflows/release.yml"


def _release(
    tmp_path: Path,
    *,
    checksum: str | None = None,
    runtime_content: bytes | None = None,
) -> Path:
    release_root = tmp_path / "releases"
    version_root = release_root / "v0.1.0"
    version_root.mkdir(parents=True, exist_ok=True)
    archive_name = "adaptive-harness-v0.1.0-linux-x86_64.tar.gz"
    archive = version_root / archive_name
    content = runtime_content or b"#!/bin/sh\nprintf '0.1.0\\n'\n"
    info = tarfile.TarInfo("runtime/harness")
    info.mode = 0o755
    info.size = len(content)
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.addfile(info, io.BytesIO(content))
        support = tarfile.TarInfo("runtime/_internal/support.dat")
        support.size = len(b"support")
        bundle.addfile(support, io.BytesIO(b"support"))
    digest = checksum or hashlib.sha256(archive.read_bytes()).hexdigest()
    (version_root / "SHA256SUMS").write_text(
        f"{digest}  {archive_name}\n", encoding="utf-8"
    )
    return release_root


def _run_installer(
    tmp_path: Path,
    release_root: Path,
    *,
    environment_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    install_dir = tmp_path / "bin"
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    environment = dict(os.environ)
    environment.update(
        {
            "HOME": str(home),
            "HARNESS_INSTALL_DIR": str(install_dir),
            "HARNESS_BWRAP_COMMAND": "__missing_bwrap__",
            "HARNESS_NONINTERACTIVE": "1",
            "HARNESS_RELEASE_BASE": release_root.as_uri(),
            "HARNESS_TARGET": "linux-x86_64",
            "HARNESS_VERSION": "0.1.0",
            "PATH": "/usr/bin:/bin",
        }
    )
    environment.update(environment_overrides or {})
    return subprocess.run(
        ("sh", str(INSTALLER)),
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_installer_verifies_and_exposes_release_without_touching_repository(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)

    completed = _run_installer(tmp_path, release_root)

    installed = tmp_path / "bin/harness"
    assert completed.returncode == 0, completed.stderr
    assert installed.is_file()
    assert installed.stat().st_mode & 0o111
    assert subprocess.check_output((str(installed),), text=True).strip() == "0.1.0"
    assert not (tmp_path / "workspace/.harness").exists()
    assert not (tmp_path / "home/.zshrc").exists()
    manifest = json.loads(
        (tmp_path / "home/.local/share/harness/installation.json").read_text(
            encoding="utf-8"
        )
    )
    runtime_pointer = tmp_path / "home/.local/share/harness/runtime/current"
    manifest_pointer = tmp_path / "home/.local/share/harness/installation.json"
    assert runtime_pointer.is_symlink()
    assert manifest_pointer.is_symlink()
    assert manifest_pointer.readlink() == Path("runtime/current/installation.json")
    assert manifest["channel"] == "standalone"
    assert manifest["version"] == "0.1.0"
    assert manifest["binary_path"] == str(installed)
    assert manifest["runtime_path"] == str(
        tmp_path / "home/.local/share/harness/runtime/current"
    )
    assert manifest["path_profile"] is None
    assert "not on PATH" in completed.stdout
    assert "Bubblewrap" in completed.stdout
    assert "observe remains available" in completed.stdout


def test_installer_rejects_checksum_mismatch_without_installing(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path, checksum="0" * 64)

    completed = _run_installer(tmp_path, release_root)

    assert completed.returncode != 0
    assert "checksum" in completed.stderr.lower()
    assert not (tmp_path / "bin/harness").exists()


def test_installer_refuses_concurrent_installation_operation(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "home/.local/share/harness.install.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(f"{os.getpid()}\n", encoding="ascii")

    completed = _run_installer(tmp_path, _release(tmp_path))

    assert completed.returncode != 0
    assert "operation is in progress" in completed.stderr
    assert lock.is_file()


def test_installer_recovers_stale_installation_lock(tmp_path: Path) -> None:
    lock = tmp_path / "home/.local/share/harness.install.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("99999999\n", encoding="ascii")

    completed = _run_installer(tmp_path, _release(tmp_path))

    assert completed.returncode == 0, completed.stderr
    assert not lock.exists()


def test_installer_publishes_only_complete_owner_lock() -> None:
    script = INSTALLER.read_text(encoding="utf-8")

    assert 'ln "$candidate_lock" "$install_lock"' in script
    assert 'mkdir "$install_lock"' not in script


def test_interactive_path_confirmation_writes_one_managed_block(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    profile = tmp_path / "home/.zshrc"

    first = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_NONINTERACTIVE": "0",
            "HARNESS_SHELL_PROFILE": str(profile),
        },
    )
    second = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_NONINTERACTIVE": "0",
            "HARNESS_SHELL_PROFILE": str(profile),
        },
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "@@" in first.stdout
    assert "+# >>> adaptive-harness PATH >>>" in first.stdout
    content = profile.read_text(encoding="utf-8")
    assert content.count("# >>> adaptive-harness PATH >>>") == 1
    assert f"export PATH='{tmp_path / 'bin'}':\"$PATH\"" in content
    manifest = json.loads(
        (tmp_path / "home/.local/share/harness/installation.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["path_profile"] == str(profile)


@pytest.mark.parametrize("trailing_newline", ["", "\n"])
def test_path_review_does_not_echo_existing_secret(
    tmp_path: Path, trailing_newline: str
) -> None:
    release_root = _release(tmp_path)
    profile = tmp_path / "home/.zshrc"
    secret = "HARNESS_TEST_TOKEN=do-not-print-this"
    profile.parent.mkdir()
    profile.write_text(f"export {secret}{trailing_newline}", encoding="utf-8")

    completed = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_SHELL_PROFILE": str(profile),
        },
    )

    if trailing_newline:
        assert completed.returncode == 0, completed.stderr
    else:
        assert completed.returncode != 0
        assert "must end with a newline" in completed.stderr
    assert secret not in completed.stdout


def test_installer_refuses_noncanonical_managed_profile_block(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    profile = tmp_path / "home/.zshrc"
    profile.parent.mkdir()
    secret = "TOKEN=must-not-be-managed"
    profile.write_text(
        "# >>> adaptive-harness PATH >>>\n"
        f"{secret}\n"
        "# <<< adaptive-harness PATH <<<\n",
        encoding="utf-8",
    )

    completed = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_SHELL_PROFILE": str(profile),
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        },
    )

    assert completed.returncode != 0
    assert "malformed" in completed.stderr
    assert secret not in completed.stdout


def test_path_apply_refuses_profile_changed_after_review(tmp_path: Path) -> None:
    release_root = _release(tmp_path)
    profile = tmp_path / "home/.zshrc"
    profile.parent.mkdir()
    profile.write_text("# original\n", encoding="utf-8")
    wrapper_directory = tmp_path / "wrappers"
    wrapper_directory.mkdir()
    diff_wrapper = wrapper_directory / "diff"
    diff_wrapper.write_text(
        "#!/bin/sh\n"
        "/usr/bin/diff \"$@\"\n"
        "status=$?\n"
        "printf '# concurrent change\\n' >> \"$HARNESS_TEST_PROFILE\"\n"
        "exit \"$status\"\n",
        encoding="utf-8",
    )
    diff_wrapper.chmod(0o755)

    completed = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_SHELL_PROFILE": str(profile),
            "HARNESS_TEST_PROFILE": str(profile),
            "PATH": f"{wrapper_directory}:/usr/bin:/bin",
        },
    )

    assert completed.returncode != 0
    assert "changed after review" in completed.stderr
    assert profile.read_text(encoding="utf-8") == (
        "# original\n# concurrent change\n"
    )
    assert not (tmp_path / "bin/harness").exists()


def test_repeat_install_keeps_managed_profile_when_path_is_already_configured(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    profile = tmp_path / "home/.zshrc"
    first = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_SHELL_PROFILE": str(profile),
        },
    )
    assert first.returncode == 0, first.stderr

    second = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_SHELL_PROFILE": str(profile),
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        },
    )

    assert second.returncode == 0, second.stderr
    manifest = json.loads(
        (tmp_path / "home/.local/share/harness/installation.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["path_profile"] == str(profile)


def test_repeat_install_preserves_recorded_profile_when_shell_selection_changes(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    original_profile = tmp_path / "home/custom-profile"
    first = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_SHELL_PROFILE": str(original_profile),
        },
    )
    assert first.returncode == 0, first.stderr

    second = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_SHELL_PROFILE": str(tmp_path / "home/other-profile"),
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        },
    )

    assert second.returncode == 0, second.stderr
    manifest = json.loads(
        (tmp_path / "home/.local/share/harness/installation.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["path_profile"] == str(original_profile)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"HARNESS_VERSION": "not-a-version"}, "semantic version"),
        ({"HARNESS_VERSION": "01.2.3"}, "semantic version"),
        ({"HARNESS_VERSION": "1.2.3-rc..1"}, "semantic version"),
        ({"HARNESS_INSTALL_DIR": "relative-bin"}, "absolute"),
        ({"HARNESS_STATE_DIR": "relative-state"}, "absolute"),
        ({"HARNESS_SHELL_PROFILE": "relative-profile"}, "absolute"),
        ({"HARNESS_RELEASE_REPOSITORY": "invalid"}, "repository"),
    ],
)
def test_installer_rejects_invalid_manifest_inputs_before_installing(
    tmp_path: Path,
    overrides: dict[str, str],
    message: str,
) -> None:
    completed = _run_installer(
        tmp_path,
        _release(tmp_path),
        environment_overrides=overrides,
    )

    assert completed.returncode != 0
    assert message in completed.stderr
    assert not (tmp_path / "bin/harness").exists()


def test_installer_accepts_semver_prerelease_and_build_syntax(
    tmp_path: Path,
) -> None:
    completed = _run_installer(
        tmp_path,
        _release(tmp_path),
        environment_overrides={"HARNESS_VERSION": "1.2.3-rc.1+build.5"},
    )

    assert "semantic version" not in completed.stderr


def test_installer_rejects_home_data_root_and_symlinked_profile_ancestor(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    home = tmp_path / "home"

    unsafe_state = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={"HARNESS_STATE_DIR": str(home)},
    )
    assert unsafe_state.returncode != 0
    assert "must not be / or HOME" in unsafe_state.stderr

    real_directory = tmp_path / "real-profile-directory"
    real_directory.mkdir()
    profile_parent = home / "linked-profile-directory"
    profile_parent.symlink_to(real_directory, target_is_directory=True)
    unsafe_profile = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_SHELL_PROFILE": str(profile_parent / ".zshrc"),
        },
    )
    assert unsafe_profile.returncode != 0
    assert "symlinked ancestor" in unsafe_profile.stderr
    assert not (real_directory / ".zshrc").exists()


def test_path_profile_update_is_all_or_nothing_when_replace_is_unavailable(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    profile_directory = tmp_path / "home/locked"
    profile_directory.mkdir(parents=True)
    profile = profile_directory / ".zshrc"
    original = "# user content\n"
    profile.write_text(original, encoding="utf-8")
    profile_directory.chmod(0o555)
    try:
        completed = _run_installer(
            tmp_path,
            release_root,
            environment_overrides={
                "HARNESS_CONFIRM_PATH": "1",
                "HARNESS_NONINTERACTIVE": "0",
                "HARNESS_SHELL_PROFILE": str(profile),
            },
        )
    finally:
        profile_directory.chmod(0o755)

    assert completed.returncode != 0
    assert profile.read_text(encoding="utf-8") == original


def test_late_install_failure_restores_launcher_and_shell_profile(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    home = tmp_path / "home"
    install_dir = tmp_path / "bin"
    install_dir.mkdir()
    launcher = install_dir / "harness"
    original_launcher = b"#!/bin/sh\nprintf 'previous-version\\n'\n"
    launcher.write_bytes(original_launcher)
    launcher.chmod(0o755)
    profile = home / ".zshrc"
    profile.parent.mkdir(parents=True)
    original_profile = "# user content\n"
    profile.write_text(original_profile, encoding="utf-8")
    manifest = home / ".local/share/harness/installation.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("unsafe pre-existing manifest\n", encoding="utf-8")

    completed = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_NONINTERACTIVE": "0",
            "HARNESS_SHELL_PROFILE": str(profile),
        },
    )

    assert completed.returncode != 0
    assert launcher.read_bytes() == original_launcher
    assert profile.read_text(encoding="utf-8") == original_profile
    assert manifest.read_text(encoding="utf-8") == "unsafe pre-existing manifest\n"
    assert not (manifest.parent / "runtime").exists()


def test_smoke_failure_restores_runtime_pointers_for_a_retry(tmp_path: Path) -> None:
    release_root = _release(tmp_path)
    first = _run_installer(tmp_path, release_root)
    assert first.returncode == 0, first.stderr
    runtime_parent = tmp_path / "home/.local/share/harness/runtime"
    current = runtime_parent / "current"
    previous = runtime_parent / "previous"
    original_target = current.readlink()
    original_slots = sorted(path.name for path in (runtime_parent / "slots").iterdir())
    counter = tmp_path / "smoke-counter"
    failing_content = (
        b"#!/bin/sh\n"
        b"if [ ! -e \"$HARNESS_TEST_SMOKE_COUNTER\" ]; then\n"
        b"  : > \"$HARNESS_TEST_SMOKE_COUNTER\"\n"
        b"  printf '0.1.0\\n'\n"
        b"else\n"
        b"  printf 'wrong-version\\n'\n"
        b"fi\n"
    )
    _release(tmp_path, runtime_content=failing_content)

    failed = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={"HARNESS_TEST_SMOKE_COUNTER": str(counter)},
    )

    assert failed.returncode != 0
    assert current.readlink() == original_target
    assert not previous.exists()
    assert sorted(path.name for path in (runtime_parent / "slots").iterdir()) == (
        original_slots
    )

    _release(tmp_path)
    retry = _run_installer(tmp_path, release_root)
    assert retry.returncode == 0, retry.stderr


def test_failed_install_preserves_concurrent_profile_change(
    tmp_path: Path,
) -> None:
    release_root = _release(
        tmp_path,
        runtime_content=(
            b"#!/bin/sh\n"
            b"if [ ! -e \"$HARNESS_TEST_SMOKE_COUNTER\" ]; then\n"
            b"  : > \"$HARNESS_TEST_SMOKE_COUNTER\"; printf '0.1.0\\n'\n"
            b"else\n"
            b"  printf '# concurrent profile change\\n' >> \"$HARNESS_TEST_PROFILE\"\n"
            b"  printf 'wrong-version\\n'\n"
            b"fi\n"
        ),
    )
    profile = tmp_path / "home/.zshrc"

    completed = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_NONINTERACTIVE": "0",
            "HARNESS_SHELL_PROFILE": str(profile),
            "HARNESS_TEST_PROFILE": str(profile),
            "HARNESS_TEST_SMOKE_COUNTER": str(tmp_path / "smoke-counter"),
        },
    )

    assert completed.returncode != 0
    assert "recovery was incomplete" in completed.stderr
    assert "# concurrent profile change" in profile.read_text(encoding="utf-8")


def test_failed_pointer_recovery_retains_the_active_runtime_slot(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    first = _run_installer(tmp_path, release_root)
    assert first.returncode == 0, first.stderr
    runtime_parent = tmp_path / "home/.local/share/harness/runtime"
    current = runtime_parent / "current"
    counter = tmp_path / "current-move-count"
    wrappers = tmp_path / "pointer-wrappers"
    wrappers.mkdir()
    mv_wrapper = wrappers / "mv"
    mv_wrapper.write_text(
        "#!/bin/sh\n"
        "target=\n"
        "for argument in \"$@\"; do target=$argument; done\n"
        "if [ \"$target\" = \"$HARNESS_TEST_CURRENT\" ]; then\n"
        "  if [ -e \"$HARNESS_TEST_COUNTER\" ]; then exit 1; fi\n"
        "  /bin/mv \"$@\"\n"
        "  status=$?\n"
        "  if [ \"$status\" -eq 0 ]; then : > \"$HARNESS_TEST_COUNTER\"; fi\n"
        "  exit \"$status\"\n"
        "fi\n"
        "exec /bin/mv \"$@\"\n",
        encoding="utf-8",
    )
    mv_wrapper.chmod(0o755)
    smoke_counter = tmp_path / "smoke-counter"
    _release(
        tmp_path,
        runtime_content=(
            b"#!/bin/sh\n"
            b"if [ ! -e \"$HARNESS_TEST_SMOKE_COUNTER\" ]; then\n"
            b"  : > \"$HARNESS_TEST_SMOKE_COUNTER\"; printf '0.1.0\\n'\n"
            b"else printf 'wrong-version\\n'; fi\n"
        ),
    )

    failed = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_TEST_CURRENT": str(current),
            "HARNESS_TEST_COUNTER": str(counter),
            "HARNESS_TEST_SMOKE_COUNTER": str(smoke_counter),
            "PATH": f"{wrappers}:/usr/bin:/bin",
        },
    )

    assert failed.returncode != 0
    assert "recovery was incomplete" in failed.stderr
    assert current.is_symlink()
    assert (current / "harness").is_file()


def test_release_packager_emits_installable_archive_and_checksum(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "built-harness"
    bundle.mkdir()
    binary = bundle / "harness"
    binary.write_bytes(b"standalone-binary")
    binary.chmod(0o755)
    support = bundle / "_internal/support.dat"
    support.parent.mkdir()
    support.write_bytes(b"support")
    output = tmp_path / "release"

    completed = subprocess.run(
        (
            sys.executable,
            str(PACKAGER),
            "--bundle",
            str(bundle),
            "--version",
            "0.1.0",
            "--target",
            "linux-x86_64",
            "--output",
            str(output),
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    archive = output / "adaptive-harness-v0.1.0-linux-x86_64.tar.gz"
    checksum = output / "adaptive-harness-v0.1.0-linux-x86_64.tar.gz.sha256"
    assert completed.returncode == 0, completed.stderr
    assert checksum.read_text(encoding="utf-8") == (
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
    )
    with tarfile.open(archive, "r:gz") as archive_bundle:
        member = archive_bundle.getmember("runtime/harness")
        assert member.mode == 0o755
        extracted = archive_bundle.extractfile(member)
        assert extracted is not None
        assert extracted.read() == b"standalone-binary"
        support_member = archive_bundle.extractfile(
            "runtime/_internal/support.dat"
        )
        assert support_member is not None
        assert support_member.read() == b"support"


@pytest.mark.parametrize(
    ("version", "valid"),
    [
        ("1.2.3-rc.1+build.5", True),
        ("01.2.3", False),
        ("1.2.3-rc..1", False),
    ],
)
def test_release_packager_enforces_semver_2(
    tmp_path: Path, version: str, valid: bool
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    binary = bundle / "harness"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)

    completed = subprocess.run(
        (
            sys.executable,
            str(PACKAGER),
            "--bundle",
            str(bundle),
            "--version",
            version,
            "--target",
            "linux-x86_64",
            "--output",
            str(tmp_path / "release"),
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    assert (completed.returncode == 0) is valid


def test_release_pipeline_covers_supported_targets_and_attests_checksums() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    for target in (
        "linux-arm64",
        "linux-x86_64",
        "macos-arm64",
        "macos-x86_64",
    ):
        assert target in workflow
    assert "actions/attest@v4" in workflow
    assert "subject-checksums: release/SHA256SUMS" in workflow
    assert "scripts/install.sh" in workflow
    assert "--onedir" in workflow
    assert "--onefile" not in workflow
    assert "quality-gate:" in workflow
    assert "uv run pytest" in workflow
    assert "uv run ruff check ." in workflow
    assert "uv run mypy src tests" in workflow
    assert "needs: [quality-gate, build-standalone]" in workflow
    assert "HARNESS_RUN_OS_SANDBOX_E2E: \"1\"" in workflow
    assert "Standalone lifecycle gate" in workflow
    assert "HARNESS_RELEASE_BASE" in workflow
    assert "self update" in workflow
    assert "self uninstall" in workflow
    assert 'python-version: ["3.12", "3.13"]' in workflow


def test_readme_documents_download_verify_then_execute_installation() -> None:
    readme = (REPOSITORY / "README.md").read_text(encoding="utf-8")

    assert "-o install.sh" in readme
    assert "SHA256SUMS" in readme
    assert "shasum -a 256" in readme or "sha256sum" in readme
    assert "sh install.sh" in readme
