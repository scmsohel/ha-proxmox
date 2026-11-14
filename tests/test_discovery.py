"""The inventory read from the device and entity registries of the Proxmox VE integration."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proxmox_panel.discovery import async_inventory, parse_identifier, slot_of

PROXMOX = "proxmoxve"


def _device(hass, entry, identifier, name, via=None):
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(PROXMOX, f"{entry.entry_id}_{identifier}")},
        name=name,
        configuration_url="https://192.168.1.10:8006/",
    )
    # The node it belongs to (async_get_or_create only takes via_device_id since Home Assistant 2026.10)
    return registry.async_update_device(device.id, via_device_id=via.id) if via else device


def _entity(hass, entry, device, domain, unique, disabled=False):
    return er.async_get(hass).async_get_or_create(
        domain,
        PROXMOX,
        f"{entry.entry_id}_{unique}",
        config_entry=entry,
        device_id=device.id,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION if disabled else None,
    )


def test_parse_identifier():
    # Built-in Proxmox VE identifier forms remain supported.
    assert parse_identifier("abc", "abc_node_node/pve1") == ("node", "node/pve1")
    assert parse_identifier("abc", "abc_vm_100") == ("vm", "100")
    assert parse_identifier("abc", "abc_container_200") == ("container", "200")
    assert parse_identifier("abc", "abc_storage_local-lvm") == ("storage", "local-lvm")
    # Proxmox VE Custom uses upper-case resource types and a different guest vocabulary.
    assert parse_identifier("abc", "abc_NODE_pve") == ("node", "pve")
    assert parse_identifier("abc", "abc_QEMU_100") == ("vm", "100")
    assert parse_identifier("abc", "abc_LXC_105") == ("container", "105")
    assert parse_identifier("abc", "abc_STORAGE_local-lvm") == ("storage", "local-lvm")
    assert parse_identifier("abc", "other_vm_100") is None
    assert parse_identifier("abc", "abc_disk_1") is None
    assert parse_identifier("abc", "abc_vm") is None


def test_slot_of_matches_the_longest_key_per_domain():
    assert slot_of("vm", "sensor", "e_100_vm_status") == "status"
    assert slot_of("vm", "binary_sensor", "e_100_status") == "running"
    assert slot_of("node", "binary_sensor", "e_node/pve1_node_backup_status") == "backup_problem"
    assert slot_of("node", "binary_sensor", "e_node/pve1_status") == "running"
    assert slot_of("node", "sensor", "e_node/pve1_node_memory_percentage") == "memory_pct"
    assert slot_of("node", "sensor", "e_node/pve1_node_max_memory") == "max_memory"
    assert slot_of("node", "sensor", "e_pve_backup_last_backup") == "last_backup"
    assert slot_of("container", "button", "e_200_snapshot_create") == "snapshot_create"
    assert slot_of("container", "button", "e_200_hibernate") is None
    assert slot_of("storage", "sensor", "e_pve1_local_storage_used_percentage") == "used_pct"
    assert slot_of("vm", "sensor", "e_100_something_else") is None


async def test_inventory(hass: HomeAssistant):
    assert async_inventory(hass).as_dict()["configured"] is False

    entry = MockConfigEntry(domain=PROXMOX, title="192.168.10.81:8006", state=ConfigEntryState.LOADED)
    entry.add_to_hass(hass)

    # Proxmox VE Custom identifiers and unique IDs.
    node = _device(hass, entry, "NODE_pve", "Node pve")
    for domain, unique in (
        ("sensor", "pve_cpu_used"),
        ("sensor", "pve_memory_used_perc"),
        ("sensor", "pve_disk_free"),
        ("binary_sensor", "pve_status"),
        ("button", "pve_reboot"),
    ):
        _entity(hass, entry, node, domain, unique)
    _entity(hass, entry, node, "sensor", "pve_backup_last_backup", disabled=True)

    qemu = _device(hass, entry, "QEMU_100", "QEMU Home-Assistant (100)", via=node)
    _entity(hass, entry, qemu, "sensor", "100_cpu_used")
    _entity(hass, entry, qemu, "sensor", "100_status")
    _entity(hass, entry, qemu, "binary_sensor", "100_status")
    _entity(hass, entry, qemu, "button", "100_shutdown")

    lxc = _device(hass, entry, "LXC_105", "LXC bot-manager (105)", via=node)
    _entity(hass, entry, lxc, "sensor", "105_cpu_used")
    _entity(hass, entry, lxc, "button", "105_restart")

    storage = _device(hass, entry, "STORAGE_pve/local", "Storage pve/local", via=node)
    _entity(hass, entry, storage, "sensor", "pve_local_storage_used_perc")
    _entity(hass, entry, storage, "binary_sensor", "pve_local_storage_active")

    # Core-style lower-case identifiers remain supported as before.
    core_node = _device(hass, entry, "node_node/pve1", "pve1")
    _entity(hass, entry, core_node, "sensor", "node/pve1_node_cpu")
    vm101 = _device(hass, entry, "vm_101", "win11", via=core_node)
    _entity(hass, entry, vm101, "button", "101_start")

    # An unrelated integration device is ignored.
    dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("other", "x")}, name="x")

    inv = async_inventory(hass).as_dict()
    assert inv["configured"] is True
    assert inv["entries"] == [
        {"entry_id": entry.entry_id, "title": "192.168.10.81:8006", "state": "loaded", "loaded": True}
    ]
    [node_item] = [item for item in inv["node"] if item["key"] == "pve"]
    assert node_item["name"] == "Node pve"
    assert set(node_item["entities"]) == {"cpu", "memory_pct", "disk", "running", "reboot"}
    assert node_item["entities"]["running"].startswith("binary_sensor.")
    assert node_item["disabled"] == ["last_backup"]

    [vm] = inv["vm"]
    assert vm["key"] == "100" and vm["name"] == "QEMU Home-Assistant (100)"
    assert vm["node"] == node.id
    assert set(vm["entities"]) == {"cpu", "status", "running", "shutdown"}

    [container] = inv["container"]
    assert container["key"] == "105"
    assert set(container["entities"]) == {"cpu", "restart"}

    [store] = inv["storage"]
    assert store["key"] == "pve/local"
    assert set(store["entities"]) == {"used_pct", "active"}

    [core_node_item] = [item for item in inv["node"] if item["key"] == "node/pve1"]
    assert core_node_item["name"] == "pve1"
    assert core_node_item["entities"]["cpu"].startswith("sensor.")
