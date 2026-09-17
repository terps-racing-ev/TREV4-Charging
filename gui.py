from tkinter import *
from tkinter import messagebox
from tkinter.ttk import *
import threading

import can

from charging import ChargingController, MAX_CURRENT_LIMIT, MAX_VOLTAGE_LIMIT

WINDOW_WIDTH = 550
WINDOW_HEIGHT = 500


class ChargingGUI:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("TREV4 Charging")
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")

        self.controller = ChargingController()
        self.stop = threading.Event()

        self.can_interfaces = ["pcan", "socketcan"]
        self.available_interfaces = self._detect_available_interfaces()
        self.available_channels = self._detect_available_channels()
        self.chg_connected = False

        self._configure_styles()
        self._create_widgets()

    def _show_controller_issue(self, title: str, fallback: str):
        messagebox.showerror(title, self.controller.issue.get() or fallback)

    def _detect_available_interfaces(self):
        try:
            configs = can.detect_available_configs()
            return list({
                config["interface"]
                for config in configs
                if config.get("interface") in self.can_interfaces
            })
        except Exception:
            return []

    def _detect_available_channels(self):
        try:
            configs = can.detect_available_configs()
            return [
                config["channel"]
                for config in configs
                if config.get("interface") in self.available_interfaces
            ]
        except Exception:
            return []

    def _configure_styles(self):
        style = Style()
        style.configure("Start.TButton", padding=10, font=("Arial", 10), foreground="#07ae17")
        style.configure("Stop.TButton", padding=10, font=("Arial", 10), foreground="#f51b07")
        style.configure("TButton", padding=10, font=("Arial", 10))

    def _create_widgets(self):
        main_frame = Frame(self.root, padding=10)
        main_frame.grid(row=0, column=0, sticky="nsew")
        self._create_connection_section(main_frame)
        self._create_charging_section(main_frame)

    def _create_connection_section(self, main_frame: Frame):
        frame = LabelFrame(main_frame, text="Charger Connection", padding="10")
        frame.grid(row=0, column=0, sticky="we", pady=(0, 10))

        Label(frame, text="Interface:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.chg_interface_var = StringVar()
        self.chg_interface_combo = Combobox(
            frame, textvariable=self.chg_interface_var,
            values=self.available_interfaces, state="readonly", width=17,
        )
        self.chg_interface_combo.grid(row=0, column=1, sticky="w", padx=5, pady=5)

        Label(frame, text="Channel:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.chg_channel_var = StringVar()
        self.chg_channel_combo = Combobox(
            frame, textvariable=self.chg_channel_var,
            values=self.available_channels, state="readonly", width=17,
        )
        self.chg_channel_combo.grid(row=1, column=1, sticky="w", padx=5, pady=5)

        Label(frame, text="Bitrate:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        bitrate = Entry(frame, justify="left")
        bitrate.grid(row=2, column=1, sticky="w", padx=5, pady=5)
        bitrate.insert(0, "250 kbit/s")
        bitrate.config(state="readonly")

        self.chg_connect_btn = Button(
            frame, text="CONNECT", style="Start.TButton", command=self._on_chg_connect
        )
        self.chg_connect_btn.grid(row=0, column=2, rowspan=3, padx=20, pady=5)

    def _create_charging_section(self, main_frame: Frame):
        frame = LabelFrame(main_frame, text="Charging", padding="10")
        frame.grid(row=1, column=0, sticky="we", pady=(0, 10))

        Label(frame, text="Voltage Limit:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.voltage_limit_entry = Entry(frame, justify="right", width=15)
        self.voltage_limit_entry.grid(row=0, column=1, padx=5, pady=5)
        Label(frame, text="V").grid(row=0, column=2, padx=2, pady=5)

        Label(frame, text="Current Limit:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.current_limit_entry = Entry(frame, justify="right", width=15)
        self.current_limit_entry.grid(row=1, column=1, padx=5, pady=5)
        Label(frame, text="A").grid(row=1, column=2, padx=2, pady=5)

        Label(frame, text="Status:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        Label(frame, textvariable=self.controller.status).grid(row=2, column=1, padx=5, pady=5, sticky="w")
        Label(frame, text="Issue:").grid(row=3, column=0, padx=5, pady=5, sticky="nw")
        Label(frame, textvariable=self.controller.issue, wraplength=320, justify="left").grid(
            row=3, column=1, columnspan=3, padx=5, pady=5, sticky="w"
        )

        Label(frame, text="Charger Control:").grid(row=4, column=0, padx=5, pady=5, sticky="w")
        Label(frame, textvariable=self.controller.chg_ctrl_voltage).grid(row=4, column=1, padx=5, pady=5, sticky="w")
        Label(frame, textvariable=self.controller.chg_ctrl_current).grid(row=4, column=2, padx=5, pady=5, sticky="w")

        Label(frame, text="Charger Status:").grid(row=5, column=0, padx=5, pady=5, sticky="w")
        Label(frame, textvariable=self.controller.chg_status_voltage).grid(row=5, column=1, padx=5, pady=5, sticky="w")
        Label(frame, textvariable=self.controller.chg_status_current).grid(row=5, column=2, padx=5, pady=5, sticky="w")

        Label(frame, text="Messages:").grid(row=6, column=0, padx=5, pady=5, sticky="nw")
        for row, variable in enumerate((self.controller.msg1, self.controller.msg2, self.controller.msg3, self.controller.msg4), start=6):
            Label(frame, textvariable=variable).grid(row=row, column=1, columnspan=4, padx=5, pady=2, sticky="w")

        self.start_btn = Button(frame, text="START", style="Start.TButton", command=self._on_start)
        self.start_btn.grid(column=4, row=0, rowspan=2, padx=(20, 10), pady=5)
        self.stop_btn = Button(frame, text="STOP", style="Stop.TButton", state="disabled", command=self._on_stop)
        self.stop_btn.grid(column=5, row=0, rowspan=2, padx=10, pady=5)
        self.reset_btn = Button(frame, text="RESET", command=self._on_reset)

    def _on_chg_connect(self):
        if not self.chg_connected:
            interface = self.chg_interface_var.get()
            channel = self.chg_channel_var.get()
            if not interface or not channel:
                messagebox.showerror("Connection Error", "Select a charger CAN interface and channel.")
                return
            if self.controller.start_chg_can(interface, channel):
                self.chg_connected = True
                self.chg_connect_btn.config(text="DISCONNECT", style="Stop.TButton")
                self.chg_interface_combo.config(state="disabled")
                self.chg_channel_combo.config(state="disabled")
            else:
                self._show_controller_issue("Connection Error", "Failed to connect to charger CAN bus.")
        elif self.controller.stop_chg_can():
            self.chg_connected = False
            self.chg_connect_btn.config(text="CONNECT", style="Start.TButton")
            self.chg_interface_combo.config(state="readonly")
            self.chg_channel_combo.config(state="readonly")
        else:
            self._show_controller_issue("Connection Error", "Failed to disconnect charger CAN bus.")

    def _on_start(self):
        if not self.chg_connected:
            messagebox.showerror("Connection Error", "Connect the charger CAN bus before starting.")
            return

        try:
            voltage = float(self.voltage_limit_entry.get()) if self.voltage_limit_entry.get() else None
            current = float(self.current_limit_entry.get()) if self.current_limit_entry.get() else None
        except ValueError:
            messagebox.showerror("Input Error", "Voltage and current limits must be numeric values.")
            return

        if voltage is not None and not 0 < voltage <= MAX_VOLTAGE_LIMIT:
            messagebox.showerror("Input Error", "Voltage limit must be greater than 0 and within the charger limit.")
            return
        if current is not None and not 0 < current <= MAX_CURRENT_LIMIT:
            messagebox.showerror("Input Error", "Current limit must be greater than 0 and within the charger limit.")
            return

        self.controller.record_user_chg_limits(voltage, current)
        self.voltage_limit_entry.config(state="disabled")
        self.current_limit_entry.config(state="disabled")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        threading.Thread(target=self._run_charging_program, args=(self.stop,), daemon=True).start()

    def _on_stop(self):
        self.stop.set()

    def _on_reset(self):
        self.reset_btn.grid_forget()
        self.start_btn.grid(column=4, row=0, rowspan=2, padx=(20, 10), pady=5)
        self.stop_btn.grid(column=5, row=0, rowspan=2, padx=10, pady=5)
        self.voltage_limit_entry.config(state="normal")
        self.current_limit_entry.config(state="normal")
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.stop.clear()

        for variable in (
            self.controller.status, self.controller.issue,
            self.controller.chg_ctrl_voltage, self.controller.chg_ctrl_current,
            self.controller.chg_status_voltage, self.controller.chg_status_current,
            self.controller.msg1, self.controller.msg2, self.controller.msg3, self.controller.msg4,
        ):
            variable.set("IDLE" if variable is self.controller.status else "")

    def _run_charging_program(self, stop: Event):
        self.controller.main(stop)
        if self.controller.status.get() == "FAULTED" and self.controller.issue.get():
            self.root.after(0, lambda: self._show_controller_issue("Charging Fault", "Charging fault occurred."))

        self.root.after(0, lambda: self.start_btn.grid_forget())
        self.root.after(0, lambda: self.stop_btn.grid_forget())
        self.root.after(0, lambda: self.reset_btn.grid(column=4, row=0, rowspan=2, padx=(20, 10), pady=5))
        self.chg_connected = False
        self.root.after(0, lambda: self.chg_connect_btn.config(text="CONNECT", style="Start.TButton"))
        self.root.after(0, lambda: self.chg_interface_combo.config(state="readonly"))
        self.root.after(0, lambda: self.chg_channel_combo.config(state="readonly"))


def main():
    root = Tk()
    ChargingGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
