# note for HVC: will receive message from laptop with status codes in first byte

# TODO: parse current limit based on HVC implementation
# TODO: CAN error handling

import can
import time
import struct
from typing import Optional
from enum import Enum
from datetime import datetime
from threading import Event
from tkinter import *

MAX_VOLTAGE_LIMIT = 453.6
MAX_CURRENT_LIMIT = 14

HVC_BAUD_RATE = 500000
CHG_BAUD_RATE = 250000

# TX CAN IDs
CAN_ID_HEARTBEAT = 0x00C00000 # PLACEHOLDER
CAN_ID_HVC_TX = 0x00C00001 # PLACEHOLDER
CAN_ID_CHG_CMD = 0x1806E5F4 # from charger datasheet
# RX CAN IDs
CAN_ID_IO_SUMMARY = 0x004001F0 # bit 0 of byte 0 is SDC (0 for open, 1 for closed)
CAN_ID_HVC_STATE = 0x004001F1
CAN_ID_CURR_LIMIT = 0x004001F8
CAN_ID_CHG_STATUS = 0x18FF50E5 # from charger datasheet

# filters for messages from HVC
HVC_FILTERS = [
    {"can_id": CAN_ID_IO_SUMMARY, "can_mask": 0xFFFFFFFF, "extended": True},
    {"can_id": CAN_ID_HVC_STATE, "can_mask": 0xFFFFFFFF, "extended": True},
    {"can_id": CAN_ID_CURR_LIMIT, "can_mask": 0xFFFFFFFF, "extended": True},
]

# interval in seconds that messages must be sent to the charger
CHG_CMD_PERIOD = 1
# period in seconds for checking for messages
CYCLE_TIME = 0.01
# communication timeout in seconds
TIMEOUT = 5

# charging states
class State(Enum):
    IDLE = 0
    PRECHARGING = 1
    SET_CURR_LIMIT = 2
    CONFIRM_STARTED = 3
    CHARGING = 4
    CONFIRM_STOPPED = 5
    BALANCING = 6
    STOPPED = 7
    FAULTED = 8
    DONE = 9

# messages to HVC
class HVC_Tx(Enum):
    CHARGING_INIT = 0x01
    CHARGING_STARTED = 0x02
    CHARGING_STOPPED = 0x03

# HVC states
class HVC_State(Enum):
    RUNNING = 0x01
    CHARGING = 0x02
    BALANCING = 0x03
    ERRORED = 0x04

# charger control codes
class Chg_Ctrl(Enum):
    CHARGING = 0
    NOT_CHARGING = 1

class ChargingController:
    def __init__(self):
        self.voltage_limit = MAX_VOLTAGE_LIMIT
        self.current_limit = MAX_CURRENT_LIMIT
        self.status = StringVar(value="IDLE")
        self.messages = StringVar(value="")

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

        self.hvc_tx_msg: Optional[can.Message] = None
        self.hvc_rx_msg: Optional[can.Message] = None
        self.chg_status_msg: Optional[can.Message] = None

        self.state: Optional[State] = None

        self.now = 0.0
        self.last_rx_timestamp = 0.0
        self.last_tx_timestamp = 0.0

    def _can_init(self):
        """Set up RX FIFOs and TX messages to HVC and charger."""
        # instantiate FIFO buffers for RX messages
        self.hvc_rx_fifo = can.BufferedReader()
        self.hvc_notifier = can.Notifier(self.hvc_bus, [self.hvc_rx_fifo])
        self.chg_rx_fifo = can.BufferedReader()
        self.chg_notifier = can.Notifier(self.chg_bus, [self.chg_rx_fifo])
        self._log("RX FIFO buffers started")
        
        # start heartbeat message to HVC
        self.heartbeat_msg = can.Message(arbitration_id=CAN_ID_HEARTBEAT, data=[0], is_extended_id=True)
        self.heartbeat_tx = self.hvc_bus.send_periodic(self.heartbeat_msg, 1) # period of 1s
        self._log("Charging heartbeat started")

        # start control messages to charger at 1s interval; voltage and current limit set to 0, control set to not charging
        voltage_limit_scaled = int(self.voltage_limit * 10) # format required by charger
        voltage_limit_scaled_high_byte = voltage_limit_scaled >> 8
        voltage_limit_scaled_low_byte = voltage_limit_scaled & 0xFF
        self.chg_cmd_msg = can.Message(arbitration_id=CAN_ID_CHG_CMD, data=[voltage_limit_scaled_high_byte, voltage_limit_scaled_low_byte, 0, 0, Chg_Ctrl.NOT_CHARGING.value, 0, 0, 0], is_extended_id=True)
        self.chg_tx = self.chg_bus.send_periodic(self.chg_cmd_msg, CHG_CMD_PERIOD)
        self._log("Charger control messages started")

        # set up HVC tx message
        self.hvc_tx_msg = can.Message(arbitration_id=CAN_ID_HVC_TX, data=[0], is_extended_id=True)
    
    def _update_chg_ctrl(self, chg_ctrl: Chg_Ctrl):
        """Set control to charging or not charging in charger command message."""
        self.chg_cmd_msg.data[4] = chg_ctrl.value
        self.chg_tx.modify_data(self.chg_cmd_msg) # pcan supports modifiable messages
        
        self.last_tx_timestamp = time.time()

    def _send_hvc_msg(self, hvc_tx: HVC_Tx) -> bool:
        """
        Send message to HVC.
        
        Returns:
            True if transmission successful, False otherwise.
        """
        self.hvc_tx_msg.data[0] = hvc_tx.value
        
        try:
            self.hvc_bus.send(self.hvc_tx_msg)
            self.last_tx_timestamp = time.time()
            return True
        except can.CanOperationError:
            self._log("CAN error")
            self.state = State.FAULTED
            return False
    
    def _set_curr_limit(self):
        """Set current limit in charger command message."""
        hvc_current_limit = struct.unpack('<H', self.hvc_rx_msg.data[0:2])[0] # PLACEHOLDER

        # set current limit to the lower of HVC and user/max limit
        current_limit = hvc_current_limit if hvc_current_limit <= self.current_limit else self.current_limit
        
        current_limit_scaled = int(current_limit * 10) # required format for charger

        self.chg_cmd_msg.data[2] = current_limit_scaled >> 8 # current limit high byte
        self.chg_cmd_msg.data[3] = current_limit_scaled & 0xFF # current limit low byte
        self.chg_tx.modify_data(self.chg_cmd_msg)

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
    
    def _log(self, msg: str):
        """Print message with timestamp and display in GUI."""
        time = datetime.now().time()
        print(f"{time}\t\t{msg}")

        formatted_time = time.strftime("%H:%M:%S")
        self.messages.set(f"{msg} ({formatted_time})")
    
    def start_chg_can(self, channel: str) -> bool:
        """
        Start charger CAN bus.
        
        Returns:
            True if successful, False otherwise.
        """
        try:
            self.chg_bus = can.interface.Bus(interface='pcan', channel=channel, bitrate=CHG_BAUD_RATE)
            return True
        except Exception:
            return False

    def start_hvc_can(self, channel: str) -> bool:
        """Start HVC CAN bus.
        
        Returns:
            True if successful, False otherwise.
        """
        try:
            self.hvc_bus = can.interface.Bus(interface='pcan', channel=channel, bitrate=HVC_BAUD_RATE, can_filters=HVC_FILTERS)
            return True
        except Exception:
            return False
        
    def stop_chg_can(self) -> bool:
        try:
            self.chg_bus.shutdown()
            return True
        except Exception:
            return False

    def stop_hvc_can(self) -> bool:
        try:
            self.hvc_bus.shutdown()
            return True
        except Exception:
            return False

    def record_user_chg_limits(self, voltage_limit: Optional[float], current_limit: Optional[float]):
        """Save user-input voltage and current limits."""
        if voltage_limit is not None and voltage_limit <= MAX_VOLTAGE_LIMIT:
            self.voltage_limit = voltage_limit
        if current_limit is not None and current_limit <= MAX_CURRENT_LIMIT:
            self.current_limit = current_limit
    
    def main(self, stop: Event):
        """State machine for charging control."""
        self.status.set("INITIALIZING")
        
        self._can_init()
    
        self.state = State.IDLE

        while True:
            self.now = time.time()
            self.last_rx_timestamp = time.time()

            # check for message from HVC
            self.hvc_rx_msg = self.hvc_rx_fifo.get_message(timeout=0) # reads oldest received message; returns None if no message

            if self.hvc_rx_msg is not None:
                # check if SDC has opened
                if self.hvc_rx_msg.arbitration_id == CAN_ID_IO_SUMMARY and self.hvc_rx_msg.data[0] & 0x01 == 0:
                    self._log("Shutdown circuit opened")
                    self._update_chg_ctrl(Chg_Ctrl.NOT_CHARGING)
                    self._log("Charger set to not charging")
                    self.state = State.FAULTED
                    break
                # check if HVC has entered ERRORED state
                elif self.hvc_rx_msg.arbitration_id == CAN_ID_HVC_STATE and self.hvc_rx_msg.data[0] == HVC_State.ERRORED.value:
                    self._log("HVC in ERRORED state")
                    self._update_chg_ctrl(Chg_Ctrl.NOT_CHARGING)
                    self._log("Charger set to not charging")
                    self.state = State.FAULTED
                    break
            
            # check if stop button has been clicked
            if stop.is_set():
                self._log("Program execution stopped")
                self._update_chg_ctrl(Chg_Ctrl.NOT_CHARGING)
                self._log("Charger set to not charging")
                self.state = State.STOPPED
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
                    if self._send_hvc_msg(HVC_Tx.CHARGING_INIT):
                        self._log("Charging init message sent to HVC")
                    else:
                        break

                    self.state = State.PRECHARGING

                case State.PRECHARGING:
                    # check for confirmation message from HVC with voltage and current limit
                    if self.hvc_rx_msg is not None and self.hvc_rx_msg.arbitration_id == CAN_ID_HVC_STATE and self.hvc_rx_msg.data[0] == HVC_State.CHARGING.value:
                        self._log("HVC confirmed ready to start charging")
                        self.state = State.SET_CURR_LIMIT
                    
                    # check for communication timeout
                    elif self.now - self.last_tx_timestamp > TIMEOUT:
                        self._log("HVC not confirmed ready to start charging in time")
                        self.state = State.FAULTED
                        break

                case State.SET_CURR_LIMIT:
                    # check for current limit message from HVC
                    if self.hvc_rx_msg is not None and self.hvc_rx_msg.arbitration_id == CAN_ID_CURR_LIMIT:
                        self._set_curr_limit()
                        self._update_chg_ctrl(Chg_Ctrl.CHARGING)
                        self._log("Charger configured to start charging")
                        
                        self.state = State.CONFIRM_STARTED
                
                case State.CONFIRM_STARTED:
                    # check that charger status indicates charging has started
                    if self.chg_status_msg is not None and self.chg_status_msg[2] >> 7 == 0: # supposedly indicates charging, NEED TO TEST
                        self._log("Charging started by charger")

                        # confirm to HVC that charging has started
                        if self._send_hvc_msg(HVC_Tx.CHARGING_STARTED):
                            self._log("Charging started message sent to HVC")
                        else:
                            break                        

                        self.state = State.CHARGING
                        self.status.set("CHARGING")

                    # check for communication timeout
                    elif self.now - self.last_tx_timestamp > TIMEOUT:
                        self._log("Charging started message not received from charger in time")
                        self.state = State.FAULTED
                        break

                case State.CHARGING:
                    if self.hvc_rx_msg is not None:
                        # update current limit based on message from HVC
                        if self.hvc_rx_msg.arbitration_id == CAN_ID_CURR_LIMIT:
                            self._set_curr_limit()
                        
                        # check for change to balancing state
                        elif self.hvc_rx_msg.arbitration_id == CAN_ID_HVC_STATE and self.hvc_rx_msg.data[0] == HVC_State.BALANCING.value:
                            self._log("HVC state changed to balancing")
                            self._update_chg_ctrl(Chg_Ctrl.NOT_CHARGING)
                            self._log("Charger control changed to stop charging")
                            
                            self.state = State.CONFIRM_STOPPED

                case State.CONFIRM_STOPPED:
                    # check that charger status indicates charging has stopped
                    if self.chg_status_msg is not None and self.chg_status_msg[2] >> 7 == 1: # supposedly indicates discharging, NEED TO TEST
                        self._log("Charging stopped by charger")

                        # confirm to HVC that charging has stopped
                        if self._send_hvc_msg(HVC_Tx.CHARGING_STOPPED):
                            self._log("Charging stopped message sent to HVC")
                        else:
                            break

                        self.state = State.BALANCING
                        self.status.set("BALANCING")

                    # check for communication timeout
                    elif self.now - self.last_tx_timestamp > TIMEOUT:
                        self._log("Charging stopped message not received from charger in time")
                        state = State.FAULTED
                        break

                case State.BALANCING:
                    if self.hvc_rx_msg is not None and self.hvc_rx_msg.arbitration_id == CAN_ID_HVC_STATE:
                        # check for change to charging state
                        if self.hvc_rx_msg.data[0] == HVC_State.CHARGING.value: # cells not at max voltage after balancing
                            self._log("HVC state changed to charging")
                            self._update_chg_ctrl(Chg_Ctrl.CHARGING)
                            self._log("Charger control changed to start charging")

                            self.state = State.CONFIRM_STARTED

                        # check for change to running state
                        elif self.hvc_rx_msg.data[0] == HVC_State.RUNNING.value: # cells at max voltage, charging done
                            self._log("HVC state changed to running")

                            self.state = State.DONE
                            break

            time.sleep(CYCLE_TIME)

        match self.state:
            case State.STOPPED:
                self._cleanup()
                self._log("Stopped")
                self.status.set("STOPPED")
            case State.FAULTED:
                self._cleanup()
                self._log("Faulted")
                self.status.set("FAULTED")
            case State.DONE:
                self._cleanup()
                self._log("Done")
                self.status.set("DONE")
