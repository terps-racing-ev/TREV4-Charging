from tkinter import *
from tkinter.ttk import *
import threading
from charging import ChargingController
import can

WINDOW_WIDTH = 600
WINDOW_HEIGHT = 400

class ChargingGUI:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("TREV4 Charging")
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")

        self.controller = ChargingController()

        # Event for shutting down charging program when STOP is clicked
        self.stop = threading.Event()

        self.available_channels = self._detect_pcan_channels()

        self.chg_connected = False
        self.hvc_connected = False

        self._configure_styles()
        self._create_widgets()
    
    def _detect_pcan_channels(self):
        try:
            configs = can.detect_available_configs()
            return [c['channel'] for c in configs if c.get('interface') == 'pcan']
        except Exception:
            return []

    def _configure_styles(self):
        """Configure ttk styles for the GUI."""
        style = Style()
        style.configure('Start.TButton', padding=10, font=('Arial', 10), foreground='#07ae17')
        style.configure('Stop.TButton', padding=10, font=('Arial', 10), foreground = '#f51b07')
        style.configure('TButton', padding=10, font=('Arial', 10))
        
    def _create_widgets(self):
        """Create and layout GUI widgets."""
        main_frame = Frame(self.root, padding=10)
        main_frame.grid(row=0, column=0, sticky="nsew")
        
        self._create_connection_section(main_frame, row=0)
        self._create_charging_section(main_frame, row=1)

    def _create_connection_section(self, main_frame: Frame, row):
        """Create connection configuration section."""
        frame = LabelFrame(main_frame, text="Connection", padding="10")
        frame.grid(row=row, column=0, sticky="we", pady=(0, 10))

        Label(frame, text="Charger").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        Label(frame, text="HVC").grid(row=0, column=2, padx=5, pady=5, sticky="w")

        Label(frame, text="PCAN Channel:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        Label(frame, text="PCAN Channel:").grid(row=1, column=2, sticky="w", padx=5, pady=5)
    
        self.chg_channel_var = StringVar()
        self.chg_channel_combo = Combobox(frame, textvariable=self.chg_channel_var, values=self.available_channels, state="readonly", width=17)
        self.chg_channel_combo.grid(row=1, column=1, sticky="w", padx=5, pady=5)
        
        self.hvc_channel_var = StringVar()
        self.hvc_channel_combo = Combobox(frame, textvariable=self.hvc_channel_var, values=self.available_channels, state="readonly", width=17)
        self.hvc_channel_combo.grid(row=1, column=3, sticky="w", padx=5, pady=5)

        Label(frame, text="Bitrate:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        Label(frame, text="Bitrate:").grid(row=2, column=2, sticky="w", padx=5, pady=5)

        chg_bitrate = Entry(frame, justify="left")
        chg_bitrate.grid(row=2, column=1, sticky="w", padx=5, pady=5)
        chg_bitrate.insert(0, "250 kbit/s")
        chg_bitrate.config(state="readonly")
        
        hvc_bitrate = Entry(frame, justify="left")
        hvc_bitrate.grid(row=2, column=3, sticky="w", padx=5, pady=5)
        hvc_bitrate.insert(0, "500 kbit/s")
        hvc_bitrate.config(state="readonly")

        self.chg_connect_btn = Button(frame, text = "CONNECT", style = "Start.TButton", command=self._on_chg_connect)
        self.chg_connect_btn.grid(row=3, column=0, rowspan=2, columnspan=2, padx=5, pady=5)
        self.hvc_connect_btn = Button(frame, text = "CONNECT", style = "Start.TButton", command=self._on_hvc_connect)
        self.hvc_connect_btn.grid(row=3, column=2, rowspan=2, columnspan=2, padx=5, pady=5)

    def _create_charging_section(self, main_frame: Frame, row):
        """Create charging configuration section."""
        frame = LabelFrame(main_frame, text="Charging", padding="10")
        frame.grid(row=row, column=0, sticky="we", pady=(0, 10))

        Label(frame, text = "Voltage Limit:").grid(column=0, row=0, padx=5, pady=5)
        Label(frame, text = "Current Limit:").grid(column=0, row=1, padx=5, pady=5)

        Label(frame, text = "V").grid(column=2, row=0, padx=2, pady=5)
        Label(frame, text = "A").grid(column=2, row=1, padx=2, pady=5)

        Label(frame, text = "Status:").grid(column=0, row=2, padx=5, pady=5, sticky="w")
        Label(frame, text = "Messages:").grid(column=0, row=3, padx=5, pady=5, sticky="w")

        Label(frame, text = self.controller.status).grid(column=1, row=2, padx=5, pady=5, sticky="w")
        Label(frame, text = self.controller.messages).grid(column=1, row=3, columnspan=7, padx=5, pady=5, sticky="w")

        self.voltage_limit_entry = Entry(frame, justify="right")
        self.voltage_limit_entry.grid(column=1, row=0, padx=5, pady=5)
        
        self.current_limit_entry = Entry(frame, justify="right")
        self.current_limit_entry.grid(column=1, row=1, padx=5, pady=5)

        self.start_btn = Button(frame, text = "START", style = "Start.TButton", command=self._on_start)
        self.start_btn.grid(column=4, row=0, rowspan=2, padx=(30, 10), pady=5)
        
        self.stop_btn = Button(frame, text = "STOP", style = "Stop.TButton", state = "disabled", command=self._on_stop)
        self.stop_btn.grid(column=6, row=0, rowspan=2, padx=10, pady=5)
        
        self.reset_btn = Button(frame, text = "RESET", command=self._on_reset)

    def _on_chg_connect(self):
        """Handle charger connect/disconnect button click."""
        if not self.chg_connected:
            channel = self.chg_channel_var.get()
            if self.controller.start_chg_can(channel=channel):
                self.chg_connected = True
                self.chg_connect_btn.config(text="DISCONNECT", style = "Stop.TButton")
                self.chg_channel_combo.config(state="disabled")
        elif self.chg_connected:
            if self.controller.stop_chg_can():
                self.chg_connected = False
                self.chg_connect_btn.config(text="CONNECT", style = "Start.TButton")
                self.chg_channel_combo.config(state="readonly")
        
    def _on_hvc_connect(self):
        """Handle HVC connect/disconnect button click."""
        if not self.hvc_connected:
            channel = self.hvc_channel_var.get()
            if self.controller.start_hvc_can(channel=channel):
                self.hvc_connected = True
                self.hvc_connect_btn.config(text="DISCONNECT", style = "Stop.TButton")
                self.hvc_channel_combo.config(state="disabled")
        elif self.hvc_connected:
            if self.controller.stop_hvc_can():
                self.hvc_connected = False
                self.hvc_connect_btn.config(text="CONNECT", style = "Start.TButton")
                self.hvc_channel_combo.config(state="readonly")

    def _on_start(self):
        """Handle start button click."""
        self.voltage_limit_entry.config(state="disabled")
        self.current_limit_entry.config(state="disabled")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

        # returns empty string if no entry
        voltage_limit_entered = self.voltage_limit_entry.get()
        current_limit_entered = self.current_limit_entry.get()

        voltage_limit = None if not voltage_limit_entered else float(voltage_limit_entered)
        current_limit = None if not current_limit_entered else float(current_limit_entered)

        # replaces max limits as user limits if not None
        self.controller.record_user_chg_limits(voltage_limit, current_limit)

        threading.Thread(target=self._run_charging_program, args=(self.stop,)).start()
    
    def _on_stop(self):
        """Handle stop button click."""
        self.stop.set()
    
    def _on_reset(self):
        """Handle reset button click."""
        self.reset_btn.grid_forget()
        self.start_btn.grid(column=4, row=0, rowspan=2)
        self.stop_btn.grid(column=6, row=0, rowspan=2)
        
        self.voltage_limit_entry.config(state="normal")
        self.current_limit_entry.config(state="normal")
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")

        self.controller.status = "IDLE"
        self.controller.messages = ""
    
    def _run_charging_program(self, stop: Event):
        """Run main charging controller."""
        self.controller.main(stop)
    
        self.root.after(0, lambda: self.start_btn.grid_forget())
        self.root.after(0, lambda: self.stop_btn.grid_forget())
        self.root.after(0, lambda: self.reset_btn.grid(column=4, row=0, rowspan=2))
    
def main():
    root = Tk()
    ChargingGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()