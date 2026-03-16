# notes for HVC:
## send voltage and current limits as integers scaled by 10
## send status message with status codes in byte 7, voltage limit in bytes 0-1, and current limit in bytes 2-3
## will receive message from laptop with status codes in last byte

# TODO: CAN error handling

import can
import time
import struct
from typing import Optional
from enum import Enum
from datetime import datetime
from threading import Event

MAX_VOLTAGE_LIMIT = 453.6
MAX_CURRENT_LIMIT = 14

HVC_BAUD_RATE = 500000
CHG_BAUD_RATE = 250000

DEFAULT_HVC_CHANNEL = "PCAN_USBBUS1"
DEFAULT_CHG_CHANNEL = "PCAN_USBBUS2"

# TX CAN IDs
CAN_ID_HEARTBEAT = 0x00000000 # PLACEHOLDER
CAN_ID_HVC_CMD = 0x00000000 # PLACEHOLDER
CAN_ID_CHG_CMD = 0x1806E5F4 # from charger datasheet
# RX CAN IDs
CAN_ID_HVC_STATUS = 0x00000000 # PLACEHOLDER
CAN_ID_CHG_STATUS = 0x18FF50E5 # from charger datasheet

# filter for HVC status message
HVC_FILTER = [{"can_id": CAN_ID_HVC_STATUS, "can_mask": 0xFFFFFFFF, "extended": True}]

# interval in seconds that messages must be sent to the charger
CHG_CMD_PERIOD = 1
# period in seconds for checking for messages
CYCLE_TIME = 0.05
# communication timeout in seconds
TIMEOUT = 5

# charging states
class State(Enum):
    IDLE = 0
    PRECHARGING = 1
    CONFIRM_STARTED = 2
    CHARGING = 3
    CONFIRM_STOPPED = 4
    BALANCING = 5
    FAULTED = 6
    DONE = 7

# messages to HVC
class HVC_Cmd(Enum):
    CHARGING_INIT = 0x01
    CHARGING_STARTED = 0x02
    CHARGING_STOPPED = 0x03

# messages from HVC
class HVC_Status(Enum):
    SHUT_DOWN = 0x00
    START_CHARGING = 0x01
    START_BALANCING = 0x02
    KEEP_CHARGING = 0x03
    DONE_CHARGING = 0x04

# charger control codes
class Chg_Ctrl(Enum):
    CHARGING = 0
    NOT_CHARGING = 1

class ChargingController:
    def __init__(self):
        self.voltage_limit = MAX_VOLTAGE_LIMIT
        self.current_limit = MAX_CURRENT_LIMIT
        self.status = "IDLE"
        self.messages = ""

        self.hvc_channel = DEFAULT_HVC_CHANNEL
        self.chg_channel = DEFAULT_CHG_CHANNEL

        self.hvc_bus: Optional[can.Bus] = None
        self.chg_bus: Optional[can.Bus] = None
        
        self.hvc_rx_fifo: Optional[can.BufferedReader] = None
        self.hvc_notifier: Optional[can.Notifier] = None
        self.chg_rx_fifo: Optional[can.BufferedReader] = None
        self.chg_notifier: Optional[can.Notifier] = None

        self.heartbeat_msg: Optional[can.Message] = None
        self.heartbeat_tx: Optional[can.CyclicSendTaskABC] = None

        self.chg_cmd_msg: Optional[can.Message] = None
        self.chg_tx: Optional[can.CyclicSendTaskABC] = None

        self.hvc_cmd_msg: Optional[can.Message] = None
        self.hvc_status_msg: Optional[can.Message] = None
        self.chg_status_msg: Optional[can.Message] = None

        self.state: Optional[State] = None

        self.now = 0.0
        self.last_rx_timestamp = 0.0
        self.last_tx_timestamp = 0.0

    def _can_bus_init(self):
        """Start CAN buses and FIFO buffers."""
        # instantiate CAN buses
        self.hvc_bus = can.interface.Bus(interface='pcan', channel=self.hvc_channel, bitrate=HVC_BAUD_RATE, can_filters=HVC_FILTER)
        self.chg_bus = can.interface.Bus(interface='pcan', channel=self.chg_channel, bitrate=CHG_BAUD_RATE)
        self._log("CAN buses started")

        # instantiate FIFO buffers for RX messages
        self.hvc_rx_fifo = can.BufferedReader()
        self.hvc_notifier = can.Notifier(self.hvc_bus, [self.hvc_rx_fifo])
        self.chg_rx_fifo = can.BufferedReader()
        self.chg_notifier = can.Notifier(self.chg_bus, [self.chg_rx_fifo])
        self._log("RX FIFO buffers started")

    def _can_message_init(self):
        """Set up CAN messages to HVC and charger."""
        # start heartbeat message to HVC
        self.heartbeat_msg = can.Message(arbitration_id=CAN_ID_HEARTBEAT, data=[0, 0, 0, 0, 0, 0, 0, 0], is_extended_id=True)
        self.heartbeat_tx = self.hvc_bus.send_periodic(self.heartbeat_msg, 1) # period of 1s
        self._log("Charging heartbeat started")

        # start control messages to charger at 1s interval; voltage and current limit set to 0, control set to not charging
        self.chg_cmd_msg = can.Message(arbitration_id=CAN_ID_CHG_CMD, data=[0, 0, 0, 0, Chg_Ctrl.NOT_CHARGING.value, 0, 0, 0], is_extended_id=True)
        self.chg_tx = self.chg_bus.send_periodic(self.chg_cmd_msg, CHG_CMD_PERIOD)
        self._log("Charger control messages started")

        # set up HVC command message
        self.hvc_cmd_msg = can.Message(arbitration_id=CAN_ID_HVC_CMD, data=[0, 0, 0, 0, 0, 0, 0, 0], is_extended_id=True)
    
    def _update_chg_ctrl(self, chg_ctrl: Chg_Ctrl):
        """Set control to charging or not charging in charger command message."""
        if isinstance(self.chg_tx, can.ModifiableCyclicTaskABC):
            self.chg_cmd_msg.data[4] = chg_ctrl.value
            self.chg_tx.modify_data(self.chg_cmd_msg)
        else:
            self.chg_tx.stop()
            self.chg_cmd_msg.data[4] = chg_ctrl.value
            self.chg_tx = self.chg_bus.send_periodic(self.chg_cmd_msg, CHG_CMD_PERIOD)
        
        self.last_tx_timestamp = time.time()

    def _send_hvc_msg(self, hvc_cmd: HVC_Cmd) -> bool:
        """
        Send message to HVC.
        
        Returns:
            True if transmission successful, False otherwise.
        """
        self.hvc_cmd_msg.data[7] = hvc_cmd.value
        
        try:
            self.hvc_bus.send(self.hvc_cmd_msg)
            self.last_tx_timestamp = time.time()
            return True
        except can.CanOperationError:
            self._log("CAN error")
            self.state = State.FAULTED
            return False
    
    def _set_chg_limits(self):
        """Set the voltage and current limits in charger command message."""
        hvc_voltage_limit = struct.unpack('<H', self.hvc_status_msg.data[0:2])[0] / 10.0 # scaled by 10
        hvc_current_limit = struct.unpack('<H', self.hvc_status_msg.data[2:4])[0] / 10.0

        # set voltage and current limits to the lower of HVC and user limits
        self.voltage_limit = hvc_voltage_limit if hvc_voltage_limit <= self.voltage_limit else self.voltage_limit
        self.current_limit = hvc_current_limit if hvc_current_limit <= self.current_limit else self.current_limit

        voltage_limit_scaled = int(self.voltage_limit * 10) # required format for charger
        current_limit_scaled = int(self.current_limit * 10)

        if isinstance(chg_tx, can.ModifiableCyclicTaskABC):
            self.chg_cmd_msg.data[0] = voltage_limit_scaled >> 8 # voltage limit high byte
            self.chg_cmd_msg.data[1] = voltage_limit_scaled & 0xFF # voltage limit low byte
            self.chg_cmd_msg.data[2] = current_limit_scaled >> 8 # current limit high byte
            self.chg_cmd_msg.data[3] = current_limit_scaled & 0xFF # current limit low byte
            chg_tx.modify_data(self.chg_cmd_msg)
        else:
            chg_tx.stop()
            self.chg_cmd_msg.data[0] = voltage_limit_scaled >> 8
            self.chg_cmd_msg.data[1] = voltage_limit_scaled & 0xFF
            self.chg_cmd_msg.data[2] = current_limit_scaled >> 8
            self.chg_cmd_msg.data[3] = current_limit_scaled & 0xFF
            chg_tx = self.chg_bus.send_periodic(self.chg_cmd_msg, CHG_CMD_PERIOD)

    def _decode_chg_fault(status: int):
        """Decode fault in charger status message."""
        err = ""

        if status & 0b1 == 1:
            err = err + "\n\t\t\t\tHardware failure"
        if status >> 1 & 0b1 == 1:
            err = err + "\n\t\t\t\tOvertemperature protection"
        if status >> 2 & 0b1 == 1:
            err = err + "\n\t\t\t\tInput voltage is wrong"
        if status >> 3 & 0b1 == 1:
            err = err + "\n\t\t\t\tBattery is not connected properly"
        if status >> 4 & 0b1 == 1:
            err = err + "\n\t\t\t\tCommunication timeout"

        return err
    
    def _cleanup(self):
        """Clean up resources at end of program."""
        self.heartbeat_tx.stop()
        self.chg_tx.stop()
        self.hvc_notifier.stop()
        self.hvc_rx_fifo.stop()
        self.chg_notifier.stop()
        self.chg_rx_fifo.stop()
        self.hvc_bus.shutdown()
        self.chg_bus.shutdown()
    
    # print with timestamp
    def _log(self, msg: str):
        """Print message with timestamp and display in GUI."""
        time = datetime.now().time()
        print(f"{time}\t\t{msg}")

        formatted_time = time.strftime("%H:%M:%S")
        self.messages = f"{msg} ({formatted_time})"
    
    def record_user_chg_limits(self, voltage_limit: Optional[float], current_limit: Optional[float]):
        """Save user-input voltage and current limits."""
        if voltage_limit is not None:
            self.voltage_limit = voltage_limit
        if current_limit is not None:
            self.current_limit = current_limit
    
    def main(self, stop: Event):
        """State machine for charging control."""
        self.status = "INITIALIZING"
        
        self._can_bus_init()
        self._can_message_init()
    
        self.state = State.IDLE

        while True:
            self.now = time.time()
            self.last_rx_timestamp = time.time()

            # check for message from HVC
            self.hvc_status_msg = self.hvc_rx_fifo.get_message(timeout=0) # reads oldest received message; returns None if no message

            # check if SDC has opened
            if self.hvc_status_msg is not None and self.hvc_status_msg.data[7] == HVC_Status.SHUT_DOWN.value:
                self._log("Shutdown circuit opened")
                self._update_chg_ctrl(Chg_Ctrl.NOT_CHARGING)
                self._log("Charger set to not charging")
                self.state = State.FAULTED
                break
            
            # check if stop button has been clicked
            if stop.is_set():
                self._log("Program execution stopped")
                self._update_chg_ctrl(Chg_Ctrl.NOT_CHARGING)
                self._log("Charger set to not charging")
                self.state = State.FAULTED
                break

            # check for status message from charger (should be sent at an interval of 1s)
            self.chg_status_msg = self.chg_rx_fifo.get_message(timeout=0)

            if self.chg_status_msg is not None:
                self.last_rx_timestamp = time.time()
                # check charger status info for faults
                if self.chg_status_msg.data[4] != 0: # status byte; 1s indicate faults
                    self._log("Charger fault:" + self._decode_chg_fault(self.chg_status_msg.data[4]))
                    self.state = State.FAULTED
                    break
            # check for communication timeout
            elif self.now - self.last_rx_timestamp > TIMEOUT:
                    self._log("Charger communication timeout")
                    self.state = State.FAULTED
                    break

            match self.state:
                case State.IDLE:
                    # send charging init message to HVC
                    if self._send_hvc_msg(HVC_Cmd.CHARGING_INIT):
                        self._log("Charging init message sent to HVC")
                    else:
                        break

                    self.state = State.PRECHARGING

                case State.PRECHARGING:
                    # check for confirmation message from HVC with voltage and current limit
                    if self.hvc_status_msg is not None and self.hvc_status_msg.data[7] == HVC_Status.START_CHARGING.value:
                        self._log("Start charging message received from HVC")
                        self._set_chg_limits()
                        self._update_chg_ctrl(Chg_Ctrl.CHARGING)
                        self._log("Charger configured to start charging")

                        self.state = State.CONFIRM_STARTED
                    
                    # check for communication timeout
                    elif self.now - self.last_tx_timestamp > TIMEOUT:
                        self._log("Start charging message not received from HVC in time")
                        self.state = State.FAULTED
                        break

                case State.CONFIRM_STARTED:
                    # check that charger status indicates charging has started
                    if self.chg_status_msg is not None and self.chg_status_msg[2] >> 7 == 0: # supposedly indicates charging, NEED TO TEST
                        self._log("Charging started by charger")

                        # confirm to HVC that charging has started
                        if self._send_hvc_msg(HVC_Cmd.CHARGING_STARTED):
                            self._log("Charging started message sent to HVC")
                        else:
                            break                        

                        self.state = State.CHARGING
                        self.status = "CHARGING"

                    # check for communication timeout
                    elif self.now - self.last_tx_timestamp > TIMEOUT:
                        self._log("Charging started message not received from charger in time")
                        self.state = State.FAULTED
                        break

                case State.CHARGING:
                    # check for balancing message from HVC
                    if self.hvc_status_msg is not None and self.hvc_status_msg.data[7] == HVC_Status.START_BALANCING.value:
                        self._log("Balancing message received from HVC")
                        self._update_chg_ctrl(Chg_Ctrl.NOT_CHARGING)
                        self._log("Charger control changed to stop charging")
                        
                        self.state = State.CONFIRM_STOPPED

                case State.CONFIRM_STOPPED:
                    # check that charger status indicates charging has stopped
                    if self.chg_status_msg is not None and self.chg_status_msg[2] >> 7 == 1: # supposedly indicates discharging, NEED TO TEST
                        self._log("Charging stopped by charger")

                        # confirm to HVC that charging has stopped
                        if self._send_hvc_msg(HVC_Cmd.CHARGING_STOPPED):
                            self._log("Charging stopped message sent to HVC")
                        else:
                            break

                        self.state = State.BALANCING
                        self.status = "BALANCING"

                    # check for communication timeout
                    elif self.now - self.last_tx_timestamp > TIMEOUT:
                        self._log("Charging stopped message not received from charger in time")
                        state = State.FAULTED
                        break

                case State.BALANCING:
                    if self.hvc_status_msg is not None:
                        # if receive charging message from HVC
                        if self.hvc_status_msg.data[7] == HVC_Status.KEEP_CHARGING.value: # cells not at max voltage after balancing
                            self._log("Charging message received from HVC")
                            self._update_chg_ctrl(Chg_Ctrl.CHARGING)
                            self._log("Charger control changed to start charging")

                            self.state = State.CONFIRM_STARTED

                        # if receive done message from HVC
                        elif self.hvc_status_msg.data[7] == HVC_Status.DONE_CHARGING.value: # cells at max voltage
                            self._log("Done charging message received from HVC")

                            self.state = State.DONE
                            break

            time.sleep(CYCLE_TIME)

        match state:
            case State.FAULTED:
                self._cleanup()
                self._log("Faulted")
                self.status = "FAULTED"
            case State.DONE:
                self._cleanup()
                self._log("Done")
                self.status = "DONE"
