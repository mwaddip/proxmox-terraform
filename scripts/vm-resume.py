#!/usr/bin/env python3
"""
VM Resume Script

Resumes a suspended VM, restoring it to active status.

This is called by blockhost-engine when a SubscriptionExtended event
comes in for an expired (but not yet destroyed) subscription.

Usage:
    blockhost-vm-resume <vm-name>
    blockhost-vm-resume <vm-name> --extend-days 30

The script:
1. Looks up VM in database, verifies status is "suspended"
2. Starts the VM via qm start <vmid>
3. Updates database: sets status back to "active"
4. Optionally extends the expiry date
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

from blockhost.config import load_db_config
from blockhost.root_agent import RootAgentError, qm_start
from blockhost.vm_db import get_database


def start_vm(vmid: int, timeout: int = 60) -> tuple[bool, str]:
    """Start a VM via root agent."""
    try:
        qm_start(vmid)
        return True, "VM started successfully"
    except RootAgentError as e:
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(
        description="Resume a suspended VM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Resume a suspended VM
    blockhost-vm-resume myvm

    # Resume and extend expiry by 30 days
    blockhost-vm-resume myvm --extend-days 30

    # Test with mock database
    blockhost-vm-resume myvm --mock
        """
    )

    parser.add_argument(
        "vm_name",
        help="Name of the VM to resume"
    )
    parser.add_argument(
        "--extend-days",
        type=int,
        default=None,
        help="Extend expiry by this many days (default: use config default_expiry_days)"
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock database for testing"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )

    args = parser.parse_args()

    # Initialize database
    db = get_database(use_mock=args.mock)

    # Look up the VM
    vm = db.get_vm(args.vm_name)

    if not vm:
        print(f"Error: VM '{args.vm_name}' not found")
        return 1

    # Verify status is suspended
    status = vm.get("status", "unknown")
    if status != "suspended":
        print(f"Error: VM '{args.vm_name}' is not suspended (status: {status})")
        if status == "active":
            print("  The VM is already active.")
        elif status == "destroyed":
            print("  The VM has been destroyed and cannot be resumed.")
        return 1

    vmid = vm["vmid"]
    print(f"Resuming VM: {args.vm_name} (VMID {vmid})")
    print(f"  Current status: {status}")
    print(f"  Owner: {vm.get('owner', 'unknown')}")
    print(f"  Suspended at: {vm.get('suspended_at', 'N/A')}")

    # Determine new expiry
    db_config = load_db_config()
    extend_days = args.extend_days if args.extend_days is not None else db_config.get("default_expiry_days", 30)
    new_expiry = datetime.now(timezone.utc) + timedelta(days=extend_days)

    print(f"  New expiry: {new_expiry.isoformat()} (+{extend_days} days)")

    if args.dry_run:
        print("\n[DRY RUN] Would:")
        print(f"  - Start VM {vmid}")
        print(f"  - Set status to 'active'")
        print(f"  - Set expiry to {new_expiry.isoformat()}")
        return 0

    # Start the VM
    print("\nStarting VM...")
    success, message = start_vm(vmid)

    if not success:
        print(f"Error: {message}")
        return 1

    print(f"  {message}")

    # Update database
    try:
        db.mark_active(args.vm_name, new_expiry=new_expiry)
        print(f"  Updated database: status='active', expiry extended")
    except Exception as e:
        print(f"Error updating database: {e}")
        print("Warning: VM was started but database may be inconsistent")
        return 1

    print(f"\nVM '{args.vm_name}' resumed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
