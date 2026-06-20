from __future__ import annotations

import subprocess
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zyenlang.transpiler import build_file, compile_c

EXAMPLES = [
    "hello.zy",
    "add.zy",
    "for_loop.zy",
    "if_stop.zy",
    "if_paren.zy",
    "struct_point.zy",
    "ptr_object.zy",
    "motor_control.zy",
    "import_main.zy",
    "std_basic.zy",
    "std_namespace_conflict.zy",
    "std_alias.zy",
    "std_heavy.zy",
    "std_geometry.zy",
    "std_string.zy",
    "std_buffer.zy",
    "ptr_index.zy",
    "owned_ptr.zy",
    "pass_basic.zy",
    "break_basic.zy",
    "cmd_basic.zy",
    "cmd_require.zy",
    "ide_demo.zy",
    "struct_method.zy",
    "struct_this.zy",
    "list_basic.zy",
    "list_ptr.zy",
    "no_any_user_facing.zy",
    "local_import_alias.zy",
    "mem_basic.zy",
    "mem_lazy.zy",
    "fstring_basic.zy",
    "fstring_arg.zy",
    "const_str.zy",
    "string_concat.zy",
        "fs_term_basic.zy",
    "tk_scene_build.zy",
    "perf_monitor.zy",
    "bug1_f_string_scanner.zy",
    "bug2_concat_newline.zy",
    "bug3_cast_list_get_concat.zy",
    "bug4_nested_method_args.zy",
    "bug6_block_scope_for.zy",
    "bug7_list_param_mutate.zy",
    "bug8_list_struct_field.zy",
    "bug9_multiline_list.zy",
    "string_new_funcs.zy",
    "else_if.zy",
    "compound_assignment.zy",
    "string_more_funcs.zy",
    "fs_path_helpers.zy",
    "multiline_syntax.zy",
    "robot_control_demo.zy",
    "default_args.zy",
    "function_declaration.zy",
]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for name in EXAMPLES:
            path = ROOT / "examples" / name
            c_path = tmp_path / f"{path.stem}.c"
            exe_path = tmp_path / path.stem
            print(f"[build/run] {name}", flush=True)
            build_file(path, c_path)
            compile_c(c_path, exe_path)
            result = subprocess.run([str(exe_path)], check=False, timeout=20)
            if result.returncode != 0:
                print(f"FAILED: {name}")
                return result.returncode
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# v0.1.37 string concat smoke test is run manually by package generation.
