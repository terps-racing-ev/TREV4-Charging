import can
import struct
import time
from datetime import datetime
from enum import Enum
from threading import Event
from tkinter import StringVar
from typing import Optional

MAX_VOLTAGE_LIMIT = 453.6
MAX_CURRENT_LIMIT = 14

CHG_BAUD_RATE = 250000

CAN_ID_CHG_CMD = 0x1806E5F4  # From charger datasheet.
CAN_ID_CHG_STATUS = 0x18FF50E5  # From charger datasheet.

CHG_FILTER = [
    {"can_id": CAN_ID_CHG_STATUS, "can_mask": 0xFFFFFFFF, "extended": True}
]

CHG_CMD_PERIOD = 1
CYCLE_TIME = 0.01
TIMEOUT = 5


class Chg_Ctrl(Enum):
    CHARGING = 0
    NOT_CHARGING = 1


class ChargingController:
    def __init__(self):
        self.voltage_limit = MAX_VOLTAGE_LIMIT
        self.current_limit = MAX_CURRENT_LIMIT

        self.chg_bus: Optional[can.Bus] = None
        self.chg_rx_fifo: Optional[can.BufferedReader] = None
        self.chg_notifier: Optional[can.Notifier] = None
        self.chg_cmd_msg: Optional[can.Message] = None
        self.chg_tx: Optional[can.CyclicSendTaskABC] = None
        self.chg_status_msg: Optional[can.Message] = None

        self.last_chg_status_timestamp = 0.0

        self.status = StringVar(value="IDLE")
        self.issue = StringVar(value="")
        self.chg_ctrl_voltage = StringVar(value="")
        self.chg_ctrl_current = StringVar(value="")
        self.chg_status_voltage = StringVar(value="")
        self.chg_status_current = StringVar(value="")
        self.msg1 = StringVar(value="")
        self.msg2 = StringVar(value="")
        self.msg3 = StringVar(value="")
        self.msg4 = StringVar(value="")

    def _clear_issue(self):
        self.issue.set("")

    def _set_issue(self, msg: str):
        self.issue.set(msg)

    def _report_error(self, context: str, exc: Optional[Exception] = None):
        msg = context if exc is None else f"{context}: {exc}"
        self._set_issue(msg)
        self._log(msg)

    def _start_charger_messages(self):
        """Start the charger command and status receiver."""
        voltage_scaled = int(self.voltage_limit * 10)
        current_scaled = int(self.current_limit * 10)
        self.chg_cmd_msg = can.Message(
            arbitration_id=CAN_ID_CHG_CMD,
            data=[
                voltage_scaled >> 8,
                voltage_scaled & 0xFF,
                current_scaled >> 8,
                current_scaled & 0xFF,
                Chg_Ctrl.CHARGING.value,
                0,
                0,
                0,
            ],
            is_extended_id=True,
        )
        self.chg_tx = self.chg_bus.send_periodic(self.chg_cmd_msg, CHG_CMD_PERIOD)
        self.chg_ctrl_voltage.set(f"{self.voltage_limit} V")
        self.chg_ctrl_current.set(f"{self.current_limit} A")
        self._log("Charger control messages started")

        self.chg_rx_fifo = can.BufferedReader()
        self.chg_notifier = can.Notifier(self.chg_bus, [self.chg_rx_fifo])
        self._log("Charger status receiver started")

    def _update_chg_ctrl(self, chg_ctrl: Chg_Ctrl):
        """Set the charger to charging or not charging."""
        self.chg_cmd_msg.data[4] = chg_ctrl.value
        self.chg_tx.modify_data(self.chg_cmd_msg)

    def _update_chg_status(self):
        """Decode actual charger voltage and current from its status message."""
        voltage = struct.unpack(">H", self.chg_status_msg.data[0:2])[0] / 10.0
        current = struct.unpack(">H", self.chg_status_msg.data[2:4])[0] / 10.0
        self.chg_status_voltage.set(f"{voltage} V")
        self.chg_status_current.set(f"{current} A")

    def _decode_chg_fault(self, status: int):
        faults = []
        fault_names = [
            "Hardware failure",
            "Overtemperature protection",
            "Input voltage is wrong",
            "Battery is not connected properly",
            "Communication timeout",
        ]
        for bit, name in enumerate(fault_names):
            if status & (1 << bit):
                faults.append(name)
        return ", ".join(faults)

    def _cleanup(self):
        """Stop periodic tasks, receivers, and the charger CAN bus."""
        if self.chg_tx is not None:
            self.chg_tx.stop()
            self.chg_tx = None
        if self.chg_notifier is not None:
            self.chg_notifier.stop()
            self.chg_notifier = None
        if self.chg_rx_fifo is not None:
            self.chg_rx_fifo.stop()
            self.chg_rx_fifo = None
        if self.chg_bus is not None:
            self.chg_bus.shutdown()
            self.chg_bus = None

    def _log(self, msg: str):
        timestamp = datetime.now().time()
        print(f"{timestamp}\t{msg}")
        self._update_messages(f"{msg} ({timestamp.strftime('%H:%M:%S')})")

    def _update_messages(self, msg: str):
        self.msg4.set(self.msg3.get())
        self.msg3.set(self.msg2.get())
        self.msg2.set(self.msg1.get())
        self.msg1.set(msg)

    def start_chg_can(self, interface: str, channel: str) -> bool:
        try:
            self.chg_bus = can.interface.Bus(
                interface=interface,
                channel=channel,
                bitrate=CHG_BAUD_RATE,
                can_filters=CHG_FILTER,
            )
            self._clear_issue()
            return True
        except Exception as exc:
            self._report_error("Failed to connect to charger CAN bus", exc)
            return False

    def stop_chg_can(self) -> bool:
        try:
            if self.chg_bus is None:
                return True
            self.chg_bus.shutdown()
            self.chg_bus = None
            self._clear_issue()
            return True
        except Exception as exc:
            self._report_error("Failed to disconnect charger CAN bus", exc)
            return False

    def record_user_chg_limits(
        self, voltage_limit: Optional[float], current_limit: Optional[float]
    ):
        """Save valid user-input voltage and current limits."""
        if voltage_limit is not None and 0 < voltage_limit <= MAX_VOLTAGE_LIMIT:
            self.voltage_limit = voltage_limit
        if current_limit is not None and 0 < current_limit <= MAX_CURRENT_LIMIT:
            self.current_limit = current_limit

    def main(self, stop: Event):
        """Run charger control until stopped, faulted, or communication times out."""
        self.status.set("INITIALIZING")
        self._clear_issue()

        try:
            self._start_charger_messages()
            self.last_chg_status_timestamp = time.time()
            self.status.set("CHARGING")

            while not stop.is_set():
                self.chg_status_msg = self.chg_rx_fifo.get_message(timeout=0)
                if self.chg_status_msg is not None:
                    self.last_chg_status_timestamp = time.time()
                    self._update_chg_status()
                    fault_status = self.chg_status_msg.data[4]
                    if fault_status != 0:
                        fault = self._decode_chg_fault(fault_status)
                        self._update_chg_ctrl(Chg_Ctrl.NOT_CHARGING)
                        self._log("Charger set to not charging")
                        self._set_issue(f"Charger fault: {fault}")
                        self._log(f"Charger fault: {fault}")
                        self.status.set("FAULTED")
                        break
                elif time.time() - self.last_chg_status_timestamp > TIMEOUT:
                    self._set_issue("Charger communication timeout")
                    self._log("Charger communication timeout")
                    self.status.set("FAULTED")
                    break

                time.sleep(CYCLE_TIME)

            if stop.is_set() and self.status.get() != "FAULTED":
                self._update_chg_ctrl(Chg_Ctrl.NOT_CHARGING)
                self._log("Charger set to not charging")
                self.status.set("STOPPED")
        except Exception as exc:
            self._report_error("Charging controller fault", exc)
            self.status.set("FAULTED")
        finally:
            self._cleanup()
            if self.status.get() == "STOPPED":
                self._log("Stopped")
            elif self.status.get() == "FAULTED":
                self._log("Faulted")
