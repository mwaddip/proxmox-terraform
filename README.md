# blockhost-provisioner

Terraform-based Proxmox VM automation with NFT web3 authentication. Creates Debian 12 VMs from a cloud-init template that includes [libpam-web3](https://github.com/mwaddip/libpam-web3) for Ethereum wallet-based SSH login.

## How it works

1. A Debian 12 cloud image is customized with `libpam-web3` and uploaded to Proxmox as a template (VMID 9001)
2. `vm-generator.py` reserves an NFT token ID, generates a Terraform `.tf.json` config with cloud-init, and optionally applies it
3. On successful VM creation, an access NFT is minted to the owner's wallet
4. Users authenticate to SSH by signing an OTP challenge with their Ethereum wallet

VMs are tracked in a JSON database with IP/VMID allocation, expiry dates, and NFT token status. Expired VMs are cleaned up by `vm-gc.py`.

## Prerequisites

- **blockhost-common** package - Provides configuration and database modules
- **libpam-web3-tools** package - Provides signing page HTML and `pam_web3_tool` CLI
- [Terraform](https://www.terraform.io/) with the [bpg/proxmox](https://registry.terraform.io/providers/bpg/proxmox/latest) provider
- Proxmox VE host accessible via SSH (`root@ix`)
- [Foundry](https://getfoundry.sh/) (`cast` CLI) for NFT minting
- `libguestfs-tools` for template image customization

## Installation

```bash
# Install dependencies
sudo dpkg -i blockhost-common_*.deb
sudo dpkg -i libpam-web3-tools_*.deb
sudo dpkg -i blockhost-provisioner_*.deb

# Initialize server (generates keys and config)
sudo /path/to/blockhost-engine/scripts/init-server.sh

# Configure settings
sudo editor /etc/blockhost/db.yaml
sudo editor /etc/blockhost/web3-defaults.yaml

# Build Proxmox template
./scripts/build-template.sh
```

## Integration / Package Usage

For programmatic integration:

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
│   ├── vm-gc.py            # Garbage collect expired VMs
│   └── mint_nft.py         # Mint access NFTs via Foundry cast
├── cloud-init/
│   └── templates/
│       ├── nft-auth.yaml   # NFT auth cloud-init (default)
│       ├── webserver.yaml  # Basic webserver cloud-init
│       └── devbox.yaml     # Dev environment cloud-init
├── accounting/
│   └── mock-db.json        # Mock database for testing
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

# With encrypted connection details (subscription system workflow)
python3 scripts/vm-generator.py web-001 \
    --owner-wallet 0xAbCd... \
    --user-signature 0x... \
    --public-secret "libpam-web3:0xAbCd...:12345" \
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
0 2 * * * cd /path/to/blockhost-provisioner && python3 scripts/vm-gc.py --execute --grace-days 3 >> logs/gc.log 2>&1
```

### `scripts/mint_nft.py`

Mints access credential NFTs after VM creation:

- Embeds the signing page HTML from libpam-web3-tools
- Optionally embeds encrypted connection details (userEncrypted, publicSecret)
- Calls the NFT contract's `mint()` function via Foundry's `cast send`

```bash
# Standalone minting
python3 scripts/mint_nft.py --owner-wallet 0x1234... --machine-id web-001

# With encrypted connection details
python3 scripts/mint_nft.py --owner-wallet 0x1234... --machine-id web-001 \
    --user-encrypted 0xabc... --public-secret "libpam-web3:0x1234...:12345"

# Dry run
python3 scripts/mint_nft.py --owner-wallet 0x1234... --machine-id web-001 --dry-run
```

## Configuration

Configuration files are provided by **blockhost-common** in `/etc/blockhost/`:

### `/etc/blockhost/web3-defaults.yaml`

Blockchain settings: chain ID, NFT contract address, RPC URL, deployer key path. Update these with your deployed contract details.

### `/etc/blockhost/db.yaml`

Database configuration: production DB file path, terraform_dir, IP pool range, VMID range, default expiry, and GC grace period.

## NFT auth flow

1. User connects via SSH
2. PAM module (`pam_web3.so`) checks the user's GECOS field for `nft=TOKEN_ID`
3. PAM queries `web3-auth-svc` to verify the connecting wallet owns that NFT on-chain
4. User is redirected to the signing page (`http://VM_IP:8080`) to sign an OTP challenge
5. PAM verifies the signature and grants access

## Setup

1. Install blockhost-common and libpam-web3-tools packages
2. Run `init-server.sh` from blockhost-engine to generate keys and config
3. Edit `/etc/blockhost/web3-defaults.yaml` with your contract address and RPC URL
4. Edit `/etc/blockhost/db.yaml` with your terraform_dir path
5. Build the template: `./scripts/build-template.sh`
6. Create `terraform.tfvars` in terraform_dir with your Proxmox credentials
7. Create VMs: `python3 scripts/vm-generator.py <name> --owner-wallet <addr> --apply`
