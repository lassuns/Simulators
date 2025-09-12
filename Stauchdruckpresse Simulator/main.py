import tkinter as tk
import sys
import threading
import time
from PIL import Image, ImageTk
import os
from tkinter import ttk
import pygame

# --- Scaling Factor ---
SCALE_FACTOR = 2

# --- Material Properties and Visuals Data ---
MATERIALS = {
    "Brick": {"E": 20, "sigma_y": 5, "color": "#8B4513", "type": "brick", "dims": (80, 40, 40)},
    #"Pipe": {"E": 200, "sigma_y": 250, "color": "#A9A9A9", "type": "pipe", "dims": (110, 90, 50)},
    "Packaging": {"E": 0.5, "sigma_y": 0.2, "color": "#D2B48C", "type": "box", "dims": (60, 60, 60)},
}

# --- Machine Dimensions (Unscaled) ---
PLATTEN_WIDTH = 100
PLATTEN_HEIGHT = 20
PLATTEN_DEPTH = 20
MACHINE_Y_TOP = 300
MACHINE_Y_BOTTOM = 600

# -----------------------------------------------------------------------------
# Class 1: EventManager - Decouples the GUI and Logic classes
# -----------------------------------------------------------------------------
class EventManager:
    """A simple event bus to handle communication between decoupled components."""
    def __init__(self):
        self.listeners = {}

    def subscribe(self, event_name, callback):
        """Adds a callback function to be called when an event occurs."""
        if event_name not in self.listeners:
            self.listeners[event_name] = []
        self.listeners[event_name].append(callback)

    def notify(self, event_name, data=None):
        """Notifies all subscribed listeners of an event."""
        if event_name in self.listeners:
            for callback in self.listeners[event_name]:
                callback(data)

# -----------------------------------------------------------------------------
# Class 2: Logic - Responsible for simulation, calculations, and state management
# -----------------------------------------------------------------------------
class Logic:
    def __init__(self, root, event_manager):
        self.root = root
        self.event_manager = event_manager
        
        self.is_running_event = threading.Event()
        self.simulation_thread = None
        self.selected_material_data = None
        
        # Initial machine state (used for calculations)
        self.initial_crosshead_y = (MACHINE_Y_TOP + 150) * SCALE_FACTOR
        self.initial_platen_y = MACHINE_Y_BOTTOM * SCALE_FACTOR
        self.current_crosshead_y = self.initial_crosshead_y
        self.actuator_x = 0 # Will be set by GUI
        self.actuator_y = MACHINE_Y_TOP + 10
        self.actuator_width = 40
        self.actuator_height = 20
        self.test_type = "Compression" # Default test type
        
        # Test-specific state
        self.current_deformation = 0
        self.current_force = 0
        self.peak_force = 0
        self.peak_stress = 0
        self.original_height = 0
        self.original_width = 0
        self.original_depth = 0
        
        # Sound setup
        pygame.mixer.init()
        self.machine_sound = None
        if os.path.exists('machine_sound.wav'):
            try:
                self.machine_sound = pygame.mixer.Sound('machine_sound.wav')
            except pygame.error:
                print("Sound error: Could not load 'machine_sound.wav'.")
        else:
            print("Info: 'machine_sound.wav' not found. Sound effects will be disabled.")
            
        self.setup_event_listeners()
        
    def setup_event_listeners(self):
        """Subscribe to events from the GUI."""
        self.event_manager.subscribe("calibrate_machine", self.calibrate_machine)
        self.event_manager.subscribe("start_test", self.start_test)
        self.event_manager.subscribe("pause_test", self.pause_test)
        self.event_manager.subscribe("resume_test", self.resume_test)
        self.event_manager.subscribe("reset_app", self.reset_state)
        self.event_manager.subscribe("material_dropped", self.set_material_data)
        self.event_manager.subscribe("test_type_changed", self.set_test_type)

    def set_test_type(self, data):
        self.test_type = data["type"]
        self.reset_state()
        
    def set_material_data(self, data):
        """Sets the selected material data based on the event."""
        self.selected_material_data = data["material_data"]

    def calibrate_machine(self, data=None):
        if not self.selected_material_data:
            return
        
        material_height = self.selected_material_data["dims"][1]
        
        if self.test_type == "Compression":
            calibration_y = self.initial_platen_y - (material_height * SCALE_FACTOR) - (PLATTEN_HEIGHT * SCALE_FACTOR)
            self.current_crosshead_y = calibration_y
            self.event_manager.notify("update_crosshead", {"y": calibration_y, "test_type": self.test_type})
        elif self.test_type == "Tensile":
            self.current_crosshead_y = self.initial_platen_y - (material_height * SCALE_FACTOR)
            self.event_manager.notify("update_crosshead", {"y": self.current_crosshead_y, "test_type": self.test_type})
            self.event_manager.notify("update_bottom_platen", {"y": self.initial_platen_y})
            
        self.event_manager.notify("update_message", {"text": "Machine calibrated. Press 'Start Test' to begin.", "color": "green"})

    def start_test(self, data=None):
        if self.is_running_event.is_set() or not self.selected_material_data:
            return
        
        self.original_height = self.selected_material_data["dims"][1]
        self.original_width = self.selected_material_data["dims"][0]
        self.original_depth = self.selected_material_data["dims"][2]
        self.current_deformation = 0
        self.current_force = 0
        self.peak_force = 0
        self.peak_stress = 0
        
        self.is_running_event.set()
        self.event_manager.notify("set_status", "running")
        
        if self.machine_sound:
            self.machine_sound.play(loops=-1)
        
        if self.test_type == "Compression":
            self.run_compression_simulation_step()
        elif self.test_type == "Tensile":
            self.run_tensile_simulation_step()
            
    def pause_test(self, data=None):
        self.is_running_event.clear()
        if self.machine_sound:
            self.machine_sound.stop()
        self.event_manager.notify("set_status", "paused")
        self.event_manager.notify("update_message", {"text": "Test paused. Press Resume Test to continue.", "color": "orange"})

    def resume_test(self, data=None):
        if not self.is_running_event.is_set():
            self.is_running_event.set()
            self.event_manager.notify("set_status", "running")
            if self.machine_sound:
                self.machine_sound.play(loops=-1)
            self.event_manager.notify("update_message", {"text": "Test resumed.", "color": "green"})
            if self.test_type == "Compression":
                self.run_compression_simulation_step()
            elif self.test_type == "Tensile":
                self.run_tensile_simulation_step()
        
    def stop_test(self):
        self.is_running_event.clear()
        if self.machine_sound:
            self.machine_sound.stop()

    def reset_state(self, data=None):
        self.stop_test()
        self.selected_material_data = None
        self.current_deformation = 0
        self.current_force = 0
        self.peak_force = 0
        self.peak_stress = 0
        self.current_crosshead_y = self.initial_crosshead_y
        self.event_manager.notify("full_reset", {"test_type": self.test_type, "initial_crosshead_y": self.initial_crosshead_y})

    def run_compression_simulation_step(self):
        if not self.is_running_event.is_set():
            return
        
        E = self.selected_material_data["E"] * 1000 # GPa to MPa
        sigma_y = self.selected_material_data["sigma_y"]
        
        step_deformation = 0.5
        
        if self.current_deformation >= self.original_height - 10:
            self.event_manager.notify("set_status", "finished")
            self.event_manager.notify("update_message", {"text": "The compression test has finished.", "color": "green"})
            self.stop_test()
            return
            
        self.current_deformation += step_deformation
        
        current_strain = self.current_deformation / self.original_height
        current_stress = E * current_strain
        
        if current_stress > sigma_y:
            current_stress = sigma_y + 0.1 * (current_stress - sigma_y)
        
        area = self.original_width * self.original_depth
        self.current_force = current_stress * area
        
        if self.current_force > self.peak_force:
            self.peak_force = self.current_force
            self.peak_stress = current_stress

        new_height = self.original_height - self.current_deformation
        
        if new_height > 0:
            dim_factor = (self.original_height / new_height)**0.5
            new_width = self.original_width * dim_factor
            new_depth = self.original_depth * dim_factor
        else:
            new_width = self.original_width * 1.5
            new_depth = self.original_depth * 1.5
        
        new_crosshead_y = self.initial_platen_y - (new_height * SCALE_FACTOR + PLATTEN_HEIGHT * SCALE_FACTOR)
        self.current_crosshead_y = new_crosshead_y
        
        data = {
            "material_height": new_height * SCALE_FACTOR,
            "material_width": new_width * SCALE_FACTOR,
            "material_depth": new_depth * SCALE_FACTOR,
            "crosshead_new_y": self.current_crosshead_y,
            "current_force": self.current_force,
            "peak_stress": self.peak_stress,
            "test_type": self.test_type
        }
        
        self.event_manager.notify("update_data", data)
        self.root.after(50, self.run_compression_simulation_step)

    def run_tensile_simulation_step(self):
        if not self.is_running_event.is_set():
            return
            
        E = self.selected_material_data["E"] * 1000
        sigma_y = self.selected_material_data["sigma_y"]
        
        step_deformation = 0.5
        
        if self.current_deformation >= self.original_height * 2:
            self.event_manager.notify("set_status", "finished")
            self.event_manager.notify("update_message", {"text": "The tensile test has finished (material broke).", "color": "green"})
            self.stop_test()
            return
        
        self.current_deformation += step_deformation
        
        current_strain = self.current_deformation / self.original_height
        current_stress = E * current_strain
        
        if current_stress > sigma_y:
            current_stress = sigma_y + 0.1 * (current_stress - sigma_y)

        new_height = self.original_height + self.current_deformation
        dim_factor = (self.original_height / new_height)**0.5
        new_width = self.original_width * dim_factor
        new_depth = self.original_depth * dim_factor
        
        area = new_width * new_depth
        self.current_force = current_stress * area
        
        if self.current_force > self.peak_force:
            self.peak_force = self.current_force
            self.peak_stress = current_stress
            
        new_crosshead_y = self.initial_platen_y - (new_height * SCALE_FACTOR) - (PLATTEN_HEIGHT*SCALE_FACTOR)
        self.current_crosshead_y = new_crosshead_y
        
        data = {
            "material_height": new_height * SCALE_FACTOR,
            "material_width": new_width * SCALE_FACTOR,
            "material_depth": new_depth * SCALE_FACTOR,
            "crosshead_new_y": self.current_crosshead_y,
            "current_force": self.current_force,
            "peak_stress": self.peak_stress,
            "test_type": self.test_type
        }

        self.event_manager.notify("update_data", data)
        self.root.after(50, self.run_tensile_simulation_step)
        
# -----------------------------------------------------------------------------
# Class 3: GUI - Responsible for all drawing and user interaction
# -----------------------------------------------------------------------------
class GUI:
    def __init__(self, root, event_manager):
        self.root = root
        self.event_manager = event_manager
        
        self.root.title("Stauchdruckpresse Simulator")
        self.root.geometry(f"{1000 * SCALE_FACTOR}x{650 * SCALE_FACTOR}")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.drag_data = {"item": None, "x": 0, "y": 0}
        self.logo_image = None
        self.material_tags = {}
        self.selected_material_tag = None
        self.test_type_var = tk.StringVar(value="Compression")
        
        self.setup_ui()
        self.draw_logo()
        self.setup_event_listeners()
        
        # CORRECTED: Force update and get dimensions after setup_ui()
        self.canvas.update_idletasks()
        canvas_width = self.canvas.winfo_width()
        machine_width = 500 * SCALE_FACTOR
        machine_x_start = 850  
        
        # (canvas_width - machine_width) / 2
        
        # Machine dimensions are now initialized dynamically
        self.platen_width = PLATTEN_WIDTH
        self.platen_height = PLATTEN_HEIGHT
        self.platen_depth = PLATTEN_DEPTH
        self.machine_x1 = machine_x_start / SCALE_FACTOR
        self.machine_x2 = (machine_x_start + machine_width) / SCALE_FACTOR
        self.machine_y_top = MACHINE_Y_TOP
        self.machine_y_bottom = MACHINE_Y_BOTTOM
        self.actuator_x = (self.machine_x1 + self.machine_x2) / 2 - 20
        self.actuator_y = MACHINE_Y_TOP + 10
        self.actuator_width = 40
        self.actuator_height = 20
        self.initial_crosshead_y = (MACHINE_Y_TOP + 150) * SCALE_FACTOR
        self.initial_platen_y = MACHINE_Y_BOTTOM * SCALE_FACTOR
        
        self.machine_area = {"x1": self.machine_x1, "y1": self.machine_y_top, "x2": self.machine_x2, "y2": self.machine_y_bottom}
        
        # Draw the machine and materials immediately after initialization
        self.draw_machine()
        self.draw_materials_to_drag()

    def on_closing(self):
        self.event_manager.notify("stop_test")
        self.root.destroy()
    
    def setup_event_listeners(self):
        """Subscribe to events from the Logic class."""
        self.event_manager.subscribe("update_data", self.update_gui)
        self.event_manager.subscribe("update_crosshead", self.update_crosshead_position)
        self.event_manager.subscribe("set_status", self.set_status)
        self.event_manager.subscribe("update_message", self.display_message)
        self.event_manager.subscribe("full_reset", self.full_reset)
        
        self.compression_rb.config(command=self.on_test_type_change)
        self.tensile_rb.config(command=self.on_test_type_change)

    def on_test_type_change(self):
        new_type = self.test_type_var.get()
        self.event_manager.notify("test_type_changed", {"type": new_type})
        
    def setup_ui(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('Dark.TFrame', background='#34495e')
        style.configure('Dark.TLabel', background='#34495e', foreground='white')
        style.configure('Dark.TButton', background='black', foreground='white', font=('Helvetica', 10, 'bold'), bordercolor='#2c3e50', borderwidth=2, relief='solid')
        style.map('Dark.TButton', background=[('active', '#333333'), ('!disabled', 'black')], foreground=[('!disabled', 'white')])

        control_panel = ttk.Frame(self.root, padding="20", style='Dark.TFrame')
        control_panel.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)

        self.canvas_frame = tk.Frame(self.root)
        self.canvas_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(self.canvas_frame, bg="white", highlightthickness=0)
        self.canvas.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        self.message_frame = ttk.Frame(self.root, padding="10", relief=tk.RAISED)
        self.message_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.message_label = ttk.Label(self.message_frame, text="", anchor="center", font=("Helvetica", 10))
        self.message_label.pack(fill=tk.BOTH, expand=True)

        self.canvas.bind("<ButtonPress-1>", self.on_drag_start)
        self.canvas.bind("<B1-Motion>", self.on_drag_motion)
        self.canvas.bind("<ButtonRelease-1>", self.on_drag_release)
        
        # --- Control Panel UI Elements ---
        ttk.Label(control_panel, text="Test Controls", font=("Helvetica", 12, "bold"), background='#34495e', foreground='white').pack(pady=20)
        ttk.Label(control_panel, text="Select Test:", background='#34495e', foreground='white').pack(pady=10)
        self.compression_rb = ttk.Radiobutton(control_panel, text="Compression", variable=self.test_type_var, value="Compression")
        self.compression_rb.pack(anchor=tk.W)
        self.tensile_rb = ttk.Radiobutton(control_panel, text="Tensile", variable=self.test_type_var, value="Tensile")
        self.tensile_rb.pack(anchor=tk.W)

        self.calibrate_button = ttk.Button(control_panel, text="Calibrate Machine", command=lambda: self.event_manager.notify("calibrate_machine"), state=tk.DISABLED, style='Dark.TButton')
        self.calibrate_button.pack(pady=20, fill=tk.X)

        data_frame = ttk.Frame(control_panel, padding="20", relief=tk.GROOVE)
        data_frame.pack(pady=20, fill=tk.BOTH, expand=True)
        ttk.Label(data_frame, text="Real-time Data", font=("Helvetica", 10, "bold")).pack(pady=10)
        
        self.force_label = ttk.Label(data_frame, text="Force: 0.0 N")
        self.force_label.pack(pady=4)
        self.stress_label = ttk.Label(data_frame, text="Stress: 0.0 MPa")
        self.stress_label.pack(pady=4)
        self.dim_label = ttk.Label(data_frame, text="New Dims: 0.0 mm (H)")
        self.dim_label.pack(pady=4)
        
        self.light_canvas = tk.Canvas(control_panel, width=50, height=50, bg='#34495e', highlightthickness=0)
        self.light_canvas.pack(pady=10)
        self.status_light = self.light_canvas.create_oval(10, 10, 40, 40, fill="#555555", outline="#333333", width=2)
        
        self.start_button = ttk.Button(control_panel, text="Start Test", command=lambda: self.event_manager.notify("start_test"), state=tk.DISABLED, style='Dark.TButton')
        self.start_button.pack(pady=10, fill=tk.X)
        self.pause_button = ttk.Button(control_panel, text="Pause Test", command=lambda: self.event_manager.notify("pause_test"), state=tk.DISABLED, style='Dark.TButton')
        self.pause_button.pack(pady=10, fill=tk.X)
        self.reset_button = ttk.Button(control_panel, text="New Test", command=lambda: self.event_manager.notify("reset_app"), style='Dark.TButton')
        self.reset_button.pack(pady=10, fill=tk.X)
        self.exit_button = ttk.Button(control_panel, text="Exit", command=self.root.destroy, style='Dark.TButton')
        self.exit_button.pack(pady=20, fill=tk.X)

    def draw_logo(self):
        logo_path = 'PAC.jpg'
        try:
            pil_image = Image.open(logo_path)
            pil_image = pil_image.resize((int(100 * SCALE_FACTOR), int(100 * SCALE_FACTOR)))
            self.logo_image = ImageTk.PhotoImage(pil_image)
            logo_label = ttk.Label(self.root, image=self.logo_image)
            logo_label.place(relx=1.0, rely=0, anchor=tk.NE, x=-20, y=20)
        except FileNotFoundError:
            self.display_message({"text": "Error: Logo file 'PAC.jpg' not found. Please add the file to the project directory.", "color": "red"})
        except Exception as e:
            self.display_message({"text": f"Error loading logo: {e}", "color": "red"})

    def display_message(self, data):
        self.message_label.config(text=data["text"], foreground=data["color"])

    def get_lighter_color(self, hex_color):
        """Generates a slightly lighter version of a hexadecimal color."""
        hex_color = hex_color.lstrip('#')
        rgb = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        r, g, b = [min(255, int(c * 1.2)) for c in rgb]
        return f'#{r:02x}{g:02x}{b:02x}'

    def is_hex_color(self, color_string):
        """Checks if a string is a valid hexadecimal color code."""
        if not isinstance(color_string, str) or not color_string.startswith('#'):
            return False
        try:
            int(color_string[1:], 16)
            return len(color_string) == 7
        except ValueError:
            return False

    def _draw_3d_box(self, x, y, width, height, depth, tags, fill_color, outline_color):
        box_color_front = fill_color
        box_color_top_side = self.get_lighter_color(fill_color) if self.is_hex_color(fill_color) else fill_color
        
        # Front face rectangle
        self.canvas.create_rectangle(x, y, x + width, y + height, fill=box_color_front, outline=outline_color, width=2, tags=tags)
        
        # Right side face polygon 
        self.canvas.create_polygon(
            x + width, y, 
            x + width + depth, y - depth/2, 
            x + width + depth, y + height - depth/2, 
            x + width, y + height, 
            fill=box_color_top_side, outline=outline_color, width=2, tags=tags
        )
        
        # Top face polygon (for a complete 3D look)
        self.canvas.create_polygon(
            x, y, 
            x + depth, y - depth/2, 
            x + width + depth, y - depth/2, 
            x + width, y, 
            fill=box_color_top_side, outline=outline_color, width=2, tags=tags
        )
        return tags

    #22222#

    def _draw_3d_piston_rod(self, x1, y1, x2, y2, test_type):
        width = 30 * SCALE_FACTOR
        x_center = x1
        y_top = min(y1, y2)
        height = abs(y2 - y1)
        fill_color = "silver"
        outline_color = "gray"

        if self.canvas.find_withtag("actuator_piston"):
            self.canvas.coords("actuator_piston", x_center - width/2, y_top, x_center + width/2, y_top + height)
        else:
            self.canvas.create_rectangle(x_center - width/2, y_top, x_center + width/2, y_top + height, fill=fill_color, outline=outline_color, tags="actuator_piston")

    def draw_machine(self):
        main_frame_color = "#ECF0F1"
        accent_color = "#2C3E50"
        actuator_color = "#3498DB"
        outline_color = "#7F8C8D"
        
        base_x1, base_y1 = self.machine_x1 * SCALE_FACTOR, self.machine_y_bottom * SCALE_FACTOR
        base_width, base_height, base_depth = (self.machine_x2 - self.machine_x1) * SCALE_FACTOR, 80 * SCALE_FACTOR, 50 * SCALE_FACTOR
        
        # Draw base
        self._draw_3d_box(base_x1, base_y1 + 200, base_width, base_height, base_depth, "machine_base", main_frame_color, outline_color)
        #------------------------------------
        def _draw_3d_platen(x, y, tags):
            platen_color_front = "#3498DB"
            platen_color_top_side = self.get_lighter_color(platen_color_front)
            outline_color = "#2980B9"
            
            width = self.platen_width * SCALE_FACTOR
            height = self.platen_height * SCALE_FACTOR
            depth = self.platen_depth * SCALE_FACTOR

            # Draw the front face (this was the missing part)
            self.canvas.create_rectangle(x, y, x + width, y + height, fill=platen_color_front, outline=outline_color, width=2, tags=tags)

            # Right side face
            self.canvas.create_polygon(x + width, y, x + width + depth, y - depth/2, x + width + depth, y + height - depth/2, x + width, y + height, fill=platen_color_top_side, outline=outline_color, width=2, tags=tags)
            
            # Top face
            self.canvas.create_polygon(x, y, x + depth, y - depth/2, x + width + depth, y - depth/2, x + width, y, fill=platen_color_top_side, outline=outline_color, width=2, tags=tags)
        # 
        # 
        # 
        # -----------------------------   

            
        
        # Draw columns (vertical bars) - now drawn AFTER the base
        col_width = 30 * SCALE_FACTOR
        col_height = 200 * SCALE_FACTOR
        self._draw_3d_box(base_x1 +0.1 * SCALE_FACTOR, base_y1 + 40 * SCALE_FACTOR, col_width, col_height, 10 * SCALE_FACTOR, "col1", accent_color, outline_color) # Adjusted y for sitting on base
        self._draw_3d_box(base_x1 + base_width - 40 * SCALE_FACTOR, base_y1 + 40 * SCALE_FACTOR, col_width, col_height, 10 * SCALE_FACTOR, "col2", accent_color, outline_color) # Adjusted y for sitting on base

        # Draw top and bottom beams
        top_beam_y = (self.machine_y_top - 40) * SCALE_FACTOR
        self._draw_3d_box(base_x1, top_beam_y, base_width, 40 * SCALE_FACTOR, base_depth, "machine_top_beam", accent_color, outline_color)
        self._draw_3d_box(base_x1, base_y1, base_width, 40 * SCALE_FACTOR, base_depth, "machine_bottom_beam", accent_color, outline_color)
        
        # Draw actuator and platens
        self._draw_3d_box(self.actuator_x * SCALE_FACTOR, self.actuator_y * SCALE_FACTOR, self.actuator_width * SCALE_FACTOR, self.actuator_height * SCALE_FACTOR, 20 * SCALE_FACTOR, "actuator", actuator_color, outline_color)

        platen_x = ((self.machine_x1 + self.machine_x2) / 2 - self.platen_width / 2) * SCALE_FACTOR
        crosshead_y = self.initial_crosshead_y
        _draw_3d_platen(platen_x, crosshead_y, "crosshead_platen")
        
        bottom_platen_y = self.initial_platen_y
        _draw_3d_platen(platen_x, bottom_platen_y, "bottom_platen")
        self.canvas.tag_raise("actuator")
        # Draw piston rod
        x_piston_center = self.actuator_x * SCALE_FACTOR + self.actuator_width * SCALE_FACTOR / 2
        self._draw_3d_piston_rod(x_piston_center, self.actuator_y * SCALE_FACTOR + self.actuator_height * SCALE_FACTOR, platen_x + (self.platen_width * SCALE_FACTOR) / 2, crosshead_y, test_type="Compression")

        # Draw the main machine frame LAST to ensure its outlines are on top
        self._draw_3d_box(base_x1, base_y1 - 400 * SCALE_FACTOR, base_width, 400 * SCALE_FACTOR, base_depth, "machine_frame", main_frame_color, outline_color)
        
        # Raise specific elements to ensure proper layering
        self.canvas.tag_raise("machine_base")
        self.canvas.tag_raise("col1")
        self.canvas.tag_raise("col2")
        self.canvas.tag_raise("machine_top_beam")
        self.canvas.tag_raise("machine_bottom_beam")
        self.canvas.tag_raise("crosshead_platen")
        self.canvas.tag_raise("bottom_platen")
        self.canvas.tag_raise("actuator_piston")
        self.canvas.tag_raise("actuator")


    def draw_materials_to_drag(self):
        for tag in list(self.material_tags.keys()):
            self.canvas.delete(tag)
        self.canvas.delete("static_name")
        self.material_tags = {}
        
        start_x, start_y = 70 * SCALE_FACTOR, 240 * SCALE_FACTOR
        y_offset = 0
        for name, data in MATERIALS.items():
            unique_tag = f"draggable_{name}"
            self.draw_material_shape(start_x, start_y + y_offset, data["dims"], data["color"], data["type"], tags=unique_tag)
            self.canvas.create_text(start_x + data["dims"][0] * SCALE_FACTOR/2, start_y + y_offset + data["dims"][1] * SCALE_FACTOR + 15 * SCALE_FACTOR, text=name, tags="static_name")
            
            self.material_tags[unique_tag] = {"x": start_x, "y": start_y + y_offset, "data": data}
            y_offset += data["dims"][1] * SCALE_FACTOR + 80 * SCALE_FACTOR

    def draw_material_shape(self, x, y, dims, color, material_type, tags):
        w, h, d = [dim * SCALE_FACTOR for dim in dims]
        
        if material_type == "pipe":
            # Drawing a pipe as a simple rectangle for now, can be enhanced with arcs if needed
            self.canvas.create_rectangle(x, y, x + w, y + h, fill=color, outline="black", tags=tags)
        else:
            # Front face rectangle
            self.canvas.create_rectangle(x, y, x + w, y + h, fill=color, outline="black", tags=tags)
            # Right side face polygon
            lighter_color = self.get_lighter_color(color) if self.is_hex_color(color) else color
            self.canvas.create_polygon(x + w, y, x + w + d, y - d/2, x + w + d, y + h - d/2, x + w, y + h, fill=lighter_color, outline="black", tags=tags)
            # Top face polygon
            self.canvas.create_polygon(x, y, x + d, y - d/2, x + w + d, y - d/2, x + w, y, fill=lighter_color, outline="black", tags=tags)
    
    def on_drag_start(self, event):
        item = self.canvas.find_closest(event.x, event.y)
        tags = self.canvas.gettags(item)
        unique_tag = next((tag for tag in tags if tag.startswith("draggable_")), None)
        if unique_tag:
            self.drag_data["item"] = unique_tag
            self.drag_data["x"] = event.x
            self.drag_data["y"] = event.y
            self.selected_material_tag = None

    def on_drag_motion(self, event):
        if self.drag_data["item"]:
            delta_x = event.x - self.drag_data["x"]
            delta_y = event.y - self.drag_data["y"]
            self.canvas.move(self.drag_data["item"], delta_x, delta_y)
            self.drag_data["x"] = event.x
            self.drag_data["y"] = event.y
            # Keep material on top during drag
            self.canvas.tag_raise(self.drag_data["item"])


    def on_drag_release(self, event):
        if not self.drag_data["item"]:
            return
        
        items_in_group = self.canvas.find_withtag(self.drag_data["item"])
        if not items_in_group:
            self.drag_data["item"] = None
            return

        bbox = self.canvas.bbox(self.drag_data["item"])
        center_x = (bbox[0] + bbox[2]) / 2
        
        if self.machine_area["x1"] * SCALE_FACTOR < center_x < self.machine_area["x2"] * SCALE_FACTOR:
            material_name = self.drag_data["item"].replace("draggable_", "")
            mat_data = MATERIALS[material_name]
            h_scaled = mat_data["dims"][1] * SCALE_FACTOR
            d_scaled = mat_data["dims"][2] * SCALE_FACTOR
            
            # Snap material on top of the bottom platen
            platen_front_x = ((self.machine_x1 + self.machine_x2) / 2 - self.platen_width / 2) * SCALE_FACTOR
            platen_top_y_front = self.initial_platen_y

            # Calculate x_snap to align the front face of the material with the front face of the platen
            x_snap = platen_front_x + (self.platen_width * SCALE_FACTOR - mat_data["dims"][0] * SCALE_FACTOR) / 2 
            y_snap = platen_top_y_front - h_scaled # Place on top of the front face of the platen

            self.canvas.delete(self.drag_data["item"]) # Delete old elements
            self.draw_material_shape(x_snap, y_snap, mat_data["dims"], mat_data["color"], mat_data["type"], tags=self.drag_data["item"])
            
            self.selected_material_tag = self.drag_data["item"]
            self.event_manager.notify("material_dropped", {"material_data": mat_data})
            self.enable_buttons()
            
            # Ensure material is above bottom platen, and crosshead above material
            self.canvas.tag_raise("bottom_platen")
            self.canvas.tag_raise(self.selected_material_tag)
            self.canvas.tag_raise("crosshead_platen")
            self.canvas.tag_raise("actuator_piston") # Piston rod should also be on top of the crosshead
            self.display_message({"text": "Material placed. Use the controls to start the test.", "color": "green"})
        else:
            material_name = self.drag_data["item"].replace("draggable_", "")
            mat_data = MATERIALS[material_name]
            start_x, start_y = 70 * SCALE_FACTOR, 240 * SCALE_FACTOR
            y_offset = 0
            for name, data in MATERIALS.items():
                if name == material_name:
                    break
                y_offset += data["dims"][1] * SCALE_FACTOR + 40 * SCALE_FACTOR

            self.canvas.delete(self.drag_data["item"])
            self.draw_material_shape(start_x, start_y + y_offset, mat_data["dims"], mat_data["color"], mat_data["type"], tags=self.drag_data["item"])
            
            self.selected_material_tag = None
            self.disable_buttons()
            self.display_message({"text": "Material returned. Drag a new one to the center.", "color": "red"})
        
        self.drag_data["item"] = None
    
    def _draw_deformed_material(self, x1, y1, width, height, depth, color, material_type, tags):
        w, h, d = width, height, depth
        c = color
        
        self.canvas.delete(tags)
        
        if material_type == "pipe":
            self.canvas.create_rectangle(x1, y1, x1 + w, y1 + h, fill=c, outline="black", tags=tags)
        else:
            # Front face rectangle
            self.canvas.create_rectangle(x1, y1, x1 + w, y1 + h, fill=c, outline="black", tags=tags)
            # Right side face polygon
            lighter_color = self.get_lighter_color(c) if self.is_hex_color(c) else c
            self.canvas.create_polygon(x1 + w, y1, x1 + w + d, y1 - d/2, x1 + w + d, y1 + h - d/2, x1 + w, y1 + h, fill=lighter_color, outline="black", tags=tags)
            # Top face polygon
            self.canvas.create_polygon(x1, y1, x1 + d, y1 - d/2, x1 + w + d, y1 - d/2, x1 + w, y1, fill=lighter_color, outline="black", tags=tags)

    def update_gui(self, data):
        if self.selected_material_tag is None:
            return
        
        material_height_scaled = data["material_height"]
        material_width_scaled = data["material_width"]
        material_depth_scaled = data["material_depth"]
        crosshead_new_y = data["crosshead_new_y"]
        current_force = data["current_force"]
        peak_stress = data["peak_stress"]
        test_type = data["test_type"]

        platen_x = ((self.machine_x1 + self.machine_x2) / 2 - self.platen_width / 2) * SCALE_FACTOR
        x1 = platen_x + (self.platen_width * SCALE_FACTOR - material_width_scaled) / 2 # Align material with platen's front face
        
        if test_type == "Compression":
            y1 = self.initial_platen_y - material_height_scaled
        elif test_type == "Tensile":
            y1 = self.initial_platen_y - material_height_scaled
            
        mat_data = MATERIALS[self.selected_material_tag.replace("draggable_", "")]
        self._draw_deformed_material(x1, y1, material_width_scaled, material_height_scaled, material_depth_scaled, mat_data["color"], mat_data["type"], tags=self.selected_material_tag)
        
        self.update_crosshead_position({"y": crosshead_new_y, "test_type": test_type})
        
        self.update_data_labels(current_force, peak_stress, data["material_height"] / SCALE_FACTOR)
        
        self.canvas.tag_raise("bottom_platen")
        self.canvas.tag_raise(self.selected_material_tag)
        self.canvas.tag_raise("crosshead_platen") # this is the moving arm that connect actuator piston with the platen
        self.canvas.tag_raise("actuator_piston") # Ensure piston rod is on top

    def update_crosshead_position(self, data):
        platen_x = ((self.machine_x1 + self.machine_x2) / 2 - self.platen_width / 2) * SCALE_FACTOR
        y_platen = data["y"]
        test_type = data["test_type"]

        platen_bbox = self.canvas.bbox("crosshead_platen")
        
        if platen_bbox:
            dy = y_platen - platen_bbox[1]
            self.canvas.move("crosshead_platen", 0, dy)
        else:
            # If platen not yet drawn, create it.
            self.canvas.create_rectangle(platen_x, y_platen, platen_x + self.platen_width * SCALE_FACTOR, y_platen + self.platen_height * SCALE_FACTOR, fill="#3498DB", outline="#2980B9", width=2, tags="crosshead_platen")
        
        x_piston_center = self.actuator_x * SCALE_FACTOR + self.actuator_width * SCALE_FACTOR / 2
        
        if test_type == "Compression":
            y_piston_top = self.actuator_y * SCALE_FACTOR + self.actuator_height * SCALE_FACTOR
            # Piston rod now connects to the current y_platen of the crosshead
            self._draw_3d_piston_rod(x_piston_center, y_piston_top, platen_x + (self.platen_width * SCALE_FACTOR) / 2, y_platen, test_type="Compression")
        elif test_type == "Tensile":
            y_piston_top = (self.machine_y_top+10) * SCALE_FACTOR + 20 * SCALE_FACTOR
            # Piston rod now connects to the current y_platen of the crosshead
            self._draw_3d_piston_rod(x_piston_center, y_piston_top, platen_x + (self.platen_width * SCALE_FACTOR) / 2, y_platen, test_type="Tensile")
        
        self.canvas.tag_raise("crosshead_platen")
        self.canvas.tag_raise("actuator_piston") # Ensure piston rod is on top
        
    def enable_buttons(self):
        self.calibrate_button.config(state=tk.NORMAL)
        self.start_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.NORMAL)
        self.reset_button.config(state=tk.NORMAL)
        
    def disable_buttons(self):
        self.calibrate_button.config(state=tk.DISABLED)
        self.start_button.config(state=tk.DISABLED)
        self.pause_button.config(state=tk.DISABLED)
        self.reset_button.config(state=tk.DISABLED)
        
    def set_status(self, status):
        if status == "running":
            self.start_button.config(state=tk.DISABLED)
            self.pause_button.config(text="Pause Test", command=lambda: self.event_manager.notify("pause_test"), state=tk.NORMAL)
            self.reset_button.config(state=tk.DISABLED)
            self.light_canvas.itemconfig(self.status_light, fill="#e74c3c", outline="#c0392b")
        elif status == "paused":
            self.pause_button.config(text="Resume Test", command=lambda: self.event_manager.notify("resume_test"))
            self.reset_button.config(state=tk.NORMAL)
            self.light_canvas.itemconfig(self.status_light, fill="#f1c40f", outline="#f39c12")
        elif status == "finished":
            self.start_button.config(state=tk.DISABLED)
            self.pause_button.config(text="Test Finished", state=tk.DISABLED)
            self.reset_button.config(state=tk.NORMAL)
            self.light_canvas.itemconfig(self.status_light, fill="#2ecc71", outline="#27ae60")
        
    def update_data_labels(self, force, stress, height):
        self.force_label.config(text=f"Force: {force:.2f} N")
        self.stress_label.config(text=f"Stress: {stress:.2f} MPa")
        self.dim_label.config(text=f"New Dims: {height:.2f} mm (H)")

    def full_reset(self, data=None):
        if self.selected_material_tag:
            self.canvas.delete(self.selected_material_tag)
            self.selected_material_tag = None
        
        self.draw_materials_to_drag()
        self.disable_buttons()
        self.start_button.config(text="Start Test")
        self.pause_button.config(text="Pause Test", command=lambda: self.event_manager.notify("pause_test"))
        self.update_data_labels(0, 0, 0)
        
        initial_crosshead_y = data.get("initial_crosshead_y", self.initial_crosshead_y)
        self.update_crosshead_position({"y": initial_crosshead_y, "test_type": self.test_type_var.get()})

# -----------------------------------------------------------------------------
# Main Application
# -----------------------------------------------------------------------------
def main():
    root = tk.Tk()
    event_manager = EventManager()
    logic = Logic(root, event_manager)
    gui = GUI(root, event_manager)
    root.mainloop()

if __name__ == "__main__":
    main()