#!/usr/bin/env python3
"""
VM Garbage Collection Script - Two-Phase Lifecycle

Implements automatic VM lifecycle management with suspend and destroy phases:

Phase 1 - Suspend: VMs past expiry but within grace period
  - Shuts down the VM (preserves disk data)
  - Updates database status to "suspended"

Phase 2 - Destroy: Suspended VMs past grace period
  - Destroys VM via Terraform or qm destroy
  - Removes from database

Usage:
    # Dry run both phases
    blockhost-vm-gc

    # Execute both phases
    blockhost-vm-gc --execute

    # Only suspend expired VMs (no destroy)
    blockhost-vm-gc --execute --suspend-only

    # Only destroy past-grace VMs
    blockhost-vm-gc --execute --destroy-only

    # Use mock database for testing
    blockhost-vm-gc --mock --execute

Designed to run as a systemd timer (daily at 2 AM).
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from blockhost.config import get_terraform_dir, load_db_config
from blockhost.root_agent import (
    RootAgentError,
    ip6_route_del,
    qm_destroy,
    qm_shutdown,
    qm_stop,
)
from blockhost.vm_db import get_database


def sanitize_resource_name(name: str) -> str:
    """Convert VM name to valid Terraform resource name."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def get_tf_file_path(name: str) -> Path:
    """Get the path to a VM's Terraform config file."""
    return get_terraform_dir() / f"{name}.tf.json"


def run_qm_command(vmid: int, command: str, timeout: int = 60) -> tuple[bool, str]:
    """Run a qm command on a VM via root agent."""
    try:
        if command == "shutdown":
            result = qm_shutdown(vmid)
        elif command == "stop":
            result = qm_stop(vmid)
        elif command == "destroy":
            result = qm_destroy(vmid)
        else:
            return False, f"Unknown command: {command}"
        return True, result.get("output", "")
    except RootAgentError as e:
        return False, str(e)


def shutdown_vm(vmid: int, graceful_timeout: int = 60) -> tuple[bool, str]:
    """
    Shut down a VM gracefully, falling back to force stop.

    Returns (success, message).
    """
    # Try graceful shutdown first
    success, output = run_qm_command(vmid, "shutdown", timeout=graceful_timeout)
    if success:
        return True, "Graceful shutdown successful"

    # Fall back to force stop
    success, output = run_qm_command(vmid, "stop", timeout=30)
    if success:
        return True, "Force stop successful (graceful shutdown failed)"

    return False, f"Failed to stop VM: {output}"


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

    print(f"    Running: {' '.join(cmd)}")

    if dry_run:
        # For dry run, just run terraform plan -destroy
        cmd = ["terraform", "plan", "-destroy", "-target", target]
        result = subprocess.run(cmd, cwd=tf_dir, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"    [DRY RUN] Would destroy {target}")
            return True
        else:
            print(f"    Error planning destroy: {result.stderr}")
            return False
    else:
        result = subprocess.run(cmd, cwd=tf_dir)
        return result.returncode == 0


def remove_tf_file(vm_name: str, dry_run: bool = True) -> bool:
    """Remove the VM's Terraform config file."""
    tf_file = get_tf_file_path(vm_name)

    if not tf_file.exists():
        print(f"    Note: Terraform file not found: {tf_file}")
        return True

    if dry_run:
        print(f"    [DRY RUN] Would remove {tf_file}")
        return True
    else:
        try:
            tf_file.unlink()
            # Also remove cloud-init file if it exists
            cloud_init_file = get_terraform_dir() / f"{vm_name}-cloud-config.yaml"
            if cloud_init_file.exists():
                cloud_init_file.unlink()
            print(f"    Removed {tf_file}")
            return True
        except Exception as e:
            print(f"    Error removing {tf_file}: {e}")
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


def phase_suspend(db, grace_days: int, execute: bool, verbose: bool) -> tuple[int, int]:
    """
    Phase 1: Suspend VMs that are expired but within grace period.

    Returns (success_count, error_count).
    """
    print("\n" + "=" * 60)
    print("PHASE 1: SUSPEND EXPIRED VMs")
    print("=" * 60)

    # Get VMs that are expired but not yet past grace period
    # These are active VMs where: expires_at < now < expires_at + grace_days
    vms_to_suspend = db.get_vms_to_suspend()

    if not vms_to_suspend:
        print("No VMs to suspend.")
        return 0, 0

    print(f"Found {len(vms_to_suspend)} VM(s) to suspend:\n")

    success_count = 0
    error_count = 0

    for vm in vms_to_suspend:
        vm_name = vm["vm_name"]
        vmid = vm["vmid"]
        owner = vm.get("owner", "unknown")
        expiry_info = format_timedelta(vm["expires_at"])

        print(f"  VM: {vm_name} (VMID {vmid})")
        print(f"    Owner: {owner}")
        print(f"    Status: {expiry_info}")

        if verbose:
            print(f"    IP: {vm.get('ip_address', 'N/A')}")
            print(f"    Purpose: {vm.get('purpose', 'N/A')}")

        if execute:
            # Shut down the VM
            print(f"    Shutting down VM...")
            success, message = shutdown_vm(vmid)

            if success:
                print(f"    {message}")
                try:
                    db.mark_suspended(vm_name)
                    print(f"    Marked as suspended")
                    success_count += 1
                except Exception as e:
                    print(f"    Error updating database: {e}")
                    error_count += 1
            else:
                print(f"    Failed: {message}")
                error_count += 1
        else:
            print(f"    [DRY RUN] Would suspend")
            success_count += 1

        print()

    return success_count, error_count


def phase_destroy(db, grace_days: int, execute: bool, verbose: bool) -> tuple[int, int]:
    """
    Phase 2: Destroy VMs that are past expiry + grace period.

    Returns (success_count, error_count).
    """
    print("\n" + "=" * 60)
    print("PHASE 2: DESTROY PAST-GRACE VMs")
    print("=" * 60)

    # Get suspended VMs that are past the grace period
    vms_to_destroy = db.get_vms_to_destroy(grace_days=grace_days)

    if not vms_to_destroy:
        print("No VMs to destroy.")
        return 0, 0

    print(f"Found {len(vms_to_destroy)} VM(s) to destroy:\n")

    success_count = 0
    error_count = 0

    for vm in vms_to_destroy:
        vm_name = vm["vm_name"]
        vmid = vm["vmid"]
        owner = vm.get("owner", "unknown")
        status = vm.get("status", "unknown")
        expiry_info = format_timedelta(vm["expires_at"])

        print(f"  VM: {vm_name} (VMID {vmid})")
        print(f"    Owner: {owner}")
        print(f"    Status: {status}, {expiry_info}")

        if verbose:
            print(f"    IP: {vm.get('ip_address', 'N/A')}")
            print(f"    Suspended at: {vm.get('suspended_at', 'N/A')}")

        # Check if terraform file exists
        tf_file = get_tf_file_path(vm_name)
        if not tf_file.exists():
            print(f"    Note: No Terraform file found, marking as destroyed...")
            if execute:
                try:
                    db.mark_destroyed(vm_name)
                    print(f"    Marked as destroyed")
                    success_count += 1
                except Exception as e:
                    print(f"    Error: {e}")
                    error_count += 1
            else:
                print(f"    [DRY RUN] Would mark as destroyed")
                success_count += 1
            print()
            continue

        # Destroy via Terraform
        print(f"    Destroying via Terraform...")
        if run_terraform_destroy(vm_name, dry_run=not execute):
            if remove_tf_file(vm_name, dry_run=not execute):
                if execute:
                    try:
                        db.mark_destroyed(vm_name)
                        print(f"    Successfully destroyed")
                        # Remove IPv6 host route if VM had an IPv6 address
                        if vm.get("ipv6_address"):
                            try:
                                ip6_route_del(f"{vm['ipv6_address']}/128", "vmbr0")
                                print(f"    Removed IPv6 host route: {vm['ipv6_address']}/128")
                            except RootAgentError:
                                pass  # Silently ignore if route doesn't exist
                        success_count += 1
                    except Exception as e:
                        print(f"    Error updating database: {e}")
                        error_count += 1
                else:
                    print(f"    [DRY RUN] Would destroy")
                    if vm.get("ipv6_address"):
                        print(f"    [DRY RUN] Would remove IPv6 host route: {vm['ipv6_address']}/128")
                    success_count += 1
            else:
                error_count += 1
        else:
            print(f"    Failed to destroy via Terraform")
            error_count += 1

        print()

    return success_count, error_count


def main():
    parser = argparse.ArgumentParser(
        description="Two-phase VM garbage collection: suspend then destroy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Dry run both phases
    blockhost-vm-gc

    # Execute both phases
    blockhost-vm-gc --execute

    # Only suspend expired VMs
    blockhost-vm-gc --execute --suspend-only

    # Only destroy past-grace VMs
    blockhost-vm-gc --execute --destroy-only

    # Test with mock database
    blockhost-vm-gc --mock --execute
        """
    )

    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform actions (default is dry run)"
    )
    parser.add_argument(
        "--suspend-only",
        action="store_true",
        help="Only run Phase 1 (suspend expired VMs)"
    )
    parser.add_argument(
        "--destroy-only",
        action="store_true",
        help="Only run Phase 2 (destroy past-grace VMs)"
    )
    parser.add_argument(
        "--grace-days",
        type=int,
        default=None,
        help="Override grace period from config (days after expiry before destroy)"
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

    # Load config for grace days
    db_config = load_db_config()
    grace_days = args.grace_days if args.grace_days is not None else db_config.get("gc_grace_days", 7)

    # Validate mutually exclusive options
    if args.suspend_only and args.destroy_only:
        print("Error: --suspend-only and --destroy-only are mutually exclusive")
        return 1

    # Timestamp for logging
    print(f"\n{'='*60}")
    print(f"VM Garbage Collection - {datetime.now().isoformat()}")
    print(f"{'='*60}")
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY RUN'}")
    print(f"Grace period: {grace_days} days")
    if args.suspend_only:
        print("Running: Phase 1 only (suspend)")
    elif args.destroy_only:
        print("Running: Phase 2 only (destroy)")
    else:
        print("Running: Both phases")

    # Initialize database
    db = get_database(use_mock=args.mock)

    # Track totals
    total_success = 0
    total_errors = 0

    # Phase 1: Suspend
    if not args.destroy_only:
        success, errors = phase_suspend(db, grace_days, args.execute, args.verbose)
        total_success += success
        total_errors += errors

    # Phase 2: Destroy
    if not args.suspend_only:
        success, errors = phase_destroy(db, grace_days, args.execute, args.verbose)
        total_success += success
        total_errors += errors

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"  {'Processed' if args.execute else 'Would process'}: {total_success}")
    print(f"  Errors: {total_errors}")
    print(f"{'='*60}\n")

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
