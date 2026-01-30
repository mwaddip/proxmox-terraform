#!/usr/bin/env python3
"""
VM Generator Script

Generates Terraform configuration files for Proxmox VMs with NFT-based
web3 authentication. Handles the reserve-then-mint workflow:

1. Reserve NFT token ID
2. Generate Terraform config with token ID in cloud-init
3. Apply Terraform to create VM
4. Mint NFT to owner wallet (only on success)

Usage:
    python3 vm-generator.py web-001 \
        --owner-wallet 0x1234... \
        --purpose "web hosting" \
        --cpu 2 --memory 1024 \
        --apply
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from string import Template

import yaml

# Add scripts directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from vm_db import get_database
from mint_nft import mint_nft


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


def load_ssh_keys() -> list[str]:
    """Load SSH public keys from common locations."""
    keys = []
    key_paths = [
        Path.home() / ".ssh" / "id_ed25519.pub",
        Path.home() / ".ssh" / "id_rsa.pub",
    ]

    for path in key_paths:
        if path.exists():
            keys.append(path.read_text().strip())

    return keys


def load_web3_defaults() -> dict:
    """Load global web3 configuration defaults."""
    config_path = get_config_path("web3-defaults.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


def load_db_config() -> dict:
    """Load database configuration."""
    config_path = get_config_path("db.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


def render_cloud_init(template_name: str, variables: dict) -> str:
    """
    Render a cloud-init template with variable substitution.

    Uses ${VAR_NAME} syntax for placeholders.
    """
    template_path = PROJECT_DIR / "cloud-init" / "templates" / f"{template_name}.yaml"

    if not template_path.exists():
        raise FileNotFoundError(f"Cloud-init template not found: {template_path}")

    content = template_path.read_text()

    # Use safe_substitute to leave unresolved variables as-is
    template = Template(content)
    return template.safe_substitute(variables)


def generate_tf_config(
    name: str,
    vmid: int,
    ip_address: str,
    gateway: str,
    cpu_cores: int = 1,
    memory_mb: int = 512,
    disk_gb: int = 10,
    template_vmid: int = 9001,
    node_name: str = "ix",
    tags: list[str] = None,
    ssh_keys: list[str] = None,
    username: str = "admin",
    cloud_init_content: str = None,
) -> dict:
    """Generate Terraform JSON configuration for a VM."""

    resource_name = sanitize_resource_name(name)

    vm_config = {
        "name": name,
        "node_name": node_name,
        "clone": {
            "vm_id": template_vmid
        },
        "cpu": {
            "cores": cpu_cores
        },
        "memory": {
            "dedicated": memory_mb
        },
        "disk": {
            "datastore_id": "local-lvm",
            "size": disk_gb,
            "interface": "scsi0"
        },
        "agent": {
            "enabled": True
        },
        "initialization": {
            "ip_config": {
                "ipv4": {
                    "address": f"{ip_address}/24",
                    "gateway": gateway
                }
            },
            "user_account": {
                "username": username,
                "keys": ssh_keys or []
            }
        }
    }

    # Add tags if specified
    if tags:
        vm_config["tags"] = tags

    tf_config = {
        "resource": {
            "proxmox_virtual_environment_vm": {
                resource_name: vm_config
            }
        }
    }

    # Add cloud-init content as a Proxmox file resource
    if cloud_init_content:
        vm_config["initialization"]["user_data_file_id"] = (
            f"${{proxmox_virtual_environment_file.cloud_config_{resource_name}.id}}"
        )

        tf_config["resource"]["proxmox_virtual_environment_file"] = {
            f"cloud_config_{resource_name}": {
                "content_type": "snippets",
                "datastore_id": "local",
                "node_name": node_name,
                "source_raw": {
                    "data": cloud_init_content,
                    "file_name": f"{name}-cloud-config.yaml"
                }
            }
        }

    return tf_config


def write_tf_file(name: str, config: dict) -> Path:
    """Write Terraform configuration to a .tf.json file in terraform_dir."""
    tf_dir = get_terraform_dir()
    tf_file = tf_dir / f"{name}.tf.json"
    with open(tf_file, "w") as f:
        json.dump(config, f, indent=2)

    return tf_file


def run_terraform(action: str = "plan", target: str = None) -> int:
    """Run terraform command in terraform_dir."""
    tf_dir = get_terraform_dir()
    cmd = ["terraform", action]
    if target:
        cmd.extend(["-target", target])
    if action == "apply":
        cmd.append("-auto-approve")

    print(f"Running: {' '.join(cmd)} (in {tf_dir})")
    result = subprocess.run(cmd, cwd=tf_dir)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description="Generate Terraform configuration for Proxmox VMs with NFT auth",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Generate config only (with NFT auth)
    python3 vm-generator.py web-001 --owner-wallet 0x1234...

    # Generate and apply (reserve token, create VM, mint NFT)
    python3 vm-generator.py web-001 --owner-wallet 0x1234... --apply

    # Without web3 auth
    python3 vm-generator.py web-001 --no-web3 --cloud-init webserver

    # Test with mock database, skip actual minting
    python3 vm-generator.py web-001 --owner-wallet 0x1234... --mock --skip-mint --apply

    # Full example
    python3 vm-generator.py web-001 \\
        --owner-wallet 0xAbCd... \\
        --purpose "production web server" \\
        --owner admin \\
        --cpu 2 --memory 2048 --disk 20 \\
        --tags web production \\
        --expiry-days 90 \\
        --apply
        """
    )

    parser.add_argument("name", help="VM name (e.g., web-001)")
    parser.add_argument("--purpose", default="", help="Purpose/description of the VM")
    parser.add_argument("--owner", default="admin", help="VM owner (default: admin)")
    parser.add_argument("--cpu", type=int, default=1, help="Number of CPU cores (default: 1)")
    parser.add_argument("--memory", type=int, default=512, help="Memory in MB (default: 512)")
    parser.add_argument("--disk", type=int, default=10, help="Disk size in GB (default: 10)")
    parser.add_argument("--template-vmid", type=int, default=9001, help="Template VM ID (default: 9001)")
    parser.add_argument("--tags", nargs="+", default=[], help="Tags for the VM")
    parser.add_argument("--expiry-days", type=int, default=30, help="Days until VM expires (default: 30)")
    parser.add_argument("--username", default="admin", help="Default user account (default: admin)")
    parser.add_argument("--node", default="ix", help="Proxmox node name (default: ix)")
    parser.add_argument("--apply", action="store_true", help="Run terraform apply after generating")
    parser.add_argument("--mock", action="store_true", help="Use mock database for testing")
    parser.add_argument("--ip", help="Specific IP address (otherwise auto-allocated)")
    parser.add_argument("--vmid", type=int, help="Specific VMID (otherwise auto-allocated)")

    # Web3 / NFT options
    parser.add_argument("--owner-wallet", help="Wallet address to receive the access NFT")
    parser.add_argument("--no-web3", action="store_true", help="Disable web3 auth (use standard cloud-init)")
    parser.add_argument("--skip-mint", action="store_true", help="Skip NFT minting (for testing)")
    parser.add_argument("--cloud-init", dest="cloud_init_template",
                        help="Cloud-init template name (default: nft-auth when web3 enabled)")

    args = parser.parse_args()

    # Validate: web3 auth requires --owner-wallet
    web3_enabled = not args.no_web3
    if web3_enabled and not args.owner_wallet:
        parser.error("--owner-wallet is required (or use --no-web3 to disable NFT auth)")

    # Initialize database
    db = get_database(use_mock=args.mock)

    # Check if VM already exists
    existing = db.get_vm(args.name)
    if existing and existing.get("status") == "active":
        print(f"Error: VM '{args.name}' already exists and is active")
        sys.exit(1)

    # Allocate resources
    if args.vmid:
        vmid = args.vmid
    else:
        vmid = db.allocate_vmid()
        print(f"Allocated VMID: {vmid}")

    if args.ip:
        ip_address = args.ip
    else:
        ip_address = db.allocate_ip()
        if not ip_address:
            print("Error: IP pool exhausted")
            sys.exit(1)
        print(f"Allocated IP: {ip_address}")

    # Load SSH keys
    ssh_keys = load_ssh_keys()
    if not ssh_keys:
        print("Warning: No SSH keys found. VM will be created without SSH keys.")

    # Get gateway from DB config
    db_config = load_db_config()
    gateway = db_config["ip_pool"]["gateway"]

    # Reserve NFT token ID if web3 enabled
    nft_token_id = None
    if web3_enabled:
        nft_token_id = db.reserve_nft_token_id(args.name)
        print(f"Reserved NFT token ID: {nft_token_id}")

    # Build cloud-init content
    cloud_init_content = None
    template_name = args.cloud_init_template

    if web3_enabled:
        # Default to nft-auth template
        if not template_name:
            template_name = "nft-auth"

        # Load web3 defaults for template variables
        web3_config = load_web3_defaults()

        # Format SSH keys for cloud-init
        ssh_keys_yaml = ""
        if ssh_keys:
            ssh_keys_yaml = "\n".join(f"      - {key}" for key in ssh_keys)

        # Render cloud-init with variables
        variables = {
            "VM_NAME": args.name,
            "VM_IP": ip_address,
            "USERNAME": args.username,
            "NFT_TOKEN_ID": str(nft_token_id),
            "CHAIN_ID": str(web3_config["blockchain"]["chain_id"]),
            "NFT_CONTRACT": web3_config["blockchain"]["nft_contract"],
            "RPC_URL": web3_config["blockchain"]["rpc_url"],
            "OTP_LENGTH": str(web3_config["auth"]["otp_length"]),
            "OTP_TTL": str(web3_config["auth"]["otp_ttl_seconds"]),
            "SSH_KEYS": f"\n{ssh_keys_yaml}" if ssh_keys_yaml else "[]",
        }

        cloud_init_content = render_cloud_init(template_name, variables)
    elif template_name:
        # Non-web3 cloud-init template (e.g., webserver, devbox)
        template_path = PROJECT_DIR / "cloud-init" / "templates" / f"{template_name}.yaml"
        if template_path.exists():
            cloud_init_content = template_path.read_text()
        else:
            print(f"Warning: Cloud-init template '{template_name}' not found")

    # Generate Terraform config
    tf_config = generate_tf_config(
        name=args.name,
        vmid=vmid,
        ip_address=ip_address,
        gateway=gateway,
        cpu_cores=args.cpu,
        memory_mb=args.memory,
        disk_gb=args.disk,
        template_vmid=args.template_vmid,
        node_name=args.node,
        tags=args.tags,
        ssh_keys=ssh_keys,
        username=args.username,
        cloud_init_content=cloud_init_content,
    )

    # Write Terraform file
    tf_file = write_tf_file(args.name, tf_config)
    print(f"Generated: {tf_file}")

    # Register VM in database
    vm = db.register_vm(
        name=args.name,
        vmid=vmid,
        ip=ip_address,
        owner=args.owner,
        expiry_days=args.expiry_days,
        purpose=args.purpose,
        wallet_address=args.owner_wallet if web3_enabled else None,
    )
    print(f"Registered VM '{args.name}' - expires {vm['expires_at']}")
    if nft_token_id is not None:
        print(f"  NFT token ID: {nft_token_id} (reserved)")

    # Apply if requested
    if args.apply:
        print("\nInitializing Terraform...")
        if run_terraform("init") != 0:
            print("Error: terraform init failed")
            if nft_token_id is not None:
                db.mark_nft_failed(nft_token_id)
                print(f"NFT token {nft_token_id} marked as failed")
            sys.exit(1)

        print("\nApplying Terraform configuration...")
        resource_name = sanitize_resource_name(args.name)
        target = f"proxmox_virtual_environment_vm.{resource_name}"
        apply_result = run_terraform("apply", target)

        if apply_result != 0:
            print(f"\nError: terraform apply failed")
            if nft_token_id is not None:
                db.mark_nft_failed(nft_token_id)
                print(f"NFT token {nft_token_id} marked as failed (VM not created)")
            sys.exit(1)

        print(f"\nVM '{args.name}' created successfully!")
        print(f"  IP: {ip_address}")
        print(f"  VMID: {vmid}")
        print(f"  SSH: ssh {args.username}@{ip_address}")

        # Mint NFT after successful VM creation
        if web3_enabled and nft_token_id is not None and not args.skip_mint:
            print(f"\nMinting NFT #{nft_token_id} to {args.owner_wallet}...")
            try:
                web3_config = load_web3_defaults()
                tx_hash = mint_nft(
                    owner_wallet=args.owner_wallet,
                    machine_id=args.name,
                    config=web3_config,
                )
                db.mark_nft_minted(nft_token_id, args.owner_wallet)
                print(f"NFT #{nft_token_id} minted successfully!")
                if tx_hash:
                    print(f"  TX: {tx_hash}")
            except Exception as e:
                print(f"\nWarning: NFT minting failed: {e}")
                print(f"VM was created but NFT was not minted.")
                print(f"Token {nft_token_id} is still reserved. Retry with:")
                print(f"  python3 scripts/mint_nft.py --owner-wallet {args.owner_wallet} --machine-id {args.name}")
        elif web3_enabled and args.skip_mint:
            print(f"\nSkipped NFT minting (--skip-mint). Token {nft_token_id} remains reserved.")
    else:
        print("\nTo apply this configuration:")
        print(f"  cd {PROJECT_DIR}")
        print(f"  terraform init")
        print(f"  terraform apply")
        if web3_enabled:
            print(f"\nAfter apply, mint the NFT:")
            print(f"  python3 scripts/mint_nft.py --owner-wallet {args.owner_wallet} --machine-id {args.name}")


if __name__ == "__main__":
    main()
