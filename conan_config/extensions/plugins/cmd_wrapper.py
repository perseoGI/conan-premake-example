"""
Command wrapper plugin to run test_package binaries in emulated environments.

This plugin enables running cross-compiled binaries in emulators/simulators:
- Android: Uses adb to push and execute binaries on an Android emulator/device
- iOS: Uses xcrun simctl to run binaries on iOS Simulator (converts ./binary to ./binary.app/binary)
- Emscripten: Uses node to execute WebAssembly binaries

To use this plugin, compile with:
    -c tools.build.cross_building:can_run=True

Example:
    conan create . -pr:h android -c tools.build.cross_building:can_run=True
    conan create . -pr:h ios -c tools.build.cross_building:can_run=True

Starting emulators/simulators:

  iOS Simulator:
    xcrun simctl list                     # List available simulators
    xcrun simctl boot "<simulator-name>"  # Boot a simulator (e.g. "iPhone 15")

  Android Emulator:
    emulator -list-avds                   # List available AVDs
    emulator -avd <avd-name>              # Start emulator (e.g. emulator -avd Pixel_8_API_35)
"""

import shlex
from pathlib import Path

REMOTE_BASE = "/data/local/tmp"
REMOTE_LIBS = f"{REMOTE_BASE}/libs"


def _get_shared_libs(conanfile):
    """Get all .so files from dependencies when building shared."""
    libs = []
    for dep in conanfile.dependencies.host.values():
        if dep.package_type == "shared-library":
            components = dep.cpp_info.aggregated_components()
            for lib in components.libs:
                for libdir in components.libdirs:
                    libdir_path = Path(libdir)
                    if libdir_path.exists():
                        libs.extend(libdir_path.glob(f"*{lib}.so*"))
    return [str(lib) for lib in libs]


def _wrap_emscripten(cmd):
    """Wrap command to run with node for Emscripten/WebAssembly."""
    return f"node {cmd}"


def _wrap_ios(cmd):
    """Wrap command to run on iOS Simulator via xcrun simctl."""
    parts = shlex.split(cmd)
    binary, *args = parts
    binary_name = Path(binary).name
    ios_binary = f"{binary}.app/{binary_name}"
    args_str = " ".join(shlex.quote(a) for a in args)
    return f"xcrun simctl spawn booted {ios_binary} {args_str}"


def _wrap_android(cmd, conanfile):
    """Wrap command to run on Android emulator/device via adb."""
    parts = shlex.split(cmd)
    binary, *args = parts
    binary_path = Path(binary)
    real_binary = str(binary_path.resolve()) if binary_path.is_symlink() else binary
    binary_name = binary_path.name
    remote_path = f"{REMOTE_BASE}/{binary_name}"
    args_str = " ".join(shlex.quote(a) for a in args)

    push_cmds = [f"adb push {shlex.quote(real_binary)} {shlex.quote(remote_path)}"]
    ld_library_path = ""

    shared_libs = _get_shared_libs(conanfile)
    if shared_libs:
        push_cmds.append(f"adb shell mkdir -p {REMOTE_LIBS}")
        for lib in shared_libs:
            lib_name = Path(lib).name
            push_cmds.append(f"adb push {shlex.quote(lib)} {REMOTE_LIBS}/{lib_name}")
        ld_library_path = f"LD_LIBRARY_PATH={REMOTE_LIBS} "

    push_block = " >/dev/null 2>&1\n        ".join(push_cmds) + " >/dev/null 2>&1"

    return f"""bash -c '
        {push_block}
        adb shell chmod +x {shlex.quote(remote_path)}
        adb shell {ld_library_path}{shlex.quote(remote_path)} {args_str}
        code=$?
        adb shell rm -rf {REMOTE_BASE}/*
        exit $code
    '"""


def cmd_wrapper(cmd, conanfile, **kwargs):
    if not cmd.startswith(("/", "./")):
        return cmd

    os_name = conanfile.settings.get_safe("os")

    wrappers = {
        "Emscripten": lambda: _wrap_emscripten(cmd),
        "iOS": lambda: _wrap_ios(cmd),
        "Android": lambda: _wrap_android(cmd, conanfile),
    }

    wrapper = wrappers.get(os_name)
    return wrapper() if wrapper else cmd
