# TODO: set channels through GUI

from tkinter import *
from tkinter.ttk import *
import threading
from charging import ChargingController

WINDOW_WIDTH = 600
WINDOW_HEIGHT = 200

class ChargingGUI:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("TREV4 Charging")
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")

        self.controller = ChargingController()

        # Event for shutting down charging program when STOP is clicked
        self.stop = threading.Event()

        self._configure_styles()
        self._create_widgets()
    
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
        
        self._create_labels(main_frame)
        self._create_entries(main_frame)
        self._create_buttons(main_frame)

    def _create_labels(self, main_frame: Frame):
        """Create text labels."""
        Label(main_frame, text = "Voltage Limit:").grid(column=0, row=0, padx=5, pady=5)
        Label(main_frame, text = "Current Limit:").grid(column=0, row=1, padx=5, pady=5)

        Label(main_frame, text = "V").grid(column=2, row=0, padx=2, pady=5)
        Label(main_frame, text = "A").grid(column=2, row=1, padx=2, pady=5)

        Label(main_frame, text = "Status:").grid(column=0, row=2, padx=5, pady=5)
        Label(main_frame, text = "Messages:").grid(column=0, row=3, padx=5, pady=5)

        Label(main_frame, text = self.controller.status).grid(column=1, row=2, padx=5, pady=5, sticky="w")
        Label(main_frame, text = self.controller.messages).grid(column=1, row=3, columnspan=7, padx=5, pady=5, sticky="w")

    def _create_entries(self, main_frame: Frame):
        """Create entry fields for voltage and current limits."""
        self.voltage_limit_entry = Entry(main_frame, justify="right")
        self.voltage_limit_entry.grid(column=1, row=0, padx=5, pady=5)
        
        self.current_limit_entry = Entry(main_frame, justify="right")
        self.current_limit_entry.grid(column=1, row=1, padx=5, pady=5)

    def _create_buttons(self, main_frame: Frame):
        """Create start, stop, and reset buttons."""
        self.start_btn = Button(main_frame, text = "START", style = "Start.TButton", command=self._on_start)
        self.start_btn.grid(column=4, row=0, rowspan=2, padx=(30, 10), pady=5)
        
        self.stop_btn = Button(main_frame, text = "STOP", style = "Stop.TButton", state = "disabled", command=self._on_stop)
        self.stop_btn.grid(column=6, row=0, rowspan=2, padx=10, pady=5)
        
        self.reset_btn = Button(main_frame, text = "RESET", command=self._on_reset)

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