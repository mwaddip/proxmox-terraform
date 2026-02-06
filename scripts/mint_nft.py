#!/usr/bin/env python3
"""
NFT Minting Script

Mints access credential NFTs after successful VM creation.
Uses Foundry's `cast` CLI for contract interaction.

Requires:
- Foundry (cast) installed: https://getfoundry.sh
- Deployer private key with funds on the target chain
- Signing page HTML file (from libpam-web3)
"""

import base64
import subprocess
import sys
from pathlib import Path
from typing import Optional

from blockhost.config import load_web3_config


def read_deployer_key(config: dict) -> str:
    """Read the deployer private key from file."""
    key_file = Path(config["deployer"]["private_key_file"])

    if not key_file.exists():
        raise FileNotFoundError(
            f"Deployer key not found at {key_file}. "
            f"Create it with: cast wallet new | grep 'Private key' | awk '{{print $3}}' > {key_file}"
        )

    return key_file.read_text().strip()


def load_signing_page(
    config: dict,
    user_encrypted: str = "",
    public_secret: str = "",
) -> str:
    """
    Load and base64-encode the signing page HTML.

    The signing page is embedded in the NFT's animationUrlBase64 field
    and extracted by VMs to serve locally for wallet authentication.

    If user_encrypted and public_secret are provided, they are embedded
    into the signing page so users can decrypt their connection details.

    Args:
        config: Web3 config dict
        user_encrypted: Hex-encoded encrypted connection details
        public_secret: Message user signs to derive decryption key

    Returns:
        Base64-encoded HTML content (not a data URI).
    """
    # Search paths in order of preference
    search_paths = [
        # Config-specified path
        Path(config.get("signing_page", {}).get("html_path", "")),
        # libpam-web3-tools package location (new)
        Path("/usr/share/libpam-web3-tools/signing-page/index.html"),
        # Legacy libpam-web3 location
        Path("/usr/share/libpam-web3/signing-page/index.html"),
        # Local development path (libpam-web3-tools)
        Path.home() / "projects/libpam-web3/packaging/libpam-web3-tools_0.4.0_amd64/usr/share/libpam-web3-tools/signing-page/index.html",
        # Local development path (legacy)
        Path.home() / "projects/libpam-web3/signing-page/index.html",
    ]

    html_path = None
    for path in search_paths:
        if path and path.is_file():
            html_path = path
            break

    if not html_path:
        raise FileNotFoundError(
            "Signing page HTML not found. Searched:\n" +
            "\n".join(f"  - {p}" for p in search_paths if p) +
            "\nInstall libpam-web3-tools or set signing_page.html_path in config."
        )

    html_content = html_path.read_text()

    # Embed decrypt credentials if provided
    if user_encrypted and public_secret:
        print(f"Embedding decrypt credentials into signing page...")
        html_content = html_content.replace("__PUBLIC_SECRET__", public_secret)
        html_content = html_content.replace("__USER_ENCRYPTED__", user_encrypted)

    # Return ONLY the base64 content - no data URI prefix
    # The contract prepends "data:text/html;base64," itself
    return base64.b64encode(html_content.encode()).decode()


def mint_nft(
    owner_wallet: str,
    machine_id: str,
    user_encrypted: str = "0x",
    public_secret: str = "",
    config: Optional[dict] = None,
    dry_run: bool = False,
) -> Optional[str]:
    """
    Mint an access credential NFT to the specified wallet.

    Args:
        owner_wallet: Ethereum address to receive the NFT
        machine_id: VM name/machine ID (used in description)
        user_encrypted: Hex-encoded encrypted connection details (from subscription system)
        public_secret: Message the user signed during subscription
        config: Web3 config dict (loaded from web3-defaults.yaml if None)
        dry_run: If True, print the command but don't execute

    Returns:
        Transaction hash if successful, None if dry run
    """
    if config is None:
        config = load_web3_config()

    nft_contract = config["blockchain"]["nft_contract"]
    rpc_url = config["blockchain"]["rpc_url"]

    # Load signing page HTML as base64, embedding decrypt credentials if provided
    print("Loading signing page...")
    signing_page_base64 = load_signing_page(
        config,
        user_encrypted=user_encrypted if user_encrypted != "0x" else "",
        public_secret=public_secret,
    )
    print(f"Signing page size: {len(signing_page_base64)} bytes (base64)")

    # Read deployer key
    deployer_key = read_deployer_key(config)

    # Build cast command with new contract signature
    # Parameters: to, userEncrypted, publicSecret, description, imageUri, animationUrlBase64, expiresAt
    cmd = [
        "cast", "send",
        nft_contract,
        "mint(address,bytes,string,string,string,string,uint256)",
        owner_wallet,
        user_encrypted,                     # Encrypted connection details
        public_secret,                    # Message user signed during subscription
        f"Access - {machine_id}",           # description
        "",                                 # imageUri (use default)
        signing_page_base64,                # animationUrlBase64 (just base64, not data URI)
        "0",                                # expiresAt (0 = never)
        "--private-key", deployer_key,
        "--rpc-url", rpc_url,
    ]

    if dry_run:
        # Mask sensitive data in output
        display_cmd = cmd.copy()
        pk_idx = display_cmd.index("--private-key") + 1
        display_cmd[pk_idx] = "0x***REDACTED***"
        # Truncate signing page base64 for display
        for i, arg in enumerate(display_cmd):
            if len(arg) > 100 and not arg.startswith("--") and not arg.startswith("0x"):
                display_cmd[i] = f"{arg[:50]}...***TRUNCATED***"
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
    parser.add_argument("--machine-id", required=True, help="Machine ID (used in NFT description)")
    parser.add_argument("--user-encrypted", default="0x",
                        help="Hex-encoded encrypted connection details (default: 0x)")
    parser.add_argument("--public-secret", default="",
                        help="Message the user signed during subscription")
    parser.add_argument("--dry-run", action="store_true", help="Print command without executing")

    args = parser.parse_args()

    try:
        tx_hash = mint_nft(
            owner_wallet=args.owner_wallet,
            machine_id=args.machine_id,
            user_encrypted=args.user_encrypted,
            public_secret=args.public_secret,
            dry_run=args.dry_run,
        )
        if tx_hash:
            print(f"\nTransaction: {tx_hash}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
