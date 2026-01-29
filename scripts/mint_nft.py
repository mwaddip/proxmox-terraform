#!/usr/bin/env python3
"""
NFT Minting Script

Mints access credential NFTs after successful VM creation.
Uses Foundry's `cast` CLI and `pam_web3_tool` for encryption.

Requires:
- Foundry (cast) installed: https://getfoundry.sh
- pam_web3_tool built with NFT feature
- Deployer private key with funds on the target chain
"""

import subprocess
import sys
from pathlib import Path
from typing import Optional

import yaml


def load_web3_defaults() -> dict:
    """Load web3 default configuration."""
    config_path = Path(__file__).parent.parent / "config" / "web3-defaults.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def encrypt_machine_id(machine_id: str, server_pubkey: str) -> str:
    """
    Encrypt a machine ID using the server's ECIES public key.

    Uses pam_web3_tool to perform the encryption.
    Returns the encrypted hex string.
    """
    cmd = [
        "pam_web3_tool", "encrypt",
        "--machine-id", machine_id,
        "--server-pubkey", server_pubkey,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Failed to encrypt machine ID: {result.stderr}")

    # Parse output - look for the encrypted hex string
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line.startswith("0x"):
            return line

    raise RuntimeError(f"Could not parse encrypted output: {result.stdout}")


def read_deployer_key(config: dict) -> str:
    """Read the deployer private key from file."""
    key_file = Path(config["deployer"]["private_key_file"])

    if not key_file.exists():
        raise FileNotFoundError(
            f"Deployer key not found at {key_file}. "
            f"Create it with: cast wallet new | grep 'Private key' | awk '{{print $3}}' > {key_file}"
        )

    return key_file.read_text().strip()


def mint_nft(
    owner_wallet: str,
    machine_id: str,
    config: Optional[dict] = None,
    dry_run: bool = False,
) -> Optional[str]:
    """
    Mint an access credential NFT to the specified wallet.

    Args:
        owner_wallet: Ethereum address to receive the NFT
        machine_id: VM name/machine ID to encrypt into the NFT
        config: Web3 config dict (loaded from web3-defaults.yaml if None)
        dry_run: If True, print the command but don't execute

    Returns:
        Transaction hash if successful, None if dry run
    """
    if config is None:
        config = load_web3_defaults()

    nft_contract = config["blockchain"]["nft_contract"]
    rpc_url = config["blockchain"]["rpc_url"]
    server_pubkey = config["server"]["public_key"]

    # Encrypt machine ID
    print(f"Encrypting machine ID '{machine_id}'...")
    encrypted = encrypt_machine_id(machine_id, server_pubkey)
    print(f"Encrypted: {encrypted[:20]}...{encrypted[-8:]}")

    # Read deployer key
    deployer_key = read_deployer_key(config)

    # Build cast command
    cmd = [
        "cast", "send",
        nft_contract,
        "mint(address,bytes,bytes,string,string,string,uint256)",
        owner_wallet,
        encrypted,
        "0x",                               # empty secondary encrypted data
        "",                                  # empty metadata URI
        f"Access - {machine_id}",           # NFT name/description
        "",                                  # empty image URI
        "0",                                # expiry (0 = never)
        "--private-key", deployer_key,
        "--rpc-url", rpc_url,
    ]

    if dry_run:
        # Mask the private key in output
        display_cmd = cmd.copy()
        pk_idx = display_cmd.index("--private-key") + 1
        display_cmd[pk_idx] = "0x***REDACTED***"
        print(f"[DRY RUN] Would execute: {' '.join(display_cmd)}")
        return None

    print(f"Minting NFT to {owner_wallet}...")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Minting failed: {result.stderr}")

    # Extract transaction hash from output
    tx_hash = None
    for line in result.stdout.strip().split("\n"):
        if "transactionHash" in line or line.startswith("0x"):
            tx_hash = line.strip().split()[-1]
            break

    if tx_hash:
        print(f"NFT minted! TX: {tx_hash}")
    else:
        print(f"NFT minted! Output: {result.stdout.strip()}")

    return tx_hash


def main():
    """CLI for testing NFT minting."""
    import argparse

    parser = argparse.ArgumentParser(description="Mint access credential NFT")
    parser.add_argument("--owner-wallet", required=True, help="Wallet address to receive the NFT")
    parser.add_argument("--machine-id", required=True, help="Machine ID to encrypt into the NFT")
    parser.add_argument("--dry-run", action="store_true", help="Print command without executing")

    args = parser.parse_args()

    try:
        tx_hash = mint_nft(
            owner_wallet=args.owner_wallet,
            machine_id=args.machine_id,
            dry_run=args.dry_run,
        )
        if tx_hash:
            print(f"\nTransaction: {tx_hash}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
