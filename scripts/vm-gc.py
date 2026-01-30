#!/usr/bin/env python3
"""
VM Garbage Collection Script

Finds and destroys VMs that have passed their expiry date plus a configurable
grace period. Designed to run as a cron job.

Usage:
    # Dry run (report only)
    python3 vm-gc.py --grace-days 3

    # Execute destruction
    python3 vm-gc.py --execute --grace-days 3

    # Use mock database for testing
    python3 vm-gc.py --mock --execute

Cron example (daily at 2 AM):
    0 2 * * * cd /home/mwaddip/proxmox-terraform && python3 scripts/vm-gc.py --execute --grace-days 3 >> logs/gc.log 2>&1
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Add scripts directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from vm_db import get_database

PROJECT_DIR = Path(__file__).parent.parent


def get_config_path(filename: str) -> Path:
    """Get config file path, checking /etc/blockhost/ first."""
    etc_path = Path("/etc/blockhost") / filename
    if etc_path.exists():
        return etc_path
    return PROJECT_DIR / "config" / filename


def get_terraform_dir() -> Path:
    """Get the Terraform working directory from config."""
    config_path = get_config_path("db.yaml")
    with open(config_path) as f:
        config = yaml.safe_load(f)
    tf_dir = config.get("terraform_dir")
    if tf_dir:
        return Path(tf_dir)
    # Fallback: look for proxmox-testserver symlink
    symlink = PROJECT_DIR / "proxmox-testserver"
    if symlink.exists():
        return symlink.resolve()
    return PROJECT_DIR


def sanitize_resource_name(name: str) -> str:
    """Convert VM name to valid Terraform resource name."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def get_tf_file_path(name: str) -> Path:
    """Get the path to a VM's Terraform config file."""
    return get_terraform_dir() / f"{name}.tf.json"


def run_terraform_destroy(vm_name: str, dry_run: bool = True) -> bool:
    """
    Run terraform destroy for a specific VM resource.

    Returns True if successful, False otherwise.
    """
    tf_dir = get_terraform_dir()
    resource_name = sanitize_resource_name(vm_name)
    target = f"proxmox_virtual_environment_vm.{resource_name}"

    cmd = ["terraform", "destroy", "-target", target]
    if not dry_run:
        cmd.append("-auto-approve")

    print(f"  Running: {' '.join(cmd)} (in {tf_dir})")

    if dry_run:
        # For dry run, just run terraform plan -destroy
        cmd = ["terraform", "plan", "-destroy", "-target", target]
        result = subprocess.run(cmd, cwd=tf_dir, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  [DRY RUN] Would destroy {target}")
            return True
        else:
            print(f"  Error planning destroy: {result.stderr}")
            return False
    else:
        result = subprocess.run(cmd, cwd=tf_dir)
        return result.returncode == 0


def remove_tf_file(vm_name: str, dry_run: bool = True) -> bool:
    """Remove the VM's Terraform config file."""
    tf_file = get_tf_file_path(vm_name)

    if not tf_file.exists():
        print(f"  Warning: Terraform file not found: {tf_file}")
        return True

    if dry_run:
        print(f"  [DRY RUN] Would remove {tf_file}")
        return True
    else:
        try:
            tf_file.unlink()
            print(f"  Removed {tf_file}")
            return True
        except Exception as e:
            print(f"  Error removing {tf_file}: {e}")
            return False


def format_timedelta(expiry_str: str) -> str:
    """Format how long ago a VM expired."""
    expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    delta = now - expiry

    days = delta.days
    if days < 0:
        return f"expires in {-days} days"
    elif days == 0:
        hours = delta.seconds // 3600
        return f"expired {hours} hours ago"
    elif days == 1:
        return "expired 1 day ago"
    else:
        return f"expired {days} days ago"


def main():
    parser = argparse.ArgumentParser(
        description="Garbage collect expired VMs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # List expired VMs (dry run)
    python3 vm-gc.py

    # List with 3-day grace period
    python3 vm-gc.py --grace-days 3

    # Actually destroy expired VMs
    python3 vm-gc.py --execute --grace-days 3

    # Test with mock database
    python3 vm-gc.py --mock --execute
        """
    )

    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually destroy VMs (default is dry run)"
    )
    parser.add_argument(
        "--grace-days",
        type=int,
        default=0,
        help="Grace period in days after expiry before destruction (default: 0)"
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock database for testing"
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output"
    )

    args = parser.parse_args()

    # Timestamp for logging
    print(f"\n{'='*60}")
    print(f"VM Garbage Collection - {datetime.now().isoformat()}")
    print(f"{'='*60}")
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY RUN'}")
    print(f"Grace period: {args.grace_days} days")
    print()

    # Initialize database
    db = get_database(use_mock=args.mock)

    # Get expired VMs
    expired_vms = db.get_expired_vms(grace_days=args.grace_days)

    if not expired_vms:
        print("No expired VMs found.")
        print(f"{'='*60}\n")
        return 0

    print(f"Found {len(expired_vms)} expired VM(s):\n")

    # Process each expired VM
    success_count = 0
    error_count = 0

    for vm in expired_vms:
        vm_name = vm["vm_name"]
        vmid = vm["vmid"]
        owner = vm.get("owner", "unknown")
        expiry_info = format_timedelta(vm["expires_at"])

        print(f"VM: {vm_name} (VMID {vmid})")
        print(f"  Owner: {owner}")
        print(f"  Status: {expiry_info}")

        if args.verbose:
            print(f"  IP: {vm.get('ip_address', 'N/A')}")
            print(f"  Purpose: {vm.get('purpose', 'N/A')}")
            print(f"  Created: {vm.get('created_at', 'N/A')}")

        # Check if terraform file exists
        tf_file = get_tf_file_path(vm_name)
        if not tf_file.exists():
            print(f"  Warning: No Terraform file found at {tf_file}")
            print(f"  Marking as destroyed in database only...")

            if args.execute:
                try:
                    db.mark_destroyed(vm_name)
                    print(f"  Marked {vm_name} as destroyed")
                    success_count += 1
                except Exception as e:
                    print(f"  Error: {e}")
                    error_count += 1
            else:
                print(f"  [DRY RUN] Would mark as destroyed")
                success_count += 1
            print()
            continue

        # Destroy via Terraform
        print(f"  Destroying via Terraform...")
        if run_terraform_destroy(vm_name, dry_run=not args.execute):
            # Remove .tf.json file
            if remove_tf_file(vm_name, dry_run=not args.execute):
                # Update database
                if args.execute:
                    try:
                        db.mark_destroyed(vm_name)
                        print(f"  Successfully destroyed {vm_name}")
                        success_count += 1
                    except Exception as e:
                        print(f"  Error updating database: {e}")
                        error_count += 1
                else:
                    print(f"  [DRY RUN] Would destroy {vm_name}")
                    success_count += 1
            else:
                error_count += 1
        else:
            print(f"  Failed to destroy {vm_name}")
            error_count += 1

        print()

    # Summary
    print(f"{'='*60}")
    print(f"Summary:")
    print(f"  Total expired: {len(expired_vms)}")
    print(f"  {'Would process' if not args.execute else 'Processed'}: {success_count}")
    print(f"  Errors: {error_count}")
    print(f"{'='*60}\n")

    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
