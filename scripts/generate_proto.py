#!/usr/bin/env python3
"""
Protocol Buffers code generation script.
Generates Python code from .proto files for both Master and Worker.

Usage:
    python scripts/generate_proto.py
    # or
    uv run python scripts/generate_proto.py
"""

import subprocess
import sys
from pathlib import Path


def main():
    # Get project root directory
    project_root = Path(__file__).parent.parent
    proto_dir = project_root / "proto"
    
    # Output directories
    master_output = project_root / "src" / "grpc_generated"
    worker_output = project_root / "src" / "tasks" / "antcode_worker" / "grpc_generated"
    
    # Create output directories
    master_output.mkdir(parents=True, exist_ok=True)
    worker_output.mkdir(parents=True, exist_ok=True)
    
    # Proto files to compile
    proto_files = list(proto_dir.glob("*.proto"))
    
    if not proto_files:
        print("No .proto files found in proto/ directory")
        sys.exit(1)
    
    print(f"Found {len(proto_files)} proto files: {[f.name for f in proto_files]}")
    
    # Generate code for Master
    print(f"\nGenerating Python code for Master -> {master_output}")
    generate_for_target(proto_dir, proto_files, master_output)
    
    # Generate code for Worker
    print(f"\nGenerating Python code for Worker -> {worker_output}")
    generate_for_target(proto_dir, proto_files, worker_output)
    
    print("\nCode generation completed successfully!")


def generate_for_target(proto_dir: Path, proto_files: list, output_dir: Path):
    """Generate Python code from proto files to target directory."""
    proto_file_names = [str(f) for f in proto_files]
    
    cmd = [
        sys.executable, "-m", "grpc_tools.protoc",
        f"--proto_path={proto_dir}",
        f"--python_out={output_dir}",
        f"--grpc_python_out={output_dir}",
        f"--pyi_out={output_dir}",
    ] + proto_file_names
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"Error generating code: {result.stderr}")
        sys.exit(1)
    
    # Create __init__.py
    init_file = output_dir / "__init__.py"
    init_content = '''"""
Auto-generated gRPC code from Protocol Buffers.
Do not edit manually - regenerate using: python scripts/generate_proto.py
"""

from .common_pb2 import Timestamp, Metrics, OSInfo
from .node_service_pb2 import (
    NodeMessage,
    MasterMessage,
    Heartbeat,
    LogBatch,
    LogEntry,
    TaskStatus,
    TaskDispatch,
    TaskAck,
    TaskCancel,
    CancelAck,
    RegisterRequest,
    RegisterResponse,
    ConfigUpdate,
    Ping,
)
from .node_service_pb2_grpc import (
    NodeServiceServicer,
    NodeServiceStub,
    add_NodeServiceServicer_to_server,
)

__all__ = [
    # Common types
    "Timestamp",
    "Metrics",
    "OSInfo",
    # Node messages
    "NodeMessage",
    "MasterMessage",
    "Heartbeat",
    "LogBatch",
    "LogEntry",
    "TaskStatus",
    "TaskDispatch",
    "TaskAck",
    "TaskCancel",
    "CancelAck",
    "RegisterRequest",
    "RegisterResponse",
    "ConfigUpdate",
    "Ping",
    # gRPC service
    "NodeServiceServicer",
    "NodeServiceStub",
    "add_NodeServiceServicer_to_server",
]
'''
    init_file.write_text(init_content)
    
    # Fix imports in generated files (grpc_tools generates relative imports incorrectly)
    fix_imports(output_dir)
    
    print(f"  Generated files in {output_dir}")


def fix_imports(output_dir: Path):
    """Fix import statements in generated files to use relative imports."""
    for py_file in output_dir.glob("*_pb2*.py"):
        content = py_file.read_text()
        modified = False
        
        # Fix import common_pb2 to relative import
        if "import common_pb2 as" in content:
            content = content.replace(
                "import common_pb2 as",
                "from . import common_pb2 as"
            )
            modified = True
        
        # Fix import node_service_pb2 to relative import
        if "import node_service_pb2 as" in content:
            content = content.replace(
                "import node_service_pb2 as",
                "from . import node_service_pb2 as"
            )
            modified = True
        
        if modified:
            py_file.write_text(content)


if __name__ == "__main__":
    main()
