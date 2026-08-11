"""Standalone test for Deploy's partial-failure handling — no full
pipeline run needed, no Gemini calls at all.

One file is complete and will write successfully (one real GitHub call).
The second file is deliberately missing 'updated_content' — this triggers
a real exception INSIDE _run_deploy's own try/except, before it even
reaches the network, so it costs nothing extra and still proves the
failure-handling path works."""

import asyncio
import json
from graph import _run_deploy

codegen_output = [
    {"path": "deploy_test.txt", "status": "ok", "is_new_file": True,
     "updated_content": "Testing partial-failure handling."},
    {"path": "broken_test.txt", "status": "ok", "is_new_file": True},
    # deliberately missing "updated_content" — simulates a malformed
    # codegen result reaching Deploy, without a second real network call
]

result = asyncio.run(_run_deploy(codegen_output))
print(json.dumps(result, indent=2))