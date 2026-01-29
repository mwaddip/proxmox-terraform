#!/usr/bin/env python3
"""
VM Database Abstraction Layer

Provides a simple JSON-based database for tracking VM lifecycle,
IP allocation, and expiry management. Designed to run on the Proxmox host.
"""

import json
import os
import fcntl
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import yaml


class VMDatabaseBase(ABC):
    """Abstract base class for VM database implementations."""

    @abstractmethod
    def get_expired_vms(self, grace_days: int = 0) -> list[dict]:
        """Get all VMs past their expiry date (plus optional grace period)."""
        pass

    @abstractmethod
    def get_vm(self, name: str) -> Optional[dict]:
        """Get a VM by name."""
        pass

    @abstractmethod
    def register_vm(self, name: str, vmid: int, ip: str, owner: str,
                    expiry_days: int, purpose: str = "") -> dict:
        """Register a new VM in the database."""
        pass

    @abstractmethod
    def mark_destroyed(self, name: str) -> None:
        """Mark a VM as destroyed."""
        pass

    @abstractmethod
    def allocate_ip(self) -> Optional[str]:
        """Allocate the next available IP address."""
        pass

    @abstractmethod
    def allocate_vmid(self) -> int:
        """Allocate the next available VMID."""
        pass

    @abstractmethod
    def extend_expiry(self, name: str, days: int) -> None:
        """Extend a VM's expiry date by the specified number of days."""
        pass

    @abstractmethod
    def list_vms(self, status: Optional[str] = None) -> list[dict]:
        """List all VMs, optionally filtered by status."""
        pass

    @abstractmethod
    def reserve_nft_token_id(self, vm_name: str) -> int:
        """Reserve the next NFT token ID for a VM."""
        pass

    @abstractmethod
    def mark_nft_minted(self, token_id: int, owner_wallet: str) -> None:
        """Mark an NFT token as successfully minted."""
        pass

    @abstractmethod
    def mark_nft_failed(self, token_id: int) -> None:
        """Mark an NFT token reservation as failed."""
        pass

    @abstractmethod
    def get_nft_token(self, token_id: int) -> Optional[dict]:
        """Get NFT token info by ID."""
        pass


class VMDatabase(VMDatabaseBase):
    """JSON file-based VM database implementation."""

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the database.

        Args:
            config_path: Path to db.yaml config file. If None, uses default location.
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config" / "db.yaml"

        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        self.db_file = Path(self.config["db_file"])
        self.fields = self.config["fields"]
        self.ip_pool = self.config["ip_pool"]
        self.vmid_range = self.config["vmid_range"]

        # Ensure database directory exists
        self.db_file.parent.mkdir(parents=True, exist_ok=True)

        # Initialize empty database if it doesn't exist
        if not self.db_file.exists():
            self._write_db({
                "vms": {},
                "next_vmid": self.vmid_range["start"],
                "allocated_ips": [],
                "next_nft_token_id": 0,
                "nft_tokens": {}
            })

    def _read_db(self) -> dict:
        """Read the database file with locking."""
        with open(self.db_file, "r") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _write_db(self, data: dict) -> None:
        """Write to the database file with locking."""
        with open(self.db_file, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2, default=str)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _with_lock(self, func):
        """Execute a function with exclusive database lock."""
        db = self._read_db()
        result = func(db)
        self._write_db(db)
        return result

    def get_expired_vms(self, grace_days: int = 0) -> list[dict]:
        """Get all VMs past their expiry date (plus optional grace period)."""
        db = self._read_db()
        now = datetime.now(timezone.utc)
        expired = []

        for vm in db["vms"].values():
            if vm.get("status") != "active":
                continue

            expires_at = datetime.fromisoformat(vm["expires_at"].replace("Z", "+00:00"))
            expiry_with_grace = expires_at + timedelta(days=grace_days)

            if now > expiry_with_grace:
                expired.append(vm)

        return expired

    def get_vm(self, name: str) -> Optional[dict]:
        """Get a VM by name."""
        db = self._read_db()
        return db["vms"].get(name)

    def register_vm(self, name: str, vmid: int, ip: str, owner: str,
                    expiry_days: int, purpose: str = "") -> dict:
        """Register a new VM in the database."""
        db = self._read_db()

        if name in db["vms"]:
            raise ValueError(f"VM '{name}' already exists")

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=expiry_days)

        vm = {
            self.fields["vm_name"]: name,
            self.fields["vmid"]: vmid,
            self.fields["ip_address"]: ip,
            self.fields["expires_at"]: expires_at.isoformat(),
            self.fields["owner"]: owner,
            self.fields["status"]: "active",
            self.fields["created_at"]: now.isoformat(),
            "purpose": purpose
        }

        db["vms"][name] = vm

        # Track allocated IP
        if ip not in db["allocated_ips"]:
            db["allocated_ips"].append(ip)

        # Update next_vmid if necessary
        if vmid >= db["next_vmid"]:
            db["next_vmid"] = vmid + 1

        self._write_db(db)
        return vm

    def mark_destroyed(self, name: str) -> None:
        """Mark a VM as destroyed and release its IP."""
        db = self._read_db()

        if name not in db["vms"]:
            raise ValueError(f"VM '{name}' not found")

        vm = db["vms"][name]
        vm["status"] = "destroyed"
        vm["destroyed_at"] = datetime.now(timezone.utc).isoformat()

        # Release IP
        ip = vm.get("ip_address")
        if ip and ip in db["allocated_ips"]:
            db["allocated_ips"].remove(ip)

        self._write_db(db)

    def allocate_ip(self) -> Optional[str]:
        """Allocate the next available IP address from the pool."""
        db = self._read_db()

        network_prefix = ".".join(self.ip_pool["network"].split(".")[:3])
        start = self.ip_pool["start"]
        end = self.ip_pool["end"]

        for i in range(start, end + 1):
            ip = f"{network_prefix}.{i}"
            if ip not in db["allocated_ips"]:
                db["allocated_ips"].append(ip)
                self._write_db(db)
                return ip

        return None  # Pool exhausted

    def allocate_vmid(self) -> int:
        """Allocate the next available VMID."""
        db = self._read_db()

        vmid = db["next_vmid"]
        if vmid > self.vmid_range["end"]:
            raise ValueError("VMID range exhausted")

        db["next_vmid"] = vmid + 1
        self._write_db(db)
        return vmid

    def extend_expiry(self, name: str, days: int) -> None:
        """Extend a VM's expiry date by the specified number of days."""
        db = self._read_db()

        if name not in db["vms"]:
            raise ValueError(f"VM '{name}' not found")

        vm = db["vms"][name]
        current_expiry = datetime.fromisoformat(vm["expires_at"].replace("Z", "+00:00"))
        new_expiry = current_expiry + timedelta(days=days)
        vm["expires_at"] = new_expiry.isoformat()

        self._write_db(db)

    def list_vms(self, status: Optional[str] = None) -> list[dict]:
        """List all VMs, optionally filtered by status."""
        db = self._read_db()
        vms = list(db["vms"].values())

        if status:
            vms = [vm for vm in vms if vm.get("status") == status]

        return vms

    def release_ip(self, ip: str) -> None:
        """Release an IP address back to the pool."""
        db = self._read_db()
        if ip in db["allocated_ips"]:
            db["allocated_ips"].remove(ip)
            self._write_db(db)

    def reserve_nft_token_id(self, vm_name: str) -> int:
        """Reserve the next NFT token ID for a VM."""
        db = self._read_db()
        db.setdefault("next_nft_token_id", 0)
        db.setdefault("nft_tokens", {})

        token_id = db["next_nft_token_id"]
        db["next_nft_token_id"] = token_id + 1
        db["nft_tokens"][str(token_id)] = {
            "status": "reserved",
            "vm_name": vm_name,
            "reserved_at": datetime.now(timezone.utc).isoformat()
        }

        self._write_db(db)
        return token_id

    def mark_nft_minted(self, token_id: int, owner_wallet: str) -> None:
        """Mark an NFT token as successfully minted."""
        db = self._read_db()
        key = str(token_id)
        if key not in db.get("nft_tokens", {}):
            raise ValueError(f"NFT token {token_id} not found")

        db["nft_tokens"][key]["status"] = "minted"
        db["nft_tokens"][key]["owner_wallet"] = owner_wallet
        db["nft_tokens"][key]["minted_at"] = datetime.now(timezone.utc).isoformat()

        self._write_db(db)

    def mark_nft_failed(self, token_id: int) -> None:
        """Mark an NFT token reservation as failed."""
        db = self._read_db()
        key = str(token_id)
        if key not in db.get("nft_tokens", {}):
            raise ValueError(f"NFT token {token_id} not found")

        db["nft_tokens"][key]["status"] = "failed"
        db["nft_tokens"][key]["failed_at"] = datetime.now(timezone.utc).isoformat()

        self._write_db(db)

    def get_nft_token(self, token_id: int) -> Optional[dict]:
        """Get NFT token info by ID."""
        db = self._read_db()
        return db.get("nft_tokens", {}).get(str(token_id))


class MockVMDatabase(VMDatabaseBase):
    """Mock database for local development/testing."""

    def __init__(self, db_file: Optional[str] = None):
        """Initialize mock database."""
        if db_file is None:
            db_file = Path(__file__).parent.parent / "accounting" / "mock-db.json"
        self.db_file = Path(db_file)

        # Load config for IP pool settings
        config_path = Path(__file__).parent.parent / "config" / "db.yaml"
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        self.ip_pool = self.config["ip_pool"]
        self.vmid_range = self.config["vmid_range"]

        if not self.db_file.exists():
            self._write_db({
                "vms": {},
                "next_vmid": self.vmid_range["start"],
                "allocated_ips": [],
                "next_nft_token_id": 0,
                "nft_tokens": {}
            })

    def _read_db(self) -> dict:
        with open(self.db_file) as f:
            return json.load(f)

    def _write_db(self, data: dict) -> None:
        with open(self.db_file, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def get_expired_vms(self, grace_days: int = 0) -> list[dict]:
        db = self._read_db()
        now = datetime.now(timezone.utc)
        expired = []

        for vm in db["vms"].values():
            if vm.get("status") != "active":
                continue

            expires_at = datetime.fromisoformat(vm["expires_at"].replace("Z", "+00:00"))
            expiry_with_grace = expires_at + timedelta(days=grace_days)

            if now > expiry_with_grace:
                expired.append(vm)

        return expired

    def get_vm(self, name: str) -> Optional[dict]:
        db = self._read_db()
        return db["vms"].get(name)

    def register_vm(self, name: str, vmid: int, ip: str, owner: str,
                    expiry_days: int, purpose: str = "") -> dict:
        db = self._read_db()

        if name in db["vms"]:
            raise ValueError(f"VM '{name}' already exists")

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=expiry_days)

        vm = {
            "vm_name": name,
            "vmid": vmid,
            "ip_address": ip,
            "expires_at": expires_at.isoformat(),
            "owner": owner,
            "status": "active",
            "created_at": now.isoformat(),
            "purpose": purpose
        }

        db["vms"][name] = vm
        if ip not in db["allocated_ips"]:
            db["allocated_ips"].append(ip)
        if vmid >= db["next_vmid"]:
            db["next_vmid"] = vmid + 1

        self._write_db(db)
        return vm

    def mark_destroyed(self, name: str) -> None:
        db = self._read_db()
        if name not in db["vms"]:
            raise ValueError(f"VM '{name}' not found")

        vm = db["vms"][name]
        vm["status"] = "destroyed"
        vm["destroyed_at"] = datetime.now(timezone.utc).isoformat()

        ip = vm.get("ip_address")
        if ip and ip in db["allocated_ips"]:
            db["allocated_ips"].remove(ip)

        self._write_db(db)

    def allocate_ip(self) -> Optional[str]:
        db = self._read_db()
        network_prefix = ".".join(self.ip_pool["network"].split(".")[:3])
        start = self.ip_pool["start"]
        end = self.ip_pool["end"]

        for i in range(start, end + 1):
            ip = f"{network_prefix}.{i}"
            if ip not in db["allocated_ips"]:
                db["allocated_ips"].append(ip)
                self._write_db(db)
                return ip
        return None

    def allocate_vmid(self) -> int:
        db = self._read_db()
        vmid = db["next_vmid"]
        if vmid > self.vmid_range["end"]:
            raise ValueError("VMID range exhausted")
        db["next_vmid"] = vmid + 1
        self._write_db(db)
        return vmid

    def extend_expiry(self, name: str, days: int) -> None:
        db = self._read_db()
        if name not in db["vms"]:
            raise ValueError(f"VM '{name}' not found")

        vm = db["vms"][name]
        current_expiry = datetime.fromisoformat(vm["expires_at"].replace("Z", "+00:00"))
        new_expiry = current_expiry + timedelta(days=days)
        vm["expires_at"] = new_expiry.isoformat()
        self._write_db(db)

    def list_vms(self, status: Optional[str] = None) -> list[dict]:
        db = self._read_db()
        vms = list(db["vms"].values())
        if status:
            vms = [vm for vm in vms if vm.get("status") == status]
        return vms

    def reserve_nft_token_id(self, vm_name: str) -> int:
        db = self._read_db()
        db.setdefault("next_nft_token_id", 0)
        db.setdefault("nft_tokens", {})

        token_id = db["next_nft_token_id"]
        db["next_nft_token_id"] = token_id + 1
        db["nft_tokens"][str(token_id)] = {
            "status": "reserved",
            "vm_name": vm_name,
            "reserved_at": datetime.now(timezone.utc).isoformat()
        }

        self._write_db(db)
        return token_id

    def mark_nft_minted(self, token_id: int, owner_wallet: str) -> None:
        db = self._read_db()
        key = str(token_id)
        if key not in db.get("nft_tokens", {}):
            raise ValueError(f"NFT token {token_id} not found")

        db["nft_tokens"][key]["status"] = "minted"
        db["nft_tokens"][key]["owner_wallet"] = owner_wallet
        db["nft_tokens"][key]["minted_at"] = datetime.now(timezone.utc).isoformat()
        self._write_db(db)

    def mark_nft_failed(self, token_id: int) -> None:
        db = self._read_db()
        key = str(token_id)
        if key not in db.get("nft_tokens", {}):
            raise ValueError(f"NFT token {token_id} not found")

        db["nft_tokens"][key]["status"] = "failed"
        db["nft_tokens"][key]["failed_at"] = datetime.now(timezone.utc).isoformat()
        self._write_db(db)

    def get_nft_token(self, token_id: int) -> Optional[dict]:
        db = self._read_db()
        return db.get("nft_tokens", {}).get(str(token_id))


def get_database(use_mock: bool = False, config_path: Optional[str] = None) -> VMDatabaseBase:
    """
    Factory function to get the appropriate database implementation.

    Args:
        use_mock: If True, use mock database for local testing
        config_path: Optional path to db.yaml config

    Returns:
        VMDatabaseBase implementation
    """
    if use_mock:
        return MockVMDatabase()
    return VMDatabase(config_path)


if __name__ == "__main__":
    # Quick test with mock database
    db = MockVMDatabase()

    print("Testing mock database...")

    # Allocate resources
    vmid = db.allocate_vmid()
    ip = db.allocate_ip()
    print(f"Allocated VMID: {vmid}, IP: {ip}")

    # Register a VM
    vm = db.register_vm(
        name="test-vm",
        vmid=vmid,
        ip=ip,
        owner="test-user",
        expiry_days=30,
        purpose="testing"
    )
    print(f"Registered VM: {vm['vm_name']}")

    # List VMs
    vms = db.list_vms(status="active")
    print(f"Active VMs: {len(vms)}")

    # Get specific VM
    retrieved = db.get_vm("test-vm")
    print(f"Retrieved: {retrieved['vm_name']} expires {retrieved['expires_at']}")

    # Check expired (should be empty)
    expired = db.get_expired_vms()
    print(f"Expired VMs: {len(expired)}")

    # Test NFT token tracking
    print("\n--- NFT Token Tracking ---")
    token_id = db.reserve_nft_token_id("test-vm")
    print(f"Reserved NFT token ID: {token_id}")

    token = db.get_nft_token(token_id)
    print(f"Token status: {token['status']}")

    db.mark_nft_minted(token_id, "0x1234567890abcdef1234567890abcdef12345678")
    token = db.get_nft_token(token_id)
    print(f"After minting: {token['status']} -> {token['owner_wallet']}")

    # Reserve and fail another token
    failed_id = db.reserve_nft_token_id("failed-vm")
    db.mark_nft_failed(failed_id)
    failed_token = db.get_nft_token(failed_id)
    print(f"Failed token {failed_id}: {failed_token['status']}")

    print("\nMock database test complete!")
