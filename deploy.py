#!/usr/bin/env python3
"""Executive Threat Advisory Agent - Deployment & Verification Script.

This script validates package integrity, verifies serialization and import resolution,
and orchestrates deployment to Google Cloud Vertex AI Reasoning Engine / Agent Engine.
"""

import argparse
import io
import json
import logging
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Optional, List
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="[%(levelname)s]: %(message)s")
logger = logging.getLogger("deploy")

WORKSPACE_ROOT = Path(__file__).resolve().parent
PACKAGE_DIR = WORKSPACE_ROOT / "exec_briefing_agent"

# Load environment variables prioritizing root .env
if (WORKSPACE_ROOT / ".env").is_file():
    load_dotenv(dotenv_path=WORKSPACE_ROOT / ".env", override=True)
if (PACKAGE_DIR / ".env").is_file():
    load_dotenv(dotenv_path=PACKAGE_DIR / ".env", override=False)
load_dotenv()


def verify_local_package_structure() -> bool:
    """Verifies that all required files and submodules exist in the package directory."""
    logger.info("🔍 Step 1: Checking package file structure...")
    required_files = [
        "__init__.py",
        "agent.py",
        "tools.py",
        "utils.py",
        "agent_engine_app.py",
        "requirements.txt",
    ]
    missing = []
    for fname in required_files:
        fpath = PACKAGE_DIR / fname
        if not fpath.is_file():
            missing.append(fname)
        else:
            logger.info("  ✓ Found %s (%d bytes)", fname, fpath.stat().st_size)

    if missing:
        logger.error("❌ Missing required package files: %s", missing)
        return False
    logger.info("✅ All core package files exist.")
    return True


def verify_imports_and_serialization() -> bool:
    """Tests local import resolution and simulates remote cloudpickle deserialization."""
    logger.info("🔍 Step 2: Testing import resolution and cloudpickle serialization/deserialization...")

    # Test 1: Direct package imports
    try:
        import exec_briefing_agent
        from exec_briefing_agent import agent, tools, utils, root_agent, app
        from exec_briefing_agent.tools import fetch_url_content
        logger.info("  ✓ Successfully imported exec_briefing_agent package and submodules.")
    except Exception as e:
        logger.error("❌ Failed to import exec_briefing_agent: %s", e)
        return False

    # Test 2: Check function module binding
    logger.info("  ✓ fetch_url_content.__module__ = %s", fetch_url_content.__module__)

    # Test 3: Serialize root_agent / app
    try:
        import cloudpickle
        pickled_agent = cloudpickle.dumps(root_agent)
        logger.info("  ✓ Successfully cloudpickled root_agent (size: %d bytes)", len(pickled_agent))
    except Exception as e:
        logger.error("❌ Failed to pickle root_agent: %s", e)
        return False

    # Test 4: Simulate unpickling in a clean isolated Python subprocess where ONLY the parent directory is on sys.path
    test_subproc_code = f"""
import sys
# Strip current directory and ensure only workspace root is on sys.path
sys.path = [p for p in sys.path if 'exec_briefing_agent' not in p or p.endswith('exec-briefing-agent')]
sys.path.insert(0, {str(WORKSPACE_ROOT)!r})

import cloudpickle
pickled_bytes = {pickled_agent!r}
try:
    unpickled = cloudpickle.loads(pickled_bytes)
    assert unpickled.name == 'root_agent', 'Unpickled agent name mismatch'
    print("SUBPROCESS_TEST_OK")
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
"""
    proc = subprocess.run(
        [sys.executable, "-c", test_subproc_code],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or "SUBPROCESS_TEST_OK" not in proc.stdout:
        logger.error("❌ Subprocess unpickling test failed:\nSTDOUT:\n%s\nSTDERR:\n%s", proc.stdout, proc.stderr)
        return False

    logger.info("  ✓ Subprocess isolated unpickling test passed without ModuleNotFoundError.")
    return True


def verify_extra_packages_bundling() -> bool:
    """Verifies that tarball bundling for ReasoningEngine extra_packages includes tools.py and utils.py."""
    logger.info("🔍 Step 3: Verifying tarball packaging for extra_packages...")
    tar_fileobj = io.BytesIO()
    with tarfile.open(fileobj=tar_fileobj, mode="w:gz") as tar:
        tar.add(str(PACKAGE_DIR), arcname="exec_briefing_agent")
    tar_fileobj.seek(0)

    # Inspect tar contents
    with tarfile.open(fileobj=tar_fileobj, mode="r:gz") as tar:
        names = tar.getnames()
        logger.info("  Bundled tarball contents:")
        for name in sorted(names):
            logger.info("    - %s", name)

        expected = [
            "exec_briefing_agent/tools.py",
            "exec_briefing_agent/utils.py",
            "exec_briefing_agent/agent.py",
            "exec_briefing_agent/__init__.py",
            "exec_briefing_agent/requirements.txt",
        ]
        missing_in_tar = [item for item in expected if item not in names]
        if missing_in_tar:
            logger.error("❌ Missing required files in extra_packages tarball: %s", missing_in_tar)
            return False

    logger.info("✅ All necessary modules (tools.py, utils.py, etc.) are correctly bundled in extra_packages.")
    return True


def run_unit_tests() -> bool:
    """Runs the repository test suite."""
    logger.info("🧪 Step 4: Running unit tests...")
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=str(WORKSPACE_ROOT),
    )
    return proc.returncode == 0


def deploy_adk(
    project_id: str,
    region: str,
    display_name: str,
    agent_engine_id: Optional[str] = None,
) -> bool:
    """Deploys to Vertex AI Agent Engine using the Google ADK CLI."""
    logger.info("🚀 Deploying via Google ADK CLI...")
    cmd = [
        "adk",
        "deploy",
        "agent_engine",
        f"--project={project_id}",
        f"--region={region}",
        f"--display_name={display_name}",
    ]
    if agent_engine_id:
        cmd.append(f"--agent_engine_id={agent_engine_id}")
    cmd.append("exec_briefing_agent")

    logger.info("Executing: %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(WORKSPACE_ROOT))
    return proc.returncode == 0


def deploy_reasoning_engine_sdk(
    project_id: str,
    region: str,
    display_name: str,
    staging_bucket: str,
) -> bool:
    """Deploys using vertexai.preview.reasoning_engines.ReasoningEngine.create with explicit extra_packages."""
    logger.info("🚀 Deploying via Vertex AI Reasoning Engine Python SDK...")
    import vertexai
    from vertexai.preview import reasoning_engines
    from exec_briefing_agent import app

    vertexai.init(
        project=project_id,
        location=region,
        staging_bucket=staging_bucket,
    )

    requirements_file = WORKSPACE_ROOT / "requirements.txt"
    requirements = []
    if requirements_file.is_file():
        with open(requirements_file, "r") as f:
            requirements = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    # Explicitly bundle the entire exec_briefing_agent package directory
    extra_packages = [str(PACKAGE_DIR)]

    logger.info("Creating ReasoningEngine with extra_packages: %s", extra_packages)
    logger.info("Requirements: %s", requirements)

    remote_engine = reasoning_engines.ReasoningEngine.create(
        app,
        requirements=requirements,
        extra_packages=extra_packages,
        display_name=display_name,
        description="Executive Threat Advisory Agent",
    )
    logger.info("🎉 ReasoningEngine created successfully: %s", remote_engine.resource_name)
    return True


def main():
    parser = argparse.ArgumentParser(description="Executive Threat Advisory Agent - Deployment & Verification Tool")
    parser.add_argument("--check-only", action="store_true", help="Run local structure, import, serialization, and package tests only")
    parser.add_argument("--deploy-method", choices=["adk", "sdk", "script"], default="script", help="Deployment method (default: script using deploy.sh)")
    parser.add_argument("--project", default=os.getenv("PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT", ""))
    parser.add_argument("--region", default=os.getenv("REGION") or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))
    parser.add_argument("--display-name", default=os.getenv("DISPLAY_NAME", "exec_briefing_agent"))
    parser.add_argument("--agent-engine-id", default=os.getenv("AGENT_ENGINE_ID"))
    parser.add_argument("--staging-bucket", default=os.getenv("STAGING_BUCKET"))

    args = parser.parse_args()

    # Always run validation
    if not verify_local_package_structure():
        sys.exit(1)
    if not verify_imports_and_serialization():
        sys.exit(1)
    if not verify_extra_packages_bundling():
        sys.exit(1)
    if not run_unit_tests():
        sys.exit(1)

    logger.info("✅ All local validation and import checks passed successfully!")

    if args.check_only:
        logger.info("Check-only completed. Exiting.")
        return

    if args.deploy_method == "script":
        deploy_sh = WORKSPACE_ROOT / "deploy.sh"
        if deploy_sh.is_file():
            logger.info("🚀 Triggering deploy.sh script...")
            proc = subprocess.run(["bash", str(deploy_sh)], cwd=str(WORKSPACE_ROOT))
            sys.exit(proc.returncode)
        else:
            logger.error("❌ deploy.sh not found.")
            sys.exit(1)
    elif args.deploy_method == "adk":
        if not args.project:
            logger.error("❌ Project ID required for deployment.")
            sys.exit(1)
        success = deploy_adk(args.project, args.region, args.display_name, args.agent_engine_id)
        sys.exit(0 if success else 1)
    elif args.deploy_method == "sdk":
        if not args.project or not args.staging_bucket:
            logger.error("❌ Project ID and --staging-bucket (e.g. gs://my-bucket) are required for SDK deployment.")
            sys.exit(1)
        success = deploy_reasoning_engine_sdk(args.project, args.region, args.display_name, args.staging_bucket)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
