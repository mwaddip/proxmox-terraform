# proxmox-terraform

Terraform-based Proxmox VM automation with NFT web3 authentication. Creates Debian 12 VMs from a cloud-init template that includes [libpam-web3](https://github.com/mwaddip/libpam-web3) for Ethereum wallet-based SSH login.

## How it works

1. A Debian 12 cloud image is customized with `libpam-web3` and uploaded to Proxmox as a template (VMID 9001)
2. `vm-generator.py` reserves an NFT token ID, generates a Terraform `.tf.json` config with cloud-init, and optionally applies it
3. On successful VM creation, an access NFT is minted to the owner's wallet
4. Users authenticate to SSH by signing an OTP challenge with their Ethereum wallet

VMs are tracked in a JSON database with IP/VMID allocation, expiry dates, and NFT token status. Expired VMs are cleaned up by `vm-gc.py`.

## Prerequisites

- [Terraform](https://www.terraform.io/) with the [bpg/proxmox](https://registry.terraform.io/providers/bpg/proxmox/latest) provider
- Proxmox VE host accessible via SSH (`root@ix`)
- [Foundry](https://getfoundry.sh/) (`cast` CLI) for NFT minting
- `libguestfs-tools` for template image customization
- `pam_web3_tool` (from libpam-web3) for ECIES encryption
- Python 3 with `pyyaml`

## Integration / Submodule Usage

For programmatic integration or when using this as a git submodule:

- **`PROJECT.yaml`** - Machine-readable API specification with all entry points, arguments, Python APIs, and configuration options
- **`CLAUDE.md`** - Instructions for AI assistants working with this codebase

Read `PROJECT.yaml` for the complete interface documentation.

## Project structure

```
.
├── PROJECT.yaml            # Machine-readable API spec
├── CLAUDE.md               # AI assistant instructions
├── scripts/
│   ├── build-template.sh   # Build Debian 12 template with libpam-web3
│   ├── vm-generator.py     # Generate + apply VM Terraform configs
│   ├── vm_db.py            # VM database (JSON with file locking)
│   ├── vm-gc.py            # Garbage collect expired VMs
│   └── mint_nft.py         # Mint access NFTs via Foundry cast
├── cloud-init/
│   └── templates/
│       ├── nft-auth.yaml   # NFT auth cloud-init (default)
│       ├── webserver.yaml  # Basic webserver cloud-init
│       └── devbox.yaml     # Dev environment cloud-init
├── config/
│   ├── web3-defaults.yaml  # Blockchain/NFT settings
│   └── db.yaml             # Database and IP pool config
├── accounting/
│   └── mock-db.json        # Mock database for testing
├── vms/                    # Generated .tf.json files (gitignored)
├── provider.tf.json        # Terraform provider config
└── variables.tf.json       # Terraform variable defaults
```

## Scripts

### `scripts/build-template.sh`

Builds the Proxmox VM template:

- Downloads the Debian 12 genericcloud qcow2 image
- Injects the `libpam-web3` `.deb` package using `virt-customize` (no VM boot required)
- Enables the `web3-auth-svc` systemd service
- Uploads to Proxmox and creates template VMID 9001

```bash
# Uses defaults (host=root@ix, template=9001)
./scripts/build-template.sh

# Override settings
PROXMOX_HOST=root@myhost TEMPLATE_VMID=9002 ./scripts/build-template.sh
```

### `scripts/vm-generator.py`

Creates VMs with NFT-based web3 authentication:

1. Reserves a sequential NFT token ID
2. Allocates an IP address and VMID from the pool
3. Renders cloud-init from the `nft-auth` template with the token ID in the user's GECOS field (`nft=TOKEN_ID`)
4. Generates a `.tf.json` Terraform config
5. Optionally runs `terraform apply`
6. On success, mints the access NFT to the owner's wallet

```bash
# Generate config only
python3 scripts/vm-generator.py web-001 --owner-wallet 0x1234...

# Generate and apply (creates VM + mints NFT)
python3 scripts/vm-generator.py web-001 \
    --owner-wallet 0xAbCd... \
    --purpose "production web server" \
    --cpu 2 --memory 2048 --disk 20 \
    --tags web production \
    --apply

# Without web3 auth
python3 scripts/vm-generator.py web-001 --no-web3 --cloud-init webserver

# Test mode (mock DB, no minting)
python3 scripts/vm-generator.py web-001 --owner-wallet 0x1234... --mock --skip-mint --apply
```

### `scripts/vm-gc.py`

Garbage collects expired VMs. Designed for cron:

```bash
# Dry run - list expired VMs
python3 scripts/vm-gc.py --grace-days 3

# Actually destroy expired VMs
python3 scripts/vm-gc.py --execute --grace-days 3

# Cron (daily at 2 AM)
0 2 * * * cd /path/to/proxmox-terraform && python3 scripts/vm-gc.py --execute --grace-days 3 >> logs/gc.log 2>&1
```

### `scripts/vm_db.py`

JSON-based VM database with file locking (`fcntl`). Tracks:

- VM records (name, VMID, IP, owner, expiry, status)
- IP address allocation pool (192.168.122.200-250)
- VMID allocation range (100-999)
- NFT token IDs with status tracking (reserved / minted / failed)

Two implementations: `VMDatabase` (production, file-locked) and `MockVMDatabase` (testing, in-memory backed by `accounting/mock-db.json`).

### `scripts/mint_nft.py`

Mints access credential NFTs after VM creation:

- Encrypts the machine ID using ECIES via `pam_web3_tool`
- Calls the NFT contract's `mint()` function via Foundry's `cast send`

```bash
# Standalone minting
python3 scripts/mint_nft.py --owner-wallet 0x1234... --machine-id web-001

# Dry run
python3 scripts/mint_nft.py --owner-wallet 0x1234... --machine-id web-001 --dry-run
```

## Configuration

### `config/web3-defaults.yaml`

Global blockchain settings shared across all VMs: chain ID, NFT contract address, RPC URL, deployer key path, and server ECIES public key. Update these with your deployed contract details.

### `config/db.yaml`

Database configuration: production DB file path, IP pool range, VMID range, default expiry, and GC grace period.

## NFT auth flow

1. User connects via SSH
2. PAM module (`pam_web3.so`) checks the user's GECOS field for `nft=TOKEN_ID`
3. PAM queries `web3-auth-svc` to verify the connecting wallet owns that NFT on-chain
4. User is redirected to the signing page (`http://VM_IP:8080`) to sign an OTP challenge
5. PAM verifies the signature and grants access

## Setup

1. Edit `config/web3-defaults.yaml` with your contract address, RPC URL, and keys
2. Build the template: `./scripts/build-template.sh`
3. Create `terraform.tfvars` with your Proxmox credentials
4. Create VMs: `python3 scripts/vm-generator.py <name> --owner-wallet <addr> --apply`
