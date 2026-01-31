# Packaging Plan for blockhost-provisioner

**Status:** SUPERSEDED - Circular dependencies resolved via blockhost-common package.

The `blockhost-common` package now provides:
- `blockhost.config` module - Config loading functions
- `blockhost.vm_db` module - Database classes
- Config files in `/etc/blockhost/`

This package (`blockhost-provisioner`) now only provides the provisioning scripts.

---

## Package Overview: `blockhost-provisioner`

### Dependencies

```
Depends: python3 (>= 3.10), blockhost-common, libpam-web3-tools (>= 0.4.0)
Recommends: terraform (>= 1.0), foundry
Suggests: libguestfs-tools
```

**Note:** `terraform` and `foundry` aren't in Debian repos - document as manual install requirements.
**Note:** `python3-yaml` is now a dependency of `blockhost-common`.

---

### File Destinations

| Source | Destination | Type |
|--------|-------------|------|
| **Executables** | | |
| `scripts/vm-generator.py` | `/usr/bin/blockhost-vm-create` | bin (symlink or rename) |
| `scripts/vm-gc.py` | `/usr/bin/blockhost-vm-gc` | bin |
| `scripts/mint_nft.py` | `/usr/bin/blockhost-mint-nft` | bin |
| `scripts/build-template.sh` | `/usr/bin/blockhost-build-template` | bin |
| **Python modules** | | |
| `scripts/mint_nft.py` | `/usr/lib/python3/dist-packages/blockhost/mint_nft.py` | module |
| `scripts/vm-generator.py` | `/usr/lib/python3/dist-packages/blockhost/vm_generator.py` | module |
| **Cloud-init templates** | | |
| `cloud-init/templates/*.yaml` | `/usr/share/blockhost/cloud-init/templates/` | data |
| **Documentation** | | |
| `README.md` | `/usr/share/doc/blockhost-provisioner/README.md` | doc |
| `PROJECT.yaml` | `/usr/share/doc/blockhost-provisioner/PROJECT.yaml` | doc |

**Note:** Config files (`db.yaml`, `web3-defaults.yaml`) and `vm_db.py` are now provided by `blockhost-common`.

---

### User-Provided Configuration Variables

#### **REQUIRED** (must be set before use)

| File | Variable | Description | Example |
|------|----------|-------------|---------|
| `web3-defaults.yaml` | `blockchain.nft_contract` | NFT contract address | `0x1234...abcd` |
| `web3-defaults.yaml` | `deployer.private_key_file` | Path to deployer private key | `/etc/blockhost/deployer.key` |
| (separate file) | Deployer private key | Hex private key with ETH for gas | `0xabcd...` |

#### **RECOMMENDED** (site-specific)

| File | Variable | Description | Default |
|------|----------|-------------|---------|
| `db.yaml` | `terraform_dir` | Where .tf.json files are written | `/var/lib/blockhost/terraform` |
| `db.yaml` | `db_file` | VM database JSON file | `/var/lib/blockhost/vms.json` |
| `db.yaml` | `ip_pool.network` | VM network CIDR | `192.168.122.0/24` |
| `db.yaml` | `ip_pool.start` | First allocatable IP suffix | `200` |
| `db.yaml` | `ip_pool.end` | Last allocatable IP suffix | `250` |
| `db.yaml` | `ip_pool.gateway` | Network gateway | `192.168.122.1` |
| `db.yaml` | `vmid_range.start` | First VMID to allocate | `100` |
| `db.yaml` | `vmid_range.end` | Last VMID to allocate | `999` |
| `web3-defaults.yaml` | `blockchain.chain_id` | Ethereum chain ID | `11155111` (Sepolia) |
| `web3-defaults.yaml` | `blockchain.rpc_url` | JSON-RPC endpoint | `https://ethereum-sepolia-rpc.publicnode.com` |

#### **OPTIONAL** (sensible defaults)

| File | Variable | Description | Default |
|------|----------|-------------|---------|
| `db.yaml` | `default_expiry_days` | VM expiry period | `30` |
| `db.yaml` | `gc_grace_days` | Grace before GC destroys | `3` |
| `web3-defaults.yaml` | `auth.otp_length` | OTP code length | `6` |
| `web3-defaults.yaml` | `auth.otp_ttl_seconds` | OTP validity | `300` |
| `web3-defaults.yaml` | `signing_page.html_path` | Signing page location | `/usr/share/libpam-web3-tools/signing-page/index.html` |

---

### Environment Variables for `build-template.sh`

| Variable | Description | Default |
|----------|-------------|---------|
| `PROXMOX_HOST` | SSH target for Proxmox server | `root@ix` |
| `TEMPLATE_VMID` | VMID for the template | `9001` |
| `STORAGE` | Proxmox storage pool | `local-lvm` |
| `LIBPAM_WEB3_DEB` | Path to libpam-web3 .deb | `~/projects/libpam-web3/packaging/libpam-web3_0.4.0_amd64.deb` |

---

### postinst Script Actions

```bash
# Create state directories
mkdir -p /var/lib/blockhost/terraform
mkdir -p /var/lib/blockhost

# Initialize empty database if not exists
if [ ! -f /var/lib/blockhost/vms.json ]; then
    echo '{"vms":{},"nft_tokens":{},"allocated_ips":[],"allocated_vmids":[]}' > /var/lib/blockhost/vms.json
fi

# Set permissions
chmod 750 /etc/blockhost
chmod 640 /etc/blockhost/*.yaml
```

---

### Terraform Provider Setup

The package should include or document Proxmox provider setup:

```json
// /var/lib/blockhost/terraform/provider.tf.json
{
  "terraform": {
    "required_providers": {
      "proxmox": {
        "source": "bpg/proxmox",
        "version": ">= 0.50.0"
      }
    }
  },
  "provider": {
    "proxmox": {
      "endpoint": "https://PROXMOX_HOST:8006",
      "username": "root@pam",
      "password": "PROXMOX_PASSWORD"
    }
  }
}
```

This requires user configuration for Proxmox credentials.

---

### Quick Setup After Install

```bash
# 1. Configure NFT contract (REQUIRED)
sudo editor /etc/blockhost/web3-defaults.yaml
# Set blockchain.nft_contract to your deployed contract address

# 2. Create deployer key (REQUIRED)
sudo bash -c 'cast wallet new | grep "Private key" | awk "{print \$3}" > /etc/blockhost/deployer.key'
sudo chmod 600 /etc/blockhost/deployer.key

# 3. Configure network settings (if not 192.168.122.0/24)
sudo editor /etc/blockhost/db.yaml

# 4. Setup Terraform provider credentials
sudo editor /var/lib/blockhost/terraform/provider.tf.json

# 5. Initialize Terraform
cd /var/lib/blockhost/terraform && terraform init

# 6. Build Proxmox template (one-time)
PROXMOX_HOST=root@your-proxmox blockhost-build-template
```

---

## TODO: Resolve Module Dependencies

Need to map out which modules exist and what they provide:
- proxmox-terraform (this repo)
- libpam-web3
- libpam-web3-tools
- blockhost-engine (?)
- Others?

Questions to resolve:
1. Which module owns `/etc/blockhost/` config files?
2. Which module owns the web3-defaults.yaml blockchain settings?
3. Should NFT minting be in this package or a separate one?
4. Where does the deployer key management belong?
