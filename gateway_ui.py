import threading
import time
import sys
import os
import requests
import urllib3
import tkinter as tk
import logging
from tkinter import ttk, messagebox, scrolledtext

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =========================
# Dependency Check
# =========================
try:
    import serial
    from serial.tools import list_ports
except ImportError:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "Dependency Error",
        "pyserial tidak ditemukan.\n\nInstall dengan:\n pip install pyserial"
    )
    sys.exit(1)

# =========================
# Configuration
# =========================
BAUD_RATE = 9600
API_URL = "https://rims.r-dev.asia/api/pick-command"
POLL_INTERVAL = 1.0
RETRY_INTERVAL = 1.0  # Lebih cepat mendeteksi ulang

# Logging Configuration
LOG_FILE = "gateway_error.log"

# Theme Colors
PRIMARY_COLOR = "#106eea"
SECONDARY_COLOR = "#FFFFFF"
BG_COLOR = "#f5f7fa"
TEXT_COLOR = "#2c3e50"
SUCCESS_COLOR = "#27ae60"
ERROR_COLOR = "#e74c3c"
WARNING_COLOR = "#f39c12"

# =========================
# Global State
# =========================
stop_event = threading.Event()

# =========================
# Logging System
# =========================
def setup_logging():
    """Konfigurasi logging ke file"""
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.ERROR,
        format='%(asctime)s - [%(levelname)s] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

def log_error(context, message):
    """Catat error ke file dan print debug"""
    full_msg = f"{context}: {message}"
    print(f"[ERROR] {full_msg}")  # Untuk debug console
    logging.error(full_msg)       # Tulis ke .txt

# =========================
# Serial Utilities
# =========================
def find_all_esp_ports():
    """
    Mendeteksi SEMUA port yang cocok dengan kriteria ESP.
    Returns: List of port names (e.g., ['COM3', 'COM4'])
    """
    found_ports = []
    ports = list_ports.comports()
    esp_identifiers = [
        ("cp210", "description"),
        ("ch340", "description"),
        ("usb serial", "description"),
        ("esp", "description"),
        ("vid:pid=10c4", "hwid"), # Silicon Labs
        ("vid:pid=1a86", "hwid")  # CH340
    ]
    
    for port in ports:
        desc = (port.description or "").lower()
        hwid = (port.hwid or "").lower()
        
        is_esp = False
        for identifier, attr_type in esp_identifiers:
            target = desc if attr_type == "description" else hwid
            if identifier in target:
                is_esp = True
                break
        
        if is_esp:
            found_ports.append(port.device)
            
    return found_ports

# =========================
# Gateway Core
# =========================
def gateway_loop(update_status, log_ui, update_stats):
    """
    Loop utama gateway dengan dukungan Multi-Device.
    Tidak akan berhenti meskipun terjadi error pada salah satu device.
    """
    active_connections = {} # Format: {'COM3': serial_object, 'COM4': serial_object}
    stats = {"commands_sent": 0, "errors": 0, "devices_count": 0}

    log_ui("🚀 Gateway Service Started")
    setup_logging()

    while not stop_event.is_set():
        try:
            # ---------------------------
            # 1. Device Discovery & Management
            # ---------------------------
            try:
                available_ports = find_all_esp_ports()
                
                # A. Handle New Connections
                for port in available_ports:
                    if port not in active_connections:
                        try:
                            ser = serial.Serial(port, BAUD_RATE, timeout=1)
                            ser.reset_input_buffer()
                            ser.reset_output_buffer()
                            active_connections[port] = ser
                            log_ui(f"✅ Device connected: {port}")
                        except serial.SerialException as e:
                            log_error(f"CONNECT_FAIL_{port}", str(e))

                # B. Handle Disconnections (Device Removed)
                # Buat list copy keys agar aman saat delete
                current_connected_ports = list(active_connections.keys())
                for port in current_connected_ports:
                    # Cek jika port fisik hilang ATAU serial object error
                    if port not in available_ports:
                        try:
                            active_connections[port].close()
                        except: pass
                        del active_connections[port]
                        log_ui(f"⚠️ Device removed: {port}")

            except Exception as e:
                log_error("DEVICE_SCAN", str(e))

            # ---------------------------
            # 2. Update UI Status
            # ---------------------------
            stats["devices_count"] = len(active_connections)
            update_stats(stats)

            if active_connections:
                update_status("gateway", "RUNNING", SUCCESS_COLOR)
                # Tampilkan list port singkat, misal: "COM3, COM5"
                port_list = ", ".join(active_connections.keys())
                if len(port_list) > 20: port_list = f"{len(active_connections)} Devices"
                update_status("serial", f"CONNECTED ({port_list})", SUCCESS_COLOR)
            else:
                update_status("gateway", "SCANNING...", WARNING_COLOR)
                update_status("serial", "NO DEVICES", ERROR_COLOR)

            # ---------------------------
            # 3. API Polling & Broadcasting
            # ---------------------------
            cmd = None
            try:
                r = requests.get(API_URL, verify=False, timeout=5)
                if r.status_code == 200:
                    update_status("api", "OK", SUCCESS_COLOR)
                    cmd_text = r.text.strip()
                    if cmd_text:
                        cmd = cmd_text
                else:
                    update_status("api", f"ERR {r.status_code}", ERROR_COLOR)
                    stats["errors"] += 1
            except requests.exceptions.RequestException as e:
                update_status("api", "TIMEOUT", ERROR_COLOR)
                log_error("API_FAIL", str(e))
                stats["errors"] += 1

            # ---------------------------
            # 4. Command Execution (Broadcast)
            # ---------------------------
            if cmd and active_connections:
                # Kirim ke SEMUA device yang terhubung
                # Jika ada device yg gagal, hanya device itu yang didrop
                dead_ports = []
                
                for port, ser in active_connections.items():
                    try:
                        ser.write((cmd + "\n").encode())
                    except (serial.SerialException, OSError) as e:
                        log_error(f"WRITE_FAIL_{port}", str(e))
                        dead_ports.append(port)
                
                # Clean up dead ports
                for p in dead_ports:
                    try:
                        active_connections[p].close()
                    except: pass
                    del active_connections[p]
                    log_ui(f"❌ Write Error: {p} dropped")

                if not dead_ports:
                    stats["commands_sent"] += 1
                    log_ui(f"📤 Sent to {len(active_connections)} devs: {cmd}")
            
            # Update stats UI terakhir
            update_stats(stats)
            time.sleep(POLL_INTERVAL)

        except Exception as e:
            # Global Loop Error Catch-All
            # Ini mencegah loop berhenti total jika ada unhandled exception
            log_error("CRITICAL_LOOP", str(e))
            log_ui(f"💥 Critical Error: {str(e)}")
            time.sleep(RETRY_INTERVAL)

    # Cleanup saat stop
    for ser in active_connections.values():
        try:
            ser.close()
        except: pass
    update_status("gateway", "STOPPED", WARNING_COLOR)

# =========================
# UI Components
# =========================
class GatewayUI(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("RIMS Multi-Gateway v2.0")
        self.geometry("600x650")
        self.resizable(False, False)
        self.configure(bg=BG_COLOR)

        self.status_indicators = {}
        self.status_vars = {
            "serial": {"text": tk.StringVar(value="SCANNING..."), "color": tk.StringVar(value=WARNING_COLOR)},
            "api": {"text": tk.StringVar(value="WAITING"), "color": tk.StringVar(value=WARNING_COLOR)},
            "gateway": {"text": tk.StringVar(value="STARTING"), "color": tk.StringVar(value=WARNING_COLOR)},
        }
        
        self.stats_vars = {
            "commands": tk.StringVar(value="0"),
            "errors": tk.StringVar(value="0"),
            "devices": tk.StringVar(value="0") # Diganti dari Uptime ke Devices
        }

        self._configure_styles()
        self._build_ui()
        self._start_gateway()

    def _configure_styles(self):
        """Configure custom ttk styles"""
        style = ttk.Style()
        style.theme_use('clam')
        
        style.configure("Header.TFrame", background=PRIMARY_COLOR)
        style.configure("Content.TFrame", background=BG_COLOR)
        style.configure("Card.TFrame", background=SECONDARY_COLOR, relief="flat")
        
        style.configure("Title.TLabel", 
                       background=PRIMARY_COLOR, 
                       foreground=SECONDARY_COLOR, 
                       font=("Segoe UI", 16, "bold"))
        
        style.configure("StatusLabel.TLabel", 
                       font=("Segoe UI", 10),
                       background=SECONDARY_COLOR,
                       foreground=TEXT_COLOR)
        
        style.configure("StatValue.TLabel",
                       font=("Segoe UI", 18, "bold"),
                       background=SECONDARY_COLOR,
                       foreground=PRIMARY_COLOR)
        
        style.configure("StatLabel.TLabel",
                       font=("Segoe UI", 9),
                       background=SECONDARY_COLOR,
                       foreground=TEXT_COLOR)

    def _build_ui(self):
        """Build the user interface"""
        # Header
        header = ttk.Frame(self, style="Header.TFrame", height=80)
        header.pack(fill="x")
        header.pack_propagate(False)

        ttk.Label(header, text="RIMS Multi-Link Gateway", style="Title.TLabel").pack(pady=25)

        # Main content area
        content = ttk.Frame(self, style="Content.TFrame", padding=20)
        content.pack(fill="both", expand=True)

        # Status card
        status_card = self._create_card(content, "System Status")
        status_card.pack(fill="x", pady=(0, 15))

        self._create_status_row(status_card, "Gateway Engine:", "gateway")
        self._create_status_row(status_card, "Serial Devices:", "serial")
        self._create_status_row(status_card, "API Connection:", "api")

        # Statistics card
        stats_card = self._create_card(content, "Statistics")
        stats_card.pack(fill="x", pady=(0, 15))

        stats_grid = ttk.Frame(stats_card, style="Card.TFrame")
        stats_grid.pack(fill="x", pady=(5, 0))

        # Layout Statistik (Devices menggantikan Uptime)
        self._create_stat_box(stats_grid, "Commands Sent", self.stats_vars["commands"], 0)
        self._create_stat_box(stats_grid, "System Errors", self.stats_vars["errors"], 1)
        self._create_stat_box(stats_grid, "Active Devices", self.stats_vars["devices"], 2)

        # Activity log card
        log_card = self._create_card(content, "Activity Log")
        log_card.pack(fill="both", expand=True)

        self.terminal = scrolledtext.ScrolledText(
            log_card,
            height=15,
            font=("Consolas", 9),
            bg="#2c3e50",
            fg="#ecf0f1",
            insertbackground="#ecf0f1",
            selectbackground=PRIMARY_COLOR,
            state="disabled",
            wrap="word",
            relief="flat"
        )
        self.terminal.pack(fill="both", expand=True, pady=(5, 0))

    def _create_card(self, parent, title):
        """Create a card container with title"""
        frame = ttk.Frame(parent, style="Card.TFrame", padding=15, relief="solid", borderwidth=1)
        ttk.Label(frame, text=title, font=("Segoe UI", 11, "bold"), 
                 background=SECONDARY_COLOR, foreground=TEXT_COLOR).pack(anchor="w", pady=(0, 10))
        return frame

    def _create_status_row(self, parent, label_text, key):
        """Create a status indicator row"""
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", pady=5)

        ttk.Label(row, text=label_text, style="StatusLabel.TLabel", width=20).pack(side="left")
        
        status_frame = ttk.Frame(row, style="Card.TFrame")
        status_frame.pack(side="left", fill="x", expand=True)
        
        indicator = tk.Canvas(status_frame, width=12, height=12, bg=SECONDARY_COLOR, 
                             highlightthickness=0)
        indicator.pack(side="left", padx=(0, 8))
        indicator.create_oval(2, 2, 10, 10, fill=self.status_vars[key]["color"].get(), 
                             outline="", tags="indicator")
        
        label = ttk.Label(status_frame, textvariable=self.status_vars[key]["text"], 
                         style="StatusLabel.TLabel", font=("Segoe UI", 10, "bold"))
        label.pack(side="left")
        
        self.status_indicators[key] = indicator

    def _create_stat_box(self, parent, label, var, col):
        """Create a statistics box"""
        box = ttk.Frame(parent, style="Card.TFrame")
        box.grid(row=0, column=col, padx=10, pady=5, sticky="ew")
        parent.columnconfigure(col, weight=1)

        ttk.Label(box, textvariable=var, style="StatValue.TLabel").pack()
        ttk.Label(box, text=label, style="StatLabel.TLabel").pack()

    def log(self, message):
        """Add message to activity log UI only"""
        def append():
            try:
                self.terminal.config(state="normal")
                timestamp = time.strftime('%H:%M:%S')
                self.terminal.insert(tk.END, f"[{timestamp}] {message}\n")
                self.terminal.see(tk.END)
                self.terminal.config(state="disabled")
            except Exception:
                pass # Prevent GUI update errors on exit
        self.after(0, append)

    def update_status(self, key, value, color):
        """Update status indicator"""
        def update():
            try:
                self.status_vars[key]["text"].set(value)
                self.status_vars[key]["color"].set(color)
                if key in self.status_indicators:
                    indicator = self.status_indicators[key]
                    indicator.delete("indicator")
                    indicator.create_oval(2, 2, 10, 10, fill=color, outline="", tags="indicator")
            except Exception:
                pass
        self.after(0, update)

    def update_stats(self, stats):
        """Update statistics display"""
        def update():
            try:
                self.stats_vars["commands"].set(str(stats["commands_sent"]))
                self.stats_vars["errors"].set(str(stats["errors"]))
                self.stats_vars["devices"].set(str(stats["devices_count"]))
            except Exception:
                pass
        self.after(0, update)

    def _start_gateway(self):
        """Start gateway thread"""
        threading.Thread(
            target=gateway_loop,
            args=(self.update_status, self.log, self.update_stats),
            daemon=True
        ).start()

# =========================
# Entry Point
# =========================
if __name__ == "__main__":
    app = GatewayUI()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        stop_event.set()
        sys.exit()