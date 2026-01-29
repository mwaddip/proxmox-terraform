#!/bin/bash
set -euo pipefail

# Proxmox Template Builder
# Creates a minimal Debian 12 cloud-init ready template with libpam-web3

PROXMOX_HOST="${PROXMOX_HOST:-root@ix}"
TEMPLATE_VMID="${TEMPLATE_VMID:-9001}"
STORAGE="${STORAGE:-local-lvm}"
IMAGE_URL="https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2"
IMAGE_NAME="debian-12-genericcloud-amd64.qcow2"
TEMPLATE_NAME="debian-12-web3-template"

# Path to libpam-web3 .deb package
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIBPAM_WEB3_DEB="${LIBPAM_WEB3_DEB:-$HOME/projects/libpam-web3/packaging/libpam-web3_0.2.0_amd64.deb}"

echo "=== Proxmox Template Builder ==="
echo "Host: ${PROXMOX_HOST}"
echo "Template VMID: ${TEMPLATE_VMID}"
echo "Storage: ${STORAGE}"
echo "libpam-web3: ${LIBPAM_WEB3_DEB}"
echo ""

# Check prerequisites
if ! command -v virt-customize &> /dev/null; then
    echo "Error: virt-customize not found. Install with: sudo apt install libguestfs-tools"
    exit 1
fi

if [[ ! -f "${LIBPAM_WEB3_DEB}" ]]; then
    echo "Error: libpam-web3 .deb not found at ${LIBPAM_WEB3_DEB}"
    exit 1
fi

# Download image locally
echo "Downloading ${IMAGE_NAME}..."
wget -N -P /tmp "${IMAGE_URL}"

# Make a working copy of the image for customization
WORK_IMAGE="/tmp/${IMAGE_NAME%.qcow2}-customized.qcow2"
echo "Creating working copy for customization..."
cp "/tmp/${IMAGE_NAME}" "${WORK_IMAGE}"

# Customize the image with libpam-web3
echo "Installing libpam-web3 into image..."
virt-customize -a "${WORK_IMAGE}" \
    --copy-in "${LIBPAM_WEB3_DEB}":/tmp \
    --run-command 'dpkg -i /tmp/libpam-web3_0.2.0_amd64.deb || apt-get -f install -y' \
    --run-command 'systemctl enable web3-auth-svc' \
    --delete /tmp/libpam-web3_0.2.0_amd64.deb

IMAGE_SIZE=$(du -h "${WORK_IMAGE}" | cut -f1)
echo "Customized image size: ${IMAGE_SIZE}"

# Upload to Proxmox
echo "Uploading customized image to ${PROXMOX_HOST}..."
ssh "${PROXMOX_HOST}" "mkdir -p /var/lib/vz/template/qcow2"
scp "${WORK_IMAGE}" "${PROXMOX_HOST}:/var/lib/vz/template/qcow2/${IMAGE_NAME}"

# Clean up working copy
rm -f "${WORK_IMAGE}"

# Create template on Proxmox
echo "Creating template VM ${TEMPLATE_VMID}..."
ssh "${PROXMOX_HOST}" << REMOTE
    set -euo pipefail

    # Remove existing template if present
    if qm status ${TEMPLATE_VMID} &>/dev/null; then
        echo "Removing existing VM ${TEMPLATE_VMID}..."
        qm destroy ${TEMPLATE_VMID} --purge
    fi

    # Create VM
    qm create ${TEMPLATE_VMID} --name "${TEMPLATE_NAME}" \
        --memory 512 --cores 1 \
        --net0 virtio,bridge=vmbr0 \
        --ostype l26 --scsihw virtio-scsi-pci

    # Import disk
    echo "Importing disk..."
    qm importdisk ${TEMPLATE_VMID} /var/lib/vz/template/qcow2/${IMAGE_NAME} ${STORAGE}

    # Attach disk and configure boot
    qm set ${TEMPLATE_VMID} --scsi0 ${STORAGE}:vm-${TEMPLATE_VMID}-disk-0
    qm set ${TEMPLATE_VMID} --boot order=scsi0

    # Add cloud-init drive
    qm set ${TEMPLATE_VMID} --ide2 ${STORAGE}:cloudinit

    # Enable QEMU guest agent
    qm set ${TEMPLATE_VMID} --agent enabled=1

    # Serial console for cloud images
    qm set ${TEMPLATE_VMID} --serial0 socket --vga serial0

    # Convert to template
    echo "Converting to template..."
    qm template ${TEMPLATE_VMID}

    echo "Template ${TEMPLATE_VMID} created successfully!"
    qm config ${TEMPLATE_VMID}
REMOTE

echo ""
echo "=== Template creation complete ==="
echo "Template VMID: ${TEMPLATE_VMID}"
echo "You can now clone this template using Terraform"
