import pytest


@pytest.fixture
def _quiet_output(monkeypatch):
    def noop(*_args, **_kwargs):
        return None

    modules = (
        "vod_recovery.common",
        "vod_recovery.downloads",
        "vod_recovery.playlist",
        "vod_recovery.recovery",
        "vod_recovery.sources",
    )
    names = (
        "print_blank",
        "print_error",
        "print_info",
        "print_progress",
        "print_success",
        "print_text",
        "print_warning",
    )
    for module_name in modules:
        module = pytest.importorskip(module_name)
        for name in names:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, noop)
    return noop
