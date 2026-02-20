"""
Unit tests for VMware adapter.

pyVmomi is mocked entirely so no vCenter connection is needed.
Tests cover: normalize functions, _has_gpu logic, and import-guard behaviour.
"""

import sys
import os
import json
import types
import pytest
from unittest.mock import MagicMock, patch

# ── Inject mock pyVmomi before the adapter is imported ───────────────────────
# This avoids requiring pyVmomi to be installed in the test environment.

def _make_mock_vim():
    vim = MagicMock()
    # Create minimal VirtualDevice hierarchy that _has_gpu checks against
    vim.vm.device.VirtualPCIPassthrough = type("VirtualPCIPassthrough", (), {})
    vim.vm.device.VirtualSriovEthernetCard = type("VirtualSriovEthernetCard", (), {})
    vim.VirtualMachine = type("VirtualMachine", (), {})
    return vim

_mock_vim = _make_mock_vim()

_pyVmomi_mock = types.ModuleType("pyVmomi")
_pyVmomi_mock.vim = _mock_vim

_pyVim_mock = types.ModuleType("pyVim")
_pyVim_connect_mock = types.ModuleType("pyVim.connect")
_pyVim_connect_mock.SmartConnect = MagicMock()
_pyVim_connect_mock.Disconnect = MagicMock()
_pyVim_mock.connect = _pyVim_connect_mock

sys.modules.setdefault("pyVmomi", _pyVmomi_mock)
sys.modules.setdefault("pyVim", _pyVim_mock)
sys.modules.setdefault("pyVim.connect", _pyVim_connect_mock)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.vmware_adapter import (
    VMwareAdapter,
    _has_gpu,
    _normalize_vm,
    _extract_gpu_devices,
)


# ─── Helpers to build mock VM objects ────────────────────────────────────────

def _make_pci_device():
    dev = _mock_vim.vm.device.VirtualPCIPassthrough()
    dev.__class__ = _mock_vim.vm.device.VirtualPCIPassthrough
    dev.key = 13000
    return dev


def _make_vm(name="vm-gpu-01", has_gpu=True):
    vm = MagicMock()
    vm.name = name
    vm.config.uuid = "aaaa-bbbb-cccc"
    vm.config.guestFullName = "Ubuntu 22.04"
    vm.config.annotation = "owner: kim"
    vm.config.hardware.numCPU = 16
    vm.config.hardware.memoryMB = 65536
    vm.config.hardware.device = [_make_pci_device()] if has_gpu else []
    vm.runtime.powerState = "poweredOn"
    vm.runtime.host.name = "esxi-gpu-02.internal"
    vm.runtime.host.parent.name = "cluster-a"
    vm.resourcePool.name = "AI-Research-Team"
    return vm


# ─── _has_gpu ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_has_gpu_true_when_pci_passthrough():
    vm = _make_vm(has_gpu=True)
    assert _has_gpu(vm) is True


@pytest.mark.unit
def test_has_gpu_false_when_no_gpu_device():
    vm = _make_vm(has_gpu=False)
    assert _has_gpu(vm) is False


@pytest.mark.unit
def test_has_gpu_false_when_config_none():
    vm = MagicMock()
    vm.config = None
    assert _has_gpu(vm) is False


# ─── _normalize_vm ───────────────────────────────────────────────────────────

@pytest.mark.unit
def test_normalize_vm_basic_fields():
    vm = _make_vm()
    row = _normalize_vm(vm)

    assert row["vm_name"] == "vm-gpu-01"
    assert row["vm_uuid"] == "aaaa-bbbb-cccc"
    assert row["vm_status"] == "poweredOn"
    assert row["esxi_host"] == "esxi-gpu-02.internal"
    assert row["cluster"] == "cluster-a"
    assert row["resource_pool"] == "AI-Research-Team"
    assert row["guest_os"] == "Ubuntu 22.04"
    assert row["vcpu_count"] == 16
    assert row["memory_mb"] == 65536
    assert row["annotation"] == "owner: kim"


@pytest.mark.unit
def test_normalize_vm_gpu_count():
    vm = _make_vm(has_gpu=True)
    row = _normalize_vm(vm)
    assert row["gpu_count"] == 1
    assert row["gpu_type"] == "passthrough"


@pytest.mark.unit
def test_normalize_vm_no_gpu():
    vm = _make_vm(has_gpu=False)
    row = _normalize_vm(vm)
    assert row["gpu_count"] == 0
    assert row["gpu_type"] == ""


@pytest.mark.unit
def test_normalize_vm_gpu_pci_ids_is_json_list():
    vm = _make_vm(has_gpu=True)
    row = _normalize_vm(vm)
    pci_ids = json.loads(row["gpu_pci_ids"])
    assert isinstance(pci_ids, list)
    assert len(pci_ids) == 1


@pytest.mark.unit
def test_normalize_vm_metadata_is_valid_json():
    vm = _make_vm()
    row = _normalize_vm(vm)
    parsed = json.loads(row["metadata"])
    assert isinstance(parsed, dict)


@pytest.mark.unit
def test_normalize_vm_handles_missing_resource_pool():
    vm = _make_vm()
    vm.resourcePool = None
    row = _normalize_vm(vm)
    assert row["resource_pool"] == ""


@pytest.mark.unit
def test_normalize_vm_handles_missing_host():
    vm = _make_vm()
    vm.runtime.host = None
    row = _normalize_vm(vm)
    assert row["esxi_host"] == ""


# ─── VMwareAdapter import guard ───────────────────────────────────────────────

@pytest.mark.unit
def test_vmware_adapter_raises_without_pyvmomi(mock_writer):
    """If pyVmomi is somehow unavailable, RuntimeError should be raised."""
    with patch("adapters.vmware_adapter.PYVMOMI_AVAILABLE", False):
        with pytest.raises(RuntimeError, match="pyVmomi"):
            VMwareAdapter(
                vcenter_url="https://vcenter.test",
                username="u",
                password="p",
                insecure=True,
                writer=mock_writer,
            )


@pytest.fixture
def mock_writer():
    return MagicMock()
