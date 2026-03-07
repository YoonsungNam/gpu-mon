"""
VMware vCenter adapter.

Uses pyVmomi to query GPU VM inventory from vCenter and normalize it
into the vmware_vm_inventory ClickHouse schema.
"""

import json
import logging
from datetime import datetime, timezone
from typing import List, Dict

logger = logging.getLogger(__name__)

try:
    from pyVim.connect import SmartConnect, Disconnect
    from pyVmomi import vim
    import ssl
    PYVMOMI_AVAILABLE = True
except ImportError:
    PYVMOMI_AVAILABLE = False
    logger.warning("pyVmomi not installed; VMware adapter will be disabled.")


class VMwareAdapter:
    def __init__(
        self,
        vcenter_url: str,
        username: str,
        password: str,
        insecure: bool,
        writer,
    ):
        if not PYVMOMI_AVAILABLE:
            raise RuntimeError(
                "pyVmomi is required for VMware adapter. "
                "Install it with: pip install pyVmomi"
            )
        # Extract host from URL
        host = vcenter_url.replace("https://", "").replace("http://", "").rstrip("/")
        self.host = host
        self.username = username
        self.password = password
        self.insecure = insecure
        self.writer = writer

    def _connect(self):
        ctx = None
        if self.insecure:
            ctx = ssl._create_unverified_context()
        return SmartConnect(
            host=self.host,
            user=self.username,
            pwd=self.password,
            sslContext=ctx,
        )

    def collect_vm_inventory(self):
        si = None
        try:
            si = self._connect()
            content = si.RetrieveContent()
            vms = _get_all_vms(content)
            rows = [_normalize_vm(vm) for vm in vms if _has_gpu(vm)]
            if rows:
                self.writer.insert("vmware_vm_inventory", rows)
            logger.debug("vmware inventory collected: %d GPU VMs", len(rows))
        except Exception as e:
            logger.error("vmware collect_vm_inventory failed: %s", e)
        finally:
            if si:
                Disconnect(si)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_all_vms(content) -> list:
    container = content.viewManager.CreateContainerView(
        content.rootFolder, [vim.VirtualMachine], True
    )
    vms = list(container.view)
    container.Destroy()
    return vms


def _has_gpu(vm) -> bool:
    if not vm.config or not vm.config.hardware:
        return False
    for device in vm.config.hardware.device:
        if isinstance(device, (vim.vm.device.VirtualPCIPassthrough,
                                vim.vm.device.VirtualSriovEthernetCard)):
            return True
    return False


def _extract_gpu_devices(vm) -> List[Dict]:
    devices = []
    for device in (vm.config.hardware.device or []):
        if isinstance(device, vim.vm.device.VirtualPCIPassthrough):
            devices.append({"type": "passthrough", "pci_id": str(device.key)})
    return devices


def _normalize_vm(vm) -> Dict:
    gpu_devices = _extract_gpu_devices(vm)
    esxi_host = ""
    cluster = ""
    try:
        esxi_host = vm.runtime.host.name if vm.runtime.host else ""
        parent = getattr(vm.runtime.host, "parent", None)
        cluster = parent.name if parent else ""
    except Exception:
        pass

    resource_pool = ""
    try:
        if vm.resourcePool:
            resource_pool = vm.resourcePool.name
    except Exception:
        pass

    return {
        "collected_at":  datetime.now(timezone.utc),
        "vm_name":       vm.name,
        "vm_uuid":       vm.config.uuid if vm.config else "",
        "vm_status":     str(vm.runtime.powerState) if vm.runtime else "unknown",
        "esxi_host":     esxi_host,
        "cluster":       cluster,
        "resource_pool": resource_pool,
        "guest_os":      (vm.config.guestFullName or "") if vm.config else "",
        "vcpu_count":    int(vm.config.hardware.numCPU) if vm.config else 0,
        "memory_mb":     int(vm.config.hardware.memoryMB) if vm.config else 0,
        "gpu_count":     len(gpu_devices),
        "gpu_type":      gpu_devices[0]["type"] if gpu_devices else "",
        "gpu_profile":   gpu_devices[0].get("profile", "") if gpu_devices else "",
        "gpu_pci_ids":   json.dumps([g["pci_id"] for g in gpu_devices]),
        "annotation":    (vm.config.annotation or "") if vm.config else "",
        "metadata":      json.dumps({}),
    }
