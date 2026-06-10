"""
LuxeGenie Device Health Handler

Collects device vitals from the LuxeGenie hardware:
  - local_ip: Device's current LAN IP address
  - device_name: Configured hostname / display name
  - firmware_version: Current software/firmware build version
  - battery_percentage: Real-time battery level (%) via UART
  - serial_number: Unique hardware identifier

Actions:
  - luxegenie.health       → Returns all 5 vitals as a single snapshot
  - luxegenie.battery      → Returns only battery percentage
  - luxegenie.serial       → Returns only serial number
  - luxegenie.firmware     → Returns only firmware version
  - luxegenie.network      → Returns only IP + hostname
"""

import subprocess
import socket
import platform
import os
import time
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("serverdeck.agent.handlers.luxegenie_health")


# ---------- Data Collection Functions ----------

def _get_local_ip() -> str:
    """Get device LAN IP address."""
    try:
        if platform.system() == "Linux":
            result = subprocess.run(
                ['hostname', '-I'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().split()[0]
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
    except Exception as e:
        logger.error(f"Failed to get local IP: {e}")
    return "unknown"


def _get_device_name() -> str:
    """Get configured hostname / display name."""
    try:
        return socket.gethostname()
    except Exception as e:
        logger.error(f"Failed to get device name: {e}")
    return "unknown"


def _get_firmware_version() -> str:
    """Get firmware/software build version from known file paths."""
    version_paths = [
        '/etc/version',
        '/etc/firmware_version',
        '/opt/health_agent/version.txt',
    ]
    for path in version_paths:
        try:
            if os.path.exists(path):
                with open(path, 'r') as f:
                    version = f.read().strip()
                    if version:
                        return version
        except Exception:
            continue
    return "unknown"


def _get_serial_number() -> str:
    """Get unique hardware serial number from device tree."""
    if platform.system() != "Linux":
        return "unavailable (not on Linux device)"
    try:
        result = subprocess.run(
            ['cat', '/proc/device-tree/serial-number'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().replace('\x00', '')
    except Exception as e:
        logger.error(f"Failed to get serial number: {e}")
    return "unknown"


def _get_battery_percentage() -> Optional[int]:
    """
    Read battery percentage via UART serial communication.
    
    TX Frame (8 bytes): [0x28, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00, 0x29]
    RX Frame (8 bytes): [0x28, 0x05, battery_percentage, 0x00, 0x00, 0x00, 0x00, 0x29]
    """
    if platform.system() != "Linux":
        return None

    try:
        import serial as pyserial
    except ImportError:
        logger.error("pyserial not installed — cannot read battery")
        return None

    try:
        possible_ports = [
            '/dev/ttyS2', '/dev/ttyS1', '/dev/ttyS0',
            '/dev/ttyAML0', '/dev/ttyAMA0'
        ]
        serial_port = None
        for port in possible_ports:
            if os.path.exists(port):
                serial_port = port
                break

        if not serial_port:
            logger.warning("No UART serial port found for battery reading")
            return None

        ser = pyserial.Serial(serial_port, 115200, timeout=1)
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        request_frame = bytes([0x28, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00, 0x29])
        ser.write(request_frame)
        ser.flush()
        time.sleep(0.5)

        response = ser.read(8)
        ser.close()

        if (len(response) == 8
                and response[0] == 0x28
                and response[1] == 0x05
                and response[-1] == 0x29):
            return response[2]

        logger.warning(f"Invalid UART response: {response.hex() if response else 'empty'}")
        return None

    except Exception as e:
        logger.error(f"Battery read error: {e}")
        return None


def _collect_all_vitals() -> dict:
    """Collect all 5 device vitals into a single dict."""
    return {
        "local_ip": _get_local_ip(),
        "device_name": _get_device_name(),
        "firmware_version": _get_firmware_version(),
        "battery_percentage": _get_battery_percentage(),
        "serial_number": _get_serial_number(),
        "timestamp": datetime.now().isoformat(),
    }


# ---------- Command Handlers ----------

async def handle_health(params: dict) -> dict:
    """
    Returns all LuxeGenie device vitals in a single snapshot.
    
    Action: luxegenie.health
    Params: (none required)
    """
    logger.info("Collecting LuxeGenie device health vitals...")
    vitals = _collect_all_vitals()
    return {"status": "success", "data": vitals}


async def handle_battery(params: dict) -> dict:
    """
    Returns only the battery percentage.
    
    Action: luxegenie.battery
    Params: (none required)
    """
    battery = _get_battery_percentage()
    return {
        "status": "success",
        "data": {
            "battery_percentage": battery,
            "timestamp": datetime.now().isoformat(),
        }
    }


async def handle_serial(params: dict) -> dict:
    """
    Returns only the hardware serial number.
    
    Action: luxegenie.serial
    Params: (none required)
    """
    serial = _get_serial_number()
    return {
        "status": "success",
        "data": {
            "serial_number": serial,
            "timestamp": datetime.now().isoformat(),
        }
    }


async def handle_firmware(params: dict) -> dict:
    """
    Returns only the firmware version.
    
    Action: luxegenie.firmware
    Params: (none required)
    """
    version = _get_firmware_version()
    return {
        "status": "success",
        "data": {
            "firmware_version": version,
            "timestamp": datetime.now().isoformat(),
        }
    }


async def handle_network(params: dict) -> dict:
    """
    Returns network identity (IP + hostname).
    
    Action: luxegenie.network
    Params: (none required)
    """
    return {
        "status": "success",
        "data": {
            "local_ip": _get_local_ip(),
            "device_name": _get_device_name(),
            "timestamp": datetime.now().isoformat(),
        }
    }
