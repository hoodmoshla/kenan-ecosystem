#!/usr/bin/env python3
"""Apply/reverse the tiny Kenan test-only overlay on the pinned SDK template."""
from __future__ import annotations

import argparse
import os
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SDK = ROOT / "polkadot" / "sdk"
EXPECTED_SDK_REV = "cd87d0b95e54c8ffc374334386f3f2b2c7a61f54"

PATCHES = [
    (
        "templates/solochain/runtime/src/lib.rs",
        '\tspec_name: alloc::borrow::Cow::Borrowed("solochain-template-runtime"),\n'
        '\timpl_name: alloc::borrow::Cow::Borrowed("solochain-template-runtime"),',
        '\tspec_name: alloc::borrow::Cow::Borrowed("kenan-test-runtime"),\n'
        '\timpl_name: alloc::borrow::Cow::Borrowed("kenan-test-runtime"),',
    ),
    (
        "templates/solochain/node/src/chain_spec.rs",
        "use sc_service::ChainType;",
        "use sc_service::{ChainType, Properties};",
    ),
    (
        "templates/solochain/node/src/chain_spec.rs",
        'pub fn local_chain_spec() -> Result<ChainSpec, String> {',
        '''/// Kenan-branded local test chain. All token values and authority presets remain the upstream
/// Solochain development defaults; this is not a tokenomics or production genesis decision.
pub fn kenan_development_chain_spec() -> Result<ChainSpec, String> {
\tlet mut properties = Properties::new();
\tproperties.insert("tokenSymbol".to_string(), "UNIT".into());
\tproperties.insert("tokenDecimals".to_string(), 12.into());
\tOk(ChainSpec::builder(
\t\tWASM_BINARY.ok_or_else(|| "Development wasm not available".to_string())?,
\t\tNone,
\t)
\t.with_name("Kenan Development (TEST ONLY)")
\t.with_id("kenan-dev-test-only")
\t.with_chain_type(ChainType::Development)
\t.with_properties(properties)
\t.with_genesis_config_preset_name(sp_genesis_builder::DEV_RUNTIME_PRESET)
\t.build())
}

pub fn local_chain_spec() -> Result<ChainSpec, String> {''',
    ),
    (
        "templates/solochain/node/src/command.rs",
        '\t\t\t"dev" => Box::new(chain_spec::development_chain_spec()?),',
        '\t\t\t"dev" => Box::new(chain_spec::development_chain_spec()?),\n'
        '\t\t\t"kenan-dev" => Box::new(chain_spec::kenan_development_chain_spec()?),',
    ),
]


def sdk_revision() -> str:
    return subprocess.check_output(
        ["git", "-C", str(SDK), "rev-parse", "HEAD"], text=True
    ).strip()


def write_atomically(path: Path, content: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("apply", "revert"))
    args = parser.parse_args()

    revision = sdk_revision()
    if revision != EXPECTED_SDK_REV:
        raise SystemExit(
            f"Expected Polkadot SDK {EXPECTED_SDK_REV}; found {revision}. Refusing to modify it."
        )

    ordered = PATCHES if args.operation == "apply" else list(reversed(PATCHES))
    staged: dict[Path, str] = {}
    originals: dict[Path, str] = {}
    for relative_path, original, replacement in ordered:
        path = SDK / relative_path
        current = staged.get(path)
        if current is None:
            current = path.read_text(encoding="utf-8")
            originals[path] = current
        source, target = (original, replacement) if args.operation == "apply" else (replacement, original)
        count = current.count(source)
        if count != 1:
            raise SystemExit(
                f"Refusing to {args.operation}: expected exactly one source block in {relative_path}, found {count}."
            )
        staged[path] = current.replace(source, target, 1)

    written: list[Path] = []
    try:
        for path, content in staged.items():
            write_atomically(path, content)
            written.append(path)
    except Exception:
        for path in reversed(written):
            write_atomically(path, originals[path])
        raise

    action = "Applied" if args.operation == "apply" else "Reversed"
    print(f"{action} Kenan test-only overlay on Polkadot SDK {revision[:12]}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
