#!/bin/bash
# Build blockhost-provisioner .deb package
#
# Creates a Debian package with:
# - CLI tools in /usr/bin/
# - Python modules in /usr/lib/python3/dist-packages/blockhost/
# - Cloud-init templates in /usr/share/blockhost/
# - Documentation in /usr/share/doc/blockhost-provisioner/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="0.1.0"
PACKAGE_NAME="blockhost-provisioner_${VERSION}_all"
BUILD_DIR="${SCRIPT_DIR}/build"

echo "Building ${PACKAGE_NAME}.deb..."

# Create clean build directory
rm -rf "${BUILD_DIR}/pkg"
mkdir -p "${BUILD_DIR}/pkg"

PKG="${BUILD_DIR}/pkg"

# Create DEBIAN control files
mkdir -p "${PKG}/DEBIAN"

cat > "${PKG}/DEBIAN/control" << 'EOF'
Package: blockhost-provisioner
Version: 0.1.0
Section: admin
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), blockhost-common (>= 0.1.0), libpam-web3-tools (>= 0.5.0)
Recommends: terraform (>= 1.0)
Suggests: libguestfs-tools
Maintainer: Blockhost Team <blockhost@example.com>
Description: Proxmox VM provisioning with NFT-based web3 authentication
 This package provides tools for provisioning Proxmox VMs with
 NFT-based web3 authentication using the libpam-web3 PAM module.
 .
 Includes:
  - blockhost-vm-create: Create VMs with NFT authentication
  - blockhost-vm-gc: Garbage collect expired VMs (two-phase: suspend then destroy)
  - blockhost-vm-resume: Resume a suspended VM
  - blockhost-mint-nft: Mint access credential NFTs
  - blockhost-build-template: Build Proxmox VM template
  - Cloud-init templates for web3-authenticated VMs
  - Systemd timer for daily garbage collection
 .
 Note: Terraform and Foundry (cast) must be installed manually.
EOF

# Create postinst script
cat > "${PKG}/DEBIAN/postinst" << 'EOF'
#!/bin/bash
# Post-installation script for blockhost-provisioner

set -e

case "$1" in
    configure)
        # Create terraform directory if it doesn't exist
        if [ ! -d /var/lib/blockhost/terraform ]; then
            mkdir -p /var/lib/blockhost/terraform
            chown root:blockhost /var/lib/blockhost/terraform 2>/dev/null || true
            chmod 750 /var/lib/blockhost/terraform
        fi

        # Enable and start the garbage collection timer
        systemctl daemon-reload
        systemctl enable blockhost-gc.timer
        systemctl start blockhost-gc.timer

        echo ""
        echo "============================================================"
        echo "  blockhost-provisioner installed successfully!"
        echo "============================================================"
        echo ""
        echo "Available commands:"
        echo "  blockhost-vm-create      - Create VMs with NFT authentication"
        echo "  blockhost-vm-gc          - Garbage collect expired VMs"
        echo "  blockhost-vm-resume      - Resume a suspended VM"
        echo "  blockhost-mint-nft       - Mint access credential NFTs"
        echo "  blockhost-build-template - Build Proxmox VM template"
        echo ""
        echo "IMPORTANT: Manual installation required for:"
        echo ""
        echo "  1. Terraform (https://terraform.io/downloads)"
        echo "     curl -fsSL https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp.gpg"
        echo "     echo \"deb [signed-by=/usr/share/keyrings/hashicorp.gpg] https://apt.releases.hashicorp.com \$(lsb_release -cs) main\" | sudo tee /etc/apt/sources.list.d/hashicorp.list"
        echo "     sudo apt update && sudo apt install terraform"
        echo ""
        echo "  2. Foundry/cast (https://book.getfoundry.sh/getting-started/installation)"
        echo "     curl -L https://foundry.paradigm.xyz | bash"
        echo "     foundryup"
        echo ""
        echo "Quick start:"
        echo "  1. Configure /etc/blockhost/web3-defaults.yaml (NFT contract)"
        echo "  2. Create deployer key: /etc/blockhost/deployer.key"
        echo "  3. Setup Terraform in /var/lib/blockhost/terraform/"
        echo "  4. Build template: blockhost-build-template"
        echo "  5. Create VM: blockhost-vm-create myvm --owner-wallet 0x... --apply"
        echo ""
        echo "See /usr/share/doc/blockhost-provisioner/ for documentation."
        echo "============================================================"
        ;;
esac

#DEBHELPER#

exit 0
EOF
chmod 755 "${PKG}/DEBIAN/postinst"

# Create prerm script (cleanup before removal)
cat > "${PKG}/DEBIAN/prerm" << 'EOF'
#!/bin/bash
set -e

case "$1" in
    remove|upgrade|deconfigure)
        # Stop and disable the garbage collection timer
        systemctl stop blockhost-gc.timer 2>/dev/null || true
        systemctl disable blockhost-gc.timer 2>/dev/null || true
        ;;
esac

#DEBHELPER#
exit 0
EOF
chmod 755 "${PKG}/DEBIAN/prerm"

# Create postrm script (cleanup after removal)
cat > "${PKG}/DEBIAN/postrm" << 'EOF'
#!/bin/bash
set -e

case "$1" in
    purge)
        # Optionally remove generated terraform files
        # (not removing by default as they may contain state)
        echo "Note: Terraform files in /var/lib/blockhost/terraform/ were not removed."
        echo "Remove manually if no longer needed."
        ;;
esac

#DEBHELPER#

exit 0
EOF
chmod 755 "${PKG}/DEBIAN/postrm"

# Create directory structure
mkdir -p "${PKG}/usr/bin"
mkdir -p "${PKG}/usr/lib/python3/dist-packages/blockhost"
mkdir -p "${PKG}/usr/lib/systemd/system"
mkdir -p "${PKG}/usr/share/blockhost/cloud-init/templates"
mkdir -p "${PKG}/usr/share/doc/blockhost-provisioner"

# Install executables to /usr/bin/
# Copy with new names and make executable
cp "${SCRIPT_DIR}/scripts/vm-generator.py" "${PKG}/usr/bin/blockhost-vm-create"
cp "${SCRIPT_DIR}/scripts/vm-gc.py" "${PKG}/usr/bin/blockhost-vm-gc"
cp "${SCRIPT_DIR}/scripts/vm-resume.py" "${PKG}/usr/bin/blockhost-vm-resume"
cp "${SCRIPT_DIR}/scripts/mint_nft.py" "${PKG}/usr/bin/blockhost-mint-nft"
cp "${SCRIPT_DIR}/scripts/build-template.sh" "${PKG}/usr/bin/blockhost-build-template"

chmod 755 "${PKG}/usr/bin/blockhost-vm-create"
chmod 755 "${PKG}/usr/bin/blockhost-vm-gc"
chmod 755 "${PKG}/usr/bin/blockhost-vm-resume"
chmod 755 "${PKG}/usr/bin/blockhost-mint-nft"
chmod 755 "${PKG}/usr/bin/blockhost-build-template"

# Install systemd units
cp "${SCRIPT_DIR}/systemd/blockhost-gc.service" "${PKG}/usr/lib/systemd/system/"
cp "${SCRIPT_DIR}/systemd/blockhost-gc.timer" "${PKG}/usr/lib/systemd/system/"

# Install Python modules to /usr/lib/python3/dist-packages/blockhost/
cp "${SCRIPT_DIR}/scripts/vm-generator.py" "${PKG}/usr/lib/python3/dist-packages/blockhost/vm_generator.py"
cp "${SCRIPT_DIR}/scripts/mint_nft.py" "${PKG}/usr/lib/python3/dist-packages/blockhost/mint_nft.py"

# Install cloud-init templates
cp "${SCRIPT_DIR}/cloud-init/templates/"*.yaml "${PKG}/usr/share/blockhost/cloud-init/templates/"

# Install documentation
cp "${SCRIPT_DIR}/README.md" "${PKG}/usr/share/doc/blockhost-provisioner/"
cp "${SCRIPT_DIR}/PROJECT.yaml" "${PKG}/usr/share/doc/blockhost-provisioner/"

# Create copyright file (required for Debian packages)
cat > "${PKG}/usr/share/doc/blockhost-provisioner/copyright" << 'EOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: blockhost-provisioner
Source: https://github.com/blockhost/blockhost-provisioner

Files: *
Copyright: 2024-2026 Blockhost Team
License: MIT
 Permission is hereby granted, free of charge, to any person obtaining a copy
 of this software and associated documentation files (the "Software"), to deal
 in the Software without restriction, including without limitation the rights
 to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 copies of the Software, and to permit persons to whom the Software is
 furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in all
 copies or substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 SOFTWARE.
EOF

# Create changelog (required for Debian packages)
cat > "${PKG}/usr/share/doc/blockhost-provisioner/changelog.Debian" << EOF
blockhost-provisioner (0.1.0) unstable; urgency=low

  * Initial release
  * VM provisioning with NFT-based web3 authentication
  * IPv6 support for public VM access
  * Cloud-init templates for web3-authenticated VMs
  * Two-phase VM lifecycle: suspend expired VMs, destroy after grace period
  * Systemd timer for daily garbage collection
  * VM resume script for extending subscriptions

 -- Blockhost Team <blockhost@example.com>  $(date -R)
EOF
gzip -9 -n "${PKG}/usr/share/doc/blockhost-provisioner/changelog.Debian"

# Build the package
echo "Building package..."
dpkg-deb --build "${PKG}" "${BUILD_DIR}/${PACKAGE_NAME}.deb"

echo ""
echo "============================================================"
echo "Package built successfully!"
echo "============================================================"
echo ""
echo "Output: ${BUILD_DIR}/${PACKAGE_NAME}.deb"
echo ""
echo "Install with:"
echo "  sudo dpkg -i ${BUILD_DIR}/${PACKAGE_NAME}.deb"
echo ""
echo "Or with dependencies:"
echo "  sudo apt install ./${BUILD_DIR}/${PACKAGE_NAME}.deb"
echo ""
